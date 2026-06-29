from __future__ import annotations

from html import escape
from pathlib import Path

_WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
_TEMPLATE = _WEB_ROOT / "templates" / "dashboard.html"
_VALID_PAGES = {"overview", "projects", "kanban", "tasks"}


def render_web_ui(active_page: str) -> str:
    page = active_page if active_page in _VALID_PAGES else "overview"
    template = _TEMPLATE.read_text(encoding="utf-8")
    return template.replace("{{ active_page }}", escape(page, quote=True))
