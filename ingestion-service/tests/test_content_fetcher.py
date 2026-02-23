"""
tests/test_content_fetcher.py
Unit tests for services/content_fetcher.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
import httpx

from services.content_fetcher import fetch_full_content


SAMPLE_HTML = """
<html>
<body>
<article>
  <h1>Kerala Elections Announced</h1>
  <p>The Election Commission of India has announced the schedule for the upcoming Kerala
  state assembly elections. Voting is expected to take place in April. Major parties have
  begun campaign preparations across all 140 constituencies in the state.</p>
</article>
<nav>Home | About | Contact</nav>
</body>
</html>
"""

FALLBACK = "<p>Short RSS excerpt</p>"


class TestFetchFullContent:

    def test_empty_url_returns_fallback(self):
        result = fetch_full_content("", fallback=FALLBACK)
        assert result == FALLBACK

    def test_none_like_empty_url(self):
        result = fetch_full_content(url="", fallback="my fallback")
        assert result == "my fallback"

    @patch("services.content_fetcher.httpx.get")
    @patch("services.content_fetcher.trafilatura.extract")
    def test_successful_extraction(self, mock_extract, mock_get):
        """trafilatura returns meaningful text → that text is returned."""
        mock_response = MagicMock()
        mock_response.text = SAMPLE_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        extracted = "Kerala Elections Announced. The Election Commission of India has announced the schedule."
        mock_extract.return_value = extracted

        result = fetch_full_content("http://example.com/article", fallback=FALLBACK)
        assert result == extracted

    @patch("services.content_fetcher.httpx.get")
    @patch("services.content_fetcher.trafilatura.extract")
    def test_trivial_extracted_content_falls_back(self, mock_extract, mock_get):
        """If trafilatura returns < 50 chars, fallback is used."""
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        mock_extract.return_value = "Too short."  # < 50 chars

        result = fetch_full_content("http://example.com/article", fallback=FALLBACK)
        assert result == FALLBACK

    @patch("services.content_fetcher.httpx.get")
    @patch("services.content_fetcher.trafilatura.extract")
    def test_none_from_trafilatura_falls_back(self, mock_extract, mock_get):
        """If trafilatura returns None (paywall/bot check), fallback is used."""
        mock_response = MagicMock()
        mock_response.text = "<html><body>Please verify you're human</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        mock_extract.return_value = None

        result = fetch_full_content("http://example.com/paywalled", fallback=FALLBACK)
        assert result == FALLBACK

    @patch("services.content_fetcher.httpx.get", side_effect=httpx.TimeoutException("timeout"))
    def test_timeout_returns_fallback(self, mock_get):
        result = fetch_full_content("http://slow-site.com/article", fallback=FALLBACK)
        assert result == FALLBACK

    @patch("services.content_fetcher.httpx.get")
    def test_http_404_returns_fallback(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        mock_get.return_value = mock_response

        result = fetch_full_content("http://example.com/missing", fallback=FALLBACK)
        assert result == FALLBACK

    @patch("services.content_fetcher.httpx.get", side_effect=Exception("Connection refused"))
    def test_unexpected_exception_returns_fallback(self, mock_get):
        result = fetch_full_content("http://unreachable.example.com/", fallback=FALLBACK)
        assert result == FALLBACK
