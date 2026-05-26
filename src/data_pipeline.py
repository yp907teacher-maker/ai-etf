"""
DataPipeline — fetches OHLCV (Alpaca) and fundamental data (yfinance).

Design for testability:
  - _fetch_bars_from_api() and _fetch_fundamentals_from_api() are the only
    methods that touch external APIs; they can be easily mocked in tests.
  - FileCache prevents duplicate same-day API calls.
  - Clients (alpaca, yfinance) are injected at construction or created lazily.
"""
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


class DataFetchError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

class FileCache:
    """Day-granularity file cache. Entries from previous days are ignored."""

    def __init__(self, cache_dir: str = ".cache"):
        self._dir = Path(cache_dir)
        self._dir.mkdir(exist_ok=True)
        self._hits = 0
        self._misses = 0

    def _path(self, key: str) -> Path:
        today = datetime.date.today().isoformat()
        safe = key.replace("/", "_").replace(":", "_").replace(" ", "_")
        return self._dir / f"{today}_{safe}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._path(key)
        if p.exists():
            self._hits += 1
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        self._misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(value, f)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses


def _df_to_cacheable(df: pd.DataFrame) -> Dict:
    """Serialize DataFrame (with DatetimeIndex) to a JSON-safe dict."""
    return {
        "index": [str(i) for i in df.index],
        "data": {
            col: [None if (isinstance(v, float) and np.isnan(v)) else v
                  for v in df[col].tolist()]
            for col in df.columns
        },
    }


def _cacheable_to_df(payload: Dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["data"], index=pd.to_datetime(payload["index"]))
    df.index.name = "date"
    return df


# ─────────────────────────────────────────────────────────────────────────────
# DataPipeline
# ─────────────────────────────────────────────────────────────────────────────

PRICE_COLS = ["open", "high", "low", "close", "adjusted_close", "volume"]
FUNDAMENTAL_FIELDS = [
    "ticker", "market_cap", "sector", "industry", "pe_ratio", "eps_ttm",
    "revenue_growth_yoy", "roe", "gross_margin", "debt_to_equity",
    "free_cash_flow_yield", "avg_volume_20d",
]


