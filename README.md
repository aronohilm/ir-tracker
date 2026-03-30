# IR Document Tracker 🇮🇸

Automatically monitors Icelandic company investor relations pages for new documents (annual reports, quarterly results, investor presentations) and sends you an email when something new appears.

## How it works

1. **Daily at 09:30 Reykjavík time** (weekdays), GitHub Actions runs the scan
2. Fetches each company's IR page using `requests` (or Playwright for JS-heavy sites)  
3. Extracts all PDF/XLSX document links
4. Compares against `state.json` (the known-documents log committed to the repo)
5. If new documents are found → downloads them → sends you an email
6. Updates `state.json` and commits it back to the repo

No server needed. No database. Completely free.

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

Edit `companies.yml`. That's the only file you ever need to touch:

```yaml
- name: "Hagar hf."
  ticker: HAGA
  sector: "Retail"
  ir_url: "https://www.hagar.is/fjarfestatengslar/"
  fetch_type: static    # or 'js' if the page needs JavaScript
  link_pattern: null    # null = all PDFs; or a regex like "arsreikn|annual"
  notify_filter: all    # or: annual_report, quarterly, investor_presentation
```

**How to find the IR URL:** go to the company's website, find "Fjárfestaupplýsingar" or "Investor Relations" and copy the URL of the page that lists their documents.

**fetch_type:** start with `static`. If you see 0 documents found for a company, switch to `js` (requires Playwright, slightly slower).

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
├── requirements.txt          ← Python dependencies
└── downloads/                ← Downloaded PDFs (uploaded as GH artifacts)
```

## Document types auto-detected

| Type | Detected when filename/link contains |
|------|--------------------------------------|
| `annual_report` | arsreikn, annual report, financial statement |
| `quarterly` | Q1-Q4, árshluta, interim |
| `investor_presentation` | fjarfesta, investor present, kynning |
| `press_release` | frettatiln, press release |
| `sustainability` | sjalfbaern, sustainab, esg |
| `risk_report` | ahaettu, pillar, risk |
| `agm` | adal, aðalfund, agm |

## Cost

**Free.** GitHub Actions gives you 2,000 free minutes/month on private repos. This job takes ~2 minutes per run × 22 weekdays = ~44 minutes/month. Well within the free tier.
