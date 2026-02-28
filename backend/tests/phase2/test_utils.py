"""Phase 2 — Tests for tools/_utils.py."""

from __future__ import annotations

from datetime import datetime, timezone

from tools._utils import build_timing, html_to_markdown, now_utc, STRIP_TAGS


class TestNowUtc:
    def test_returns_naive_datetime(self):
        result = now_utc()
        assert result.tzinfo is None

    def test_is_approximately_utc(self):
        before = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        result = now_utc()
        after = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        assert before <= result <= after


class TestBuildTiming:
    def test_returns_tool_timing(self):
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        timing = build_timing("my_tool", t0)
        assert timing.tool_name == "my_tool"
        assert timing.duration_ms >= 0


class TestHtmlToMarkdown:
    def test_strips_script_tags(self):
        html = "<html><body><script>alert('xss')</script><p>Hello</p></body></html>"
        md = html_to_markdown(html)
        assert "alert" not in md
        assert "Hello" in md

    def test_strips_style_tags(self):
        html = "<style>body{color:red}</style><p>Content</p>"
        md = html_to_markdown(html)
        assert "color:red" not in md
        assert "Content" in md

    def test_strips_nav_footer_header(self):
        html = "<nav>Menu</nav><main><h1>Title</h1></main><footer>Foot</footer>"
        md = html_to_markdown(html)
        assert "Menu" not in md
        assert "Foot" not in md
        assert "Title" in md

    def test_empty_input(self):
        md = html_to_markdown("")
        assert md == "" or md.strip() == ""

    def test_deeply_nested_divs(self):
        html = "<div>" * 50 + "<p>Deep content</p>" + "</div>" * 50
        md = html_to_markdown(html)
        assert "Deep content" in md

    def test_unicode_content(self):
        html = "<p>日本語テスト — café résumé</p>"
        md = html_to_markdown(html)
        assert "日本語" in md
        assert "café" in md

    def test_heading_style_atx(self):
        html = "<h2>Heading Two</h2>"
        md = html_to_markdown(html)
        assert "##" in md


class TestStripTags:
    def test_strip_tags_constant(self):
        assert "script" in STRIP_TAGS
        assert "style" in STRIP_TAGS
        assert "nav" in STRIP_TAGS
