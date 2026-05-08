#!/usr/bin/env python3
"""Generate dashboard.html from companies.yml and state.json."""

import json
import re
import yaml
from datetime import datetime, timezone

with open("companies.yml") as f:
    companies = yaml.safe_load(f)["companies"]

try:
    with open("state.json") as f:
        state = json.load(f)
except FileNotFoundError:
    state = {}

YEAR_RANGE = list(range(2015, datetime.now().year + 2))  # 2015–current+1

def extract_year(url, text):
    # Search text first, then filename only (avoids /uploads/YYYY/MM/ path pollution)
    from pathlib import Path as _P
    from urllib.parse import unquote as _unquote
    filename = _unquote(_P(url.split("?")[0]).name)
    for src in (text, filename):
        # Full 4-digit year
        m = re.search(r'20(1[5-9]|2[0-6])', src)
        if m:
            return m.group(0)
        # 2-digit year in Icelandic date format e.g. 31.12.16 or 31.12.17
        m2 = re.search(r'31\.12\.(1[5-9]|2[0-6])', src)
        if m2:
            return "20" + m2.group(1)
    return None

rows = []
for c in companies:
    ticker = c["ticker"]
    s = state.get(ticker, {})
    known = s.get("known", {})
    last_scan = s.get("last_scan")

    docs = []
    for fp, d in known.items():
        year = extract_year(d.get("url", ""), d.get("text", ""))
        docs.append({
            "fp": fp,
            "url": d.get("url", ""),
            "text": d.get("text", "") or d.get("url", "").split("/")[-1],
            "doc_type": d.get("doc_type", "other"),
            "year": year,
            "discovered_at": d.get("discovered_at", ""),
        })
    docs.sort(key=lambda d: (d["year"] or "0000", d["doc_type"]), reverse=True)

    annual_years = sorted({d["year"] for d in docs if d["doc_type"] == "annual_report" and d["year"]}, reverse=True)
    doc_type_counts = {}
    for d in docs:
        doc_type_counts[d["doc_type"]] = doc_type_counts.get(d["doc_type"], 0) + 1

    if ticker not in state:
        status = "pending"
    elif not docs:
        status = "empty"
    else:
        status = "ok"

    last_scan_fmt = ""
    if last_scan:
        dt = datetime.fromisoformat(last_scan)
        last_scan_fmt = dt.strftime("%Y-%m-%d")

    rows.append({
        "ticker": ticker,
        "name": c["name"],
        "sector": c.get("sector", ""),
        "fetch_type": c.get("fetch_type", "static"),
        "ir_url": c.get("ir_url", ""),
        "status": status,
        "last_scan": last_scan_fmt,
        "docs": docs,
        "annual_years": annual_years,
        "doc_type_counts": doc_type_counts,
        "total_docs": len(docs),
    })

rows.sort(key=lambda r: ({"ok": 0, "empty": 1, "pending": 2}[r["status"]], r["name"]))

stats = {
    "scraped": sum(1 for r in rows if r["status"] == "ok"),
    "empty":   sum(1 for r in rows if r["status"] == "empty"),
    "pending": sum(1 for r in rows if r["status"] == "pending"),
    "total":   len(rows),
    "annual_reports": sum(r["doc_type_counts"].get("annual_report", 0) for r in rows),
}
generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

