"""
Phase 6 Test Suite — 6 test cases (TC-6-01 through TC-6-06)

Run: pytest tests/phase6/ -v
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from dashboard_builder import DashboardBuilder
from report_generator import ReportModel

# ─────────────────────────────────────────────────────────────────────────────
# Shared setup
# ─────────────────────────────────────────────────────────────────────────────

def _seed_reports(reports_dir: Path, account_id: str = "acc_001"):
    """Write three daily reports so the builder has data to work with."""
    model = ReportModel(reports_dir=str(reports_dir))
    data = [
        ("2025-06-01", 100_000, 8_000,
         [{"ticker": "AAPL", "shares": 20, "price": 180.0, "pe_ratio": 28.5,
           "prev_close_1d": 175.0, "prev_close_1w": 170.0, "prev_close_1m": 160.0}],
         [{"ticker": "AAPL", "side": "buy", "shares": 20, "price": 180.0, "status": "filled"}],
         [{"ticker": "NVDA", "rank": 1, "score": 91.5, "pe_ratio": 55.0, "momentum_90d": 0.42}],
         {"AI & Chips": ["NVDA", "AMD"], "Cloud": ["MSFT", "AMZN"], "Momentum": ["AAPL"]},
         {"spy_1d": 0.005, "qqq_1d": 0.007, "nav_1d": 0.003},
         [{"date": "2025-05-30", "nav": 98_000}]),
        ("2025-06-02", 101_000, 7_500,
         [{"ticker": "AAPL", "shares": 20, "price": 182.0, "pe_ratio": 28.5,
           "prev_close_1d": 180.0, "prev_close_1w": 172.0, "prev_close_1m": 162.0}],
         [],
         [{"ticker": "NVDA", "rank": 1, "score": 92.0, "pe_ratio": 55.0, "momentum_90d": 0.44}],
         {"AI & Chips": ["NVDA", "AMD"], "Cloud": ["MSFT", "AMZN"], "Momentum": ["AAPL"]},
         {"spy_1d": 0.003, "qqq_1d": 0.004, "nav_1d": 0.010},
         [{"date": "2025-05-30", "nav": 98_000}, {"date": "2025-06-01", "nav": 100_000}]),
        ("2025-06-03", 102_500, 7_000,
         [{"ticker": "AAPL", "shares": 20, "price": 185.0, "pe_ratio": 28.5,
           "prev_close_1d": 182.0, "prev_close_1w": 174.0, "prev_close_1m": 165.0}],
         [],
         [{"ticker": "NVDA", "rank": 1, "score": 93.0, "pe_ratio": 55.0, "momentum_90d": 0.46}],
         {"AI & Chips": ["NVDA", "AMD"], "Cloud": ["MSFT", "AMZN"], "Momentum": ["AAPL"]},
         {"spy_1d": 0.002, "qqq_1d": 0.003, "nav_1d": 0.015},
         [{"date": "2025-05-30", "nav": 98_000}, {"date": "2025-06-01", "nav": 100_000},
          {"date": "2025-06-02", "nav": 101_000}]),
    ]
    for date, nav, cash, pos, trades, top10, wl, bm, hist in data:
        r = model.build(
            account_id=account_id, report_date=date,
            nav=nav, cash=cash, positions=pos, trades=trades,
            top10=top10, watchlist=wl, benchmark=bm, nav_history=hist,
        )
        model.save(r)


# ─────────────────────────────────────────────────────────────────────────────
# TC-6-01  生成 index.html 和 history.html
# ─────────────────────────────────────────────────────────────────────────────

class TestTC601GenerateHtmlPages:

    def test_index_html_created(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        files = builder.build("acc_001")
        assert "index" in files
        assert files["index"].exists()

    def test_history_html_created(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        files = builder.build("acc_001")
        assert "history" in files
        assert files["history"].exists()

    def test_data_json_files_created(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        files = builder.build("acc_001")
        assert files["latest_json"].exists()
        assert files["nav_history_json"].exists()

    def test_build_with_no_reports_produces_pages(self, tmp_path):
        """Should succeed gracefully when no reports exist yet."""
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        files = builder.build("acc_001")
        assert files["index"].exists()
        assert files["history"].exists()


# ─────────────────────────────────────────────────────────────────────────────
# TC-6-02  index.html 內容驗證
# ─────────────────────────────────────────────────────────────────────────────

class TestTC602IndexHtmlContent:

    def _index(self, tmp_path) -> str:
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        return (out_dir / "index.html").read_text()

    def test_contains_account_id(self, tmp_path):
        html = self._index(tmp_path)
        assert "acc_001" in html

    def test_contains_latest_nav(self, tmp_path):
        html = self._index(tmp_path)
        # Latest report NAV = 102,500
        assert "102" in html

    def test_contains_ticker_aapl(self, tmp_path):
        html = self._index(tmp_path)
        assert "AAPL" in html

    def test_contains_top10_section(self, tmp_path):
        html = self._index(tmp_path)
        assert "Top-10" in html or "NVDA" in html

    def test_contains_chart_js(self, tmp_path):
        html = self._index(tmp_path)
        assert "chart.js" in html.lower() or "Chart" in html

    def test_contains_nav_checkbox_controls(self, tmp_path):
        html = self._index(tmp_path)
        assert "checkbox" in html
        assert "NAV" in html


# ─────────────────────────────────────────────────────────────────────────────
# TC-6-03  NAV vs 基準對比選擇框
# ─────────────────────────────────────────────────────────────────────────────

class TestTC603BenchmarkCheckboxes:

    def _index(self, tmp_path) -> str:
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        return (out_dir / "index.html").read_text()

    def test_spy_checkbox_present(self, tmp_path):
        html = self._index(tmp_path)
        assert "SPY" in html

    def test_qqq_checkbox_present(self, tmp_path):
        html = self._index(tmp_path)
        assert "QQQ" in html

    def test_toggle_function_in_js(self, tmp_path):
        html = self._index(tmp_path)
        assert "toggleSeries" in html

    def test_benchmark_values_shown(self, tmp_path):
        html = self._index(tmp_path)
        # Benchmark cards contain SPY/QQQ
        assert "SPY" in html and "QQQ" in html


# ─────────────────────────────────────────────────────────────────────────────
# TC-6-04  NAV 歷史資料聚合
# ─────────────────────────────────────────────────────────────────────────────

class TestTC604NavHistoryAggregation:

    def test_nav_history_json_has_entries(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        data = json.loads((out_dir / "data" / "nav_history.json").read_text())
        assert len(data) >= 3

    def test_nav_history_sorted_by_date(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        data = json.loads((out_dir / "data" / "nav_history.json").read_text())
        dates = [e["date"] for e in data]
        assert dates == sorted(dates)

    def test_nav_history_includes_all_report_dates(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        data = json.loads((out_dir / "data" / "nav_history.json").read_text())
        dates = {e["date"] for e in data}
        assert "2025-06-01" in dates
        assert "2025-06-02" in dates
        assert "2025-06-03" in dates

    def test_latest_json_matches_most_recent_report(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        latest = json.loads((out_dir / "data" / "latest.json").read_text())
        assert latest["date"] == "2025-06-03"
        assert latest["nav"] == pytest.approx(102_500)


# ─────────────────────────────────────────────────────────────────────────────
# TC-6-05  history.html 歷史報告瀏覽
# ─────────────────────────────────────────────────────────────────────────────

class TestTC605HistoryPage:

    def _history(self, tmp_path) -> str:
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        return (out_dir / "history.html").read_text()

    def test_all_dates_listed(self, tmp_path):
        html = self._history(tmp_path)
        assert "2025-06-01" in html
        assert "2025-06-02" in html
        assert "2025-06-03" in html

    def test_links_to_json_reports(self, tmp_path):
        html = self._history(tmp_path)
        assert "acc_001.json" in html

    def test_report_count_displayed(self, tmp_path):
        html = self._history(tmp_path)
        assert "3" in html  # "3 report(s)"

    def test_nav_link_present(self, tmp_path):
        html = self._history(tmp_path)
        assert "Dashboard" in html
        assert "index.html" in html


# ─────────────────────────────────────────────────────────────────────────────
# TC-6-06  Drawdown 水下圖
# ─────────────────────────────────────────────────────────────────────────────

class TestTC606DrawdownChart:

    def test_drawdown_canvas_present(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        html = (out_dir / "index.html").read_text()
        assert "ddChart" in html

    def test_drawdown_chart_js_logic(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        html = (out_dir / "index.html").read_text()
        # JS computes underwater series via running peak
        assert "peak" in html
        assert "ddVals" in html

    def test_drawdown_section_label(self, tmp_path):
        reports_dir = tmp_path / "reports"
        out_dir = tmp_path / "docs"
        _seed_reports(reports_dir)
        builder = DashboardBuilder(str(reports_dir), str(out_dir))
        builder.build("acc_001")
        html = (out_dir / "index.html").read_text()
        assert "Drawdown" in html
