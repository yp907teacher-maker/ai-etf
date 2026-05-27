"""
PipelineAdapter — adapts the real component APIs to the interface E2ERunner expects.

E2ERunner calls:
  data_pipeline.fetch_snapshot(strategy)
  indicator_engine.compute_snapshot(snapshot, strategy)
  filter_engine.apply_snapshot(snapshot, strategy)
  ranking_engine.rank_snapshot(snapshot, strategy)
  signal_engine.entry_signals_snapshot(snapshot, strategy, account)
  signal_engine.exit_signals_snapshot(snapshot, strategy, account)

Real component APIs work on DataFrames per ticker, not on snapshot dicts.
This module provides adapter classes that translate between the two.

"snapshot" dict structure:
  {
    nav, cash,
    positions:   [{ticker, shares, price, pe_ratio, prev_close_1d, ...}],
    benchmark:   {spy_1d, qqq_1d, nav_1d},
    nav_history: [{date, nav}],
    top10:       [{ticker, rank, score, pe_ratio, momentum_90d}],
    _ticker_dfs: {ticker: DataFrame},    # internal, per-ticker OHLCV+indicators
    _fund:       {ticker: dict},         # fundamentals
    _ranked_df:  DataFrame or None,      # cross-sectional ranked snapshot
  }
"""
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

_LOOKBACK_DAYS = 252   # ~1 year of trading days for indicators


# ─────────────────────────────────────────────────────────────────────────────
# DataPipelineAdapter
# ─────────────────────────────────────────────────────────────────────────────

class DataPipelineAdapter:
    """
    Wraps DataPipeline; adds fetch_snapshot() for E2ERunner.
    Also fetches the live Alpaca account state (NAV, cash, positions).
    """

    def __init__(self, data_pipeline, alpaca_client=None):
        self._dp = data_pipeline
        self._alpaca = alpaca_client

    def fetch_snapshot(self, strategy: Dict) -> Dict:
        today = str(date.today())
        start = str(date.today() - timedelta(days=_LOOKBACK_DAYS * 1.5))

        # ── universe tickers ─────────────────────────────────────────────────
        tickers = self._universe_tickers(strategy)

        # ── per-ticker OHLCV ─────────────────────────────────────────────────
        ticker_dfs: Dict[str, pd.DataFrame] = {}
        fundamentals: Dict[str, Dict] = {}
        for ticker in tickers:
            try:
                df = self._dp.fetch_ohlcv(ticker, start, today)
                if df is not None and len(df) >= 30:
                    ticker_dfs[ticker] = df
                fund = self._dp.fetch_fundamentals(ticker)
                if fund:
                    fundamentals[ticker] = fund
            except Exception as exc:
                log.warning("Skipping %s: %s", ticker, exc)

        # ── live account state ───────────────────────────────────────────────
        nav, cash, positions = 0.0, 0.0, []
        if self._alpaca:
            try:
                info = self._alpaca.get_account()
                nav  = float(info.portfolio_value)
                cash = float(info.cash)
                for p in self._alpaca.get_all_positions():
                    ticker = p.symbol
                    price  = float(p.current_price)
                    last   = float(p.lastday_price)
                    positions.append({
                        "ticker": ticker,
                        "shares": int(float(p.qty)),
                        "price": price,
                        "pe_ratio": fundamentals.get(ticker, {}).get("pe_ratio"),
                        "prev_close_1d": last,
                        "prev_close_1w": self._prev_close(ticker_dfs.get(ticker), 5),
                        "prev_close_1m": self._prev_close(ticker_dfs.get(ticker), 21),
                    })
            except Exception as exc:
                log.warning("Could not fetch Alpaca account state: %s", exc)

        return {
            "nav": nav,
            "cash": cash,
            "positions": positions,
            "benchmark": {},
            "nav_history": [],
            "top10": [],
            "_ticker_dfs": ticker_dfs,
            "_fund": fundamentals,
            "_ranked_df": None,
        }

    def _universe_tickers(self, strategy: Dict) -> List[str]:
        """
        Build ticker list from strategy universe config.
        If include_symbols is non-empty, use that.
        Otherwise fetch tradeable NASDAQ/NYSE assets from Alpaca (capped at 100),
        falling back to a hardcoded large-cap list when Alpaca is unavailable.
        """
        universe = strategy.get("universe", strategy.get("strategy", {}).get("universe", {}))
        include = universe.get("include_symbols", [])
        if include:
            return include

        # Always use hardcoded large-cap list for universe
        # (Alpaca asset list returns 7000+ random tickers; most fail yfinance)
        # Hardcoded large-cap list (all NASDAQ/NYSE, market-cap > $20B)
        return [
            "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","NFLX","CRM",
            "AMD","INTC","QCOM","TXN","AMAT","MU","LRCX","KLAC","SNPS","CDNS",
            "NOW","ADBE","INTU","PANW","CRWD","FTNT","ZS","OKTA","DDOG","SNOW",
            "UBER","LYFT","ABNB","BKNG","EXPE","DASH","SHOP","SQ","PYPL","MA",
            "V","JPM","BAC","WFC","GS","MS","BLK","SPGI","ICE","CME",
            "UNH","ABBV","LLY","JNJ","MRK","PFE","AMGN","GILD","BIIB","VRTX",
            "XOM","CVX","COP","SLB","HAL","OXY","EOG","PXD","MPC","VLO",
            "HD","LOW","TGT","WMT","COST","AMZN","TJX","ROST","ULTA","EL",
        ]

    @staticmethod
    def _prev_close(df: Optional[pd.DataFrame], n_days: int) -> Optional[float]:
        if df is None or len(df) < n_days + 1:
            return None
        return float(df["adjusted_close"].iloc[-(n_days + 1)])


