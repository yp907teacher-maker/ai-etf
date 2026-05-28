"""
report_runner.py — generate daily JSON reports + HTML dashboard + send emails.

Called by GitHub Actions daily-report.yml at 06:00 ET.

Usage:
    python src/report_runner.py --all-accounts
    python src/report_runner.py --account acc_001
"""
import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from account_manager import AccountManager
from dashboard_builder import DashboardBuilder
from notifier import Notifier
from report_generator import ReportModel, ReportView
from strategy_loader import StrategyLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _fetch_benchmark(today: str) -> dict:
    """Fetch SPY & QQQ 1-day return via yfinance. Returns {} on error."""
    try:
        import yfinance as yf
        from datetime import datetime, timedelta as td
        start = (datetime.fromisoformat(today) - td(days=5)).strftime("%Y-%m-%d")
        result = {}
        for ticker in ("SPY", "QQQ"):
            try:
                raw = yf.download(ticker, start=start, end=today,
                                  auto_adjust=True, progress=False, threads=False)
                if len(raw) >= 2:
                    if isinstance(raw.columns, __import__("pandas").MultiIndex):
                        raw = raw.droplevel(1, axis=1)
                    closes = raw["Close"].dropna()
                    if len(closes) >= 2:
                        ret = float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2])
                        result[ticker.lower() + "_1d"] = round(ret, 6)
            except Exception:
                pass
        log.info("Benchmark: %s", result)
        return result
    except Exception as exc:
        log.warning("Could not fetch benchmark: %s", exc)
        return {}


def _run_strategy_pipeline(account: dict, alpaca_client, creds: dict) -> list:
    """
    Run the full data → indicator → filter → ranking pipeline.
    Returns top10 list of dicts. Falls back to [] on any error.
    """
    try:
        from data_pipeline import DataPipeline
        from pipeline_adapter import (DataPipelineAdapter, IndicatorEngineAdapter,
                                      FilterEngineAdapter, RankingEngineAdapter)
        from indicator_engine import IndicatorEngine
        from derived_factor_engine import DerivedFactorEngine
        from filter_engine import FilterEngine
        from ranking_engine import RankingEngine

        strategy_id = account.get("active_strategy_id", "")
        strategy_path = Path("strategies") / f"{strategy_id}.json"
        if not strategy_path.exists():
            log.warning("Strategy file not found: %s", strategy_path)
            return []
        with open(strategy_path, encoding="utf-8") as f:
            strategy = json.load(f)

        dp = DataPipeline(
            api_key=creds.get("key", ""),
            api_secret=creds.get("secret", ""),
        )
        # Pass alpaca_client=None so universe falls back to hardcoded large-cap list
        # (avoids fetching 7000+ random Alpaca assets via yfinance)
        snap = DataPipelineAdapter(dp, None).fetch_snapshot(strategy)
        snap = IndicatorEngineAdapter(IndicatorEngine(), DerivedFactorEngine()).compute_snapshot(snap, strategy)
        snap = FilterEngineAdapter(FilterEngine()).apply_snapshot(snap, strategy)
        snap = RankingEngineAdapter(RankingEngine()).rank_snapshot(snap, strategy)
        top10 = snap.get("top10", [])
        log.info("Strategy pipeline: %d tickers ranked, top10=%d",
                 len(snap.get("_ticker_dfs", {})), len(top10))
        return top10
    except Exception as exc:
        log.warning("Strategy pipeline failed (top10 will be empty): %s", exc)
        return []


