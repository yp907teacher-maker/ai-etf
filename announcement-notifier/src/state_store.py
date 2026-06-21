"""Tracks which announcements have already been notified, persisted as JSON
so state survives across separate GitHub Actions runs.
"""
import json
import logging
from pathlib import Path
from typing import List, Set

from .scraper import Announcement

log = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def is_first_run(self) -> bool:
        return not self.path.exists()

    def load_seen_keys(self) -> Set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read state file %s: %s", self.path, exc)
            return set()
        return set(data.get("seen_keys", []))

    def save_seen_keys(self, keys: Set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"seen_keys": sorted(keys)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def diff_new(self, announcements: List[Announcement]) -> List[Announcement]:
        seen = self.load_seen_keys()
        return [a for a in announcements if a.key not in seen]

    def mark_seen(self, announcements: List[Announcement]) -> None:
        seen = self.load_seen_keys()
        seen.update(a.key for a in announcements)
        self.save_seen_keys(seen)
