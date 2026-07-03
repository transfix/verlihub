"""
Web access tools for the hub security bot.

Gives the bot the ability to search the web, fetch pages, and read RSS
feeds — all through LLM tool calls.  Uses only ``httpx`` (already a
project dependency) and stdlib — no extra packages needed.

Tools:

  * ``web_search(query)``   — search via DuckDuckGo instant answers
  * ``fetch_webpage(url)``  — fetch a URL and extract readable text
  * ``read_rss(url)``       — parse an RSS/Atom feed, return headlines
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

log = logging.getLogger("verlihub.bot.web")

# Maximum text returned from a single fetch (characters)
_MAX_TEXT = 4000

# ── HTML → plain text (stdlib only) ─────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s{2,}")
_SCRIPT_RE = re.compile(
    r"<(script|style|noscript)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)


def _html_to_text(raw: str, max_len: int = _MAX_TEXT) -> str:
    """Strip HTML tags and collapse whitespace.  Basic but effective."""
    text = _SCRIPT_RE.sub("", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text


# ── Tool implementations ────────────────────────────────────────────────

async def web_search(query: str) -> str:
    """Search the web using DuckDuckGo instant answers + HTML lite.

    Returns a summary with top results.
    """
    import httpx

    query = query.strip()
    if not query:
        return "Error: search query is required."

    results_parts: list[str] = []

    # 1. DuckDuckGo instant answer API (fast, structured)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Abstract
                abstract = data.get("AbstractText", "")
                if abstract:
                    source = data.get("AbstractSource", "")
                    url = data.get("AbstractURL", "")
                    results_parts.append(
                        f"**{source}**: {abstract}"
                        + (f"\nSource: {url}" if url else "")
                    )
                # Related topics
                for topic in data.get("RelatedTopics", [])[:5]:
                    text = topic.get("Text", "")
                    first_url = topic.get("FirstURL", "")
                    if text:
                        results_parts.append(f"- {text}" + (f" ({first_url})" if first_url else ""))
    except Exception as exc:
        log.debug("DuckDuckGo API error: %s", exc)

    # 2. DuckDuckGo HTML lite fallback (more results)
    if len(results_parts) < 3:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}",
                    headers={"User-Agent": "Verlihub-Bot/1.0"},
                )
                if resp.status_code == 200:
                    # Extract result snippets from the HTML lite page
                    text = resp.text
                    # Each result is in a <td> with class "result-snippet"
                    snippets = re.findall(
                        r'class="result-snippet"[^>]*>(.*?)</td>',
                        text, re.DOTALL | re.IGNORECASE,
                    )
                    links = re.findall(
                        r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                        text, re.DOTALL | re.IGNORECASE,
                    )
                    for i, (url, title) in enumerate(links[:5]):
                        title_clean = _TAG_RE.sub("", title).strip()
                        snippet = (
                            _TAG_RE.sub("", snippets[i]).strip()
                            if i < len(snippets)
                            else ""
                        )
                        results_parts.append(
                            f"- [{title_clean}]({url})"
                            + (f": {snippet}" if snippet else "")
                        )
        except Exception as exc:
            log.debug("DuckDuckGo HTML lite error: %s", exc)

    if not results_parts:
        return f"No results found for: {query}"

    return f"Web search results for '{query}':\n\n" + "\n\n".join(results_parts[:8])


async def fetch_webpage(url: str) -> str:
    """Fetch a webpage and extract its text content."""
    import httpx

    url = url.strip()
    if not url:
        return "Error: URL is required."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Verlihub-Bot/1.0"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                import json
                try:
                    text = json.dumps(resp.json(), indent=2)
                except Exception:
                    text = resp.text
                if len(text) > _MAX_TEXT:
                    text = text[:_MAX_TEXT] + "…"
                return f"Content from {url}:\n\n{text}"

            # HTML or plain text
            text = _html_to_text(resp.text)
            return f"Content from {url}:\n\n{text}"

    except Exception as exc:
        return f"Error fetching {url}: {exc}"


async def read_rss(url: str) -> str:
    """Fetch and parse an RSS or Atom feed, returning recent headlines."""
    import httpx

    url = url.strip()
    if not url:
        return "Error: RSS feed URL is required."

    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Verlihub-Bot/1.0"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        root = ET.fromstring(resp.text)

        items: list[str] = []

        # RSS 2.0
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            if title:
                desc_clean = _html_to_text(desc, max_len=200) if desc else ""
                items.append(
                    f"- {title}" + (f" ({link})" if link else "")
                    + (f"\n  {desc_clean}" if desc_clean else "")
                )
            if len(items) >= 15:
                break

        # Atom
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                link_el = entry.find("atom:link", ns)
                summary_el = entry.find("atom:summary", ns)
                title = title_el.text.strip() if title_el is not None and title_el.text else ""
                link = link_el.get("href", "") if link_el is not None else ""
                summary = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
                if title:
                    summary_clean = _html_to_text(summary, max_len=200) if summary else ""
                    items.append(
                        f"- {title}" + (f" ({link})" if link else "")
                        + (f"\n  {summary_clean}" if summary_clean else "")
                    )
                if len(items) >= 15:
                    break

        if not items:
            return f"No feed items found at {url}."

        return f"RSS feed ({url}) — {len(items)} items:\n\n" + "\n".join(items)

    except ET.ParseError as exc:
        return f"Error parsing feed {url}: {exc}"
    except Exception as exc:
        return f"Error fetching feed {url}: {exc}"


# ── Tool definitions (OpenAI function-calling format) ────────────────

def build_web_tools() -> list[dict[str, Any]]:
    """Return tool schemas for the bot's web access capabilities."""
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web for information on any topic.  Returns "
                    "snippets and links from top results.  Use this to look "
                    "up current events, facts, or anything you're not sure about."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_webpage",
                "description": (
                    "Fetch a specific webpage and extract its text content.  "
                    "Good for reading articles, documentation, or any URL."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch.",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_rss",
                "description": (
                    "Read an RSS or Atom news feed and return recent "
                    "headlines with summaries.  Great for checking news, "
                    "blogs, and updates."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The RSS/Atom feed URL.",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
    ]


async def execute_web_tool(
    fn_name: str,
    fn_args: dict[str, Any],
) -> str | None:
    """Execute a web tool call.  Returns result string, or None if
    *fn_name* is not a web tool."""
    if fn_name == "web_search":
        return await web_search(fn_args.get("query", ""))
    elif fn_name == "fetch_webpage":
        return await fetch_webpage(fn_args.get("url", ""))
    elif fn_name == "read_rss":
        return await read_rss(fn_args.get("url", ""))
    return None  # not a web tool
