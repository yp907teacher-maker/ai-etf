"""
Entry point — called by GitHub Actions trade-execute.yml for each account.

Usage:
    python src/main.py --account acc_001
    python src/main.py --account acc_001 --dry-run
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from account_manager import AccountManager, ConfigError, AuthError
from dashboard_builder import DashboardBuilder
from data_pipeline import DataPipeline
from derived_factor_engine import DerivedFactorEngine
from e2e_runner import E2ERunner, PipelineError
from execution_engine import ExecutionEngine
from filter_engine import FilterEngine
from indicator_engine import IndicatorEngine
from notifier import Notifier
from pipeline_adapter import build_adapters
from ranking_engine import RankingEngine
from report_generator import ReportModel, ReportView
from signal_engine import RiskGuard, SignalEngine
from strategy_loader import StrategyLoader, SchemaError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alpaca auto-trading engine")
    p.add_argument("--account", required=True, help="Account ID from accounts.json")
    p.add_argument("--dry-run", action="store_true", help="Validate only, no orders")
    p.add_argument("--accounts-path", default="accounts/accounts.json")
    p.add_argument("--strategies-dir", default="strategies")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--docs-dir", default="docs")
    return p.parse_args()


class _AccountManagerAdapter:
    """Thin wrapper so E2ERunner can call list_accounts()."""
    def __init__(self, am: AccountManager):
        self._am = am
    def list_accounts(self):
        return self._am.list_accounts()


class _StrategyLoaderAdapter:
    def __init__(self, sl: StrategyLoader):
        self._sl = sl
    def load(self, strategy_id: str):
        return self._sl.load(strategy_id)


def main() -> int:
    args = parse_args()

    try:
        am = AccountManager(args.accounts_path)
        account = am.get_account(args.account)
        if account is None:
            log.error("Account '%s' not found", args.account)
            return 1

        sl = StrategyLoader(args.strategies_dir)
        notifier = Notifier()

        # ── credentials (always loaded for DataPipeline market-data reads) ──────
        try:
            creds = am.get_credentials(account)
        except AuthError:
            if not args.dry_run:
                raise
            log.warning("Credentials not set — dry-run will use empty market data")
            creds = {}

        # ── Alpaca TradingClient (order placement — skipped in dry-run) ────────
        alpaca_client = None
        if not args.dry_run:
            try:
                from alpaca.trading.client import TradingClient
                alpaca_client = TradingClient(
                    api_key=creds["key"],
                    secret_key=creds["secret"],
                    paper=account.get("paper_trading", True),
                )
                log.info("Alpaca client ready (paper=%s)", account.get("paper_trading", True))
            except Exception as exc:
                log.error("Alpaca client init failed: %s", exc)
                return 1

        # ── real component instances ─────────────────────────────────────────
        # DataPipeline uses StockHistoricalDataClient (market data via api_key/secret).
        # alpaca_client (TradingClient) is used only for account state + order placement.
        data_pipeline = DataPipeline(
            api_key=creds.get("key", ""),
            api_secret=creds.get("secret", ""),
        )
        risk_guard = RiskGuard()

        adapters = build_adapters(
            data_pipeline=data_pipeline,
            indicator_engine=IndicatorEngine(),
            derived_factor_engine=DerivedFactorEngine(),
            filter_engine=FilterEngine(),
            ranking_engine=RankingEngine(),
            signal_engine=SignalEngine(),
            alpaca_client=alpaca_client,
        )
        # inject risk_guard into signal adapter
        adapters["signal_engine"]._rg = risk_guard

        exec_engine = ExecutionEngine(
            alpaca_client=alpaca_client,
            notifier=notifier,
            max_attempts=3,
            backoff_seconds=5.0,
        )

        runner = E2ERunner(
            account_manager=_AccountManagerAdapter(am),
            strategy_loader=_StrategyLoaderAdapter(sl),
            data_pipeline=adapters["data_pipeline"],
            indicator_engine=adapters["indicator_engine"],
            filter_engine=adapters["filter_engine"],
            ranking_engine=adapters["ranking_engine"],
            signal_engine=adapters["signal_engine"],
            execution_engine=exec_engine,
            report_model=ReportModel(reports_dir=args.reports_dir),
            report_view=ReportView(template_dir="dashboard/templates"),
            dashboard_builder=DashboardBuilder(
                reports_dir=args.reports_dir,
                output_dir=args.docs_dir,
            ),
            notifier=notifier,
            dry_run=args.dry_run,
        )

        result = runner.run(args.account)
        orders = result.get("orders", [])
        log.info(
            "Done — %d orders placed, dry_run=%s, steps=%s",
            len(orders), args.dry_run, runner.steps_completed,
        )
        return 0

    except (ConfigError, SchemaError) as exc:
        log.error("Configuration error: %s", exc)
        return 1
    except AuthError as exc:
        log.error("Authentication error: %s", exc)
        return 1
    except PipelineError as exc:
        log.error("Pipeline error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
