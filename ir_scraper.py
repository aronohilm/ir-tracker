"""
ir_scraper.py — Fetches IR pages, extracts document links, detects new ones.

Supports:
  - static pages (requests + BeautifulSoup)
  - JS-rendered pages (Playwright, only installed when needed)

State is stored in state.json — committed back to the repo by the GitHub Action
so it persists across runs without any external database.
"""

import re
import json
import hashlib
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent / "state.json"
COMPANIES_FILE = Path(__file__).parent / "companies.yml"
DOWNLOADS_DIR = Path(__file__).parent / "downloads"

# Document type classifier based on filename / link text keywords
DOC_TYPE_PATTERNS = {
    "annual_report":            r"arsreikn|annual.report|financial.statement|samstæðureikn",
    "quarterly":                r"q[1-4].20\d\d|árshluta|interim|q[1-4]-20",
    "investor_presentation":    r"fjarfesta|investor.present|kynning",
    "press_release":            r"frettatiln|press.release|uppgjör.*frétt",
    "sustainability":           r"sjalfbaern|sustainab|esg",
    "risk_report":              r"ahaettu|pillar|risk",
    "agm":                      r"adal|aðalfund|agm|annual.general",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IR-Tracker/1.0; "
        "+https://github.com/your-org/ir-tracker)"
    )
}


# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def url_fingerprint(url: str) -> str:
    """Stable short ID for a URL"""
    return hashlib.sha1(url.encode()).hexdigest()[:12]


# ── Document type detection ───────────────────────────────────────────────────

def classify_document(url: str, link_text: str) -> str:
    combined = (url + " " + link_text).lower()
    for doc_type, pattern in DOC_TYPE_PATTERNS.items():
        if re.search(pattern, combined, re.IGNORECASE):
            return doc_type
    # Fallback by extension
    if url.endswith(".pdf"):
        return "pdf"
    if url.endswith((".xlsx", ".xls")):
        return "spreadsheet"
    if url.endswith(".zip"):
        return "archive"
    return "other"


def should_notify(doc_type: str, notify_filter: str) -> bool:
    if notify_filter == "all":
        return True
    return doc_type == notify_filter


# ── Page fetching ─────────────────────────────────────────────────────────────

def fetch_static(url: str, retries: int = 3) -> str | None:
    """Fetch page HTML using requests with retry logic"""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # exponential backoff
    return None


