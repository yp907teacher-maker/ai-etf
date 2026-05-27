"""
E2ERunner — orchestrates the full daily trading pipeline for one account.

Pipeline steps (in order):
  1. Load account + strategy config
  2. Fetch prices / fundamentals (DataPipeline)
  3. Compute indicators + derived factors (IndicatorEngine / DerivedFactorEngine)
  4. Filter universe (FilterEngine / UniverseFilter)
  5. Rank (RankingEngine)
  6. Generate signals (SignalEngine)
  7. Execute orders (ExecutionEngine)
  8. Build + save report (ReportModel)
  9. Build dashboard (DashboardBuilder)
 10. Send daily email (Notifier)

All steps are injectable for testing — pass mock objects in the constructor.
On any unrecoverable error the notifier is called with a failure alert.
"""
import logging
from datetime import date
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


class PipelineError(Exception):
    pass


class E2ERunner:

    def __init__(
        self,
        account_manager=None,
        strategy_loader=None,
        data_pipeline=None,
        indicator_engine=None,
        derived_factor_engine=None,
        filter_engine=None,
        ranking_engine=None,
        signal_engine=None,
        execution_engine=None,
        report_model=None,
        report_view=None,
        dashboard_builder=None,
        notifier=None,
        dry_run: bool = False,
    ):
        self.account_manager = account_manager
        self.strategy_loader = strategy_loader
        self.data_pipeline = data_pipeline
        self.indicator_engine = indicator_engine
        self.derived_factor_engine = derived_factor_engine
        self.filter_engine = filter_engine
        self.ranking_engine = ranking_engine
        self.signal_engine = signal_engine
        self.execution_engine = execution_engine
        self.report_model = report_model
        self.report_view = report_view
        self.dashboard_builder = dashboard_builder
        self.notifier = notifier
        self.dry_run = dry_run

        # State visible to tests
        self.last_result: Optional[Dict] = None
        self.steps_completed: List[str] = []

    # ── main entry point ──────────────────────────────────────────────────────

    def run(self, account_id: str, run_date: Optional[str] = None) -> Dict:
        """
        Execute the full pipeline for one account.
        Returns a result dict with keys: success, account_id, date, report, orders.
        Raises PipelineError on unrecoverable failure (after notifying).
        """
        today = run_date or str(date.today())
        result = {
            "success": False,
            "account_id": account_id,
            "date": today,
            "report": None,
            "orders": [],
            "dry_run": self.dry_run,
        }

        try:
            # Step 1 — config
            account, strategy = self._load_config(account_id)
            self.steps_completed.append("config")

            # Step 2 — market data
            snapshot = self._fetch_data(account, strategy)
            self.steps_completed.append("data")

            # Step 3 — indicators
            snapshot = self._compute_indicators(snapshot, strategy)
            self.steps_completed.append("indicators")

            # Step 4 — filter
            snapshot = self._apply_filters(snapshot, strategy)
            self.steps_completed.append("filters")

            # Step 5 — rank
            ranked = self._rank(snapshot, strategy)
            self.steps_completed.append("ranking")

            # Step 6 — signals
            buys, exits = self._signals(ranked, strategy, account)
            self.steps_completed.append("signals")

            # Step 7 — execute
            orders = self._execute(buys, exits, account_id, strategy)
            result["orders"] = orders
            self.steps_completed.append("execution")

            # Step 8 — report
            report = self._build_report(account, today, snapshot, ranked, orders)
            result["report"] = report
            self.steps_completed.append("report")

            # Step 9 — dashboard
            self._build_dashboard(account_id)
            self.steps_completed.append("dashboard")

            # Step 10 — email
            self._send_daily_email(account, report)
            self.steps_completed.append("email")

            result["success"] = True
            log.info("Pipeline complete for %s on %s", account_id, today)

        except Exception as exc:
            log.error("Pipeline failed for %s: %s", account_id, exc)
            self._notify_failure(account_id, today, exc)
            raise PipelineError(f"Pipeline failed for {account_id}: {exc}") from exc

        self.last_result = result
        return result

    # ── step implementations ──────────────────────────────────────────────────

    def _load_config(self, account_id: str):
        if self.account_manager is None or self.strategy_loader is None:
            raise PipelineError("account_manager and strategy_loader are required")
        accounts = self.account_manager.list_accounts()
        account = next((a for a in accounts if a["id"] == account_id), None)
        if account is None:
            raise PipelineError(f"Account not found: {account_id}")
        strategy = self.strategy_loader.load(account.get("active_strategy_id", ""))
        return account, strategy

    def _fetch_data(self, account: Dict, strategy: Dict) -> Dict:
        if self.data_pipeline is None:
            return {}
        return self.data_pipeline.fetch_snapshot(strategy)

    def _compute_indicators(self, snapshot: Dict, strategy: Dict) -> Dict:
        if self.indicator_engine is None:
            return snapshot
        return self.indicator_engine.compute_snapshot(snapshot, strategy)

    def _apply_filters(self, snapshot: Dict, strategy: Dict) -> Dict:
        if self.filter_engine is None:
            return snapshot
        return self.filter_engine.apply_snapshot(snapshot, strategy)

    def _rank(self, snapshot: Dict, strategy: Dict) -> Dict:
        if self.ranking_engine is None:
            return snapshot
        return self.ranking_engine.rank_snapshot(snapshot, strategy)

    def _signals(self, ranked: Dict, strategy: Dict, account: Dict):
        if self.signal_engine is None:
            return [], {}
        buys = self.signal_engine.entry_signals_snapshot(ranked, strategy, account)
        exits = self.signal_engine.exit_signals_snapshot(ranked, strategy, account)
        return buys, exits

    def _execute(
        self,
        buys: List[str],
        exits: Dict[str, str],
        account_id: str,
        strategy: Dict,
    ) -> List[Dict]:
        orders = []
        if self.execution_engine is None or self.dry_run:
            if buys:
                log.info("dry_run=True — would BUY: %s", buys)
            else:
                log.info("dry_run=True — no BUY signals (entry conditions not met)")
            if exits:
                log.info("dry_run=True — would SELL: %s", list(exits.keys()))
            log.info("dry_run=True — skipping order submission")
            return orders
        for ticker in buys:
            try:
                rec = self.execution_engine.buy(ticker, 1, account_id)
                orders.append(rec)
            except Exception as exc:
                log.warning("Buy failed for %s: %s", ticker, exc)
        for ticker, reason in exits.items():
            try:
                rec = self.execution_engine.sell(ticker, 1, account_id)
                orders.append(rec)
            except Exception as exc:
                log.warning("Sell failed for %s: %s", ticker, exc)
        return orders

    def _build_report(
        self,
        account: Dict,
        today: str,
        snapshot: Dict,
        ranked: Dict,
        orders: List[Dict],
    ) -> Dict:
        if self.report_model is None:
            return {"date": today, "account_id": account.get("id", "")}
        report = self.report_model.build(
            account_id=account.get("id", ""),
            report_date=today,
            nav=snapshot.get("nav", 0),
            cash=snapshot.get("cash", 0),
            positions=snapshot.get("positions", []),
            trades=orders,
            top10=ranked.get("top10", []),
            watchlist=account.get("watchlist_categories", {}),
            benchmark=snapshot.get("benchmark", {}),
            nav_history=snapshot.get("nav_history", []),
        )
        self.report_model.save(report)
        return report

    def _build_dashboard(self, account_id: str):
        if self.dashboard_builder is None:
            return
        self.dashboard_builder.build(account_id)

    def _send_daily_email(self, account: Dict, report: Dict):
        if self.notifier is None or self.report_view is None:
            return
        to_email = account.get("notify_email", "")
        if not to_email:
            return
        plain, html = self.report_view.render_email_body(report)
        subject = f"[Daily Report] {report.get('date','')} — {report.get('account_id','')}"
        self.notifier.send_daily_report(to_email, subject, html, plain)

    def _notify_failure(self, account_id: str, today: str, exc: Exception):
        if self.notifier is None:
            return
        try:
            self.notifier.send_trade_alert({
                "ticker": "PIPELINE",
                "side": "error",
                "shares": 0,
                "status": "failed",
                "account_id": account_id,
                "error": str(exc),
                "date": today,
            })
        except Exception:
            pass  # don't let notification failure hide original error


# ─────────────────────────────────────────────────────────────────────────────
# GoLiveChecklist
# ─────────────────────────────────────────────────────────────────────────────

_CHECKLIST_ITEMS = [
    "paper_trading_tested",
    "live_credentials_set",
    "strategy_json_validated",
    "max_drawdown_limit_set",
    "notify_email_configured",
    "github_actions_enabled",
    "github_pages_deployed",
]


class GoLiveChecklist:
    """Validates that all go-live requirements are met before switching to live trading."""

    def __init__(self, items: Optional[List[str]] = None):
        self._items = items or _CHECKLIST_ITEMS
        self._checked: Dict[str, bool] = {item: False for item in self._items}

    def check(self, item: str, passed: bool = True):
        if item not in self._checked:
            raise ValueError(f"Unknown checklist item: {item}")
        self._checked[item] = passed

    def all_passed(self) -> bool:
        return all(self._checked.values())

    def missing(self) -> List[str]:
        return [k for k, v in self._checked.items() if not v]

    def status(self) -> Dict[str, bool]:
        return dict(self._checked)
