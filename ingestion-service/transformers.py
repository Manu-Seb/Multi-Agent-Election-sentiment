import asyncio
from datetime import datetime, timezone
from typing import List
from schemas import Article, TTRSSRawArticle
from services.content_fetcher import fetch_full_content


async def normalize_article(raw: TTRSSRawArticle | dict, query_tags: List[str]) -> Article:
    """
    Transforms a raw TT-RSS article (dict or model) into the strict API schema.

    Fetches the full article body from the article URL using trafilatura
    (in a thread pool so the async event loop is not blocked).
    Falls back to the TT-RSS RSS excerpt on any failure.
    """
    data = raw
    if isinstance(raw, TTRSSRawArticle):
        data = raw.model_dump()

    # Handle timestamp: TT-RSS 'updated' is usually Unix int
    unix_time = data.get("updated", 0)
    dt = datetime.fromtimestamp(unix_time, tz=timezone.utc)

    # Fetch full article content in a thread pool (non-blocking)
    ttrss_excerpt = data.get("content", "")
    full_content = await asyncio.to_thread(
        fetch_full_content, data.get("link", ""), ttrss_excerpt
    )

    return Article(
        id=data.get("id"),
        title=data.get("title", ""),
        content=full_content,
        link=data.get("link", ""),
        feed_id=data.get("feed_id"),
        feed_title=data.get("feed_title", ""),
        published_at=dt,
        author=data.get("author") if data.get("author") else None,
        tags=query_tags
    )