def fetch_js(url: str) -> str | None:
    """Fetch JS-rendered page using Playwright"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=HEADERS)
            page.goto(url, wait_until="networkidle", timeout=30000)
            # Wait a bit extra for any lazy-loading
            page.wait_for_timeout(2000)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        log.error(f"Playwright error for {url}: {e}")
        return None


def fetch_page(url: str, fetch_type: str) -> str | None:
    if fetch_type == "js":
        return fetch_js(url)
    return fetch_static(url)


# ── Link extraction ───────────────────────────────────────────────────────────

def extract_document_links(html: str, base_url: str,
                            link_pattern: str | None = None) -> list[dict]:
    """
    Extract all document links from a page.
    Returns list of {url, text, doc_type, extension}
    """
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc
    results = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)

        # Resolve relative URLs
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        # Only keep http/https
        if parsed.scheme not in ("http", "https"):
            continue

        # Skip anchors/empty
        if not parsed.path or parsed.path == "/":
            continue

        # Only documents we care about
        ext = Path(parsed.path).suffix.lower()
        if ext not in (".pdf", ".xlsx", ".xls", ".zip", ".docx"):
            continue

        # Apply optional regex filter
        if link_pattern:
            if not re.search(link_pattern, full_url + " " + text, re.IGNORECASE):
                continue

        # Deduplicate
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        doc_type = classify_document(full_url, text)
        results.append({
            "url": full_url,
            "text": text or Path(parsed.path).name,
            "doc_type": doc_type,
            "extension": ext,
            "fingerprint": url_fingerprint(full_url),
        })

    return results


# ── Download ──────────────────────────────────────────────────────────────────

def download_file(url: str, dest_dir: Path, company_ticker: str) -> Path | None:
    """Download a document to dest_dir. Returns path if successful."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(urlparse(url).path).name
    # Sanitize filename
    filename = re.sub(r'[^\w\-_\. ]', '_', filename)
    if not filename:
        filename = url_fingerprint(url) + ".pdf"

    # Prefix with ticker for easy identification
    dest_path = dest_dir / f"{company_ticker}_{filename}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info(f"Downloaded: {dest_path.name} ({dest_path.stat().st_size // 1024}KB)")
        return dest_path
    except Exception as e:
        log.error(f"Download failed for {url}: {e}")
        return None


# ── Main scan logic ───────────────────────────────────────────────────────────

def scan_company(company: dict, state: dict, download: bool = True) -> list[dict]:
    """
    Scan one company's IR page.
    Returns list of NEW document dicts found since last run.
    """
    name = company["name"]
    ticker = company.get("ticker", name)
    url = company["ir_url"]
    fetch_type = company.get("fetch_type", "static")
    link_pattern = company.get("link_pattern")
    notify_filter = company.get("notify_filter", "all")

    log.info(f"Scanning: {name} ({ticker})")

    html = fetch_page(url, fetch_type)
    if not html:
        log.error(f"Failed to fetch page for {name}")
        return []

    documents = extract_document_links(html, url, link_pattern)
    log.info(f"  Found {len(documents)} document links")

    # Load known documents for this company
    company_state = state.get(ticker, {"known": {}, "last_scan": None})
    known = company_state.get("known", {})

    new_docs = []
    for doc in documents:
        fp = doc["fingerprint"]
        if fp not in known:
            # New document!
            doc["company"] = name
            doc["ticker"] = ticker
            doc["discovered_at"] = datetime.now(timezone.utc).isoformat()
            doc["notifiable"] = should_notify(doc["doc_type"], notify_filter)

            new_docs.append(doc)

            # Download if enabled
            if download and doc["notifiable"]:
                dest_dir = DOWNLOADS_DIR / ticker
                local_path = download_file(doc["url"], dest_dir, ticker)
                doc["local_path"] = str(local_path) if local_path else None

            # Mark as known
            known[fp] = {
                "url": doc["url"],
                "text": doc["text"],
                "doc_type": doc["doc_type"],
                "discovered_at": doc["discovered_at"],
            }

    # Update state
    company_state["known"] = known
    company_state["last_scan"] = datetime.now(timezone.utc).isoformat()
    company_state["total_documents"] = len(documents)
    state[ticker] = company_state

    if new_docs:
        log.info(f"  🆕 {len(new_docs)} NEW document(s) found for {name}")
    else:
        log.info(f"  ✓ No new documents")

    return new_docs


def run_scan(companies_file: Path = COMPANIES_FILE,
             download: bool = True,
             dry_run: bool = False) -> list[dict]:
    """
    Full scan of all companies. Returns all new documents found.
    """
    config = yaml.safe_load(companies_file.read_text(encoding="utf-8"))
    companies = config.get("companies", [])

    state = load_state()
    all_new_docs = []

    for company in companies:
        try:
            new_docs = scan_company(
                company, state,
                download=download and not dry_run
            )
            all_new_docs.extend(new_docs)
        except Exception as e:
            log.error(f"Error scanning {company.get('name', '?')}: {e}")
        # Be polite — don't hammer servers
        time.sleep(1)

    if not dry_run:
        save_state(state)
        log.info(f"State saved to {STATE_FILE}")

    log.info(f"\nScan complete: {len(all_new_docs)} new document(s) across {len(companies)} companies")
    return all_new_docs


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan but don't download or save state")
    parser.add_argument("--no-download", action="store_true",
                        help="Detect new docs but don't download")
    parser.add_argument("--company", help="Only scan this ticker")
    args = parser.parse_args()

    if args.company:
        # Single company mode
        config = yaml.safe_load(COMPANIES_FILE.read_text())
        companies = [c for c in config["companies"]
                     if c.get("ticker") == args.company]
        if not companies:
            print(f"Company {args.company} not found in companies.yml")
            exit(1)
        state = load_state()
        new = scan_company(companies[0], state, download=not args.no_download)
        if not args.dry_run:
            save_state(state)
        print(json.dumps(new, indent=2, ensure_ascii=False))
    else:
        new_docs = run_scan(
            download=not args.no_download,
            dry_run=args.dry_run
        )
        if new_docs:
            print("\n" + "="*60)
            print("NEW DOCUMENTS FOUND:")
            for doc in new_docs:
                print(f"  [{doc['ticker']}] {doc['doc_type']}: {doc['text']}")
                print(f"    {doc['url']}")
