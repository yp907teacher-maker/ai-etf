"""
DashboardBuilder — generates the static GitHub Pages site from report JSON files.

Outputs:
  docs/index.html       — main dashboard (latest report)
  docs/history.html     — historical report browser
  docs/data/latest.json — latest report data for JS
  docs/data/nav_history.json — aggregated NAV history for charting

The 'docs/' folder is served by GitHub Pages (configured in repo settings).
All charts are rendered client-side via Chart.js loaded from CDN.
"""
import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"


class DashboardBuilder:

    def __init__(self, reports_dir: str = "reports", output_dir: str = "docs"):
        self.reports_dir = Path(reports_dir)
        self.output_dir = Path(output_dir)
        self._data_dir = self.output_dir / "data"

    # ── public API ────────────────────────────────────────────────────────────

    def build(self, account_id: str) -> Dict[str, Path]:
        """
        Build all dashboard pages for account_id.
        Returns {name: path} of generated files.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        latest = self._latest_report(account_id)
        all_dates = self._all_dates(account_id)
        nav_history = self._aggregate_nav_history(account_id, all_dates)

        generated = {}

        # JSON data files (consumed by JS)
        if latest:
            p = self._data_dir / "latest.json"
            p.write_text(json.dumps(latest, ensure_ascii=False, indent=2))
            generated["latest_json"] = p

        p = self._data_dir / "nav_history.json"
        p.write_text(json.dumps(nav_history, ensure_ascii=False, indent=2))
        generated["nav_history_json"] = p

        trades_log = self._aggregate_trades(account_id, all_dates)
        p = self._data_dir / "trades.json"
        p.write_text(json.dumps(trades_log, ensure_ascii=False, indent=2))
        generated["trades_json"] = p

        # HTML pages
        index_path = self.output_dir / "index.html"
        index_path.write_text(self._render_index(latest, account_id))
        generated["index"] = index_path

        history_path = self.output_dir / "history.html"
        history_path.write_text(self._render_history(all_dates, account_id))
        generated["history"] = history_path

        log.info("Dashboard built: %d files", len(generated))
        return generated

    # ── data aggregation ──────────────────────────────────────────────────────

    def _latest_report(self, account_id: str) -> Optional[Dict]:
        dates = self._all_dates(account_id)
        if not dates:
            return None
        path = self.reports_dir / dates[-1] / f"{account_id}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def _all_dates(self, account_id: str) -> List[str]:
        if not self.reports_dir.exists():
            return []
        dates = []
        for d in sorted(self.reports_dir.iterdir()):
            if d.is_dir() and (d / f"{account_id}.json").exists():
                dates.append(d.name)
        return dates

    def _aggregate_nav_history(self, account_id: str, dates: List[str]) -> List[Dict]:
        """
        Build [{date, nav}] from saved daily reports.
        Combines each report's stored nav_history + the report's own nav.
        Deduplicates and sorts by date.
        """
        seen: Dict[str, float] = {}
        for date in dates:
            path = self.reports_dir / date / f"{account_id}.json"
            if not path.exists():
                continue
            report = json.loads(path.read_text())
            # include historical series stored in report
            for entry in report.get("nav_history", []):
                seen[entry["date"]] = entry["nav"]
            # include this report's own NAV
            seen[report["date"]] = report["nav"]
        return [{"date": d, "nav": v} for d, v in sorted(seen.items())]

    def _aggregate_trades(self, account_id: str, dates: List[str]) -> List[Dict]:
        """Collect all trades from every saved report, newest first."""
        seen_keys: set = set()
        trades: List[Dict] = []
        for date in reversed(dates):
            path = self.reports_dir / date / f"{account_id}.json"
            if not path.exists():
                continue
            report = json.loads(path.read_text())
            for t in report.get("trades", []):
                key = (date, t.get("ticker"), t.get("side"), t.get("shares"), t.get("price"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                trades.append({
                    "date": date,
                    "ticker": t.get("ticker", ""),
                    "side": t.get("side", ""),
                    "shares": t.get("shares", 0),
                    "price": t.get("price", 0),
                    "value": t.get("value", 0),
                    "status": t.get("status", ""),
                })
        return trades

    # ── HTML rendering ────────────────────────────────────────────────────────

    def _render_index(self, report: Optional[Dict], account_id: str) -> str:
        date_str = report["date"] if report else "—"
        nav = report["nav"] if report else 0
        cash = report["cash"] if report else 0
        dd = ((report.get("drawdown_pct", 0) or 0) * 100) if report else 0
        invested = report.get("invested_value", 0) if report else 0

        positions_rows = ""
        if report:
            for p in report.get("positions", []):
                r1d = f"{p['return_1d']*100:+.2f}%" if p.get("return_1d") is not None else "N/A"
                r1w = f"{p['return_1w']*100:+.2f}%" if p.get("return_1w") is not None else "N/A"
                r1m = f"{p['return_1m']*100:+.2f}%" if p.get("return_1m") is not None else "N/A"
                pe = f"{p['pe_ratio']:.1f}" if p.get("pe_ratio") else "—"
                cls1d = "pos" if p.get("return_1d") and p["return_1d"] > 0 else ("neg" if p.get("return_1d") and p["return_1d"] < 0 else "")
                positions_rows += (
                    f"<tr><td>{p['ticker']}</td><td>{p['shares']}</td>"
                    f"<td>${p['price']:.2f}</td><td>${p['value']:,.2f}</td>"
                    f"<td>{p['pct_of_nav']*100:.1f}%</td><td>{pe}</td>"
                    f"<td class='{cls1d}'>{r1d}</td><td>{r1w}</td><td>{r1m}</td></tr>\n"
                )

        top10_rows = ""
        if report:
            for s in report.get("top10", []):
                mom = f"{s['momentum_90d']*100:+.1f}%" if s.get("momentum_90d") is not None else "N/A"
                pe = f"{s['pe_ratio']:.1f}" if s.get("pe_ratio") else "—"
                top10_rows += (
                    f"<tr><td>#{s.get('rank','')}</td><td><strong>{s.get('ticker','')}</strong></td>"
                    f"<td>{s.get('score',0):.1f}</td><td>{pe}</td><td>{mom}</td></tr>\n"
                )

        watchlist_html = ""
        if report:
            for cat, tickers in report.get("watchlist", {}).items():
                watchlist_html += f"<h3>{self._esc(cat)}</h3><p>{', '.join(tickers)}</p>\n"

        benchmark_html = ""
        if report:
            for k, v in report.get("benchmark", {}).items():
                cls = "pos" if v and v > 0 else ("neg" if v and v < 0 else "")
                val = f"{v*100:+.2f}%" if v is not None else "N/A"
                benchmark_html += f'<div class="card"><div class="label">{k.upper()}</div><div class="value {cls}">{val}</div></div>\n'

        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI ETF 儀表板 — {account_id}</title>
{_COMMON_STYLE}
<script src="{_CHART_JS_CDN}"></script>
</head>
<body>
<nav><span class="brand">AI ETF</span>
  <a href="index.html" class="active">總覽</a>
  <a href="history.html">歷史報告</a>
</nav>

<div class="page">
<h1>投資組合總覽 — {account_id}</h1>
<p class="subtitle">最後更新：{date_str}</p>

<div class="cards">
  <div class="card"><div class="label">淨資產值</div><div class="value">${nav:,.2f}</div></div>
  <div class="card"><div class="label">現金</div><div class="value">${cash:,.2f}</div></div>
  <div class="card"><div class="label">已投資</div><div class="value">${invested:,.2f}</div></div>
  <div class="card"><div class="label">最大回撤</div><div class="value {'neg' if dd>0 else ''}">{dd:.2f}%</div></div>
  {benchmark_html}
</div>

<h2>淨資產值走勢</h2>
<div class="chart-controls">
  <label><input type="checkbox" id="cb-nav" checked onchange="toggleSeries(0)"> 本帳戶</label>
  <label><input type="checkbox" id="cb-spy" onchange="toggleSeries(1)"> SPY</label>
  <label><input type="checkbox" id="cb-qqq" onchange="toggleSeries(2)"> QQQ</label>
</div>
<canvas id="navChart" height="80"></canvas>

<h2>回撤走勢</h2>
<canvas id="ddChart" height="50"></canvas>

<h2>目前持倉</h2>
<table>
  <tr><th>股票</th><th>股數</th><th>現價</th><th>市值</th><th>佔比</th><th>本益比</th><th>日漲跌</th><th>週漲跌</th><th>月漲跌</th></tr>
  {positions_rows}
</table>

<h2>前10名排行</h2>
<table>
  <tr><th>排名</th><th>股票</th><th>評分</th><th>本益比</th><th>90日動能</th></tr>
  {top10_rows}
</table>

<h2>觀察名單</h2>
{watchlist_html}

<h2>交易日誌</h2>
<table id="tradesTable">
  <tr><th>日期</th><th>股票</th><th>方向</th><th>股數</th><th>成交價</th><th>金額</th><th>狀態</th></tr>
  <tr><td colspan="7" style="text-align:center;color:#8b949e">載入中…</td></tr>
</table>

<div class="disclaimer" id="disclaimer-text"></div>
</div>

<script>
async function loadDashboard() {{
  let latest, navHist, trades;
  try {{
    latest  = await fetch('data/latest.json').then(r=>r.json());
    navHist = await fetch('data/nav_history.json').then(r=>r.json());
    trades  = await fetch('data/trades.json').then(r=>r.json()).catch(()=>[]);
  }} catch(e) {{ return; }}

  // Render trades log
  const tbl = document.getElementById('tradesTable');
  if (trades && trades.length > 0) {{
    tbl.innerHTML = '<tr><th>日期</th><th>股票</th><th>方向</th><th>股數</th><th>成交價</th><th>金額</th><th>狀態</th></tr>';
    trades.forEach(t => {{
      const sideCls = t.side === 'buy' ? 'pos' : 'neg';
      const sideText = t.side === 'buy' ? '買入' : (t.side === 'sell' ? '賣出' : t.side);
      tbl.innerHTML += `<tr>
        <td style="text-align:left">${{t.date}}</td>
        <td style="text-align:left"><strong>${{t.ticker}}</strong></td>
        <td class="${{sideCls}}">${{sideText}}</td>
        <td>${{t.shares}}</td>
        <td>$${{(+t.price).toFixed(2)}}</td>
        <td>$${{(+t.value).toLocaleString('en-US',{{minimumFractionDigits:0,maximumFractionDigits:0}})}}</td>
        <td>${{t.status}}</td>
      </tr>`;
    }});
  }} else {{
    tbl.innerHTML = '<tr><th>日期</th><th>股票</th><th>方向</th><th>股數</th><th>成交價</th><th>金額</th><th>狀態</th></tr>' +
      '<tr><td colspan="7" style="text-align:center;color:#8b949e">尚無交易記錄</td></tr>';
  }}

  document.getElementById('disclaimer-text').textContent =
    latest.risk_disclaimer || '';

  const labels = navHist.map(d=>d.date);
  const navVals = navHist.map(d=>d.nav);

  // Drawdown series
  let peak = -Infinity;
  const ddVals = navVals.map(v => {{
    peak = Math.max(peak, v);
    return peak > 0 ? -((peak - v) / peak * 100) : 0;
  }});

  window._navChart = new Chart(document.getElementById('navChart'), {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label:'NAV', data:navVals, borderColor:'#58a6ff', tension:0.3, pointRadius:2, fill:false }},
        {{ label:'SPY', data:[], borderColor:'#3fb950', tension:0.3, pointRadius:2, fill:false, hidden:true }},
        {{ label:'QQQ', data:[], borderColor:'#e3b341', tension:0.3, pointRadius:2, fill:false, hidden:true }},
      ]
    }},
    options:{{ plugins:{{legend:{{labels:{{color:'#c9d1d9'}}}}}}, scales:{{ x:{{ticks:{{color:'#8b949e'}}}}, y:{{ticks:{{color:'#8b949e'}}}} }} }}
  }});

  new Chart(document.getElementById('ddChart'), {{
    type: 'line',
    data: {{
      labels,
      datasets: [{{ label:'回撤 %', data:ddVals, borderColor:'#f85149',
        backgroundColor:'rgba(248,81,73,0.15)', fill:true, tension:0.3, pointRadius:0 }}]
    }},
    options:{{ plugins:{{legend:{{labels:{{color:'#c9d1d9'}}}}}}, scales:{{ x:{{ticks:{{color:'#8b949e'}}}}, y:{{ticks:{{color:'#8b949e'}}}} }} }}
  }});
}}

function toggleSeries(idx) {{
  const chart = window._navChart;
  const meta = chart.getDatasetMeta(idx);
  meta.hidden = !meta.hidden;
  chart.update();
}}

loadDashboard();
</script>
</body>
</html>"""

    def _render_history(self, dates: List[str], account_id: str) -> str:
        rows = ""
        for d in reversed(dates):
            rows += (
                f"<tr><td>{d}</td>"
                f"<td><a href='../reports/{d}/{account_id}.json' target='_blank'>JSON</a></td></tr>\n"
            )
        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>歷史報告 — {account_id}</title>
{_COMMON_STYLE}
</head>
<body>
<nav><span class="brand">AI ETF</span>
  <a href="index.html">總覽</a>
  <a href="history.html" class="active">歷史報告</a>
