from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("AGENTSCOPE_DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "agentscope.db"
FRONTEND_DIST = Path(os.environ.get("AGENTSCOPE_FRONTEND_DIST", "")) if os.environ.get("AGENTSCOPE_FRONTEND_DIST") else None
CORS_ORIGINS = [o for o in os.environ.get("AGENTSCOPE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o]
ADMIN_USER = os.environ.get("AGENTSCOPE_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("AGENTSCOPE_ADMIN_PASSWORD", "")
TOKEN_TTL_SECONDS = int(os.environ.get("AGENTSCOPE_TOKEN_TTL", str(12 * 3600)))

# Mutable runtime settings (persisted in DB, editable from the Settings page).
DEFAULT_SETTINGS = {
    "retention_days": 14,
    "geoip_provider": "off",          # off | ipapi  (ipapi sends destination IPs to ip-api.com)
    "resolve_dns": True,              # resolve hostnames seen in tool args to IPs
    "allow_file_content_view": False, # let operators read file contents out of monitored containers
    "allow_exec_probe": False,        # allow `docker exec cat /proc/net/tcp` network probe (opt-in)
    "allow_intervention": False,      # allow pause/resume of monitored agents (admin only)
    "extra_redaction_patterns": [],
    "sensitive_paths": [],
    "max_text_len": 20000,
}
