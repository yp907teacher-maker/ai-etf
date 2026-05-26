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


def _build_account_report(
    account: dict,
    today: str,
    alpaca_client,
    report_model: ReportModel,
    report_view: ReportView,
    notifier: Notifier,
) -> dict:
    """
    Fetch live account data from Alpaca, build the report, save it, send email.
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

    # ── trades (today's filled orders) ───────────────────────────────────────
    trades = []
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        import datetime as _dt
        after_dt = _dt.datetime.fromisoformat(today)
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=after_dt, limit=100)
        orders = alpaca_client.get_orders(req)
        for o in orders:
            if str(o.status).lower() == "filled":
                trades.append({
                    "ticker": o.symbol,
                    "side": str(o.side).lower(),
                    "shares": int(float(o.filled_qty or 0)),
                    "price": float(o.filled_avg_price or 0),
                    "status": "filled",
                })
    except Exception as exc:
        log.warning("Could not fetch orders: %s", exc)

    # ── nav history from saved reports ────────────────────────────────────────
    nav_history = []
    for d in report_model.list_dates(account["id"])[-90:]:
        r = report_model.load(account["id"], d)
        if r:
            nav_history.append({"date": r["date"], "nav": r["nav"]})

    report = report_model.build(
        account_id=account["id"],
        report_date=today,
        nav=nav,
        cash=cash,
        positions=positions,
        trades=trades,
        top10=[],
        watchlist=account.get("watchlist_categories", {}),
        benchmark={},
        nav_history=nav_history,
    )
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
            _build_account_report(account, today, client, report_model, report_view, notifier)
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
