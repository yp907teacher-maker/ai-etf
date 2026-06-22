from pathlib import Path
from unittest.mock import Mock, patch

from src.scraper import AnnouncementScraper

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "sample_list_page.html").read_text(
    encoding="utf-8"
)


def _fake_response(text="", status_code=200, content_type="text/html"):
    resp = Mock()
    resp.text = text
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = Mock()
    return resp


class TestFeedDiscovery:
    def test_no_feed_found_falls_back_to_html(self):
        scraper = AnnouncementScraper(list_url="https://example.com/news")
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _fake_response(status_code=404) for _ in range(5)
            ] + [_fake_response(text=FIXTURE_HTML)]
            items = scraper.fetch()

        assert len(items) == 3
        assert items[0].title == "期末考試時間表公告"


class TestHtmlFallback:
    def test_table_selector_extracts_title_url_date(self):
        scraper = AnnouncementScraper(
            list_url="https://example.com/news", base_url="https://example.com/"
        )
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _fake_response(status_code=404) for _ in range(5)
            ] + [_fake_response(text=FIXTURE_HTML)]
            items = scraper.fetch()

        assert [a.title for a in items] == [
            "期末考試時間表公告",
            "畢業典禮彩排通知",
            "暑假輔導課程報名",
        ]
        assert items[0].url == "https://example.com/news/101"
        assert items[0].date == "2026-06-20"

    def test_no_matches_returns_empty_list(self):
        scraper = AnnouncementScraper(list_url="https://example.com/news")
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _fake_response(status_code=404) for _ in range(5)
            ] + [_fake_response(text="<html><body><p>no links here</p></body></html>")]
            items = scraper.fetch()

        assert items == []

    def test_explicit_selector_is_tried_first(self):
        scraper = AnnouncementScraper(
            list_url="https://example.com/news", selector="table tr td a"
        )
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _fake_response(status_code=404) for _ in range(5)
            ] + [_fake_response(text=FIXTURE_HTML)]
            items = scraper.fetch()

        assert len(items) == 3


class TestFetchImage:
    def test_prefers_og_image(self):
        scraper = AnnouncementScraper(list_url="https://example.com/news")
        html = """
        <html><head>
          <meta property="og:image" content="/img/og.jpg">
        </head><body>
          <img src="/img/content.jpg">
        </body></html>
        """
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.return_value = _fake_response(text=html)
            url = scraper.fetch_image("https://example.com/news/1")

        assert url == "https://example.com/img/og.jpg"

    def test_falls_back_to_first_content_image(self):
        scraper = AnnouncementScraper(list_url="https://example.com/news")
        html = """
        <html><body>
          <img src="/img/logo.png">
          <img src="/img/photo.jpg">
        </body></html>
        """
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.return_value = _fake_response(text=html)
            url = scraper.fetch_image("https://example.com/news/1")

        assert url == "https://example.com/img/photo.jpg"

    def test_returns_none_when_no_image_found(self):
        scraper = AnnouncementScraper(list_url="https://example.com/news")
        html = "<html><body><img src='/img/logo.png'></body></html>"
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.return_value = _fake_response(text=html)
            url = scraper.fetch_image("https://example.com/news/1")

        assert url is None

    def test_returns_none_on_request_failure(self):
        import requests

        scraper = AnnouncementScraper(list_url="https://example.com/news")
        with patch("src.scraper.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("boom")
            url = scraper.fetch_image("https://example.com/news/1")

        assert url is None


class TestAnnouncementKey:
    def test_key_uses_url_when_present(self):
        from src.scraper import Announcement

        ann = Announcement(title="t", url="https://example.com/1", date="2026-01-01")
        assert ann.key == "https://example.com/1"

    def test_key_falls_back_to_title_and_date(self):
        from src.scraper import Announcement

        ann = Announcement(title="t", url="", date="2026-01-01")
        assert ann.key == "t|2026-01-01"
