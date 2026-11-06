"""OpenAI-compatible observing proxy.

Point an agent's `base_url` at  http://<agentscope>/proxy/<integration-id>/<proxy-key>/v1  and AgentScope
forwards each call to the real upstream while recording model, token usage, duration and any
tool calls the model *requested*. The client's own Authorization header is forwarded but never stored.

Observable: requests/responses that pass through the proxy. NOT observable: whether/how the agent
then executed a requested tool (that is a separate instrumentation point).
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..schema import Event
from .base import Adapter, register

HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "accept-encoding", "x-agentscope-key"}


@register
class OpenAIProxy(Adapter):
    framework = "openai_proxy"
    display_name = "OpenAI-compatible proxy (custom agents)"
    mode = "push"
    description = "Observe any agent that talks to an OpenAI-compatible API (OpenAI, Ollama, LM Studio, vLLM, OpenRouter …)."
    config_schema = [
        {"name": "upstream_url", "label": "Upstream base URL", "type": "text", "default": "http://localhost:11434/v1", "required": True,
         "help": "Real API base, e.g. https://api.openai.com/v1"},
        {"name": "agent_name", "label": "Agent name", "type": "text", "default": "custom-agent"},
        {"name": "api_key", "label": "Proxy key (goes in the URL)", "type": "password", "default": "", "help": "Auto-generated on save if blank."},
        {"name": "capture_content", "label": "Record prompt / completion text (redacted)", "type": "bool", "default": True},
    ]
    capabilities = {"llm_calls": "verified (they pass through the proxy)", "tool_calls": "tool calls requested by the model (execution not observed)",
                    "tokens": "from the upstream usage field when provided"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.http: httpx.AsyncClient | None = None

    async def run(self):
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))
        self.set_status("running", f"proxying -> {self.config.get('upstream_url')}", calls=self.stats.get("calls", 0))
        while True:
            await asyncio.sleep(3600)

    async def stop(self):
        if self.http:
            await self.http.aclose()

    def check_key(self, key: str) -> bool:
        exp = self.config.get("api_key") or ""
        return bool(exp) and secrets.compare_digest(exp, key or "")

    async def test(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get(self.config["upstream_url"].rstrip("/") + "/models")
            return {"ok": r.status_code < 500, "message": f"Upstream answered HTTP {r.status_code}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}

    # ---- request handling ----
    async def handle(self, request: Request, path: str) -> Response:
        assert self.http
        body = await request.body()
        url = self.config["upstream_url"].rstrip("/") + "/" + path.lstrip("/")
        if url.rstrip("/").endswith("/v1/v1") or "/v1/v1" in url:
            url = url.replace("/v1/v1", "/v1")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        is_llm = path.rstrip("/").endswith(("chat/completions", "completions", "responses", "embeddings"))
        req: dict[str, Any] = {}
        if body and is_llm:
            try:
                req = json.loads(body)
            except Exception:
                req = {}
        agent_id = f"proxy:{self.id}"
        eid = uuid.uuid4().hex
        t0 = time.time()
        model = req.get("model")
        capture = bool(self.config.get("capture_content", True))
        prompt = None
        if capture and req.get("messages"):
            last = [m for m in req["messages"] if m.get("role") in ("user", "tool")][-1:]
            prompt = json.dumps(last[0].get("content"), ensure_ascii=False)[:3000] if last else None
        base = dict(event_id=eid, agent_id=agent_id, agent_name=self.config.get("agent_name") or "custom-agent",
                    event_type="llm_call", action="llm_call", target=model or path, tool="llm", source="openai_proxy",
                    instance_id=self.id, evidence="execution", input=prompt)
        meta = {"model": model, "endpoint": path, "stream": bool(req.get("stream")), "message_count": len(req.get("messages") or []),
                "tools_offered": [t.get("function", {}).get("name") for t in req.get("tools") or []][:30]}
        if is_llm:
            await self.emit(Event(status="running", metadata=meta, **base))
        self.stats["calls"] = self.stats.get("calls", 0) + 1
        try:
            upstream = await self.http.send(self.http.build_request(request.method, url, headers=headers, content=body or None,
                                                                    params=request.query_params), stream=True)
        except Exception as e:  # noqa: BLE001
            if is_llm:
                await self.emit(Event(status="failed", metadata={**meta, "error": str(e)}, duration_ms=(time.time() - t0) * 1000, **base))
            return JSONResponse({"error": {"message": f"AgentScope proxy could not reach upstream: {e}"}}, status_code=502)
        out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP | {"content-encoding"}}
        streaming = "text/event-stream" in upstream.headers.get("content-type", "")

        async def finish(data: dict | None, raw_text: str | None):
            if not is_llm:
                return
            usage = (data or {}).get("usage") or {}
            tool_calls = []
            text = None
            if data:
                ch = (data.get("choices") or [{}])[0].get("message") or {}
                tool_calls = ch.get("tool_calls") or []
                text = ch.get("content")
            m = {**meta, "status_code": upstream.status_code,
                 "tokens": {"input": usage.get("prompt_tokens") or usage.get("input_tokens"),
                            "output": usage.get("completion_tokens") or usage.get("output_tokens")}}
            await self.emit(Event(status="completed" if upstream.status_code < 400 else "failed", metadata=m,
                                  duration_ms=(time.time() - t0) * 1000, output=(text or raw_text) if capture else None, **base))
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                await self.emit(Event(event_id=f"{eid}:tc{i}", agent_id=agent_id, agent_name=base["agent_name"], event_type="tool_execution",
                                      action="tool_call", target=fn.get("name"), tool=fn.get("name"), status="info", source="openai_proxy",
                                      instance_id=self.id, evidence="tool_call", parent_event_id=eid, input=fn.get("arguments"),
                                      metadata={"requested_by_model": model, "note": "Model requested this tool; execution is not observed by the proxy."}))

        if streaming:
            async def gen() -> AsyncIterator[bytes]:
                buf, text, calls, usage = b"", [], {}, {}
                try:
                    async for chunk in upstream.aiter_raw():
                        buf += chunk
                        yield chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line = line.strip()
                            if not line.startswith(b"data:") or line.endswith(b"[DONE]"):
                                continue
                            try:
                                j = json.loads(line[5:])
                            except Exception:
                                continue
                            usage = j.get("usage") or usage
                            for ch in j.get("choices") or []:
                                d = ch.get("delta") or {}
                                if d.get("content"):
                                    text.append(d["content"])
                                for tc in d.get("tool_calls") or []:
                                    c = calls.setdefault(tc.get("index", 0), {"function": {"name": "", "arguments": ""}})
                                    f = tc.get("function") or {}
                                    c["function"]["name"] += f.get("name") or ""
                                    c["function"]["arguments"] += f.get("arguments") or ""
                finally:
                    await upstream.aclose()
                    await finish({"usage": usage, "choices": [{"message": {"content": "".join(text), "tool_calls": list(calls.values())}}]}, None)
            return StreamingResponse(gen(), status_code=upstream.status_code, headers=out_headers,
                                     media_type=upstream.headers.get("content-type"))
        raw = await upstream.aread()
        await upstream.aclose()
        data = None
        try:
            data = json.loads(raw)
        except Exception:
            pass
        await finish(data, raw.decode(errors="replace")[:2000] if data is None else None)
        return Response(raw, status_code=upstream.status_code, headers=out_headers, media_type=upstream.headers.get("content-type"))