# ─────────────────────────────────────────────────────────────────────────────
# IndicatorEngineAdapter
# ─────────────────────────────────────────────────────────────────────────────

class IndicatorEngineAdapter:
    """Runs IndicatorEngine + DerivedFactorEngine on each ticker's DataFrame."""

    def __init__(self, indicator_engine, derived_factor_engine=None):
        self._ie  = indicator_engine
        self._dfe = derived_factor_engine

    def compute_snapshot(self, snapshot: Dict, strategy: Dict) -> Dict:
        indicators_cfg = strategy.get("indicators",
                         strategy.get("strategy", {}).get("indicators", []))
        derived_cfg    = strategy.get("derived_factors",
                         strategy.get("strategy", {}).get("derived_factors", []))

        updated: Dict[str, pd.DataFrame] = {}
        for ticker, df in snapshot.get("_ticker_dfs", {}).items():
            try:
                df2 = self._ie.compute(df, indicators_cfg)
                if self._dfe and derived_cfg:
                    from derived_factor_engine import DerivedFactorEngine
                    df2 = DerivedFactorEngine().compute(df2, derived_cfg)
                updated[ticker] = df2
            except Exception as exc:
                log.warning("Indicator compute failed for %s: %s", ticker, exc)
                updated[ticker] = df

        return {**snapshot, "_ticker_dfs": updated}


# ─────────────────────────────────────────────────────────────────────────────
# FilterEngineAdapter
# ─────────────────────────────────────────────────────────────────────────────

class FilterEngineAdapter:
    """Applies FilterEngine to the cross-sectional latest-row snapshot."""

    def __init__(self, filter_engine):
        self._fe = filter_engine

    def apply_snapshot(self, snapshot: Dict, strategy: Dict) -> Dict:
        filters_cfg = strategy.get("filters",
                      strategy.get("strategy", {}).get("filters", {}))
        if not filters_cfg:
            return snapshot

        snap_df = self._build_cross_section(snapshot)
        if snap_df.empty:
            return snapshot

        try:
            filtered_df = self._fe.apply(snap_df, filters_cfg)
            keep = set(filtered_df["ticker"])
            dfs = {t: df for t, df in snapshot["_ticker_dfs"].items() if t in keep}
            return {**snapshot, "_ticker_dfs": dfs}
        except Exception as exc:
            log.warning("Filter step failed: %s", exc)
            return snapshot

    @staticmethod
    def _build_cross_section(snapshot: Dict) -> pd.DataFrame:
        rows = []
        for ticker, df in snapshot.get("_ticker_dfs", {}).items():
            if df.empty:
                continue
            row = dict(df.iloc[-1])
            row["ticker"] = ticker
            fund = snapshot.get("_fund", {}).get(ticker, {})
            row.update(fund)
            rows.append(row)
        return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# RankingEngineAdapter
