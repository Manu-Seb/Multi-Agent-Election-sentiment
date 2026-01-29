import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import sys
import os

# Add parent to path to import main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from schemas import Article

client = TestClient(app)

# Sample Response from TT-RSS (Mock)
MOCK_TTRSS_RESPONSE = [
    {
        "id": 100,
        "title": "Kerala Elections Announced",
        "content": "<p>Big news in Kerala today...</p>",
        "link": "http://example.com/100",
        "feed_id": 1,
        "feed_title": "News Feed",
        "updated": 1735689600, # 2025-01-01 00:00:00 UTC
        "author": "Editor"
    },
    {
        "id": 101,
        "title": "Unrelated News",
        "content": "Something about mars",
        "link": "http://example.com/101",
        "feed_id": 2,
        "feed_title": "Space Feed",
        "updated": 1704067200, # 2024-01-01 (Old)
        "author": None
    }
]

@patch("services.ttrss.TTRSSClient.get_headlines", new_callable=AsyncMock)
def test_get_articles_success(mock_get_headlines):
    mock_get_headlines.return_value = MOCK_TTRSS_RESPONSE
    
    # query matches first item
    response = client.get("/api/v1/articles?q=Kerala")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == 100 # Or string "100" depending on pydantic coercion
    assert item["title"] == "Kerala Elections Announced"
    assert item["tags"] == ["Kerala"]
    assert item["published_at"] == "2025-01-01T00:00:00Z"

@patch("services.ttrss.TTRSSClient.get_headlines", new_callable=AsyncMock)
def test_get_articles_since_filter(mock_get_headlines):
    mock_get_headlines.return_value = MOCK_TTRSS_RESPONSE
    
    # Both items theoretically match "News" if we assume loose matching (but logic enforces content match)
    # Let's adjust mock so both match "query" but we filter by time
    
    two_match_response = [
        MOCK_TTRSS_RESPONSE[0],
        {
            "id": 102,
            "title": "Old Kerala News",
            "content": "Kerala history...",
            "updated": 1609459200, # 2021
            "feed_id": 1, "feed_title": "News", "link": "x"
        }
    ]
    mock_get_headlines.return_value = two_match_response
    
    # Filter since 2024
    since_ts = "2024-01-01T00:00:00Z"
    response = client.get(f"/api/v1/articles?q=Kerala&since={since_ts}")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == 100

@patch("services.ttrss.TTRSSClient.get_headlines", new_callable=AsyncMock)
def test_get_articles_strict_query(mock_get_headlines):
    mock_get_headlines.return_value = MOCK_TTRSS_RESPONSE
    
    # "Banana" is in neither
    response = client.get("/api/v1/articles?q=Banana")
    
    assert response.status_code == 200
    assert len(response.json()) == 0

@patch("services.ttrss.TTRSSClient.get_headlines", new_callable=AsyncMock)
def test_get_articles_no_query(mock_get_headlines):
    mock_get_headlines.return_value = MOCK_TTRSS_RESPONSE
    
    # No query provided -> fetch all
    response = client.get("/api/v1/articles")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["tags"] == []

