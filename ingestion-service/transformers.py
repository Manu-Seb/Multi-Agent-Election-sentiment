from datetime import datetime, timezone
from typing import List
from schemas import Article, TTRSSRawArticle

def normalize_article(raw: TTRSSRawArticle | dict, query_tags: List[str]) -> Article:
    """
    Transforms a raw TT-RSS article (dict or model) into the strict API schema.
    """
    # If raw is a dict, parse it first to ensure validation? 
    # Or just handle dict access for speed/simplicity. Let's assume dict from API.
    
    data = raw
    if isinstance(raw, TTRSSRawArticle):
        data = raw.model_dump()
        
    # Handle timestamp: TT-RSS 'updated' is usually Unix int
    # We must return ISO-8601 UTC
    unix_time = data.get("updated", 0)
    dt = datetime.fromtimestamp(unix_time, tz=timezone.utc)
    
    return Article(
        id=data.get("id"),
        title=data.get("title", ""),
        content=data.get("content", ""),
        link=data.get("link", ""),
        feed_id=data.get("feed_id"),
        feed_title=data.get("feed_title", ""),
        published_at=dt,
        author=data.get("author") if data.get("author") else None,
        tags=query_tags
    )