def _build_account_report(
    account: dict,
    today: str,
    alpaca_client,
    creds: dict,
    report_model: ReportModel,
    report_view: ReportView,
    notifier: Notifier,
) -> dict:
    """
    Fetch live account data from Alpaca, run strategy pipeline for top10,
    build the report, save it, send email.
    Returns the report dict.
    """
    # ── fetch account state from Alpaca ──────────────────────────────────────
    try:
        acct_info = alpaca_client.get_account()
        nav   = float(acct_info.portfolio_value)
        cash  = float(acct_info.cash)
    except Exception as exc:
        log.warning("Could not fetch account info: %s — using zeros", exc)
        nav, cash = 0.0, 0.0

    # ── positions ─────────────────────────────────────────────────────────────
    positions = []
    try:
        raw_positions = alpaca_client.get_all_positions()
        for p in raw_positions:
            positions.append({
                "ticker": p.symbol,
                "shares": int(float(p.qty)),
                "price": float(p.current_price),
                "pe_ratio": None,       # populated by data pipeline in full run
                "prev_close_1d": float(p.lastday_price),
                "prev_close_1w": None,
                "prev_close_1m": None,
            })
    except Exception as exc:
        log.warning("Could not fetch positions: %s", exc)

    # ── trades (recent filled orders, 往回查 2 天以確保抓到) ──────────────────
    trades = []
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        import datetime as _dt
        after_dt = _dt.datetime.fromisoformat(today) - _dt.timedelta(days=2)
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=after_dt, limit=200)
        orders = alpaca_client.get_orders(req)
        for o in orders:
            # alpaca-py OrderStatus 可能是 str enum，用 .value 或 in 判斷
            status_str = getattr(o.status, "value", str(o.status)).lower()
            if "filled" in status_str:
                side_raw = getattr(o.side, "value", str(o.side)).lower()
                side_str = "buy" if "buy" in side_raw else "sell"
                trades.append({
                    "ticker": o.symbol,
                    "side": side_str,
                    "shares": int(float(o.filled_qty or 0)),
                    "price": float(o.filled_avg_price or 0),
                    "status": "filled",
                    "date": str(o.filled_at.date()) if o.filled_at else today,
                })
    except Exception as exc:
        log.warning("Could not fetch orders: %s", exc)

    # ── nav history from saved reports ────────────────────────────────────────
    nav_history = []
    for d in report_model.list_dates(account["id"])[-90:]:
        r = report_model.load(account["id"], d)
        if r:
            nav_history.append({"date": r["date"], "nav": r["nav"]})

    # ── benchmark (SPY / QQQ 1-day return) ───────────────────────────────────
    benchmark = _fetch_benchmark(today)

    # ── run strategy pipeline for LIVE top10 ─────────────────────────────────
    top10 = _run_strategy_pipeline(account, alpaca_client, creds)

    report = report_model.build(
        account_id=account["id"],
        report_date=today,
        nav=nav,
        cash=cash,
        positions=positions,
        trades=trades,
        top10=top10,
        watchlist=account.get("watchlist_categories", {}),
        benchmark=benchmark,
        nav_history=nav_history,
    )
    # inject dashboard URL for email template
    report["dashboard_url"] = account.get("dashboard_url", "")
    report_model.save(report)

    # ── send email ────────────────────────────────────────────────────────────
    to_email = account.get("notify_email", "")
    if to_email:
        plain, html = report_view.render_email_body(report)
        subject = f"[Daily Report] {today} — {account['id']}"
        notifier.send_daily_report(to_email, subject, html, plain)

    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Generate daily reports for all accounts")
    p.add_argument("--all-accounts", action="store_true")
    p.add_argument("--account", default="")
    p.add_argument("--accounts-path", default="accounts/accounts.json")
    p.add_argument("--strategies-dir", default="strategies")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--docs-dir", default="docs")
    args = p.parse_args()

    today = str(date.today())
    am = AccountManager(args.accounts_path)
    sl = StrategyLoader(args.strategies_dir)
    report_model = ReportModel(reports_dir=args.reports_dir)
    report_view = ReportView(template_dir="dashboard/templates")
    dashboard = DashboardBuilder(reports_dir=args.reports_dir, output_dir=args.docs_dir)
    notifier = Notifier()

    accounts = am.list_accounts()
    if args.account:
        accounts = [a for a in accounts if a["id"] == args.account]

    ok = 0
    for account in accounts:
        log.info("Generating report for %s", account["id"])
        try:
            creds = am.get_credentials(account)
            from alpaca.trading.client import TradingClient
            client = TradingClient(
                api_key=creds["key"],
                secret_key=creds["secret"],
                paper=account.get("paper_trading", True),
            )
            _build_account_report(account, today, client, creds, report_model, report_view, notifier)
            dashboard.build(account["id"])
            log.info("Report done for %s", account["id"])
            ok += 1
        except Exception as exc:
            log.error("Failed report for %s: %s", account["id"], exc)
            notifier.send_trade_alert({
                "ticker": "REPORT",
                "side": "error",
                "shares": 0,
                "status": "failed",
                "account_id": account["id"],
                "error": str(exc),
            })

    log.info("Reports complete: %d/%d accounts", ok, len(accounts))
    return 0 if ok == len(accounts) else 1


if __name__ == "__main__":
    sys.exit(main())
