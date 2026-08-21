from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def load_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_DOTENV = load_dotenv()


def setting(name: str, default: str) -> str:
    return os.environ.get(name) or _DOTENV.get(name) or default


def int_setting(name: str, default: int) -> int:
    value = setting(name, str(default))
    try:
        return int(value)
    except ValueError:
        return default


SOFIFA_VERSION_URL_PART = setting("SOFIFA_VERSION_URL_PART", "260045")
SOFIFA_VERSION_LABEL = setting("SOFIFA_VERSION_LABEL", "Jul 16, 2026")
SOFIFA_LOCALE = setting("SOFIFA_LOCALE", "es-ES")
TRANSFERMARKT_BATCH_LIMIT = int_setting("TRANSFERMARKT_BATCH_LIMIT", 100)
