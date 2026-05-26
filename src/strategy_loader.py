"""
StrategyLoader — reads JSON files from strategies/, validates schema, caches results.

Adding a new strategy: drop a new .json file into strategies/.
No Python code changes required.
"""
import json
from pathlib import Path
from typing import Dict


class SchemaError(Exception):
    pass


_REQUIRED_TOP_FIELDS = [
    "strategy", "universe", "indicators", "filters",
    "ranking", "entry_signals", "exit_signals",
    "portfolio", "risk_management", "execution",
]

_REQUIRED_STRATEGY_META = [
    "id", "name", "asset_class", "market", "timeframe", "enabled",
]


class StrategyLoader:
    def __init__(self, strategies_dir: str = "strategies"):
        self.strategies_dir = Path(strategies_dir)
        self._cache: Dict[str, Dict] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, strategy_id: str) -> Dict:
        """Load and validate a single strategy by ID."""
        if strategy_id in self._cache:
            return self._cache[strategy_id]

        path = self.strategies_dir / f"{strategy_id}.json"
        if not path.exists():
            raise SchemaError(f"Strategy file not found: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        self._validate(data, strategy_id)
        self._cache[strategy_id] = data
        return data

    def load_all(self) -> Dict[str, Dict]:
        """Load all enabled strategies from the strategies directory."""
        result: Dict[str, Dict] = {}
        if not self.strategies_dir.exists():
            return result

        for path in sorted(self.strategies_dir.glob("*.json")):
            strategy_id = path.stem
            data = self.load(strategy_id)
            if data["strategy"].get("enabled", True):
                result[strategy_id] = data

        return result

    # ── validation ────────────────────────────────────────────────────────────

    def _validate(self, data: Dict, strategy_id: str) -> None:
        for field in _REQUIRED_TOP_FIELDS:
            if field not in data:
                raise SchemaError(
                    f"Strategy '{strategy_id}' missing required top-level field: '{field}'"
                )

        meta = data["strategy"]
        for field in _REQUIRED_STRATEGY_META:
            if field not in meta:
                raise SchemaError(
                    f"Strategy '{strategy_id}' strategy block missing field: '{field}'"
                )

        self._validate_weights(data, strategy_id)

    def _validate_weights(self, data: Dict, strategy_id: str) -> None:
        factors = data.get("ranking", {}).get("factors", [])
        if not factors:
            return

        total = sum(f.get("weight", 0.0) for f in factors)
        if not (0.98 <= total <= 1.02):
            raise SchemaError(
                f"Strategy '{strategy_id}' ranking weights sum to {total:.4f}; "
                f"expected 1.0 (±0.02)"
            )
