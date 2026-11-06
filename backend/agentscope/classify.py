"""Heuristics: network scope classification, command/URL extraction, risk hints.

Everything produced here from script/command text is *inferred* and must be
labelled as such by the caller.
"""
from __future__ import annotations

import ipaddress
import re
import shlex
from urllib.parse import urlparse

URL_RE = re.compile(r"""\b(?:https?|wss?|ftp)://[^\s'"<>)\]}]+""", re.I)
DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "ssh": 22}
PORT_PROTO = {80: "http", 443: "https", 22: "ssh", 53: "dns", 21: "ftp", 25: "smtp", 5432: "postgres",
              3306: "mysql", 6379: "redis", 27017: "mongodb", 11434: "ollama", 8080: "http-alt"}

SENSITIVE_PATHS = [
    "/etc/shadow", "/etc/passwd", "/etc/sudoers", "/root", ".ssh", ".aws", ".gnupg", ".kube",
    ".env", "id_rsa", "id_ed25519", "secrets.env", "/proc/", "/sys/", "/var/run/docker.sock",
    ".git-credentials", ".netrc", ".npmrc", ".pypirc", "credentials",
]

COMMON_EXECUTABLES = {
    "ls", "cat", "echo", "cd", "pwd", "python", "python3", "pip", "pip3", "node", "npm", "npx", "git", "grep",
    "find", "head", "tail", "sed", "awk", "mkdir", "touch", "cp", "mv", "curl", "wget", "tar", "unzip", "which",
    "env", "printf", "sleep", "sort", "uniq", "wc", "diff", "tee", "chmod", "export", "source", "true", "false",
    "test", "date", "whoami", "uname", "df", "du", "ps", "top", "apt", "apt-get", "bash", "sh", "yarn", "pnpm",
}


def classify_host(host: str | None, ip: str | None = None) -> str:
    """Return: localhost | docker | internal | external | unknown."""
    h = (host or "").lower().strip("[]")
    if h in ("localhost", "ip6-localhost") or h.endswith(".localhost"):
        return "localhost"
    if h in ("host.docker.internal", "gateway.docker.internal") or h.endswith(".docker.internal"):
        return "docker"
    candidate = ip or h
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        if h and "." not in h:  # bare hostname: compose service name / LAN host
            return "docker" if h else "unknown"
        if h.endswith((".local", ".lan", ".internal", ".home.arpa")):
            return "internal"
        return "external" if h else "unknown"
    if addr.is_loopback:
        return "localhost"
    if isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("172.16.0.0/12"):
        return "docker"
    if addr.is_private or addr.is_link_local:
        return "internal"
    if addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        return "unknown"
    return "external"


def parse_target(target: str) -> dict:
    """Parse URL/host[:port] into host, port, scheme."""
    t = target.strip()
    if "://" not in t:
        t = "//" + t
    try:
        u = urlparse(t)
        host = u.hostname or ""
        port = u.port
    except ValueError:
        return {"host": target, "port": None, "scheme": None, "path": None}
    scheme = u.scheme or None
    if port is None and scheme:
        port = DEFAULT_PORTS.get(scheme)
    return {"host": host, "port": port, "scheme": scheme, "path": (u.path or "") + (("?" + u.query) if u.query else "")}


def extract_urls(text: str, limit: int = 8) -> list[str]:
    seen, out = set(), []
    for m in URL_RE.findall(text or ""):
        m = m.rstrip(".,;:")
        if m not in seen:
            seen.add(m)
            out.append(m)
        if len(out) >= limit:
            break
    return out


def first_executable(cmd: str) -> str | None:
    """Best-effort name of the first executable in a shell command line."""
    for part in re.split(r"(?:&&|\|\||;|\|)", cmd or ""):
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = part.split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith(("/", "."))):
            tokens.pop(0)
        if tokens and tokens[0] in ("sudo", "nohup", "time", "exec"):
            tokens.pop(0)
        if tokens:
            return tokens[0].rsplit("/", 1)[-1]
    return None


def all_executables(cmd: str) -> list[str]:
    out = []
    for part in re.split(r"(?:&&|\|\||;|\|)", cmd or ""):
        e = first_executable(part)
        if e:
            out.append(e)
    return out


DELETE_RE = re.compile(r"(?:^|[;&|\s])(?:rm|rmdir|unlink|shred)\s|os\.(?:remove|unlink|rmdir)|shutil\.rmtree|Path\([^)]*\)\.unlink|fs\.(?:unlink|rm)", re.I)


def looks_like_delete(text: str) -> bool:
    return bool(DELETE_RE.search(text or ""))


def sensitive_path_hit(path: str | None, extra: list[str] | None = None) -> str | None:
    p = (path or "").lower()
    for s in SENSITIVE_PATHS + [x.lower() for x in (extra or [])]:
        if s.lower() in p:
            return s
    return None


def sensitive_in_text(text: str, extra: list[str] | None = None) -> str | None:
    t = (text or "").lower()
    for s in SENSITIVE_PATHS + [x.lower() for x in (extra or [])]:
        if len(s) > 4 and s.lower() in t:
            return s
    return None
