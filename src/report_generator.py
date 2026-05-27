"""
ReportGenerator — builds the daily JSON report model and HTML view.

Report JSON stored at:  reports/YYYY-MM-DD/{account_id}.json
HTML rendered on demand from JSON via Jinja2 template.

Model fields:
  date, account_id, nav, cash, invested_value,
  positions        [{ticker, shares, price, value, pct_of_nav, pe_ratio,
                     return_1d, return_1w, return_1m}],
  trades           [{ticker, side, shares, price, value, status}],
  drawdown_pct,    (current drawdown from peak NAV)
  top10            [{ticker, rank, score, pe_ratio, momentum_90d, price}],
  watchlist        {category_name: [ticker, ...]},
  benchmark        {spy_1d, qqq_1d, nav_1d},
  nav_history      [{date, nav}],    (last 90 days)
  generated_at     (ISO timestamp)
"""
import base64
import io
import json
import logging
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_RISK_DISCLAIMER = (
    "⚠️ 風險提示：本通知所有內容僅供資訊整理與研究參考，"
    "不構成投資建議。股票投資有風險，過去績效不代表未來獲利。"
)


# ─────────────────────────────────────────────────────────────────────────────
# ReportModel
# ─────────────────────────────────────────────────────────────────────────────

class ReportModel:
    """Builds and stores the daily JSON report model."""

    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)

    # ── public API ────────────────────────────────────────────────────────────

    def build(
        self,
        account_id: str,
        report_date: str,
        nav: float,
        cash: float,
        positions: List[Dict],
        trades: List[Dict],
        top10: List[Dict],
        watchlist: Dict[str, List[str]],
        benchmark: Dict[str, float],
        nav_history: List[Dict],
    ) -> Dict:
        """
        Build the report dict.

        positions items: {ticker, shares, price, pe_ratio, prev_close_1d,
                          prev_close_1w, prev_close_1m}
        trades items:    {ticker, side, shares, price, status}
        """
        invested_value = sum(p["shares"] * p["price"] for p in positions)
        peak_nav = self._peak_nav(nav_history, nav)
        drawdown_pct = self._drawdown(nav, peak_nav)

        enriched_positions = [
            self._enrich_position(p, nav) for p in positions
        ]

        report = {
            "date": report_date,
            "account_id": account_id,
            "nav": round(nav, 2),
            "cash": round(cash, 2),
            "invested_value": round(invested_value, 2),
            "positions": enriched_positions,
            "trades": [self._enrich_trade(t) for t in trades],
            "drawdown_pct": drawdown_pct,
            "top10": top10,
            "watchlist": watchlist,
            "benchmark": benchmark,
            "nav_history": nav_history,
            "risk_disclaimer": _RISK_DISCLAIMER,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        return report

    def save(self, report: Dict) -> Path:
        """Persist report to reports/YYYY-MM-DD/{account_id}.json."""
        date_dir = self.reports_dir / report["date"]
        date_dir.mkdir(parents=True, exist_ok=True)
        path = date_dir / f"{report['account_id']}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        log.info("Report saved: %s", path)
        return path

    def load(self, account_id: str, report_date: str) -> Optional[Dict]:
        """Load a previously saved report; return None if not found."""
        path = self.reports_dir / report_date / f"{account_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def list_dates(self, account_id: str) -> List[str]:
        """Return sorted list of dates for which reports exist."""
        if not self.reports_dir.exists():
            return []
        dates = []
        for d in sorted(self.reports_dir.iterdir()):
            if d.is_dir() and (d / f"{account_id}.json").exists():
                dates.append(d.name)
        return dates

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _peak_nav(nav_history: List[Dict], current_nav: float) -> float:
        if not nav_history:
            return current_nav
        all_navs = [h["nav"] for h in nav_history] + [current_nav]
        return max(all_navs)

    @staticmethod
    def _drawdown(current_nav: float, peak_nav: float) -> float:
        if peak_nav <= 0:
            return 0.0
        return (peak_nav - current_nav) / peak_nav

    @staticmethod
    def _calc_return(current_price: float, prev_price: Optional[float]) -> Optional[float]:
        if prev_price is None or prev_price <= 0:
            return None
        return (current_price - prev_price) / prev_price

    def _enrich_position(self, pos: Dict, nav: float) -> Dict:
        price = pos.get("price", 0)
        shares = pos.get("shares", 0)
        value = shares * price
        pct_of_nav = round(value / nav, 4) if nav > 0 else 0.0
        return {
            "ticker": pos["ticker"],
            "shares": shares,
            "price": price,
            "value": round(value, 2),
            "pct_of_nav": pct_of_nav,
            "pe_ratio": pos.get("pe_ratio"),
            "return_1d": self._calc_return(price, pos.get("prev_close_1d")),
            "return_1w": self._calc_return(price, pos.get("prev_close_1w")),
            "return_1m": self._calc_return(price, pos.get("prev_close_1m")),
        }

    @staticmethod
    def _enrich_trade(trade: Dict) -> Dict:
        shares = trade.get("shares", 0)
        price = trade.get("price", 0)
        return {
            "ticker": trade.get("ticker", ""),
            "side": trade.get("side", ""),
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2),
            "status": trade.get("status", ""),
        }


