"""
Phase 1 Test Suite — 6 test cases (TC-1-01 through TC-1-06)

Run:  pytest tests/phase1/ -v
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from account_manager import AccountManager, AuthError, ConfigError
from strategy_loader import SchemaError, StrategyLoader

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

VALID_ACCOUNT = {
    "id": "acc_001",
    "name": "主帳戶",
    "alpaca_key_env": "TEST_ALPACA_KEY",
    "alpaca_secret_env": "TEST_ALPACA_SECRET",
    "paper_trading": True,
    "active_strategy_id": "toprank_ma_momentum_v2",
    "notify_email": "test@example.com",
    "watchlist_categories": ["AI_TECH"],
    "rebalance_trigger": {"monthly_first_day": True, "on_new_cash_threshold_pct": 0.05},
}


@pytest.fixture
def accounts_file(tmp_path):
    data = {
        "accounts": [
            VALID_ACCOUNT,
            {**VALID_ACCOUNT, "id": "acc_002", "name": "副帳戶",
             "alpaca_key_env": "TEST_ALPACA_KEY_2",
             "alpaca_secret_env": "TEST_ALPACA_SECRET_2"},
        ]
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture
def strategies_dir(tmp_path):
    d = tmp_path / "strategies"
    d.mkdir()
    return d


def _make_valid_strategy(strategy_id: str, enabled: bool = True) -> dict:
    return {
        "schema_version": "2.0.0",
        "strategy": {
            "id": strategy_id, "name": f"Strategy {strategy_id}",
            "asset_class": "equity", "market": "US_STOCK",
            "timeframe": "1d", "enabled": enabled,
        },
        "universe": {"exchanges": ["NASDAQ"]},
        "indicators": [],
        "filters": {"fundamental": {}, "technical": {}},
        "ranking": {
            "factors": [
                {"field": "momentum_90d", "weight": 0.6, "direction": "desc"},
                {"field": "roe", "weight": 0.4, "direction": "desc"},
            ]
        },
        "entry_signals": {"logic": "AND", "conditions": []},
        "exit_signals": {"logic": "OR", "conditions": []},
        "portfolio": {"max_positions": 8},
        "risk_management": {"max_daily_loss_pct": 0.03},
        "execution": {"broker": "alpaca", "order_type": "market"},
    }


# ─────────────────────────────────────────────────────────────────────────────
# TC-1-01  多帳戶載入
# ─────────────────────────────────────────────────────────────────────────────

class TestTC101MultipleAccountsLoading:
    def test_loads_two_accounts(self, accounts_file):
        am = AccountManager(str(accounts_file))
        assert len(am.accounts) == 2

    def test_get_account_by_id(self, accounts_file):
        am = AccountManager(str(accounts_file))
        assert am.get_account("acc_001") is not None
        assert am.get_account("acc_002") is not None
        assert am.get_account("acc_999") is None

    def test_missing_required_field_raises_config_error(self, tmp_path):
        bad = {"accounts": [{"id": "x", "name": "x"}]}  # missing required fields
        f = tmp_path / "accounts.json"
        f.write_text(json.dumps(bad))
        with pytest.raises(ConfigError, match="missing required field"):
            AccountManager(str(f))

    def test_file_not_found_raises_config_error(self, tmp_path):
        with pytest.raises(ConfigError):
            AccountManager(str(tmp_path / "nonexistent.json"))

    def test_all_account_ids(self, accounts_file):
        am = AccountManager(str(accounts_file))
        ids = am.all_account_ids()
        assert "acc_001" in ids
        assert "acc_002" in ids


# ─────────────────────────────────────────────────────────────────────────────
# TC-1-02  策略 JSON Schema 驗證
# ─────────────────────────────────────────────────────────────────────────────

class TestTC102StrategySchemaValidation:
    def test_valid_strategy_loads_successfully(self, strategies_dir):
        strategy_id = "test_valid"
        (strategies_dir / f"{strategy_id}.json").write_text(
            json.dumps(_make_valid_strategy(strategy_id))
        )
        sl = StrategyLoader(str(strategies_dir))
        data = sl.load(strategy_id)
        assert data["strategy"]["id"] == strategy_id

    def test_missing_top_level_field_raises_schema_error(self, strategies_dir):
        bad = {
            "strategy": {
                "id": "bad", "name": "bad", "asset_class": "equity",
                "market": "US_STOCK", "timeframe": "1d", "enabled": True,
            }
            # missing: universe, indicators, filters, ranking, etc.
        }
        (strategies_dir / "bad.json").write_text(json.dumps(bad))
        with pytest.raises(SchemaError, match="missing required top-level field"):
            StrategyLoader(str(strategies_dir)).load("bad")

    def test_missing_strategy_meta_field_raises_schema_error(self, strategies_dir):
        bad = _make_valid_strategy("bad2")
        del bad["strategy"]["timeframe"]
        (strategies_dir / "bad2.json").write_text(json.dumps(bad))
        with pytest.raises(SchemaError, match="strategy block missing field"):
            StrategyLoader(str(strategies_dir)).load("bad2")

    def test_disabled_strategy_excluded_from_load_all(self, strategies_dir):
        sid = "disabled_strat"
        (strategies_dir / f"{sid}.json").write_text(
            json.dumps(_make_valid_strategy(sid, enabled=False))
        )
        result = StrategyLoader(str(strategies_dir)).load_all()
        assert sid not in result

    def test_weights_not_summing_to_one_raises(self, strategies_dir):
        bad = _make_valid_strategy("bad_weights")
        bad["ranking"]["factors"] = [
            {"field": "momentum_90d", "weight": 0.5, "direction": "desc"},
            {"field": "roe", "weight": 0.3, "direction": "desc"},
        ]  # sum = 0.8
        (strategies_dir / "bad_weights.json").write_text(json.dumps(bad))
        with pytest.raises(SchemaError, match="weights"):
            StrategyLoader(str(strategies_dir)).load("bad_weights")

    def test_real_strategy_schema_file_is_valid(self):
        """The committed strategies/toprank_ma_momentum_v2.json must be valid."""
        strategies_path = Path(__file__).parent.parent.parent / "strategies"
        if not strategies_path.exists():
            pytest.skip("strategies/ directory not found")
        sl = StrategyLoader(str(strategies_path))
        data = sl.load("toprank_ma_momentum_v2")
        assert data["strategy"]["enabled"] is True


# ─────────────────────────────────────────────────────────────────────────────
# TC-1-03  Alpaca 連線健康
# ─────────────────────────────────────────────────────────────────────────────

class TestTC103AlpacaConnection:
    def _set_env(self, monkeypatch):
        monkeypatch.setenv("TEST_ALPACA_KEY", "FAKE_KEY")
        monkeypatch.setenv("TEST_ALPACA_SECRET", "FAKE_SECRET")

    def test_successful_connection_returns_true(self, accounts_file, monkeypatch):
        self._set_env(monkeypatch)
        am = AccountManager(str(accounts_file))
        acc = am.get_account("acc_001")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with patch("account_manager.requests.get", return_value=mock_resp) as mock_get:
            result = am.verify_connection(acc)

        assert result is True
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert "paper-api.alpaca.markets" in call_url  # paper trading
        assert "/v2/account" in call_url

    def test_401_raises_auth_error(self, accounts_file, monkeypatch):
        self._set_env(monkeypatch)
        am = AccountManager(str(accounts_file))
        acc = am.get_account("acc_001")

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("account_manager.requests.get", return_value=mock_resp):
            with pytest.raises(AuthError):
                am.verify_connection(acc)

    def test_missing_env_var_raises_auth_error(self, accounts_file):
        # Do NOT set env vars — should fail before HTTP call
        am = AccountManager(str(accounts_file))
        acc = am.get_account("acc_001")
        with pytest.raises(AuthError, match="not set"):
            am.verify_connection(acc)

    def test_live_url_used_when_paper_false(self, accounts_file, monkeypatch):
        monkeypatch.setenv("TEST_ALPACA_KEY_2", "FAKE_KEY_2")
        monkeypatch.setenv("TEST_ALPACA_SECRET_2", "FAKE_SECRET_2")

        am = AccountManager(str(accounts_file))
        acc = {**am.get_account("acc_002"), "paper_trading": False}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with patch("account_manager.requests.get", return_value=mock_resp) as mock_get:
            am.verify_connection(acc)

        call_url = mock_get.call_args[0][0]
        assert "api.alpaca.markets" in call_url
        assert "paper" not in call_url


# ─────────────────────────────────────────────────────────────────────────────
# TC-1-04  同帳戶僅一策略，可切換並記錄歷史
# ─────────────────────────────────────────────────────────────────────────────

class TestTC104OneStrategyPerAccount:
    def test_get_active_strategy_id(self, accounts_file):
        am = AccountManager(str(accounts_file))
        assert am.get_active_strategy_id("acc_001") == "toprank_ma_momentum_v2"

    def test_nonexistent_account_raises_config_error(self, accounts_file):
        am = AccountManager(str(accounts_file))
        with pytest.raises(ConfigError):
            am.get_active_strategy_id("acc_999")

    def test_switch_strategy_updates_active(self, accounts_file):
        am = AccountManager(str(accounts_file))
        am.set_strategy("acc_001", "new_strategy_v1")
        assert am.get_active_strategy_id("acc_001") == "new_strategy_v1"

    def test_switch_strategy_records_old_in_history(self, accounts_file):
        am = AccountManager(str(accounts_file))
        am.set_strategy("acc_001", "new_strategy_v1")
        acc = am.get_account("acc_001")
        history = acc.get("strategy_history", [])
        assert any(h["strategy_id"] == "toprank_ma_momentum_v2" for h in history)

    def test_switch_strategy_persisted_to_file(self, accounts_file):
        am = AccountManager(str(accounts_file))
        am.set_strategy("acc_001", "new_strategy_v1")

        # Reload from disk and verify persisted
        am2 = AccountManager(str(accounts_file))
        assert am2.get_active_strategy_id("acc_001") == "new_strategy_v1"


# ─────────────────────────────────────────────────────────────────────────────
# TC-1-05  GitHub Actions Workflow 檔案存在且含必要欄位
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


class TestTC105GitHubActionsWorkflows:
    def _load_yaml(self, filename: str) -> str:
        path = WORKFLOWS_DIR / filename
        assert path.exists(), f"Workflow file not found: {path}"
        return path.read_text()

    def test_trade_execute_workflow_exists(self):
        self._load_yaml("trade-execute.yml")

    def test_daily_report_workflow_exists(self):
        self._load_yaml("daily-report.yml")

    def test_rebalance_workflow_exists(self):
        self._load_yaml("rebalance.yml")

    def test_trade_execute_has_cron_schedule(self):
        content = self._load_yaml("trade-execute.yml")
        assert "cron" in content, "trade-execute.yml must have a cron schedule"

    def test_daily_report_has_cron_at_6am(self):
        content = self._load_yaml("daily-report.yml")
        assert "cron" in content, "daily-report.yml must have a cron schedule"
        # 06:00 ET = 11:00 UTC
        assert "11 " in content or "11\n" in content, \
            "daily-report cron should run at 11:00 UTC (06:00 ET)"

    def test_workflows_use_secrets_not_plaintext(self):
        for fname in ["trade-execute.yml", "daily-report.yml"]:
            content = self._load_yaml(fname)
            assert "secrets." in content, \
                f"{fname} must reference GitHub Secrets (secrets.<NAME>)"


# ─────────────────────────────────────────────────────────────────────────────
# TC-1-06  Secrets 不洩漏至日誌
# ─────────────────────────────────────────────────────────────────────────────

class TestTC106SecretsNotLeaked:
    def test_accounts_json_has_no_plaintext_keys(self):
        """accounts.json should store env var names, not raw API keys."""
        path = REPO_ROOT / "accounts" / "accounts.json"
        if not path.exists():
            pytest.skip("accounts.json not found")
        data = json.loads(path.read_text())
        for acc in data.get("accounts", []):
            for field in ("alpaca_key_env", "alpaca_secret_env"):
                value = acc.get(field, "")
                # Value should be an env var NAME (uppercase, no spaces)
                # not an actual key (which starts with PK... or SK...)
                assert not value.startswith("PK"), \
                    f"'{field}' looks like a raw Alpaca key; store env var name instead"
                assert not value.startswith("SK"), \
                    f"'{field}' looks like a raw Alpaca secret; store env var name instead"

    def test_credentials_resolved_from_env_not_json(self, accounts_file, monkeypatch):
        """get_credentials() must read from os.environ, not from accounts.json."""
        monkeypatch.setenv("TEST_ALPACA_KEY", "RESOLVED_KEY")
        monkeypatch.setenv("TEST_ALPACA_SECRET", "RESOLVED_SECRET")

        am = AccountManager(str(accounts_file))
        acc = am.get_account("acc_001")
        key, secret = am.get_credentials(acc)

        assert key == "RESOLVED_KEY"
        assert secret == "RESOLVED_SECRET"
        # The raw value stored in JSON ("TEST_ALPACA_KEY") must NOT be the resolved key
        assert acc["alpaca_key_env"] != key

    def test_log_output_does_not_contain_secret_value(self, accounts_file, monkeypatch, caplog):
        """When verify_connection is called, the actual key must not appear in logs."""
        import logging
        monkeypatch.setenv("TEST_ALPACA_KEY", "SUPER_SECRET_XYZ")
        monkeypatch.setenv("TEST_ALPACA_SECRET", "SUPER_SECRET_ABC")

        am = AccountManager(str(accounts_file))
        acc = am.get_account("acc_001")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        with caplog.at_level(logging.DEBUG):
            with patch("account_manager.requests.get", return_value=mock_resp):
                am.verify_connection(acc)

        for record in caplog.records:
            assert "SUPER_SECRET_XYZ" not in record.message
            assert "SUPER_SECRET_ABC" not in record.message
