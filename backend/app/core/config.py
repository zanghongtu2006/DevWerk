# app/core/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pathlib import Path
from dotenv import load_dotenv

def _is_dev_env(env_name: str) -> bool:
    v = (env_name or "").strip().lower()
    print("==================================" + v)
    return v in ("dev", "development", "develop", "local")


def _detect_env_name(root: Path) -> str:
    # 1) 真实环境变量优先
    for k in ("APP_ENV", "ENV", "ENVIRONMENT"):
        v = os.getenv(k)
        if v and str(v).strip():
            return str(v).strip()

    # 2) 如果没设置真实 env，则从 .env 里读取（只用于判断，不注入环境变量）
    base_env = _parse_env_file(root / ".env")
    for k in ("APP_ENV", "ENV", "ENVIRONMENT"):
        v = base_env.get(k)
        if v and str(v).strip():
            return str(v).strip()

    return "production"

def _load_dotenvs() -> None:
    """
    Priority:
      1) real env vars (never overwritten)
      2) .env.development / .env.developement (dev only)
      3) .env
    """
    root = _project_root()
    env_name = _detect_env_name(root)
    print("==========================env_name: " + env_name)
    if _is_dev_env(env_name):
        for fname in [".env.development", ".env.dev"]:
            print("=========================" + fname)
            p = root / fname
            if p.exists():
                load_dotenv(dotenv_path=p, override=False)

    p = root / ".env"
    if p.exists():
        load_dotenv(dotenv_path=p, override=False)

def _project_root() -> Path:
    # backend/app/core/config.py -> backend/
    return Path(__file__).resolve().parents[2]

def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Very small .env parser:
    - supports KEY=VALUE
    - ignores blank lines and comments (# ...)
    - strips optional quotes around VALUE
    """
    data: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return data

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()

        # remove inline comment: KEY=VALUE # comment
        # only if there is a space before '#'
        if " #" in v:
            v = v.split(" #", 1)[0].strip()

        # strip quotes
        if (len(v) >= 2) and ((v[0] == v[-1]) and v[0] in ("'", '"')):
            v = v[1:-1]

        if k:
            data[k] = v
    return data


def _load_env_file_if_present(path: Path) -> None:
    """
    Load KEY=VALUE into os.environ ONLY IF the key is not already present.
    This ensures: real env vars > .env files
    """
    kv = _parse_env_file(path)
    for k, v in kv.items():
        if k not in os.environ:
            os.environ[k] = v

def _get_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


def _get_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None:
        return default
    s = str(v).strip()
    return s if s != "" else default


def _get_opt_str(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None


@dataclass(frozen=True)
class Settings:
    """
    LLM Provider:
      - ollama (your current local/remote ollama)
      - openai (GPT / OpenAI-compatible)
      - xai (Grok / usually OpenAI-compatible style)
      - gemini (Google Gemini)
    """
    llm_provider: str

    # ---- ollama (keep backward compatibility) ----
    ollama_base_url: str
    ollama_model: str
    ollama_timeout: float

    # ---- OpenAI (GPT) ----
    openai_base_url: str
    openai_api_key: Optional[str]
    openai_model: str
    openai_timeout: float

    # ---- xAI (Grok) ----
    xai_base_url: str
    xai_api_key: Optional[str]
    xai_model: str
    xai_timeout: float

    # ---- Gemini ----
    gemini_base_url: str
    gemini_api_key: Optional[str]
    gemini_model: str
    gemini_timeout: float

    @staticmethod
    def from_env() -> "Settings":
        # 1) Load .env files (env vars still have priority)
        _load_dotenvs()

        # 2) Provider selection
        provider = _get_str("LLM_PROVIDER", "ollama").lower()

        # 3) Ollama
        ollama_base = _get_str("OLLAMA_BASE_URL", "http://127.0.0.1:12434").rstrip("/")
        ollama_model = _get_str("OLLAMA_MODEL", "deepseek-r1:32b")
        ollama_timeout = _get_float("OLLAMA_TIMEOUT", 180.0)

        # 4) OpenAI / GPT (also supports OPENAI_API_KEY)
        openai_base = _get_str("OPENAI_BASE_URL", "").rstrip("/")
        openai_key = _get_opt_str("OPENAI_API_KEY")
        openai_model = _get_str("OPENAI_MODEL", "")
        openai_timeout = _get_float("OPENAI_TIMEOUT", 180.0)

        # 5) xAI / Grok
        xai_base = _get_str("XAI_BASE_URL", "").rstrip("/")
        xai_key = _get_opt_str("XAI_API_KEY")
        xai_model = _get_str("XAI_MODEL", "")
        xai_timeout = _get_float("XAI_TIMEOUT", 180.0)

        # 6) Gemini (support GEMINI_API_KEY)
        gemini_base = _get_str("GEMINI_BASE_URL", "").rstrip("/")
        gemini_key = _get_opt_str("GEMINI_API_KEY") or _get_opt_str("GOOGLE_API_KEY")
        gemini_model = _get_str("GEMINI_MODEL", "")
        gemini_timeout = _get_float("GEMINI_TIMEOUT", 180.0)

        return Settings(
            llm_provider=provider,

            ollama_base_url=ollama_base,
            ollama_model=ollama_model,
            ollama_timeout=ollama_timeout,

            openai_base_url=openai_base,
            openai_api_key=openai_key,
            openai_model=openai_model,
            openai_timeout=openai_timeout,

            xai_base_url=xai_base,
            xai_api_key=xai_key,
            xai_model=xai_model,
            xai_timeout=xai_timeout,

            gemini_base_url=gemini_base,
            gemini_api_key=gemini_key,
            gemini_model=gemini_model,
            gemini_timeout=gemini_timeout,
        )
