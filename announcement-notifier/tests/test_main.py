import json
from unittest.mock import patch

import pytest

from src import main as main_module
from src.scraper import Announcement


def _write_config(tmp_path):
    config = {
        "source": {
            "name": "測試學校",
            "list_url": "https://example.com/news",
            "base_url": "https://example.com/",
            "list_selector": None,
        },
        "state_file": "data/state.json",
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


@pytest.fixture(autouse=True)
def _base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "BASE_DIR", tmp_path)
    return tmp_path


class TestRun:
    def test_first_run_records_baseline_without_notifying(self, tmp_path):
        config_path = _write_config(tmp_path)
        items = [Announcement(title="A", url="https://example.com/1")]

        with patch.object(main_module.AnnouncementScraper, "fetch", return_value=items), \
             patch.object(main_module.AnnouncementScraper, "fetch_image", return_value=None), \
             patch.object(main_module.LineNotifier, "send_announcement") as mock_line, \
             patch.object(main_module.EmailNotifier, "send") as mock_email:
            main_module.run(config_path)

        mock_line.assert_not_called()
        mock_email.assert_not_called()
        assert (tmp_path / "data" / "state.json").exists()

    def test_no_new_announcements_skips_notify(self, tmp_path):
        config_path = _write_config(tmp_path)
        items = [Announcement(title="A", url="https://example.com/1")]

        with patch.object(main_module.AnnouncementScraper, "fetch", return_value=items):
            main_module.run(config_path)  # first run: baseline only

        with patch.object(main_module.AnnouncementScraper, "fetch", return_value=items), \
             patch.object(main_module.AnnouncementScraper, "fetch_image", return_value=None), \
             patch.object(main_module.LineNotifier, "send_announcement") as mock_line, \
             patch.object(main_module.EmailNotifier, "send") as mock_email:
            main_module.run(config_path)  # second run: same items, no new

        mock_line.assert_not_called()
        mock_email.assert_not_called()

    def test_new_announcement_triggers_notifications(self, tmp_path):
        config_path = _write_config(tmp_path)
        first = [Announcement(title="A", url="https://example.com/1")]
        second = first + [Announcement(title="B", url="https://example.com/2")]

        with patch.object(main_module.AnnouncementScraper, "fetch", return_value=first):
            main_module.run(config_path)  # baseline

        with patch.object(main_module.AnnouncementScraper, "fetch", return_value=second), \
             patch.object(
                 main_module.AnnouncementScraper, "fetch_image", return_value="https://example.com/img.jpg"
             ), \
             patch.object(main_module.LineNotifier, "send_announcement") as mock_line, \
             patch.object(main_module.EmailNotifier, "send") as mock_email:
            main_module.run(config_path)

        mock_line.assert_called_once()
        mock_email.assert_called_once()
        assert "B" in mock_line.call_args.args[0]
        assert mock_line.call_args.kwargs["image_url"] == "https://example.com/img.jpg"
        assert mock_email.call_args.kwargs["image_url"] == "https://example.com/img.jpg"
