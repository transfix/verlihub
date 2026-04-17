"""
Tests for the bot web access tools (verlihub.bot_web).

Covers:
- HTML → plain text conversion
- web_search with mocked HTTP responses
- fetch_webpage with mocked HTTP responses + JSON and HTML paths
- read_rss with RSS 2.0 and Atom feeds
- build_web_tools schema
- execute_web_tool dispatcher
- Edge cases (empty inputs, HTTP errors)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verlihub.bot.web import (
    _html_to_text,
    build_web_tools,
    execute_web_tool,
    fetch_webpage,
    read_rss,
    web_search,
)


# ---------------------------------------------------------------------------
# _html_to_text helper
# ---------------------------------------------------------------------------

class TestHtmlToText:

    def test_simple_html(self):
        text = _html_to_text("<p>Hello <b>world</b></p>")
        assert "Hello" in text
        assert "world" in text
        assert "<" not in text

    def test_strips_script_tags(self):
        html = "<script>alert('x')</script><p>Content</p>"
        text = _html_to_text(html)
        assert "alert" not in text
        assert "Content" in text

    def test_strips_style_tags(self):
        html = "<style>body { color: red; }</style><p>Content</p>"
        text = _html_to_text(html)
        assert "color" not in text
        assert "Content" in text

    def test_unescapes_entities(self):
        text = _html_to_text("&amp; &lt; &gt; &quot;")
        assert "&" in text
        assert "<" in text
        assert ">" in text

    def test_max_length(self):
        long_html = "<p>" + "x" * 10000 + "</p>"
        text = _html_to_text(long_html, max_len=100)
        assert len(text) <= 101  # 100 + "…"
        assert text.endswith("…")

    def test_collapses_whitespace(self):
        text = _html_to_text("<p>  lots   of    spaces  </p>")
        assert "  " not in text


# ---------------------------------------------------------------------------
# web_search (mocked HTTP)
# ---------------------------------------------------------------------------

class TestWebSearch:

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await web_search("")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_ddg_instant_answer(self):
        """Mock DuckDuckGo instant answer API response."""
        import httpx

        ddg_resp = MagicMock()
        ddg_resp.status_code = 200
        ddg_resp.json.return_value = {
            "AbstractText": "Python is a programming language.",
            "AbstractSource": "Wikipedia",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python",
            "RelatedTopics": [],
        }

        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = "<html><body>No results</body></html>"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[ddg_resp, html_resp])

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await web_search("Python")

        assert "Python" in result
        assert "programming" in result

    @pytest.mark.asyncio
    async def test_search_http_error(self):
        """HTTP error should return gracefully."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await web_search("test")

        # Should still return something (possibly an error or partial)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# fetch_webpage (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchWebpage:

    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await fetch_webpage("")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_html_response(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html><body><p>Hello from the web</p></body></html>"
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_webpage("https://example.com")

        assert "Hello from the web" in result
        assert "example.com" in result

    @pytest.mark.asyncio
    async def test_json_response(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"key": "value"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_webpage("https://api.example.com/data")

        assert "key" in result
        assert "value" in result

    @pytest.mark.asyncio
    async def test_prepends_https(self):
        """URLs without scheme should get https:// prepended."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<p>OK</p>"
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_webpage("example.com")

        assert "OK" in result

    @pytest.mark.asyncio
    async def test_http_error(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_webpage("https://example.com/404")

        assert "Error" in result


# ---------------------------------------------------------------------------
# read_rss (mocked HTTP)
# ---------------------------------------------------------------------------

_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Article One</title>
      <link>https://example.com/1</link>
      <description>First article description</description>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://example.com/2</link>
      <description>Second article description</description>
    </item>
  </channel>
</rss>"""

_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Atom Entry</title>
    <link href="https://example.com/atom/1"/>
    <summary>Atom summary text</summary>
  </entry>
</feed>"""


class TestReadRss:

    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await read_rss("")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_rss_feed(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _RSS_XML
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await read_rss("https://example.com/feed")

        assert "Article One" in result
        assert "Article Two" in result
        assert "2 items" in result

    @pytest.mark.asyncio
    async def test_atom_feed(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _ATOM_XML
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await read_rss("https://example.com/atom")

        assert "Atom Entry" in result

    @pytest.mark.asyncio
    async def test_invalid_xml(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "this is not XML"
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await read_rss("https://example.com/bad")

        assert "Error" in result


# ---------------------------------------------------------------------------
# build_web_tools
# ---------------------------------------------------------------------------

class TestBuildWebTools:

    def test_returns_three_tools(self):
        tools = build_web_tools()
        assert len(tools) == 3

    def test_tool_names(self):
        tools = build_web_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == {"web_search", "fetch_webpage", "read_rss"}

    def test_tool_format(self):
        tools = build_web_tools()
        for tool in tools:
            assert tool["type"] == "function"
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]


# ---------------------------------------------------------------------------
# execute_web_tool dispatcher
# ---------------------------------------------------------------------------

class TestExecuteWebTool:

    @pytest.mark.asyncio
    async def test_web_search_dispatch(self):
        with patch("verlihub.bot.web.web_search", new_callable=AsyncMock, return_value="results"):
            result = await execute_web_tool("web_search", {"query": "test"})
        assert result == "results"

    @pytest.mark.asyncio
    async def test_fetch_webpage_dispatch(self):
        with patch("verlihub.bot.web.fetch_webpage", new_callable=AsyncMock, return_value="page"):
            result = await execute_web_tool("fetch_webpage", {"url": "https://x.com"})
        assert result == "page"

    @pytest.mark.asyncio
    async def test_read_rss_dispatch(self):
        with patch("verlihub.bot.web.read_rss", new_callable=AsyncMock, return_value="feed"):
            result = await execute_web_tool("read_rss", {"url": "https://x.com/rss"})
        assert result == "feed"

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await execute_web_tool("unknown_tool", {})
        assert result is None
