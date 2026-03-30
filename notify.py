"""
notify.py — Sends email notification when new IR documents are detected.

Uses Gmail SMTP (free). Configure via GitHub Actions secrets:
  - NOTIFY_EMAIL_TO   : recipient address
  - NOTIFY_EMAIL_FROM : your Gmail address
  - NOTIFY_EMAIL_PASS : Gmail App Password (NOT your regular password)
      → Create at: myaccount.google.com/apppasswords

For Slack instead: set SLACK_WEBHOOK_URL secret and use notify_slack().
"""

import os
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

log = logging.getLogger(__name__)

# Doc type → friendly Icelandic/English label
DOC_TYPE_LABELS = {
    "annual_report":            "📊 Annual Report / Ársreikningur",
    "quarterly":                "📈 Quarterly Report / Árshlutauppgjör",
    "investor_presentation":    "📋 Investor Presentation / Fjárfestakynning",
    "press_release":            "📰 Press Release / Fréttatilkynning",
    "sustainability":           "🌱 Sustainability Report / Sjálfbærniskýrsla",
    "risk_report":              "⚠️ Risk Report / Áhættuskýrsla",
    "agm":                      "🏛️ AGM Document / Aðalfundur",
    "pdf":                      "📄 Document / Skjal",
    "spreadsheet":              "📊 Spreadsheet / Tafla",
    "archive":                  "📦 Archive / Skjalasafn",
    "other":                    "📎 Document",
}


def format_email_html(new_docs: list[dict]) -> str:
    """Build HTML email body"""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Group by company
    by_company: dict[str, list] = {}
    for doc in new_docs:
        ticker = doc.get("ticker", "?")
        by_company.setdefault(ticker, []).append(doc)

    rows_html = ""
    for ticker, docs in sorted(by_company.items()):
        company_name = docs[0].get("company", ticker)
        rows_html += f"""
        <tr style="background:#f5f5f5;">
          <td colspan="2" style="padding:10px 12px; font-weight:bold; 
              font-size:15px; border-top:2px solid #ddd;">
            {company_name} <span style="color:#888;font-weight:normal;">({ticker})</span>
          </td>
        </tr>"""
        for doc in docs:
            label = DOC_TYPE_LABELS.get(doc.get("doc_type", "other"), "📎 Document")
            url = doc.get("url", "#")
            text = doc.get("text", "Document")
            discovered = doc.get("discovered_at", "")[:10]
            rows_html += f"""
        <tr>
          <td style="padding:8px 12px 8px 24px; color:#555;">{label}</td>
          <td style="padding:8px 12px;">
            <a href="{url}" style="color:#0066cc; text-decoration:none;">{text}</a>
            <span style="color:#aaa; font-size:12px; margin-left:8px;">{discovered}</span>
          </td>
        </tr>"""

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, Arial, sans-serif; max-width:680px; margin:0 auto; color:#333;">
  <div style="background:#1a1a2e; padding:20px 24px; border-radius:8px 8px 0 0;">
    <h1 style="margin:0; color:#fff; font-size:20px;">
      📡 IR Tracker — New Documents Found
    </h1>
    <p style="margin:4px 0 0; color:#aaa; font-size:13px;">{now}</p>
  </div>
  
  <div style="border:1px solid #ddd; border-top:none; border-radius:0 0 8px 8px; overflow:hidden;">
    <table width="100%" cellpadding="0" cellspacing="0" 
           style="border-collapse:collapse; font-size:14px;">
      {rows_html}
    </table>
  </div>

  <p style="color:#aaa; font-size:12px; margin-top:16px; text-align:center;">
    IR Tracker • {len(new_docs)} new document(s) across {len(by_company)} company/companies<br>
    <a href="https://github.com/your-org/ir-tracker" style="color:#aaa;">View on GitHub</a>
  </p>
</body>
</html>
"""


def format_email_text(new_docs: list[dict]) -> str:
    """Plain text fallback"""
    lines = ["IR TRACKER — New Documents Found", "=" * 40, ""]
    by_company: dict[str, list] = {}
    for doc in new_docs:
        by_company.setdefault(doc.get("ticker", "?"), []).append(doc)

    for ticker, docs in sorted(by_company.items()):
        lines.append(f"{docs[0].get('company', ticker)} ({ticker})")
        for doc in docs:
            lines.append(f"  • {doc.get('doc_type', 'doc')}: {doc.get('text', '')}")
            lines.append(f"    {doc.get('url', '')}")
        lines.append("")
    return "\n".join(lines)


def notify_email(new_docs: list[dict]) -> bool:
    """Send email notification via Gmail SMTP"""
    to_addr = os.environ.get("NOTIFY_EMAIL_TO")
    from_addr = os.environ.get("NOTIFY_EMAIL_FROM")
    password = os.environ.get("NOTIFY_EMAIL_PASS")

    if not all([to_addr, from_addr, password]):
        log.warning("Email env vars not set (NOTIFY_EMAIL_TO/FROM/PASS). Skipping email.")
        return False

    n = len(new_docs)
    tickers = sorted(set(d.get("ticker", "?") for d in new_docs))
    subject = f"[IR Tracker] {n} new document{'s' if n > 1 else ''} — {', '.join(tickers)}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(format_email_text(new_docs), "plain", "utf-8"))
    msg.attach(MIMEText(format_email_html(new_docs), "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(from_addr, password)
            smtp.sendmail(from_addr, to_addr, msg.as_string())
        log.info(f"Email sent to {to_addr}")
        return True
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False


def notify_slack(new_docs: list[dict]) -> bool:
    """Send Slack notification via webhook"""
    import urllib.request
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        log.warning("SLACK_WEBHOOK_URL not set. Skipping Slack.")
        return False

    by_company: dict[str, list] = {}
    for doc in new_docs:
        by_company.setdefault(doc.get("ticker", "?"), []).append(doc)

    blocks = [{"type": "header", "text": {"type": "plain_text",
               "text": f"📡 {len(new_docs)} New IR Document(s)"}}]

    for ticker, docs in sorted(by_company.items()):
        company = docs[0].get("company", ticker)
        doc_lines = "\n".join(
            f"• <{d['url']}|{d.get('text', d['doc_type'])}> "
            f"_{DOC_TYPE_LABELS.get(d.get('doc_type','other'), d.get('doc_type',''))}_"
            for d in docs
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{company}* ({ticker})\n{doc_lines}"}
        })

    payload = json.dumps({"blocks": blocks}).encode()
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        log.info("Slack notification sent")
        return True
    except Exception as e:
        log.error(f"Slack failed: {e}")
        return False


def send_notifications(new_docs: list[dict]):
    """Send all configured notifications"""
    if not new_docs:
        log.info("No new docs — nothing to notify")
        return

    notifiable = [d for d in new_docs if d.get("notifiable", True)]
    if not notifiable:
        log.info("New docs found but none match notify_filter")
        return

    log.info(f"Sending notifications for {len(notifiable)} document(s)...")

    # Try email
    notify_email(notifiable)

    # Try Slack (if configured)
    if os.environ.get("SLACK_WEBHOOK_URL"):
        notify_slack(notifiable)


if __name__ == "__main__":
    # Test with dummy data
    test_docs = [
        {
            "company": "Kvika banki hf.", "ticker": "KVIKA",
            "doc_type": "annual_report", "text": "Ársreikningur 2025",
            "url": "https://example.com/ar2025.pdf",
            "discovered_at": "2026-02-11T09:00:00Z", "notifiable": True,
        }
    ]
    print(format_email_text(test_docs))
    print("\n--- Would send email & Slack if env vars are set ---")
