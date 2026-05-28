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

_CHART_JS_CDN  = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"
_TAILWIND_CDN  = "https://cdn.tailwindcss.com"


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

        benchmark_history = self._aggregate_benchmark_history(account_id, all_dates)
        p = self._data_dir / "benchmark_history.json"
        p.write_text(json.dumps(benchmark_history, ensure_ascii=False, indent=2))
        generated["benchmark_history_json"] = p

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

    def _aggregate_benchmark_history(self, account_id: str, dates: List[str]) -> Dict:
        """Build cumulative SPY/QQQ index from saved daily benchmark returns."""
        spy_cum, qqq_cum = 1.0, 1.0
        spy_out, qqq_out = [], []
        for d in dates:
            path = self.reports_dir / d / f"{account_id}.json"
            if not path.exists():
                continue
            report = json.loads(path.read_text())
            bm = report.get("benchmark", {})
            spy_cum *= (1 + bm.get("spy_1d", 0))
            qqq_cum *= (1 + bm.get("qqq_1d", 0))
            spy_out.append({"date": d, "value": round(spy_cum, 6)})
            qqq_out.append({"date": d, "value": round(qqq_cum, 6)})
        return {"spy": spy_out, "qqq": qqq_out}

    # ── HTML rendering ────────────────────────────────────────────────────────

    def _render_index(self, report: Optional[Dict], account_id: str) -> str:
        date_str = report["date"] if report else "—"
        # Embed report data inline so tests can find values and page loads faster
        inline_report = json.dumps(report or {}, ensure_ascii=False)
        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI ETF Dashboard — {account_id}</title>
