"""
AccountManager — loads accounts.json, validates config, verifies Alpaca connections.

accounts.json stores the *name* of the env var holding the real key, e.g.
  "alpaca_key_env": "ACC_001_ALPACA_KEY"
At runtime os.environ["ACC_001_ALPACA_KEY"] is read for the actual secret.
"""
import json
import os
import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests


class ConfigError(Exception):
    pass


class AuthError(Exception):
    pass


ALPACA_BASE_URL_PAPER = "https://paper-api.alpaca.markets"
ALPACA_BASE_URL_LIVE = "https://api.alpaca.markets"

_REQUIRED_ACCOUNT_FIELDS = [
    "id", "name", "alpaca_key_env", "alpaca_secret_env",
    "active_strategy_id", "notify_email",
]


class AccountManager:
    def __init__(self, accounts_path: str = "accounts/accounts.json"):
        self.accounts_path = Path(accounts_path)
        self.accounts: List[Dict] = []
        self._load()

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.accounts_path.exists():
            raise ConfigError(f"accounts.json not found: {self.accounts_path}")

        with open(self.accounts_path, encoding="utf-8") as f:
            data = json.load(f)

        for acc in data.get("accounts", []):
            for field in _REQUIRED_ACCOUNT_FIELDS:
                if field not in acc:
                    raise ConfigError(
                        f"Account '{acc.get('id', '?')}' missing required field: '{field}'"
                    )

        self.accounts = data["accounts"]

    def _save(self) -> None:
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            json.dump({"accounts": self.accounts}, f, indent=2, ensure_ascii=False)

    # ── queries ───────────────────────────────────────────────────────────────

    def all_account_ids(self) -> List[str]:
        return [acc["id"] for acc in self.accounts]

    def get_account(self, account_id: str) -> Optional[Dict]:
        for acc in self.accounts:
            if acc["id"] == account_id:
                return acc
        return None

    def get_active_strategy_id(self, account_id: str) -> str:
        acc = self.get_account(account_id)
        if acc is None:
            raise ConfigError(f"Account '{account_id}' not found")
        return acc["active_strategy_id"]

    # ── mutations ─────────────────────────────────────────────────────────────

    def set_strategy(self, account_id: str, new_strategy_id: str) -> None:
        """Switch the active strategy; record old one in strategy_history."""
        acc = self.get_account(account_id)
        if acc is None:
            raise ConfigError(f"Account '{account_id}' not found")

        today = datetime.date.today().isoformat()
        history_entry = {
            "strategy_id": acc["active_strategy_id"],
            "from": acc.get("strategy_start_date", "unknown"),
            "to": today,
        }
        acc.setdefault("strategy_history", []).append(history_entry)
        acc["active_strategy_id"] = new_strategy_id
        acc["strategy_start_date"] = today
        self._save()

    # ── Alpaca connection ─────────────────────────────────────────────────────

    def get_credentials(self, account: Dict) -> tuple[str, str]:
        """Resolve env-var references to actual API key/secret strings."""
        key_env = account.get("alpaca_key_env", "")
        secret_env = account.get("alpaca_secret_env", "")

        api_key = os.environ.get(key_env, "")
        api_secret = os.environ.get(secret_env, "")

        if not api_key or not api_secret:
            raise AuthError(
                f"Missing credentials for account '{account['id']}': "
                f"env vars '{key_env}' / '{secret_env}' not set"
            )
        return api_key, api_secret

    def verify_connection(self, account: Dict) -> bool:
        """
        Call GET /v2/account to confirm the Alpaca key is valid.
        Returns True on success; raises AuthError on 401.
        """
        api_key, api_secret = self.get_credentials(account)
        paper = account.get("paper_trading", True)
        base_url = ALPACA_BASE_URL_PAPER if paper else ALPACA_BASE_URL_LIVE

        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

        resp = requests.get(f"{base_url}/v2/account", headers=headers, timeout=10)

        if resp.status_code == 401:
            raise AuthError(f"Invalid API credentials for account '{account['id']}'")

        resp.raise_for_status()
        return True