class DataPipeline:

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        cache_dir: str = ".cache",
        _alpaca_client=None,
        _yf=None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self.cache = FileCache(cache_dir)
        self.__alpaca_client = _alpaca_client
        self.__yf = _yf
        self._api_call_count = 0

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    def fetch_ohlcv(
        self,
        ticker: str,
        start: str,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return DataFrame[PRICE_COLS] indexed by date.

        Columns: open, high, low, close, adjusted_close, volume.
        """
        if end is None:
            end = datetime.date.today().isoformat()

        cache_key = f"ohlcv_{ticker}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return _cacheable_to_df(cached)

        df = self._fetch_bars_from_api(ticker, start, end)
        self.cache.set(cache_key, _df_to_cacheable(df))
        return df

    def _fetch_bars_from_api(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Call Alpaca Market Data API; fall back to yfinance on 403/subscription error."""
        self._api_call_count += 1
        # ── try Alpaca first ──────────────────────────────────────────────────
        if self._api_key and self._api_secret:
            try:
                import datetime as dt
                from alpaca.data.historical import StockHistoricalDataClient
                from alpaca.data.requests import StockBarsRequest
                from alpaca.data.timeframe import TimeFrame

                client = self._get_alpaca_client()
                req = StockBarsRequest(
                    symbol_or_symbols=[ticker],
                    timeframe=TimeFrame.Day,
                    start=dt.date.fromisoformat(start),
                    end=dt.date.fromisoformat(end),
                    adjustment="all",
                )
                raw = client.get_stock_bars(req).df.xs(ticker, level="symbol")
                raw.index.name = "date"
                df = raw.rename(columns={"close": "adjusted_close"})
                df["close"] = df["adjusted_close"]
                return df[PRICE_COLS]
            except ImportError:
                raise DataFetchError(
                    "alpaca-py not installed. Run: pip install alpaca-py"
                )
            except Exception as exc:
                # 403 = free-tier subscription limit; fall through to yfinance
                if "403" not in str(exc) and "forbidden" not in str(exc).lower() and "subscription" not in str(exc).lower():
                    raise
                import logging as _logging
                _logging.getLogger(__name__).debug(
                    "Alpaca data 403 for %s — falling back to yfinance", ticker)

        # ── yfinance fallback ─────────────────────────────────────────────────
        return self._fetch_bars_yfinance(ticker, start, end)

    def _fetch_bars_yfinance(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Fetch OHLCV from Yahoo Finance (free, no subscription required)."""
        try:
            import yfinance as yf
        except ImportError:
            raise DataFetchError("yfinance not installed. Run: pip install yfinance")

        raw = yf.download(ticker, start=start, end=end, auto_adjust=True,
                          progress=False, threads=False)
        if raw.empty:
            raise DataFetchError(f"yfinance returned empty data for {ticker}")

        # yfinance returns MultiIndex columns when single ticker sometimes
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs(ticker, axis=1, level=1) if ticker in raw.columns.get_level_values(1) else raw.droplevel(1, axis=1)

        raw = raw.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        raw["adjusted_close"] = raw["close"]
        raw.index.name = "date"
        available = [c for c in PRICE_COLS if c in raw.columns]
        return raw[available]

    def _get_alpaca_client(self):
        if self.__alpaca_client is not None:
            return self.__alpaca_client
        from alpaca.data.historical import StockHistoricalDataClient
        self.__alpaca_client = StockHistoricalDataClient(
            self._api_key, self._api_secret
        )
        return self.__alpaca_client

    # ── Fundamentals ──────────────────────────────────────────────────────────

    def fetch_fundamentals(self, ticker: str) -> Dict:
        """Return dict with pe_ratio, eps_ttm, roe, sector, market_cap, etc."""
        cache_key = f"fundamentals_{ticker}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        data = self._fetch_fundamentals_from_api(ticker)
        self.cache.set(cache_key, data)
        return data

    def _fetch_fundamentals_from_api(self, ticker: str) -> Dict:
        """Call yfinance.  Override / mock-patch in tests."""
        self._api_call_count += 1
        try:
            yf = self._get_yf()
            info = yf.Ticker(ticker).info
            eps = info.get("trailingEps") or 0.0
            price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
            pe = round(price / eps, 2) if eps and eps > 0 else None

            return {
                "ticker": ticker,
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "pe_ratio": pe,
                "eps_ttm": eps,
                "revenue_growth_yoy": info.get("revenueGrowth"),
                "roe": info.get("returnOnEquity"),
                "gross_margin": info.get("grossMargins"),
                "debt_to_equity": info.get("debtToEquity"),
                "free_cash_flow_yield": None,
                "avg_volume_20d": info.get("averageVolume"),
            }
        except ImportError:
            raise DataFetchError("yfinance not installed. Run: pip install yfinance")

    def _get_yf(self):
        if self.__yf is not None:
            return self.__yf
        import yfinance as yf
        self.__yf = yf
        return yf

    # ── NASDAQ Top-10 ─────────────────────────────────────────────────────────

    # fmt: off
    _NASDAQ_CANDIDATES = [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","COST",
        "NFLX","AMD","ADBE","QCOM","INTC","AMAT","CSCO","INTU","AMGN",
        "SBUX","PANW",
    ]
    # fmt: on

    def fetch_nasdaq_top10_by_marketcap(self) -> List[Dict]:
        """Return the 10 largest NASDAQ stocks by market cap (cached daily)."""
        cache_key = "nasdaq_top10"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        results: List[Dict] = []
        for ticker in self._NASDAQ_CANDIDATES:
            try:
                f = self._fetch_fundamentals_from_api(ticker)
                if f.get("market_cap"):
                    results.append(f)
            except Exception:
                continue

        results.sort(key=lambda x: x.get("market_cap", 0), reverse=True)
        top10 = results[:10]
        self.cache.set(cache_key, top10)
        return top10
