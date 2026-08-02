"""Entry point: scrape announcements, notify on new ones via LINE + Email.

Usage:
  python -m src.main [--config path/to/config.json]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from .email_notifier import EmailNotifier
from .line_notifier import LineNotifier
from .scraper import Announcement, AnnouncementScraper
from .state_store import StateStore

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def format_message(source_name: str, ann: Announcement) -> str:
    lines = [f"📢 {source_name} 新公告", ann.title]
    if ann.date:
        lines.append(f"日期: {ann.date}")
    lines.append(ann.url)
    return "\n".join(lines)


def run(config_path: Path = None) -> int:
    config_path = config_path or (BASE_DIR / "config.json")
    config = load_config(config_path)

    source = config["source"]
    source_name = source.get("name", "公告")
    state = StateStore(str(BASE_DIR / config["state_file"]))
    scraper = AnnouncementScraper(
        list_url=source["list_url"],
        base_url=source.get("base_url"),
        selector=source.get("list_selector"),
    )

    announcements = scraper.fetch()
    if not announcements:
        log.warning("No announcements found — check list_selector in config.json")
        return 0

    if state.is_first_run():
        log.info(
            "First run — recording %d announcement(s) as baseline, no notifications sent",
            len(announcements),
        )
        state.mark_seen(announcements)
        return 0

    new_items = state.diff_new(announcements)
    if not new_items:
        log.info("No new announcements")
        return 0

    log.info("Found %d new announcement(s)", len(new_items))
    line = LineNotifier()
    email = EmailNotifier()

    for ann in new_items:
        ann.image_url = scraper.fetch_image(ann.url)
        if ann.image_url:
            log.info("Image found for %r: %s", ann.title, ann.image_url)
        else:
            log.info("No image found for %r — sending text only", ann.title)
        message = format_message(source_name, ann)
        line.send_announcement(message, image_url=ann.image_url)
        email.send(
            subject=f"[{source_name}] {ann.title}",
            body=message,
            image_url=ann.image_url,
        )

    state.mark_seen(new_items)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Check for new announcements and notify.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.json")
    args = parser.parse_args()
    return run(args.config)


if __name__ == "__main__":
    sys.exit(main())
