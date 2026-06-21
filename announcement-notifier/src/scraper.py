"""Generic announcement scraper.

Tries an RSS/Atom feed first (common on school CMS sites), then falls back
to configurable CSS-selector based HTML scraping. When no selector is
configured, a list of common selectors is tried in order so this works
out-of-the-box against typical announcement/news-list page layouts.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Tried in order when no explicit selector is configured.
FALLBACK_SELECTORS = [
    "ul.news-list li a",
    "ul.list li a",
    ".announcement-list a",
    ".news a",
    "table tr td a",
    "article a",
    "main a",
]

FEED_PATHS = ("?feed=rss2", "rss.xml", "feed", "feed/", "rss")

_DATE_PATTERN = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")


@dataclass
class Announcement:
    title: str
    url: str
    date: Optional[str] = None

    @property
    def key(self) -> str:
        """Stable identity for de-duplication across runs."""
        return self.url or f"{self.title}|{self.date or ''}"


class AnnouncementScraper:
    def __init__(
        self,
        list_url: str,
        base_url: Optional[str] = None,
        selector: Optional[str] = None,
        timeout: int = 15,
    ):
        self.list_url = list_url
        self.base_url = base_url or list_url
        self.selector = selector
        self.timeout = timeout

    def fetch(self) -> List[Announcement]:
        items = self._try_feed()
        if items:
            return items
        return self._scrape_html()

    # ── RSS/Atom feed discovery ──────────────────────────────────────────────

    def _try_feed(self) -> List[Announcement]:
        for path in FEED_PATHS:
            url = urljoin(self.base_url, path)
            try:
                resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=self.timeout)
            except requests.RequestException:
                continue
            content_type = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or "xml" not in content_type:
                continue
            items = self._parse_feed(resp.text)
            if items:
                log.info("Using RSS/Atom feed: %s", url)
                return items
        return []

    def _parse_feed(self, xml_text: str) -> List[Announcement]:
        soup = BeautifulSoup(xml_text, "xml")
        items: List[Announcement] = []
        for entry in soup.find_all(["item", "entry"]):
            title_tag = entry.find("title")
            link_tag = entry.find("link")
            date_tag = entry.find("pubDate") or entry.find("updated") or entry.find("published")

            title = title_tag.get_text(strip=True) if title_tag else ""
            link = ""
            if link_tag:
                link = link_tag.get_text(strip=True) or link_tag.get("href", "")
            if title and link:
                items.append(
                    Announcement(
                        title=title,
                        url=urljoin(self.base_url, link),
                        date=date_tag.get_text(strip=True) if date_tag else None,
                    )
                )
        return items

    # ── HTML fallback ────────────────────────────────────────────────────────

    def _scrape_html(self) -> List[Announcement]:
        resp = requests.get(self.list_url, headers=DEFAULT_HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        selectors = [self.selector] if self.selector else FALLBACK_SELECTORS
        for sel in selectors:
            if not sel:
                continue
            anchors = soup.select(sel)
            items = self._anchors_to_announcements(anchors)
            if items:
                log.info("Matched %d announcement(s) with selector %r", len(items), sel)
                return items

        log.warning("No announcements matched any selector for %s", self.list_url)
        return []

    def _anchors_to_announcements(self, anchors) -> List[Announcement]:
        items: List[Announcement] = []
        seen_urls = set()
        for a in anchors:
            title = a.get_text(strip=True)
            href = (a.get("href") or "").strip()
            if not title or not href or href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            url = urljoin(self.base_url, href)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            date = None
            context = a.find_parent("tr") or a.find_parent("li") or a.parent
            context_text = context.get_text(" ", strip=True) if context else ""
            match = _DATE_PATTERN.search(context_text)
            if match:
                date = match.group(0)

            items.append(Announcement(title=title, url=url, date=date))
        return items