data_json = json.dumps({"rows": rows, "yearRange": YEAR_RANGE}, ensure_ascii=False)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IR Tracker</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f0f4f8;color:#1e293b;font-size:14px}}
a{{color:#2563eb;text-decoration:none}}a:hover{{text-decoration:underline}}

/* Header */
header{{background:#0f172a;color:#fff;padding:18px 28px;display:flex;align-items:center;justify-content:space-between}}
header h1{{font-size:1.15rem;font-weight:600;letter-spacing:-.01em}}
header .gen{{font-size:.75rem;opacity:.5}}

/* Stats */
.statsbar{{display:flex;gap:12px;padding:16px 28px}}
.stat{{background:#fff;border-radius:8px;padding:12px 18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.stat .n{{font-size:1.6rem;font-weight:700;line-height:1}}
.stat .l{{font-size:.7rem;color:#64748b;margin-top:3px}}
.stat.s-ok .n{{color:#16a34a}}.stat.s-empty .n{{color:#d97706}}.stat.s-pending .n{{color:#94a3b8}}.stat.s-ar .n{{color:#1d4ed8}}

/* Controls */
.controls{{display:flex;gap:10px;padding:0 28px 16px;flex-wrap:wrap;align-items:center}}
.controls input{{border:1px solid #e2e8f0;border-radius:6px;padding:6px 12px;font-size:.85rem;width:220px;outline:none}}
.controls input:focus{{border-color:#2563eb}}
.sort-label{{font-size:.75rem;color:#64748b;margin-left:4px}}
select{{border:1px solid #e2e8f0;border-radius:6px;padding:5px 10px;font-size:.82rem;background:#fff;outline:none;cursor:pointer}}
.type-filter{{display:flex;gap:6px;flex-wrap:wrap;margin-left:8px}}
.tf-btn{{border:1px solid #e2e8f0;background:#fff;border-radius:20px;padding:3px 10px;font-size:.75rem;cursor:pointer;color:#475569;transition:all .15s}}
.tf-btn.active{{background:#1e293b;color:#fff;border-color:#1e293b}}

/* Main table */
.wrapper{{padding:0 28px 40px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
thead th{{background:#f8fafc;padding:9px 12px;font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:#64748b;border-bottom:1px solid #e2e8f0;cursor:pointer;user-select:none;white-space:nowrap}}
thead th:hover{{background:#f1f5f9}}
thead th.sorted{{color:#1e293b}}
thead th .arr{{margin-left:3px;opacity:.5}}
tbody tr.company-row{{cursor:pointer;transition:background .1s}}
tbody tr.company-row:hover{{background:#f8fafc}}
tbody tr.company-row.expanded{{background:#f0f4ff}}
tbody tr.detail-row td{{padding:0;background:#f8fafc;border-bottom:1px solid #e2e8f0}}
td{{padding:9px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}}
tbody tr:last-child td{{border-bottom:none}}
.row-empty td{{color:#94a3b8}}.row-empty a{{color:#94a3b8}}
.row-pending td{{color:#cbd5e1}}.row-pending a{{color:#cbd5e1}}

/* Status pill */
.pill{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.7rem;font-weight:600}}
.pill-ok{{background:#dcfce7;color:#15803d}}.pill-empty{{background:#fef3c7;color:#b45309}}.pill-pending{{background:#f1f5f9;color:#94a3b8}}

/* Fetch badge */
.fb{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:.68rem;font-weight:500}}
.fb-static{{background:#e0f2fe;color:#0369a1}}.fb-js{{background:#fef9c3;color:#92400e}}

/* Year cells */
.years-row{{display:flex;gap:3px;flex-wrap:wrap}}
.yr{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:.7rem;font-weight:600;background:#dbeafe;color:#1d4ed8}}
.yr-miss{{background:#f1f5f9;color:#cbd5e1}}

/* Doc type badges */
.dt{{display:inline-block;padding:1px 7px;border-radius:4px;font-size:.68rem;font-weight:500;white-space:nowrap}}
.dt-annual_report{{background:#dbeafe;color:#1d4ed8}}
.dt-quarterly{{background:#ede9fe;color:#6d28d9}}
.dt-investor_presentation{{background:#fce7f3;color:#9d174d}}
.dt-press_release{{background:#fee2e2;color:#b91c1c}}
.dt-risk_report{{background:#fef3c7;color:#92400e}}
.dt-sustainability{{background:#dcfce7;color:#166534}}
.dt-agm{{background:#e0f2fe;color:#075985}}
.dt-pdf{{background:#f1f5f9;color:#475569}}
.dt-spreadsheet{{background:#f0fdf4;color:#166534}}
.dt-archive{{background:#f5f3ff;color:#6d28d9}}
.dt-other{{background:#f1f5f9;color:#64748b}}

/* Detail panel */
.detail-inner{{padding:12px 16px 16px 48px}}
.detail-controls{{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center}}
.detail-controls .dc-label{{font-size:.72rem;color:#64748b}}
.d-tf-btn{{border:1px solid #e2e8f0;background:#fff;border-radius:14px;padding:2px 9px;font-size:.7rem;cursor:pointer;color:#475569}}
.d-tf-btn.active{{background:#1e293b;color:#fff;border-color:#1e293b}}
.doc-table{{width:100%;border-collapse:collapse}}
.doc-table th{{padding:5px 10px;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;color:#64748b;border-bottom:1px solid #e2e8f0;text-align:left;background:#f8fafc;cursor:pointer}}
.doc-table th:hover{{background:#f1f5f9}}
.doc-table td{{padding:6px 10px;font-size:.78rem;border-bottom:1px solid #f1f5f9;vertical-align:middle}}
.doc-table tr:last-child td{{border-bottom:none}}
.doc-table tr:hover{{background:#f0f4ff}}
.yr-badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:.7rem;font-weight:600;background:#f1f5f9;color:#475569;min-width:38px;text-align:center}}
.yr-badge.has-year{{background:#dbeafe;color:#1d4ed8}}
.no-docs{{padding:20px;text-align:center;color:#94a3b8;font-size:.82rem}}
.toggle-icon{{float:right;opacity:.4;font-size:.9rem}}
</style>
</head>
<body>
<header>
  <h1>IR Tracker</h1>
  <span class="gen">Generated {generated_at} &nbsp;·&nbsp; <code style="font-size:.7rem;opacity:.7">python3 generate_dashboard.py</code></span>
</header>

<div class="statsbar">
  <div class="stat s-ok"><div class="n">{stats["scraped"]}</div><div class="l">Companies scraped</div></div>
  <div class="stat s-ar"><div class="n">{stats["annual_reports"]}</div><div class="l">Annual reports</div></div>
  <div class="stat s-empty"><div class="n">{stats["empty"]}</div><div class="l">No docs found</div></div>
  <div class="stat s-pending"><div class="n">{stats["pending"]}</div><div class="l">Not yet run</div></div>
  <div class="stat"><div class="n">{stats["total"]}</div><div class="l">Total companies</div></div>
</div>

<div class="controls">
  <input type="text" id="search" placeholder="Search company or ticker…" oninput="render()">
  <span class="sort-label">Sort:</span>
  <select id="sortBy" onchange="render()">
    <option value="name">Company name</option>
    <option value="ticker">Ticker</option>
    <option value="sector">Sector</option>
    <option value="annual_reports">Annual reports ↓</option>
    <option value="total_docs">Total docs ↓</option>
    <option value="years_covered">Years covered ↓</option>
  </select>
  <span class="sort-label" style="margin-left:12px">Filter type:</span>
  <div class="type-filter" id="typeFilter"></div>
</div>

<div class="wrapper">
  <table id="mainTable">
    <thead>
      <tr>
        <th style="width:70px">Ticker</th>
        <th>Company</th>
        <th style="width:110px">Sector</th>
        <th style="width:48px">Fetch</th>
        <th style="width:80px">Status</th>
        <th style="width:200px">Annual report years (2015–2025)</th>
        <th style="width:180px">Document types</th>
        <th style="width:85px">Last scan</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<script>
const DATA = {data_json};
const rows = DATA.rows;
const yearRange = DATA.yearRange;

// Collect all doc types
const allTypes = [...new Set(rows.flatMap(r => Object.keys(r.doc_type_counts)))].sort();
let activeTypes = new Set(); // empty = show all

// Build type filter buttons
const tfEl = document.getElementById('typeFilter');
allTypes.forEach(t => {{
  const b = document.createElement('button');
  b.className = 'tf-btn';
  b.textContent = t.replace(/_/g,' ');
  b.dataset.type = t;
  b.onclick = () => {{
    if (activeTypes.has(t)) activeTypes.delete(t); else activeTypes.add(t);
    b.classList.toggle('active');
    render();
  }};
  tfEl.appendChild(b);
}});

let expandedTickers = new Set();
let detailTypeFilters = {{}}; // ticker -> Set of active types

function typeClass(t) {{
  return 'dt dt-' + t;
}}

function render() {{
  const search = document.getElementById('search').value.toLowerCase();
  const sortBy = document.getElementById('sortBy').value;

  let filtered = rows.filter(r => {{
    if (search && !r.name.toLowerCase().includes(search) && !r.ticker.toLowerCase().includes(search) && !r.sector.toLowerCase().includes(search)) return false;
    if (activeTypes.size > 0) {{
      // Only show companies that have at least one doc of the active types
      const hasType = [...activeTypes].some(t => (r.doc_type_counts[t] || 0) > 0);
      if (!hasType) return false;
    }}
    return true;
  }});

  filtered.sort((a, b) => {{
    if (sortBy === 'ticker') return a.ticker.localeCompare(b.ticker);
    if (sortBy === 'sector') return a.sector.localeCompare(b.sector) || a.name.localeCompare(b.name);
    if (sortBy === 'annual_reports') return (b.doc_type_counts.annual_report||0) - (a.doc_type_counts.annual_report||0) || a.name.localeCompare(b.name);
    if (sortBy === 'total_docs') return b.total_docs - a.total_docs || a.name.localeCompare(b.name);
    if (sortBy === 'years_covered') return b.annual_years.length - a.annual_years.length || a.name.localeCompare(b.name);
    // default: name, but status first
    const so = {{ok:0,empty:1,pending:2}};
    return (so[a.status]||0) - (so[b.status]||0) || a.name.localeCompare(b.name);
  }});

  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';

  filtered.forEach(r => {{
    // Summary row
    const tr = document.createElement('tr');
    tr.className = 'company-row row-' + r.status;
    tr.dataset.ticker = r.ticker;

    const isExpanded = expandedTickers.has(r.ticker);
    if (isExpanded) tr.classList.add('expanded');

    // Year grid
    const yearHtml = yearRange.map(y => {{
      const ys = String(y);
      const has = r.annual_years.includes(ys);
      return `<span class="yr${{has ? '' : ' yr-miss'}}">${{ys.slice(2)}}</span>`;
    }}).join('');

    // Type badges
    const typeHtml = Object.entries(r.doc_type_counts)
      .sort((a,b) => b[1]-a[1])
      .map(([t,n]) => `<span class="${{typeClass(t)}}">${{t.replace(/_/g,' ')}} ${{n}}</span>`)
      .join(' ');

    const pillClass = {{ok:'pill-ok',empty:'pill-empty',pending:'pill-pending'}}[r.status];
    const pillText = r.status === 'ok' ? r.total_docs + ' docs' : r.status === 'empty' ? 'empty' : 'pending';

    tr.innerHTML = `
      <td><strong>${{r.ticker}}</strong></td>
      <td><a href="${{r.ir_url}}" target="_blank" onclick="event.stopPropagation()">${{r.name}}</a><span class="toggle-icon">${{isExpanded ? '▲' : '▼'}}</span></td>
      <td style="color:#64748b">${{r.sector}}</td>
      <td><span class="fb fb-${{r.fetch_type}}">${{r.fetch_type}}</span></td>
      <td><span class="pill ${{pillClass}}">${{pillText}}</span></td>
      <td><div class="years-row">${{yearHtml}}</div></td>
      <td style="display:flex;flex-wrap:wrap;gap:3px">${{typeHtml || '<span style="color:#cbd5e1">—</span>'}}</td>
      <td style="color:#94a3b8;font-size:.72rem">${{r.last_scan}}</td>
    `;
    tr.onclick = () => toggleDetail(r.ticker);
    tbody.appendChild(tr);

    // Detail row
    if (isExpanded) {{
      const dtr = document.createElement('tr');
      dtr.className = 'detail-row';
      dtr.dataset.ticker = r.ticker;
      const dtd = document.createElement('td');
      dtd.colSpan = 8;
      dtd.innerHTML = buildDetail(r);
      dtr.appendChild(dtd);
      tbody.appendChild(dtr);
    }}
  }});
}}

function buildDetail(r) {{
  if (!r.docs.length) return '<div class="no-docs">No documents found yet.</div>';

  const tf = detailTypeFilters[r.ticker] || new Set();
  const dtypes = [...new Set(r.docs.map(d => d.doc_type))].sort();

  const typeButtons = dtypes.map(t => {{
    const active = tf.has(t);
    return `<button class="d-tf-btn${{active ? ' active' : ''}}" onclick="toggleDetailType('${{r.ticker}}','${{t}}')">${{t.replace(/_/g,' ')}}</button>`;
  }}).join('');

  const docs = tf.size > 0 ? r.docs.filter(d => tf.has(d.doc_type)) : r.docs;

  const docRows = docs.map(d => {{
    const yrBadge = d.year
      ? `<span class="yr-badge has-year">${{d.year}}</span>`
      : `<span class="yr-badge">—</span>`;
    const title = d.text || d.url.split('/').pop();
    return `<tr>
      <td>${{yrBadge}}</td>
      <td><span class="${{typeClass(d.doc_type)}}">${{d.doc_type.replace(/_/g,' ')}}</span></td>
      <td><a href="${{d.url}}" target="_blank">${{title}}</a></td>
    </tr>`;
  }}).join('');

  return `<div class="detail-inner">
    <div class="detail-controls">
      <span class="dc-label">Filter:</span>
      <button class="d-tf-btn${{tf.size===0?' active':''}}" onclick="clearDetailType('${{r.ticker}}')">All</button>
      ${{typeButtons}}
    </div>
    <table class="doc-table">
      <thead><tr><th style="width:60px">Year</th><th style="width:160px">Type</th><th>Document</th></tr></thead>
      <tbody>${{docRows}}</tbody>
    </table>
  </div>`;
}}

function toggleDetail(ticker) {{
  if (expandedTickers.has(ticker)) expandedTickers.delete(ticker);
  else expandedTickers.add(ticker);
  render();
}}

function toggleDetailType(ticker, type) {{
  if (!detailTypeFilters[ticker]) detailTypeFilters[ticker] = new Set();
  const tf = detailTypeFilters[ticker];
  if (tf.has(type)) tf.delete(type); else tf.add(type);
  render();
}}

function clearDetailType(ticker) {{
  detailTypeFilters[ticker] = new Set();
  render();
}}

render();
</script>
</body>
</html>
"""

with open("dashboard.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"dashboard.html generated — {stats['scraped']} scraped, {stats['annual_reports']} annual reports, {stats['empty']} empty, {stats['pending']} pending")
