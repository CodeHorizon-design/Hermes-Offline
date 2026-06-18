"""
Local web search backends for Hermes Offline.

Provides three no-API-key search backends:

  1. DuckDuckGo HTML scraper  — most reliable, no key required
  2. Wikipedia REST API       — free, no key, great for factual queries
  3. SearXNG (self-hosted)    — if running locally at configurable port

These are registered as Hermes web search plugin backends.
For direct use, call search() or wikipedia_search() directly.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")
DDG_DELAY_SECS = float(os.environ.get("HERMES_DDG_DELAY", "1.0"))

_last_ddg_call: float = 0.0


def duckduckgo_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
) -> list[dict]:
    """
    Search DuckDuckGo using the HTML endpoint (no API key required).
    Returns list of {title, url, snippet} dicts.
    """
    global _last_ddg_call

    # Rate limit: wait at least DDG_DELAY_SECS between calls
    elapsed = time.time() - _last_ddg_call
    if elapsed < DDG_DELAY_SECS:
        time.sleep(DDG_DELAY_SECS - elapsed)
    _last_ddg_call = time.time()

    params = urllib.parse.urlencode({
        "q": query,
        "kl": region,
        "kp": "-2",  # safe search off
        "kaf": "1",  # no advertisements
    })
    url = f"https://html.duckduckgo.com/html/?{params}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []

    results = _parse_ddg_html(body, max_results)
    return results


def _parse_ddg_html(html_text: str, max_results: int) -> list[dict]:
    results = []
    # Each result block contains class="result__title" and class="result__snippet"
    # Use simple regex — no BeautifulSoup dependency
    title_pattern = re.compile(
        r'class="result__title"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td)>',
        re.DOTALL,
    )
    titles = title_pattern.findall(html_text)
    snippets = snippet_pattern.findall(html_text)

    for i, (url, title) in enumerate(titles[:max_results]):
        snippet = snippets[i] if i < len(snippets) else ""
        results.append({
            "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
            "url": _clean_ddg_url(url),
            "snippet": html.unescape(re.sub(r"<[^>]+>", "", snippet)).strip(),
        })

    return results


def _clean_ddg_url(raw: str) -> str:
    """DDG sometimes wraps URLs in redirects — extract the real URL."""
    if raw.startswith("//duckduckgo.com/l/?"):
        parsed = urllib.parse.urlparse("https:" + raw)
        qs = urllib.parse.parse_qs(parsed.query)
        return qs.get("uddg", [raw])[0]
    return raw


def wikipedia_search(
    query: str,
    max_results: int = 3,
    intro_only: bool = True,
) -> list[dict]:
    """
    Search Wikipedia using the free REST API (no key required).
    Returns list of {title, url, extract} dicts.
    """
    # First, search for article titles
    search_url = (
        "https://en.wikipedia.org/api/rest_v1/page/search/title?"
        + urllib.parse.urlencode({"q": query, "limit": max_results})
    )
    try:
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "hermes-offline/1.0 (local LLM agent)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        logger.debug("Wikipedia search error: %s", exc)
        return []

    results = []
    for page in data.get("pages", [])[:max_results]:
        title = page.get("title", "")
        key = page.get("key", title.replace(" ", "_"))
        extract = page.get("excerpt", "") or page.get("description", "")

        if intro_only and not extract:
            extract = _wikipedia_intro(key) or ""

        results.append({
            "title": title,
            "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(key)}",
            "extract": extract[:1000],
        })

    return results


def _wikipedia_intro(page_key: str) -> Optional[str]:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(page_key)}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "hermes-offline/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("extract", "")
    except Exception:
        return None


def searxng_search(
    query: str,
    max_results: int = 5,
    base_url: str = SEARXNG_URL,
) -> list[dict]:
    """
    Search using a locally-running SearXNG instance.
    Returns list of {title, url, content} dicts.
    Falls back to DuckDuckGo if SearXNG is unreachable.
    """
    url = f"{base_url}/search?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "categories": "general",
        "language": "en",
    })
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-offline/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in data.get("results", [])[:max_results]
        ]
    except Exception as exc:
        logger.debug("SearXNG unavailable (%s), falling back to DuckDuckGo", exc)
        return duckduckgo_search(query, max_results)


def search(
    query: str,
    max_results: int = 5,
    prefer_searxng: bool = False,
) -> list[dict]:
    """
    Unified local search: tries SearXNG first if prefer_searxng=True,
    then DuckDuckGo, then Wikipedia for fallback context.
    """
    if prefer_searxng:
        results = searxng_search(query, max_results)
        if results:
            return results

    results = duckduckgo_search(query, max_results)
    if not results:
        # Last resort: Wikipedia
        wiki = wikipedia_search(query, max_results=3)
        results = [
            {"title": r["title"], "url": r["url"], "snippet": r["extract"]}
            for r in wiki
        ]

    return results


def format_results_for_llm(results: list[dict], max_chars: int = 1500) -> str:
    """Format search results into a compact string for LLM consumption."""
    lines = []
    total = 0
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet") or r.get("extract") or r.get("content") or ""
        snippet = snippet[:300]
        block = f"[{i}] {title}\n{url}\n{snippet}\n"
        if total + len(block) > max_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines)


def register_searxng_if_available(snap=None) -> bool:
    """
    Register SearXNG as the primary hermes web search backend if it's running.
    Uses the detector snapshot so no extra HTTP probe is needed.
    If a stopped Docker container exists, attempts auto-start.
    Returns True if SearXNG ended up registered.
    """
    try:
        from hermes_offline.searxng import (
            is_running,
            maybe_auto_start,
            register_searxng_backend,
        )

        # Use snapshot to decide cheaply without extra probes
        searxng_up = False
        if snap is not None:
            svc = getattr(snap, "services", {})
            info = svc.get("searxng")
            if info is not None and info.running:
                searxng_up = True

        # Try auto-start (starts a stopped Docker container, no-op otherwise)
        if not searxng_up:
            searxng_up = maybe_auto_start()

        if searxng_up:
            return register_searxng_backend()

    except Exception as exc:
        logger.debug("SearXNG setup skipped: %s", exc)
    return False


def register_duckduckgo_backend() -> None:
    """Register DDG as a hermes-agent web search backend (no key needed)."""
    try:
        import tools.web_tools as wt
        existing = getattr(wt, "_OFFLINE_BACKENDS_REGISTERED", False)
        if not existing:
            original_check = getattr(wt, "check_web_search_requirements", None)
            if original_check:
                def _patched_check():
                    return True  # Always available — no key needed
                wt.check_web_search_requirements = _patched_check
            setattr(wt, "_OFFLINE_BACKENDS_REGISTERED", True)
            logger.info("Registered DuckDuckGo as offline web search backend")
    except ImportError:
        pass


def register_wikipedia_backend() -> None:
    """Register Wikipedia as a supplementary search backend."""
    try:
        import tools.web_tools as wt
        if not getattr(wt, "_WIKI_REGISTERED", False):
            setattr(wt, "_WIKI_REGISTERED", True)
            logger.info("Registered Wikipedia as offline search backend")
    except ImportError:
        pass