# ─────────────────────────────────────────────────────────────────────────────
# ReportView
# ─────────────────────────────────────────────────────────────────────────────

class ReportView:
    """Renders a report dict to HTML using a Jinja2 template (or fallback)."""

    TEMPLATE_NAME = "daily_report.html"

    def __init__(self, template_dir: str = "dashboard/templates"):
        self._env = None
        self._template_dir = Path(template_dir)
        self._load_jinja()

    def _load_jinja(self):
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
            if self._template_dir.exists():
                self._env = Environment(
                    loader=FileSystemLoader(str(self._template_dir)),
                    autoescape=select_autoescape(["html"]),
                )
        except ImportError:
            log.warning("Jinja2 not installed — HTML rendering unavailable")

    def render(self, report: Dict) -> str:
        """Return HTML string for the report. Falls back to plain text if no template."""
        if self._env:
            try:
                tmpl = self._env.get_template(self.TEMPLATE_NAME)
                nav_chart = self._generate_nav_chart(report.get("nav_history", []))
                return tmpl.render(report=report, nav_chart=nav_chart)
            except Exception as exc:
                log.warning("Jinja2 render failed: %s — using plain fallback", exc)
        return self._plain_fallback(report)

    @staticmethod
    def _generate_nav_chart(nav_history: List[Dict]) -> Optional[str]:
        """Generate NAV line chart; return base64 data URI or None."""
        if not nav_history or len(nav_history) < 2:
            return None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            dates = [datetime.fromisoformat(h["date"]) for h in nav_history]
            navs  = [h["nav"] for h in nav_history]
            base  = navs[0] if navs[0] > 0 else 1

            fig, ax = plt.subplots(figsize=(10, 2.8))
            fig.patch.set_facecolor("#0d1117")
            ax.set_facecolor("#0d1117")

            color = "#3fb950" if navs[-1] >= navs[0] else "#f85149"
            ax.plot(dates, navs, color=color, linewidth=2, zorder=3)
            ax.fill_between(dates, navs, base, alpha=0.12, color=color)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            ax.tick_params(colors="#8b949e", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#30363d")
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"${x:,.0f}")
            )
            ax.grid(True, color="#21262d", linewidth=0.5, zorder=0)
            plt.tight_layout(pad=0.4)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150,
                        bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("utf-8")
            return f"data:image/png;base64,{b64}"
        except Exception as exc:
            log.warning("Could not generate NAV chart: %s", exc)
            return None

    @staticmethod
    def _plain_fallback(report: Dict) -> str:
        lines = [
            f"<h1>Daily Report — {report.get('date', '')}</h1>",
            f"<p>Account: {report.get('account_id', '')}</p>",
            f"<p>NAV: ${report.get('nav', 0):,.2f}</p>",
            f"<p>Cash: ${report.get('cash', 0):,.2f}</p>",
            f"<p>Drawdown: {report.get('drawdown_pct', 0)*100:.2f}%</p>",
            "<h2>Positions</h2><ul>",
        ]
        for p in report.get("positions", []):
            lines.append(
                f"<li>{p['ticker']}: {p['shares']} shares @ ${p['price']:.2f} "
                f"= ${p['value']:,.2f} ({p['pct_of_nav']*100:.1f}%)</li>"
            )
        lines.append("</ul>")
        lines.append(f"<p><em>{report.get('risk_disclaimer', '')}</em></p>")
        return "\n".join(lines)

    def render_email_body(self, report: Dict) -> tuple:
        """Return (plain_text, html) for use with Notifier.send_daily_report()."""
        html = self.render(report)
        plain = self._email_plain(report)
        return plain, html

    @staticmethod
    def _email_plain(report: Dict) -> str:
        date_str = report.get("date", "")
        acct = report.get("account_id", "")
        nav = report.get("nav", 0)
        cash = report.get("cash", 0)
        dd = report.get("drawdown_pct", 0) * 100
        lines = [
            f"[Daily Report] {date_str} — {acct}",
            f"NAV: ${nav:,.2f}  Cash: ${cash:,.2f}  Drawdown: {dd:.2f}%",
            "",
            "Positions:",
        ]
        for p in report.get("positions", []):
            r1d = f"{p['return_1d']*100:+.2f}%" if p.get("return_1d") is not None else "N/A"
            lines.append(f"  {p['ticker']}: {p['shares']} shares  1D: {r1d}")
        lines += ["", "Top-10 Ranked:"]
        for s in report.get("top10", [])[:10]:
            lines.append(f"  #{s.get('rank','')} {s.get('ticker','')}  score={s.get('score',0):.1f}")
        lines += ["", report.get("risk_disclaimer", "")]
        return "\n".join(lines)
