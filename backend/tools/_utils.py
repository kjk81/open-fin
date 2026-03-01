from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup
import markdownify

from schemas.tool_contracts import ToolTiming

STRIP_TAGS: list[str] = ["script", "style", "nav", "footer", "header", "aside"]

# Backward-compatible alias
_STRIP_TAGS = STRIP_TAGS


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def build_timing(tool_name: str, started_at: datetime) -> ToolTiming:
    return ToolTiming(tool_name=tool_name, started_at=started_at, ended_at=now_utc())


def html_to_markdown(html: str) -> str:
    """Strip boilerplate tags then convert HTML into dense markdown."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()
    return markdownify.markdownify(str(soup), heading_style="ATX", strip=["a"])