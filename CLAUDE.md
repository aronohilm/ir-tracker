# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

IR Tracker monitors Icelandic company investor relations pages for new documents (annual reports, quarterly results, etc.) and sends email/Slack notifications when something new appears. It runs daily via GitHub Actions — no server, no database. State is stored in `state.json` and committed back to the repo after each scan.

## Running the scanner locally

```bash
# Activate the virtual environment
source .venv/bin/activate

# Full dry run (no downloads, no notifications, no state changes)
python main.py --dry-run

# Scan a single company without touching global state
python ir_scraper.py --company KVIKA --dry-run

# Scan and detect but skip downloading files
python main.py --no-download

# Regenerate the HTML dashboard from current state.json
python generate_dashboard.py
```

Playwright is only needed when a company uses `fetch_type: js`. Install it on demand:
```bash
pip install playwright && playwright install chromium
```

## Architecture

The pipeline is: `main.py` → `ir_scraper.run_scan()` → `notify.send_notifications()`.

**`ir_scraper.py`** — core engine:
- `fetch_static()` uses `requests` + retry/backoff for plain HTML pages.
- `fetch_js()` uses Playwright (headless Chromium) for JS-rendered pages. Handles tab/accordion click-through, dropdown menus (`open_selector`), native `<select>` elements, and cookie banners.
- `extract_document_links()` finds PDF/XLSX/ZIP/DOCX links and classifies them via `DOC_TYPE_PATTERNS` regex dict.
- `scan_company()` compares found links against `state.json` using SHA-1 URL fingerprints. New links are downloaded (if `download=True`) and added to state.
- State is keyed by ticker symbol: `state[ticker]["known"][fingerprint]`.

**`companies.yml`** — the only file that needs regular editing. Key per-company fields:
- `fetch_type`: `static` or `js`
- `tab_selector`: CSS selector for year tabs/accordion to click through (JS mode)
- `open_selector`: CSS selector for a dropdown trigger that must be re-opened before each tab click
- `tab_text_filter`: regex to skip non-year tabs (e.g. `"^20[12][0-9]$"`)
- `wait_until`: `networkidle` (default) or `load` for pages that never settle
- `link_pattern`: regex to filter which hrefs count as documents (`null` = all PDF/XLSX)
- `notify_filter`: `all` or a specific doc type (`annual_report`, `quarterly`, etc.)

**`notify.py`** — sends email (Gmail SMTP) and/or Slack (webhook). Both are no-ops if the relevant env vars are absent. Reads `NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`, `NOTIFY_EMAIL_PASS`, `SLACK_WEBHOOK_URL`.

**`generate_dashboard.py`** — reads `companies.yml` + `state.json` and writes `dashboard.html`. The dashboard is a static file; run manually or add to the GitHub Actions workflow to publish it.

**`state.json`** — auto-managed. Structure: `{ "TICKER": { "known": { "<sha1_12>": { url, text, doc_type, discovered_at } }, "last_scan": "<iso>", "total_documents": N } }`. Never manually edit fingerprints; they are derived from `hashlib.sha1(url).hexdigest()[:12]`.

## GitHub Actions

The workflow (`.github/workflows/ir_tracker.yml`) runs at 09:30 Reykjavík time on weekdays (UTC 07:30, cron `30 7 * * 1-5`). It:
1. Runs `python main.py` (or `--dry-run` if triggered manually with that option)
2. Runs `summarize.py` to print results to the Actions log
3. Commits updated `state.json` back with `[skip ci]` to avoid a loop
4. Uploads any downloaded files as build artifacts (90-day retention)

Required secrets: `NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`, `NOTIFY_EMAIL_PASS`. Optional: `SLACK_WEBHOOK_URL`.

## Adding a new company

Edit `companies.yml`. Start with `fetch_type: static`. If 0 documents are found, switch to `fetch_type: js` and identify the tab/year selector with browser DevTools. Test with:
```bash
python ir_scraper.py --company TICKER --dry-run
```
