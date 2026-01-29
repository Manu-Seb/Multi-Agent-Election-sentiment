from fastapi import FastAPI, HTTPException, Query
from typing import List, Optional
from datetime import datetime, timezone
import logging

from config import settings
from schemas import Article
from services.ttrss import TTRSSClient
from transformers import normalize_article

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion-service")

app = FastAPI(title="TT-RSS Ingestion Service")

# Dependency: Client
# For simplicity, using a global instance since it holds the session state.
# In a rigorous dependency injection setup, we might manage this differently.
ttrss_client = TTRSSClient()

@app.get("/api/v1/articles", response_model=List[Article])
async def get_articles(
    q: Optional[str] = Query(None, min_length=1, description="Search query (optional)"),
    limit: int = Query(20, ge=1, le=100, description="Max articles to fetch"),
    since: Optional[datetime] = Query(None, description="Filter articles published after this timestamp (ISO-8601)")
):
    """
    Fetches, filters, and normalizes articles from TT-RSS.
    """
    logger.info(f"Received request: q='{q}', limit={limit}, since={since}")
    
    try:
        # 1. Fetch from TT-RSS
        search_query = q if q else ""
        raw_items = await ttrss_client.get_headlines(query=search_query, limit=limit)
        
        filtered_items = []
        
        # 2. Local Filtering (Time & Robust Query Check)
        for item in raw_items:
            # Check Time
            item_ts = item.get("updated", 0)
            item_dt = datetime.fromtimestamp(item_ts, tz=timezone.utc)
            
            if since:
                check_since = since
                if check_since.tzinfo is None:
                    check_since = check_since.replace(tzinfo=timezone.utc)
                
                if item_dt < check_since:
                    continue
            
            # Robust Keyword Check only if Q is provided
            if q:
                title_text = item.get("title", "").lower()
                content_text = item.get("content", "").lower()
                query_lower = q.lower()
                
                if query_lower not in title_text and query_lower not in content_text:
                    continue
                
            filtered_items.append(item)
            
        # 3. Normalization
        # If Q is provided, use it as tag. If not, empty tags.
        tags = [q] if q else []
        
        normalized_response = [
            normalize_article(item, query_tags=tags) 
            for item in filtered_items
        ]
        
        return normalized_response

    except Exception as e:
        logger.error(f"Error processing request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}
