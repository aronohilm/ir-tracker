# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

IR Tracker monitors Icelandic company investor relations pages for new annual reports and sends email/Slack notifications when something new appears. It runs daily via GitHub Actions — no server, no database. State is stored in `state.json` and committed back to the repo after each scan.

## Branches

- **`main`** — active branch. All 26 Nasdaq Iceland companies use the shared CloudFront CDN at `https://d3q7p1a9jb8ol9.cloudfront.net/{TICKER}-Y-{year}.pdf`. HEAD requests only, no Playwright, ~36s scan time.
- **`cloudfront-refactor`** — original refactor branch, now superseded by `main`.

## Running the scanner locally

```bash
source .venv/bin/activate

# Full dry run (no downloads, no notifications, no state changes)
python main.py --dry-run

# Scan a single company without touching global state
python ir_scraper.py --company KVIKA --dry-run

# Scan and detect but skip downloading files (use this to seed state.json)
python main.py --no-download

# Regenerate the HTML dashboard from current state.json
python generate_dashboard.py
```

Playwright is only needed on `main` branch (companies with `fetch_type: js`):
```bash
pip install playwright && playwright install chromium
```

## Architecture

The pipeline is: `main.py` → `ir_scraper.run_scan()` → `notify.send_notifications()`.

**`ir_scraper.py`** — core engine. All current companies use `fetch_url_template()`. The other strategies remain in the codebase for future use:

- `fetch_url_template()` — probes a URL template with `{year}` via HEAD requests from `start_year` up to `current_year + 1`. Used for the CloudFront CDN pattern. No browser needed.
- `fetch_static()` — `requests` + retry/backoff for plain HTML pages.
- `fetch_js()` — Playwright (headless Chromium) for JS-rendered pages. Handles tab/accordion click-through, dropdown menus (`open_selector`), native `<select>` elements, and cookie banners.
- `fetch_edgar()` — calls SEC EDGAR submissions API (`data.sec.gov/submissions/CIK{cik}.json`) to get 20-F filing URLs. Used for US-listed companies whose IR sites are Cloudflare-protected.

`extract_document_links()` finds PDF/XLSX/ZIP/DOCX links (also `.htm` for `sec.gov` URLs) and classifies them via `DOC_TYPE_PATTERNS` regex dict.

`scan_company()` compares found links against `state.json` using SHA-1 URL fingerprints. New links are downloaded (if `download=True`) and added to state. State is keyed by ticker: `state[ticker]["known"][fingerprint]`.

**`companies.yml`** — the only file that needs editing. Fields vary by `fetch_type`:

For `url_template` (used on `cloudfront-refactor` for all companies):
- `url_template`: URL with `{year}` placeholder, e.g. `https://d3q7p1a9jb8ol9.cloudfront.net/KVIKA-Y-{year}.pdf`
- `start_year`: first year to probe (set to current year to only watch for new ones; `ir_url` is optional)

For `edgar`:
- `cik`: SEC CIK number (string)

For `js`:
- `tab_selector`: CSS selector for year tabs/accordion to click through
- `open_selector`: CSS selector for a dropdown trigger that must be re-opened before each tab click
- `tab_text_filter`: regex to skip non-year tabs (e.g. `"^20[12][0-9]$"`)
- `wait_until`: `networkidle` (default) or `load` for pages that never settle

Common fields:
- `fetch_type`: `url_template`, `static`, `js`, or `edgar`
- `link_pattern`: regex to filter which hrefs count as documents (`null` = all)
- `notify_filter`: `all` or a specific doc type (`annual_report`, `quarterly`, etc.)

**`notify.py`** — sends email (Gmail SMTP) and/or Slack (webhook). No-ops if env vars are absent. Reads `NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`, `NOTIFY_EMAIL_PASS`, `SLACK_WEBHOOK_URL`.

**`generate_dashboard.py`** — reads `companies.yml` + `state.json` and writes `dashboard.html`.

**`state.json`** — auto-managed. Structure: `{ "TICKER": { "known": { "<sha1_12>": { url, text, doc_type, discovered_at } }, "last_scan": "<iso>", "total_documents": N } }`. Fingerprints are `hashlib.sha1(url).hexdigest()[:12]` — never edit manually.

## GitHub Actions

The workflow runs at 09:30 Reykjavík time on weekdays (UTC 07:30, cron `30 7 * * 1-5`). It:
1. Runs `python main.py --output-json new_docs.json` (or `--dry-run` if triggered manually)
2. Runs `summarize.py` to print results to the Actions log
3. Commits updated `state.json` back with `[skip ci]` to avoid a loop
4. Uploads downloaded files as artifacts (90-day retention)

No Playwright install step needed.

Required secrets: `NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`, `NOTIFY_EMAIL_PASS`. Optional: `SLACK_WEBHOOK_URL`.

## Adding a new company

Check if the ticker exists on the CDN first:
```bash
curl -I "https://d3q7p1a9jb8ol9.cloudfront.net/TICKER-Y-2025.pdf"
```
If it returns 200, add a `url_template` entry to `companies.yml` with `start_year` set to the current year, then run `python main.py --no-download` to seed historical years into state.json.

If the company isn't on the CDN, use `fetch_type: static` (or `js` for JS-rendered pages, requires Playwright). Test with:
```bash
python ir_scraper.py --company TICKER --dry-run
```
