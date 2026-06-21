from src.scraper import Announcement
from src.state_store import StateStore


def _ann(n):
    return Announcement(title=f"title-{n}", url=f"https://example.com/{n}")


class TestStateStore:
    def test_is_first_run_when_no_file(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        assert store.is_first_run() is True

    def test_mark_seen_then_not_first_run(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.mark_seen([_ann(1)])
        assert store.is_first_run() is False

    def test_diff_new_excludes_seen(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.mark_seen([_ann(1)])
        new_items = store.diff_new([_ann(1), _ann(2)])
        assert [a.key for a in new_items] == ["https://example.com/2"]

    def test_mark_seen_accumulates_across_calls(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.mark_seen([_ann(1)])
        store.mark_seen([_ann(2)])
        seen = store.load_seen_keys()
        assert seen == {"https://example.com/1", "https://example.com/2"}

    def test_load_seen_keys_handles_corrupt_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not valid json", encoding="utf-8")
        store = StateStore(str(path))
        assert store.load_seen_keys() == set()