</nav>
<div class="page">
<h1>歷史報告 — {account_id}</h1>
<p>共 {len(dates)} 筆報告</p>
<table>
  <tr><th>日期</th><th>報告</th></tr>
  {rows}
</table>
</div>
</body>
</html>"""

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── shared CSS ────────────────────────────────────────────────────────────────

_COMMON_STYLE = """<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh}
  nav{background:#161b22;padding:.6rem 1.5rem;display:flex;align-items:center;gap:1rem;border-bottom:1px solid #30363d}
  .brand{font-weight:700;color:#58a6ff;margin-right:auto}
  nav a{color:#c9d1d9;text-decoration:none;padding:.3rem .6rem;border-radius:4px}
  nav a:hover,nav a.active{background:#21262d;color:#58a6ff}
  .page{padding:1.5rem 2rem;max-width:1200px}
  h1,h2,h3{color:#58a6ff;margin:1.2rem 0 .6rem}
  .subtitle{color:#8b949e;margin-bottom:1rem}
  .cards{display:flex;flex-wrap:wrap;gap:.75rem;margin-bottom:1.5rem}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:.75rem 1rem;min-width:130px}
  .card .label{font-size:.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}
  .card .value{font-size:1.3rem;font-weight:700;margin-top:.2rem}
  .pos{color:#3fb950}.neg{color:#f85149}
  table{border-collapse:collapse;width:100%;margin-bottom:1.5rem}
  th,td{border:1px solid #30363d;padding:6px 10px;text-align:right;font-size:.85rem}
  th{background:#161b22;color:#8b949e;text-align:center}
  td:first-child,td:nth-child(2){text-align:left}
  canvas{max-width:100%;margin-bottom:1.5rem}
  .chart-controls{display:flex;gap:1rem;margin-bottom:.5rem;color:#8b949e;font-size:.85rem}
  .chart-controls label{cursor:pointer}
  .disclaimer{color:#8b949e;font-size:.75rem;border-top:1px solid #30363d;padding-top:.75rem;margin-top:1.5rem}
</style>"""
