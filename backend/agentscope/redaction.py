"""Secret redaction applied to every event before it is stored or broadcast."""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|apikey|secret|token|passw(or)?d|passwd|pwd|authorization|auth|cookie|"
    r"set-cookie|credential|private[_-]?key|access[_-]?key|session[_-]?id|bearer|x-api-key)",
    re.I,
)

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), REDACTED),
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*(bearer|basic|token)?\s*[^\s'\",;]+"), r"\1: " + REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{12,}=*"), "Bearer " + REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), REDACTED),
    # scheme://user:pass@host
    (re.compile(r"(?<=://)([^/\s:@]+):([^/\s@]+)@"), r"\1:" + REDACTED + "@"),
    # --password foo / --token=foo / -p foo style flags
    (re.compile(r"(?i)(--?(?:password|passwd|pass|token|secret|api[-_]?key|apikey))([=\s]+)(?!\[REDACTED\])('[^']*'|\"[^\"]*\"|\S+)"), r"\1\2" + REDACTED),
    # KEY=value, "key": "value" where the key looks sensitive
    (re.compile(r"(?i)\b([A-Z0-9_\-]*(?:api[_-]?key|secret|token|passw(?:or)?d|passwd|credential|private[_-]?key|access[_-]?key)[A-Z0-9_\-]*)(\s*[=:]\s*)(?!\[REDACTED\])('[^']*'|\"[^\"]*\"|[^\s,;&}\]]+)"), r"\1\2" + REDACTED),
    (re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token|secret|password|sig|signature)=)[^&\s]+"), r"\1" + REDACTED),
]


class Redactor:
    def __init__(self, extra_patterns: list[str] | None = None, max_len: int = 20000):
        self.extra: list[re.Pattern] = []
        self.max_len = max_len
        for p in extra_patterns or []:
            try:
                self.extra.append(re.compile(p))
            except re.error:
                pass

    def text(self, value: str | None, max_len: int | None = None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        for pat, repl in _PATTERNS:
            value = pat.sub(repl, value)
        for pat in self.extra:
            value = pat.sub(REDACTED, value)
        limit = max_len or self.max_len
        if len(value) > limit:
            value = value[:limit] + f"\n… [truncated {len(value) - limit} chars]"
        return value

    def obj(self, value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "[depth limit]"
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if isinstance(k, str) and SENSITIVE_KEY.search(k) and not isinstance(v, (dict, list)):
                    out[k] = REDACTED
                else:
                    out[k] = self.obj(v, depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            return [self.obj(v, depth + 1) for v in value[:200]]
        if isinstance(value, str):
            return self.text(value, 5000)
        return value