# ─────────────────────────────────────────────────────────────────────────────

class RankingEngineAdapter:
    """Builds ranked snapshot and top-10 list."""

    def __init__(self, ranking_engine):
        self._re = ranking_engine

    def rank_snapshot(self, snapshot: Dict, strategy: Dict) -> Dict:
        ranking_cfg = strategy.get("ranking",
                      strategy.get("strategy", {}).get("ranking", {}))

        snap_df = FilterEngineAdapter._build_cross_section(snapshot)
        if snap_df.empty:
            return snapshot

        try:
            ranked_df = self._re.score_and_rank(snap_df, ranking_cfg)
            top10_df = self._re.get_watchlist(ranked_df, ranking_cfg)
            top10 = top10_df[["ticker", "rank", "score"]].to_dict(orient="records") \
                if hasattr(top10_df, "to_dict") else list(top10_df)
            return {**snapshot, "_ranked_df": ranked_df, "top10": top10}
        except Exception as exc:
            log.warning("Ranking step failed: %s", exc)
            return snapshot


# ─────────────────────────────────────────────────────────────────────────────
# SignalEngineAdapter
# ─────────────────────────────────────────────────────────────────────────────

class SignalEngineAdapter:
    """Translates SignalEngine.entry_signals / exit_signals to snapshot interface."""

    def __init__(self, signal_engine, risk_guard=None):
        self._se = signal_engine
        self._rg = risk_guard

        from signal_engine import RiskGuard
        self._rg = risk_guard or RiskGuard()

    def entry_signals_snapshot(
        self,
        snapshot: Dict,
        strategy: Dict,
        account: Dict,
    ) -> List[str]:
        ranked_df = snapshot.get("_ranked_df")
        if ranked_df is None or ranked_df.empty:
            return []
        entry_cfg = strategy.get("entry_signals",
                    strategy.get("strategy", {}).get("entry_signals", {}))
        nav = snapshot.get("nav", 0)
        histories = snapshot.get("_ticker_dfs", {})
        try:
            return self._se.entry_signals(ranked_df, histories, entry_cfg, self._rg, nav)
        except Exception as exc:
            log.warning("Entry signals failed: %s", exc)
            return []

    def exit_signals_snapshot(
        self,
        snapshot: Dict,
        strategy: Dict,
        account: Dict,
    ) -> Dict[str, str]:
        ranked_df = snapshot.get("_ranked_df")
        if ranked_df is None or ranked_df.empty:
            return {}
        exit_cfg = strategy.get("exit_signals",
                   strategy.get("strategy", {}).get("exit_signals", {}))
        histories = snapshot.get("_ticker_dfs", {})
        positions = {p["ticker"]: p for p in snapshot.get("positions", [])}
        try:
            return self._se.exit_signals(ranked_df, histories, positions, exit_cfg)
        except Exception as exc:
            log.warning("Exit signals failed: %s", exc)
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# Factory — build all adapters from real component instances
# ─────────────────────────────────────────────────────────────────────────────

def build_adapters(
    data_pipeline,
    indicator_engine,
    derived_factor_engine,
    filter_engine,
    ranking_engine,
    signal_engine,
    alpaca_client=None,
) -> Dict:
    """
    Return a dict of adapter instances ready to inject into E2ERunner.
    """
    return {
        "data_pipeline":          DataPipelineAdapter(data_pipeline, alpaca_client),
        "indicator_engine":       IndicatorEngineAdapter(indicator_engine, derived_factor_engine),
        "filter_engine":          FilterEngineAdapter(filter_engine),
        "ranking_engine":         RankingEngineAdapter(ranking_engine),
        "signal_engine":          SignalEngineAdapter(signal_engine),
    }
