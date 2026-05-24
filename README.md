# IR Document Tracker 🇮🇸

Automatically monitors all 26 Nasdaq Iceland listed companies for new annual reports and sends you an email when something new appears.

## How it works

1. **Daily at 09:30 Reykjavík time** (weekdays), GitHub Actions runs the scan
2. Probes the shared Nasdaq Iceland CloudFront CDN via HEAD requests — no browser, no scraping
3. Compares found documents against `state.json` (the known-documents log committed to the repo)
4. If new documents are found → downloads them → sends you an email
5. Updates `state.json` and commits it back to the repo

No server needed. No database. Completely free. Each scan takes ~36 seconds.

---

## Setup (one-time, ~10 minutes)

### 1. Create a private GitHub repo and push this code

```bash
git init ir-tracker
cd ir-tracker
# copy all files here
git add .
git commit -m "Initial setup"
git remote add origin https://github.com/YOUR_USERNAME/ir-tracker.git
git push -u origin main
```

### 2. Configure email notifications

You need a **Gmail App Password** (not your regular password):
1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Select "Mail" → "Other (custom name)" → type "IR Tracker" → Generate
3. Copy the 16-character password

Then in your GitHub repo:
- Go to **Settings → Secrets and variables → Actions → New repository secret**
- Add these three secrets:

| Secret name | Value |
|-------------|-------|
| `NOTIFY_EMAIL_TO` | Where you want emails sent (can be any address) |
| `NOTIFY_EMAIL_FROM` | Your Gmail address |
| `NOTIFY_EMAIL_PASS` | The App Password from step above |

### 3. (Optional) Add Slack notifications

Create a Slack Incoming Webhook at [api.slack.com/apps](https://api.slack.com/apps) and add:

| Secret name | Value |
|-------------|-------|
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/services/...` |

### 4. Test it manually

Go to **Actions → IR Document Tracker → Run workflow** → check "dry run" first to verify everything works without downloading anything.

---

## Adding companies

Edit `companies.yml`. All current companies use the shared Nasdaq Iceland CloudFront CDN:

```yaml
- name: "Hagar hf."
  ticker: HAGA
  sector: "Retail"
  fetch_type: url_template
  url_template: "https://d3q7p1a9jb8ol9.cloudfront.net/HAGA-Y-{year}.pdf"
  start_year: 2025
  notify_filter: annual_report
```

**To check if a ticker is on the CDN:**
```bash
curl -I "https://d3q7p1a9jb8ol9.cloudfront.net/TICKER-Y-2025.pdf"
```
If it returns 200, add the entry above with `start_year` set to the current year, then run `python main.py --no-download` to seed historical years into state.json.

**For companies not on the CDN:** use `fetch_type: static` (or `fetch_type: js` for JS-rendered IR pages). See `CLAUDE.md` for details.

---

## Running locally

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

---

## File structure

```
ir-tracker/
├── .github/
│   └── workflows/
│       └── ir_tracker.yml    ← GitHub Actions schedule & steps
├── companies.yml             ← YOUR COMPANY LIST (edit this)
├── state.json                ← Auto-managed: known documents log
├── main.py                   ← Entry point
├── ir_scraper.py             ← Page fetching & link extraction
├── notify.py                 ← Email & Slack notifications
├── summarize.py              ← Prints scan results to Actions log
├── generate_dashboard.py     ← Writes dashboard.html from state.json
├── dashboard.html            ← Auto-generated HTML dashboard
├── requirements.txt          ← Python dependencies
└── downloads/                ← Downloaded PDFs (uploaded as GH artifacts)
```

## Cost

**Free.** GitHub Actions gives you 2,000 free minutes/month on private repos. This job takes ~1 minute per run × 22 weekdays = ~22 minutes/month. Well within the free tier.
