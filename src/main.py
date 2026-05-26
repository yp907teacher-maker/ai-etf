"""
Entry point — called by GitHub Actions for each account.

Usage:
    python src/main.py --account acc_001
    python src/main.py --account acc_001 --dry-run
"""
import argparse
import sys
import logging
from pathlib import Path

# Allow `python src/main.py` from repo root
sys.path.insert(0, str(Path(__file__).parent))

from account_manager import AccountManager, ConfigError, AuthError
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
    p.add_argument("--dry-run", action="store_true", help="Validate config only, no orders")
    p.add_argument(
        "--accounts-path", default="accounts/accounts.json",
        help="Path to accounts.json"
    )
    p.add_argument(
        "--strategies-dir", default="strategies",
        help="Directory containing strategy JSON files"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    try:
        log.info("Loading account config: %s", args.account)
        am = AccountManager(args.accounts_path)
        account = am.get_account(args.account)
        if account is None:
            log.error("Account '%s' not found in accounts.json", args.account)
            return 1

        log.info("Loading strategy: %s", account["active_strategy_id"])
        sl = StrategyLoader(args.strategies_dir)
        strategy = sl.load(account["active_strategy_id"])
        log.info(
            "Strategy loaded: %s (enabled=%s)",
            strategy["strategy"]["name"],
            strategy["strategy"]["enabled"],
        )

        if not args.dry_run:
            log.info("Verifying Alpaca connection for account: %s", args.account)
            am.verify_connection(account)
            log.info("Alpaca connection OK")

        log.info("Phase 1 health check passed for account: %s", args.account)
        return 0

    except (ConfigError, SchemaError) as e:
        log.error("Configuration error: %s", e)
        return 1
    except AuthError as e:
        log.error("Authentication error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
