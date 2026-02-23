"""
content_fetcher.py
Responsible for fetching the full HTML of an article URL and extracting
clean plain-text article body using trafilatura.

This module is deliberately synchronous so it can be used directly in:
  - producer.py  (sync Kafka loop)
  - transformers.py (called via asyncio.to_thread in the async FastAPI path)
"""

import logging
import httpx
import trafilatura

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 15.0  # seconds
MIN_CONTENT_LENGTH = 50  # chars — anything shorter is likely a paywall / bot-check page

# Identify ourselves politely to news sites
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ElectionSentimentBot/1.0; "
        "+https://github.com/your-org/election-sentiment)"
    )
}


def fetch_full_content(url: str, fallback: str = "") -> str:
    """
    Fetches the article at `url` and extracts clean plain-text using trafilatura.

    Args:
        url:      The article URL to fetch.
        fallback: Value to return on any failure (e.g. TT-RSS RSS excerpt).

    Returns:
        Extracted plain-text on success, `fallback` on any error.
    """
    if not url:
        logger.debug("fetch_full_content called with empty URL, returning fallback")
        return fallback

    try:
        resp = httpx.get(
            url,
            headers=_HEADERS,
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            verify=False,
        )
        resp.raise_for_status()

        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=False,   # favor recall — better for news
        )

        if text and len(text.strip()) >= MIN_CONTENT_LENGTH:
            logger.debug(f"Fetched full content for {url} ({len(text)} chars)")
            return text.strip()

        logger.warning(
            f"trafilatura returned empty/trivial content for {url} "
            f"(got {len(text.strip()) if text else 0} chars), using fallback"
        )
        return fallback

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching article content: {url}")
        return fallback
    except httpx.HTTPStatusError as e:
        logger.warning(f"HTTP {e.response.status_code} fetching article: {url}")
        return fallback
    except Exception as e:
        logger.warning(f"Unexpected error fetching article content for {url}: {e}")
        return fallback