<script src="{_TAILWIND_CDN}"></script>
<script src="{_CHART_JS_CDN}"></script>
<style>
  body {{ background:#0f172a; color:#e2e8f0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif }}
  .card {{ background:#1e293b; border-radius:12px; padding:20px; margin-bottom:16px }}
  .kpi-card {{ background:#1e293b; border-radius:10px; padding:16px }}
  .pos-row:hover {{ background:rgba(255,255,255,.04) }}
  .sym-badge {{ display:inline-block; font-size:12px; font-weight:600; padding:3px 10px;
                border-radius:20px; margin:2px; border:1px solid }}
  select, option {{ background:#1e293b; color:#e2e8f0 }}
  .spinner {{ animation:spin 1s linear infinite }}
  @keyframes spin {{ from{{transform:rotate(0deg)}} to{{transform:rotate(360deg)}} }}
</style>
</head>
<body>

<!-- Nav -->
<nav style="background:#1e293b;border-bottom:1px solid #334155;padding:.65rem 1.5rem;
            display:flex;align-items:center;gap:1rem">
  <span style="font-weight:700;color:#38bdf8;font-size:1rem;margin-right:auto">🤖 AI ETF</span>
  <a href="index.html"
     style="color:#38bdf8;text-decoration:none;font-size:.875rem;font-weight:600;
            background:#0f172a;padding:.35rem .85rem;border-radius:6px">總覽</a>
  <a href="history.html"
     style="color:#94a3b8;text-decoration:none;font-size:.875rem;
            padding:.35rem .85rem;border-radius:6px">歷史報告</a>
</nav>

<!-- Loading -->
<div id="loading" class="text-center py-20 text-slate-400">
  <svg class="spinner mx-auto mb-4" width="32" height="32" viewBox="0 0 24 24"
       fill="none" stroke="currentColor" stroke-width="2">
    <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke-opacity=".25"/>
    <path d="M21 12a9 9 0 00-9-9"/>
  </svg>
  正在載入資料…
</div>

<!-- Main Content -->
<div id="main-content" style="display:none" class="max-w-5xl mx-auto px-4 py-6">

  <!-- Header -->
  <div class="card mb-4">
    <div class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h1 id="title" class="text-xl font-bold text-white">AI ETF — {account_id}</h1>
        <p class="text-sm text-slate-400 mt-1">帳戶：{account_id}</p>
      </div>
      <div id="status-badge"></div>
    </div>
    <p class="text-xs text-slate-500 mt-3">
      最後更新：<span id="report-date">{date_str}</span>
    </p>
  </div>

  <!-- KPI Cards -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
    <div class="kpi-card">
      <div class="text-xs text-slate-400 uppercase tracking-wide mb-2">NAV 淨值</div>
      <div id="kpi-nav" class="text-2xl font-bold text-white">—</div>
      <div id="kpi-cash" class="text-xs text-slate-400 mt-1">—</div>
    </div>
    <div class="kpi-card">
      <div class="text-xs text-slate-400 uppercase tracking-wide mb-2">今日損益</div>
      <div id="kpi-today-pct" class="text-2xl font-bold">—</div>
      <div id="kpi-today-amt" class="text-xs mt-1">—</div>
    </div>
    <div class="kpi-card">
      <div class="text-xs text-slate-400 uppercase tracking-wide mb-2">總報酬 TWR</div>
      <div id="kpi-twr" class="text-2xl font-bold">—</div>
      <div id="kpi-invested" class="text-xs text-slate-400 mt-1">—</div>
    </div>
    <div class="kpi-card">
      <div class="text-xs text-slate-400 uppercase tracking-wide mb-2">最大回撤</div>
      <div id="kpi-dd" class="text-2xl font-bold">—</div>
      <div id="kpi-benchmark" class="text-xs mt-1">—</div>
    </div>
  </div>

  <!-- Portfolio badges -->
  <div class="card" id="portfolio-section" style="display:none">
    <h2 id="portfolio-label" class="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-3">目前持倉</h2>
    <div id="portfolio-badges"></div>
  </div>

  <!-- NAV Chart -->
  <div class="card">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wide">NAV 走勢</h2>
      <div id="nav-toggles" class="flex gap-4 text-xs"></div>
    </div>
    <canvas id="navChart" height="110"></canvas>
  </div>

  <!-- Drawdown Chart -->
  <div class="card">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wide">回撤對比</h2>
      <div id="dd-toggles" class="flex gap-4 text-xs"></div>
    </div>
    <canvas id="ddChart" height="90"></canvas>
  </div>

  <!-- Holdings Table -->
  <div class="card" id="holdings-section" style="display:none">
    <h2 id="holdings-title" class="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-4">當前持倉</h2>
    <div class="overflow-x-auto">
      <table class="w-full text-sm" id="positions-table">
        <thead>
          <tr class="text-xs text-slate-500 border-b border-slate-700">
            <th class="py-2 px-3 text-left">#</th>
            <th class="py-2 px-3 text-left">股票</th>
            <th class="py-2 px-3 text-right">股數</th>
            <th class="py-2 px-3 text-right">股價</th>
            <th class="py-2 px-3 text-right">市值</th>
            <th class="py-2 px-3 text-right">權重</th>
            <th class="py-2 px-3 text-right">日漲跌</th>
            <th class="py-2 px-3 text-right">週漲跌</th>
          </tr>
        </thead>
        <tbody id="positions-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Rankings (Top 10) -->
  <div class="card" id="rankings-section" style="display:none">
    <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-4">🏆 前10名排行</h2>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-xs text-slate-500 border-b border-slate-700">
            <th class="py-2 px-3 text-left">#</th>
            <th class="py-2 px-3 text-left">股票</th>
            <th class="py-2 px-3 text-right">評分</th>
            <th class="py-2 px-3 text-right">90日動能</th>
            <th class="py-2 px-3 text-right">本益比</th>
          </tr>
        </thead>
        <tbody id="rankings-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Watchlist -->
  <div class="card" id="watchlist-section" style="display:none">
    <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-3">👀 觀察名單</h2>
    <div id="watchlist-content"></div>
  </div>

  <!-- Trade Log -->
  <div class="card">
    <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-4">⚡ 交易日誌</h2>
    <div id="trade-log"><div class="text-sm text-slate-500">載入中…</div></div>
  </div>

  <!-- Disclaimer -->
  <div style="border-top:1px solid #334155;padding-top:16px;margin-top:8px;
              color:#475569;font-size:11px;line-height:1.6;text-align:center"
       id="disclaimer"></div>

</div><!-- /main-content -->

<script>
const ACCENT = "#38bdf8";
// Inline report data (embedded at build time for instant load + test compatibility)
const INLINE_REPORT = {inline_report};

async function loadDashboard() {{
  let latest, navHist, trades, bm;
  try {{
    // Use inline data for latest report; fetch the rest from JSON files
    latest = INLINE_REPORT && INLINE_REPORT.date ? INLINE_REPORT : await fetch('data/latest.json').then(r=>r.json());
    [navHist, trades, bm] = await Promise.all([
      fetch('data/nav_history.json').then(r=>r.json()).catch(()=>[]),
      fetch('data/trades.json').then(r=>r.json()).catch(()=>[]),
      fetch('data/benchmark_history.json').then(r=>r.json()).catch(()=>({{spy:[],qqq:[]}})),
    ]);
  }} catch(e) {{
    document.getElementById('loading').innerHTML =
      '<div class="text-red-400 py-10 text-center">❌ 無法載入資料：' + e.message + '</div>';
    return;
  }}

  document.getElementById('loading').style.display = 'none';
  document.getElementById('main-content').style.display = 'block';

  renderKPI(latest, navHist);
  renderPortfolioBadges(latest);
  renderNavChart(navHist, bm);
  renderDrawdownChart(navHist, bm);
  renderPositions(latest);
  renderRankings(latest);
  renderWatchlist(latest);
  renderTradeLog(trades);

  document.getElementById('disclaimer').textContent = latest.risk_disclaimer || '';
  document.getElementById('report-date').textContent = latest.date || '—';
}}

// ── KPI ──────────────────────────────────────────────────────────────────────
function renderKPI(d, navHist) {{
  const fmtPct = v => (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  const fmtAmt = v => (v < 0 ? '−$' : '+$') + Math.abs(v).toLocaleString('en-US',{{maximumFractionDigits:0}});
  const col    = v => v >= 0 ? '#22c55e' : '#ef4444';

  // NAV
  document.getElementById('kpi-nav').textContent =
    '$' + (d.nav||0).toLocaleString('en-US',{{maximumFractionDigits:0}});
  document.getElementById('kpi-cash').textContent =
    '現金 $' + (d.cash||0).toLocaleString('en-US',{{maximumFractionDigits:0}});

  // Today P&L from nav_history
  const todayEl = document.getElementById('kpi-today-pct');
  const todayAmtEl = document.getElementById('kpi-today-amt');
  if (navHist && navHist.length >= 2) {{
    const prev = navHist[navHist.length-2].nav;
    const curr = navHist[navHist.length-1].nav;
    const chgPct = (curr - prev) / prev * 100;
    const chgAmt = curr - prev;
    todayEl.textContent = fmtPct(chgPct);
    todayEl.style.color = col(chgPct);
    todayAmtEl.textContent = fmtAmt(chgAmt);
    todayAmtEl.style.color = col(chgAmt);
  }} else {{
    todayEl.textContent = '—'; todayEl.style.color = '#64748b';
    todayAmtEl.textContent = '';
  }}

  // TWR total return
  const twrEl = document.getElementById('kpi-twr');
  if (navHist && navHist.length >= 1) {{
    const first = navHist[0].nav;
    const twr = (d.nav - first) / first * 100;
    twrEl.textContent = fmtPct(twr);
    twrEl.style.color = col(twr);
  }} else {{
    twrEl.textContent = '—'; twrEl.style.color = '#64748b';
  }}
  document.getElementById('kpi-invested').textContent =
    '已投資 $' + (d.invested_value||0).toLocaleString('en-US',{{maximumFractionDigits:0}});

  // Drawdown
  const ddEl = document.getElementById('kpi-dd');
  const dd = (d.drawdown_pct || 0) * 100;
  ddEl.textContent = (dd > 0 ? '-' : '') + dd.toFixed(2) + '%';
  ddEl.style.color = dd > 0 ? '#ef4444' : '#22c55e';

  // Benchmark
  const bm = d.benchmark || {{}};
  const spyTxt = bm.spy_1d != null ? (bm.spy_1d>=0?'+':'') + (bm.spy_1d*100).toFixed(2)+'%' : '—';
  const qqqTxt = bm.qqq_1d != null ? (bm.qqq_1d>=0?'+':'') + (bm.qqq_1d*100).toFixed(2)+'%' : '—';
  const bmEl = document.getElementById('kpi-benchmark');
  bmEl.innerHTML = `<span style="color:#94a3b8">SPY </span><span style="color:${{bm.spy_1d>=0?'#22c55e':'#ef4444'}}">${{spyTxt}}</span>
    <span style="color:#94a3b8"> · QQQ </span><span style="color:${{bm.qqq_1d>=0?'#22c55e':'#ef4444'}}">${{qqqTxt}}</span>`;
}}

// ── Portfolio Badges ──────────────────────────────────────────────────────────
function renderPortfolioBadges(d) {{
  const positions = d.positions || [];
  if (!positions.length) return;
  const sec = document.getElementById('portfolio-section');
  sec.style.display = '';
  document.getElementById('portfolio-label').textContent =
    '持倉組合（' + positions.length + ' 檔）';
  document.getElementById('portfolio-badges').innerHTML =
    positions.map(p =>
      `<span class="sym-badge" style="background:${{ACCENT}}22;color:${{ACCENT}};border-color:${{ACCENT}}55">
        ${{p.ticker}}
      </span>`
    ).join('');
}}

// ── NAV Chart ────────────────────────────────────────────────────────────────
let _navChart;
function renderNavChart(navHist, bm) {{
  if (!navHist || !navHist.length) return;
  const labels  = navHist.map(d=>d.date);
  const navVals = navHist.map(d=>d.nav);
  const nav0    = navVals[0] || 1;
  const spyVals = (bm.spy||[]).map(d=>d.value*nav0);
  const qqqVals = (bm.qqq||[]).map(d=>d.value*nav0);

  const datasets = [
    {{ label:'本帳戶', data:navVals,  borderColor:ACCENT,    backgroundColor:ACCENT+'22',    fill:true,  tension:0.3, pointRadius:2, borderWidth:2 }},
    {{ label:'SPY',    data:spyVals,  borderColor:'#a78bfa', backgroundColor:'transparent', fill:false, tension:0.3, pointRadius:0, borderDash:[4,3], borderWidth:1.5 }},
    {{ label:'QQQ',    data:qqqVals,  borderColor:'#fb923c', backgroundColor:'transparent', fill:false, tension:0.3, pointRadius:0, borderDash:[4,3], borderWidth:1.5 }},
  ];

  const ctx = document.getElementById('navChart').getContext('2d');
  if (_navChart) _navChart.destroy();
  _navChart = new Chart(ctx, {{
    type: 'line',
    data: {{ labels, datasets }},
    options: {{
      responsive:true,
      plugins:{{ legend:{{ display:false }} }},
      scales:{{
        x:{{ ticks:{{color:'#64748b',maxTicksLimit:8}}, grid:{{color:'#1e293b'}} }},
        y:{{ ticks:{{color:'#64748b',callback:v=>'$'+v.toLocaleString()}}, grid:{{color:'#334155'}} }}
      }}
    }}
  }});

  // Toggle checkboxes
  const tog = document.getElementById('nav-toggles');
  datasets.forEach((ds, i) => {{
    const lb = document.createElement('label');
    lb.style.cssText = 'display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;';
    const cb = document.createElement('input');
    cb.type='checkbox'; cb.checked=true; cb.style.accentColor=ds.borderColor;
    cb.addEventListener('change', ()=>{{ _navChart.setDatasetVisibility(i,cb.checked); _navChart.update(); }});
    const dot = document.createElement('span');
    dot.style.cssText=`display:inline-block;width:10px;height:10px;border-radius:2px;background:${{ds.borderColor}};flex-shrink:0`;
    const txt = document.createElement('span'); txt.textContent=ds.label; txt.style.color='#94a3b8';
    lb.append(cb,dot,txt); tog.appendChild(lb);
  }});
}}

// ── Drawdown Chart ────────────────────────────────────────────────────────────
let _ddChart;
function renderDrawdownChart(navHist, bm) {{
  if (!navHist || !navHist.length) return;
  const labels  = navHist.map(d=>d.date);
  const navVals = navHist.map(d=>d.nav);

  let peak = -Infinity;
  const ddVals = navVals.map(v=>{{ peak=Math.max(peak,v); return peak>0?-((peak-v)/peak*100):0; }});
  const portDD = ddVals;

  // SPY drawdown from cumulative
  const spyCum  = (bm.spy||[]).map(d=>d.value);
  let spyPeak = -Infinity;
  const spyDD  = spyCum.map(v=>{{ spyPeak=Math.max(spyPeak,v||0); return spyPeak>0?-((spyPeak-(v||0))/spyPeak*100):0; }});

  const qqqCum  = (bm.qqq||[]).map(d=>d.value);
  let qqqPeak = -Infinity;
  const qqqDD  = qqqCum.map(v=>{{ qqqPeak=Math.max(qqqPeak,v||0); return qqqPeak>0?-((qqqPeak-(v||0))/qqqPeak*100):0; }});

  const datasets = [
    {{ label:'投組', data:portDD, borderColor:ACCENT,    backgroundColor:ACCENT+'22',    fill:true,  tension:0.3, borderWidth:2, pointRadius:0 }},
    {{ label:'SPY',  data:spyDD,  borderColor:'#a78bfa', backgroundColor:'transparent', fill:false, tension:0.3, borderDash:[4,3], borderWidth:1.5, pointRadius:0 }},
    {{ label:'QQQ',  data:qqqDD,  borderColor:'#fb923c', backgroundColor:'transparent', fill:false, tension:0.3, borderDash:[4,3], borderWidth:1.5, pointRadius:0 }},
  ];

  const ctx = document.getElementById('ddChart').getContext('2d');
  if (_ddChart) _ddChart.destroy();
  _ddChart = new Chart(ctx, {{
    type:'line',
    data:{{ labels, datasets }},
    options:{{
      responsive:true,
      plugins:{{ legend:{{ display:false }}, tooltip:{{ callbacks:{{ label:c=>` ${{c.dataset.label}}: ${{c.parsed.y.toFixed(2)}}%` }} }} }},
      scales:{{
        x:{{ ticks:{{color:'#64748b',maxTicksLimit:8}}, grid:{{color:'#1e293b'}} }},
        y:{{ ticks:{{color:'#64748b',callback:v=>v.toFixed(1)+'%'}}, grid:{{color:'#334155'}} }}
      }}
    }}
  }});

  // Toggle checkboxes
  const tog = document.getElementById('dd-toggles');
  datasets.forEach((ds,i)=>{{
    const lb=document.createElement('label');
    lb.style.cssText='display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;';
    const cb=document.createElement('input'); cb.type='checkbox'; cb.checked=true; cb.style.accentColor=ds.borderColor;
    cb.addEventListener('change',()=>{{ _ddChart.setDatasetVisibility(i,cb.checked); _ddChart.update(); }});
    const dot=document.createElement('span');
    dot.style.cssText=`display:inline-block;width:10px;height:10px;border-radius:2px;background:${{ds.borderColor}};flex-shrink:0`;
    const txt=document.createElement('span'); txt.textContent=ds.label; txt.style.color='#94a3b8';
    lb.append(cb,dot,txt); tog.appendChild(lb);
  }});
}}

// ── Positions ────────────────────────────────────────────────────────────────
function renderPositions(d) {{
  const pos = d.positions || [];
  if (!pos.length) return;
  const sec = document.getElementById('holdings-section');
  sec.style.display='';
  document.getElementById('holdings-title').textContent = '當前持倉（' + pos.length + ' 檔）';
  const col = v => v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : '#94a3b8';
  const fmtPct = v => v != null ? (v>=0?'+':'')+( v*100).toFixed(2)+'%' : 'N/A';
  document.getElementById('positions-tbody').innerHTML = pos.map((p,i) => `
    <tr class="pos-row border-b border-slate-700/50">
      <td class="py-2 px-3 text-slate-500">${{i+1}}</td>
      <td class="py-2 px-3 font-bold font-mono" style="color:${{ACCENT}}">${{p.ticker}}</td>
      <td class="py-2 px-3 text-right text-slate-300">${{p.shares}}</td>
      <td class="py-2 px-3 text-right">$${{(+p.price).toFixed(2)}}</td>
      <td class="py-2 px-3 text-right">$${{(+p.value).toLocaleString('en-US',{{maximumFractionDigits:0}})}}</td>
      <td class="py-2 px-3 text-right text-slate-400">${{(p.pct_of_nav*100).toFixed(1)}}%</td>
      <td class="py-2 px-3 text-right font-semibold" style="color:${{col(p.return_1d)}}">${{fmtPct(p.return_1d)}}</td>
      <td class="py-2 px-3 text-right" style="color:${{col(p.return_1w)}}">${{fmtPct(p.return_1w)}}</td>
    </tr>`).join('');
}}

// ── Rankings ─────────────────────────────────────────────────────────────────
function renderRankings(d) {{
  const top10 = d.top10 || [];
  if (!top10.length) return;
  document.getElementById('rankings-section').style.display='';
  const col = v => v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : '#94a3b8';
  const heldTickers = new Set((d.positions||[]).map(p=>p.ticker));
  document.getElementById('rankings-tbody').innerHTML = top10.slice(0,10).map(s => {{
    const mom = s.momentum_90d != null ? (s.momentum_90d>=0?'+':'')+(s.momentum_90d*100).toFixed(1)+'%' : 'N/A';
    const pe  = s.pe_ratio ? s.pe_ratio.toFixed(1) : '—';
    const inPort = heldTickers.has(s.ticker);
    return `<tr class="pos-row border-b border-slate-700/50${{inPort?' ':''}}" style="${{inPort?'background:rgba(56,189,248,.05)':''}}">
      <td class="py-2 px-3 text-slate-500 text-xs">${{s.rank||''}}</td>
      <td class="py-2 px-3 font-bold font-mono" style="color:${{ACCENT}}">
        ${{s.ticker}}${{inPort?' <span style="color:#22c55e;font-size:10px">●</span>':''}}
      </td>
      <td class="py-2 px-3 text-right font-semibold" style="color:${{ACCENT}}">${{(+s.score).toFixed(1)}}</td>
      <td class="py-2 px-3 text-right font-semibold" style="color:${{col(s.momentum_90d)}}">${{mom}}</td>
      <td class="py-2 px-3 text-right text-slate-400">${{pe}}</td>
    </tr>`;
  }}).join('');
}}

// ── Watchlist ────────────────────────────────────────────────────────────────
function renderWatchlist(d) {{
  const wl = d.watchlist || {{}};
  if (!Object.keys(wl).length) return;
  document.getElementById('watchlist-section').style.display='';
  document.getElementById('watchlist-content').innerHTML =
    Object.entries(wl).map(([cat, tickers]) => `
      <div style="margin-bottom:10px">
        <span style="color:#e3b341;font-size:12px;font-weight:700">${{cat}}</span>
        <span style="color:#64748b;font-size:13px;margin-left:8px">${{tickers.join('  ·  ')}}</span>
      </div>`).join('');
}}

// ── Trade Log ────────────────────────────────────────────────────────────────
function renderTradeLog(trades) {{
  const el = document.getElementById('trade-log');
  if (!trades || !trades.length) {{
    el.innerHTML = '<div class="text-sm text-slate-500">尚無交易記錄</div>';
    return;
  }}
  // Group by date
  const byDate = {{}};
  trades.forEach(t => {{
    if (!byDate[t.date]) byDate[t.date] = [];
    byDate[t.date].push(t);
  }});
  el.innerHTML = Object.entries(byDate).map(([date, orders]) => {{
    const orderHtml = orders.map(o => {{
      const c = o.side === 'buy' ? '#22c55e' : '#ef4444';
      const label = o.side === 'buy' ? '買入' : '賣出';
      const val = (+o.value)||0;
      return `<span class="inline-block mr-3 text-xs">
        <span style="color:${{c}};font-weight:600">${{label}}</span>
        ${{o.ticker}} ×${{o.shares}} @$${{(+o.price).toFixed(2)}}
        <span style="color:#64748b">=$${{val.toLocaleString('en-US',{{maximumFractionDigits:0}})}}</span>
      </span>`;
    }}).join('');
    const totalVal = orders.reduce((s,o)=>(o.side==='buy'?1:-1)*(+o.value||0)+s,0);
    return `<div class="border-b border-slate-700/40 py-3">
      <div class="flex items-center justify-between mb-1">
        <span class="text-sm font-semibold text-slate-300">${{date}}</span>
        <span class="text-xs text-slate-400">${{orders.length}} 筆</span>
      </div>
      <div class="text-slate-400">${{orderHtml}}</div>
    </div>`;
  }}).join('');
}}

// Alias kept for backward compatibility with tests
function toggleSeries(idx) {{
  if (_navChart) {{ _navChart.setDatasetVisibility(idx, !_navChart.isDatasetVisible(idx)); _navChart.update(); }}
}}

loadDashboard();
</script>
</body>
</html>"""

    def _render_history(self, dates: List[str], account_id: str) -> str:
        rows = ""
        for d in reversed(dates):
            rows += (
                f"<tr class='border-b border-slate-700/40'>"
                f"<td class='py-2 px-4 text-slate-300'>{d}</td>"
                f"<td class='py-2 px-4'><a href='../reports/{d}/{account_id}.json' target='_blank' "
                f"style='color:#38bdf8;text-decoration:none;font-size:.85rem'>JSON ↗</a></td></tr>\n"
            )
        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>歷史報告 — {account_id}</title>
<script src="{_TAILWIND_CDN}"></script>
<style>body{{background:#0f172a;color:#e2e8f0}}</style>
</head>
<body>
<nav style="background:#1e293b;border-bottom:1px solid #334155;padding:.65rem 1.5rem;
            display:flex;align-items:center;gap:1rem">
  <span style="font-weight:700;color:#38bdf8;font-size:1rem;margin-right:auto">🤖 AI ETF</span>
  <a href="index.html" style="color:#94a3b8;text-decoration:none;font-size:.875rem;padding:.35rem .85rem;border-radius:6px">總覽</a>
  <a href="history.html" style="color:#38bdf8;text-decoration:none;font-size:.875rem;font-weight:600;background:#0f172a;padding:.35rem .85rem;border-radius:6px">歷史報告</a>
</nav>
<div class="max-w-2xl mx-auto px-4 py-8">
  <div style="background:#1e293b;border-radius:12px;padding:20px">
    <h1 class="text-xl font-bold text-white mb-1">歷史報告</h1>
    <p class="text-sm text-slate-400 mb-6">帳戶：{account_id}　共 {len(dates)} 筆</p>
    <table class="w-full text-sm">
      <thead><tr class="text-xs text-slate-500 border-b border-slate-700">
        <th class="py-2 px-4 text-left">日期</th>
        <th class="py-2 px-4 text-left">報告</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
