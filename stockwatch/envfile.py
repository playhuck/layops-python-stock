"""프로젝트 루트 .env 읽기 (값은 로그/예외 메시지에 싣지 않는다)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env(key: str, default: str | None = None) -> str:
    v = os.environ.get(key)
    if v:
        return v
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    if default is not None:
        return default
    raise KeyError(f".env 에 {key} 없음")
