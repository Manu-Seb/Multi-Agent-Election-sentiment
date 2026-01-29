from pydantic import BaseModel, HttpUrl, Field
from typing import Optional, List
from datetime import datetime

class Article(BaseModel):
    id: str | int = Field(..., description="Unique ID of the article from TT-RSS")
    title: str = Field(..., description="Article title")
    content: str = Field(..., description="Article content (HTML or text)")
    link: str = Field(..., description="URL to the original article")
    feed_id: int = Field(..., description="ID of the source feed")
    feed_title: str = Field(..., description="Title of the source feed")
    published_at: datetime = Field(..., description="ISO-8601 UTC timestamp")
    author: Optional[str] = Field(None, description="Article author")
    tags: List[str] = Field(default_factory=list, description="Matched query keywords acting as tags")

# Internal model for raw TT-RSS response parsing (partial)
class TTRSSRawArticle(BaseModel):
    id: int
    title: str
    content: str
    link: str
    feed_id: int
    feed_title: str
    updated: int  # TT-RSS returns unix timestamp
    author: Optional[str] = None
