import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import producer


def test_parse_search_terms_dedupes_and_trims():
    assert producer.parse_search_terms(" election,Trump, election ,  ,trump ") == ["election", "Trump"]


def test_active_bluesky_queries_prioritizes_recent_topics(monkeypatch):
    monkeypatch.setattr(
        producer,
        "load_priority_topics",
        lambda: [
            {"topic": "biden", "requested_at": "2026-03-22T14:10:00Z"},
            {"topic": "trump", "requested_at": "2026-03-22T14:09:00Z"},
        ],
    )
    monkeypatch.setattr(producer, "prune_priority_topics", lambda items: items)
    monkeypatch.setattr(producer, "save_priority_topics", lambda items: None)
    monkeypatch.setattr(producer, "BLUESKY_SEARCH_TERMS", "trump,election")

    assert producer.active_bluesky_queries() == ["biden", "trump", "election"]


def test_normalize_ttrss_article_sets_rss_tag(monkeypatch):
    monkeypatch.setattr(producer, "fetch_full_content", lambda url, fallback="": fallback)
    raw_article = {
        "id": 123,
        "title": "Election update",
        "content": "RSS body",
        "link": "https://example.com/article",
        "feed_id": 9,
        "feed_title": "TT Feed",
        "updated": 1736510400,
        "author": "Editor",
    }

    article = producer.normalize_ttrss_article(raw_article)

    assert article.source == "ttrss"
    assert article.tags == ["rss feed"]


def test_normalize_bluesky_post_sets_social_media_tag():
    raw_post = {
        "text": "Election turnout looks stronger than expected in several districts.",
        "created_at": "2025-01-10T12:30:00Z",
        "web_url": "https://bsky.app/profile/test-user/post/3kabc",
        "uri": "at://did:plc:123/app.bsky.feed.post/3kabc",
        "author": {"handle": "test-user.bsky.social", "display_name": "Test User"},
    }

    article = producer.normalize_bluesky_post(raw_post, "election")

    assert article.source == "bluesky"
    assert article.tags == ["social media", "election"]
    assert article.author == "Test User"
    assert article.link == "https://bsky.app/profile/test-user/post/3kabc"
    assert article.content.startswith("Election turnout looks stronger")


def test_process_bluesky_skips_known_posts(monkeypatch):
    class StubFetcher:
        def search_posts(self, query, limit):
            return [
                {
                    "text": "Trump rally post",
                    "created_at": "2025-01-10T12:30:00Z",
                    "uri": "at://did:plc:123/app.bsky.feed.post/known",
                    "author": {"handle": "reporter.bsky.social"},
                },
                {
                    "text": "New trump post",
                    "created_at": "2025-01-10T12:35:00Z",
                    "uri": "at://did:plc:123/app.bsky.feed.post/new",
                    "author": {"handle": "reporter.bsky.social"},
                },
            ]

    published_ids = []

    def fake_publish(_producer, article):
        published_ids.append(article.id)

    monkeypatch.setattr(producer, "BLUESKY_SEARCH_TERMS", "trump")
    monkeypatch.setattr(producer, "publish", fake_publish)
    monkeypatch.setattr(producer, "save_bluesky_state", lambda state: None)

    state = {"trump": ["at://did:plc:123/app.bsky.feed.post/known"]}
    processed, updated_state = producer.process_bluesky(object(), StubFetcher(), state)

    assert processed == 1
    assert published_ids == ["at://did:plc:123/app.bsky.feed.post/new"]
    assert updated_state["trump"][-1] == "at://did:plc:123/app.bsky.feed.post/new"
