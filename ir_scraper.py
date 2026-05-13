"""
ir_scraper.py — Fetches IR pages, extracts document links, detects new ones.

Supports:
  - static pages (requests + BeautifulSoup)
  - JS-rendered pages (Playwright, only installed when needed)

State is stored in state.json — committed back to the repo by the GitHub Action
so it persists across runs without any external database.
"""

from __future__ import annotations

import re
import json
import hashlib
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, unquote

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
    "quarterly":                r"q[1-4].20\d\d|[áa]rshluta|interim|q[1-4]-20|[1-4]f.20\d\d",
    "agm":                      r"adal|aðalfund|agm|annual.general",
    "risk_report":              r"ahaettu|pillar|risk",
    "sustainability":           r"sjalfbaern|sustainab|esg",
    "investor_presentation":    r"investor.present|kynning",
    "annual_report":            r"rsreikn|annual.?report|financial.statement|samstæðureikn|[áa]rssk|samst.*reikn|consolidated.fs",
    "press_release":            r"frettatiln|press.release|uppgj",
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


def fetch_js(url: str, tab_selector: str | None = None,
             wait_until: str = "networkidle",
             open_selector: str | None = None,
             tab_text_filter: str | None = None) -> str | None:
    """Fetch JS-rendered page using Playwright.
    If tab_selector is given, clicks each matching element and accumulates HTML
    from every state so all dynamically-loaded content is captured.
    open_selector: CSS selector for a trigger button that must be clicked to
      open a dropdown/menu before each tab_selector item can be clicked (e.g.
      Headless UI menus that close after each selection).
    tab_text_filter: regex — only click tabs whose innerText matches (use to
      skip non-year tabs like "Lykill" or "Eldra").
    wait_until: 'networkidle' (default) or 'load' for pages that never settle.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=HEADERS)
            page.goto(url, wait_until=wait_until, timeout=30000)
            page.wait_for_timeout(2000)

            if not tab_selector:
                content = page.content()
                browser.close()
                return content

            # Dismiss common cookie consent banners before interacting
            cookie_selectors = [
                ".ch2-allow-all-btn",           # CookieHub
                "#onetrust-accept-btn-handler",  # OneTrust
                ".cc-allow",                     # cookieconsent
                "button[id*='accept']",
                "button[class*='accept']",
            ]
            dismissed = False
            for sel in cookie_selectors:
                btn = page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=3000)
                        page.wait_for_timeout(800)
                        log.info(f"  Dismissed cookie banner via {sel!r}")
                        dismissed = True
                        break
                    except Exception:
                        pass

            # If click-based dismissal failed, force-remove overlay via JS
            if not dismissed:
                removed = page.evaluate("""() => {
                    const selectors = [
                        '.ch2-container', '.ch2', '[class*="ch2-"]',
                        '#cookiehub', '[id*="cookiehub"]',
                        '.onetrust-pc-dark-filter', '#onetrust-banner-sdk',
                        '.cc-window', '[class*="cookie-banner"]',
                        '[class*="cookie_banner"]', '[id*="cookie-banner"]',
                    ];
                    let removed = 0;
                    for (const sel of selectors) {
                        document.querySelectorAll(sel).forEach(el => {
                            el.remove(); removed++;
                        });
                    }
                    // Also remove pointer-events blocking from body
                    document.body.style.pointerEvents = 'auto';
                    document.body.style.overflow = 'auto';
                    return removed;
                }""")
                if removed:
                    log.info(f"  Force-removed {removed} cookie overlay element(s) via JS")
                    page.wait_for_timeout(500)

            # Click-through mode: collect HTML after clicking each matching element
            all_html = page.content()

            if open_selector:
                # Dropdown mode: must re-open the menu before each item click.
                # open_selector may point to a child element — we traverse up to
                # the nearest <button> so the click registers correctly.
                open_js = f"""() => {{
                    const el = document.querySelector({open_selector!r});
                    const btn = el?.tagName === 'BUTTON' ? el : el?.closest('button');
                    btn?.click();
                }}"""
                # Open once to count items
                page.evaluate(open_js)
                page.wait_for_timeout(600)
                tab_count = len(page.query_selector_all(tab_selector))
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                log.info(f"  Dropdown mode: {tab_count} items matching '{tab_selector}'")
                for i in range(tab_count):
                    try:
                        page.evaluate(open_js)
                        page.wait_for_timeout(600)
                        tabs = page.query_selector_all(tab_selector)
                        if i >= len(tabs):
                            break
                        label = tabs[i].inner_text().strip()
                        page.evaluate("el => el.click()", tabs[i])
                        page.wait_for_timeout(1200)
                        all_html += page.content()
                        log.info(f"    Selected {i+1}/{tab_count}: {label!r}")
                    except Exception as e:
                        log.warning(f"    Dropdown item {i+1} failed: {e}")
            else:
                first = page.query_selector(tab_selector)
                is_select = first and first.evaluate("el => el.tagName") == "SELECT"

                if is_select:
                    # Native <select> element — use select_option then scrape once
                    label = tab_text_filter or "Öll ár"
                    page.select_option(tab_selector, label=label)
                    page.wait_for_timeout(1500)
                    all_html += page.content()
                    log.info(f"  Select mode: chose '{label}' from '{tab_selector}'")
                else:
                    # Accordion / tab mode: elements stay in DOM, click each once
                    tabs = page.query_selector_all(tab_selector)
                    if tab_text_filter:
                        tabs = [t for t in tabs
                                if re.search(tab_text_filter, t.inner_text().strip())]
                    tab_count = len(tabs)
                    log.info(f"  Click-through: found {tab_count} elements matching '{tab_selector}'"
                             + (f" (filtered by {tab_text_filter!r})" if tab_text_filter else ""))
                    for i, tab in enumerate(tabs):
                        try:
                            label = tab.inner_text().strip()
                            # JS click fires the real event and works even when
                            # pointer-events are blocked (cookie banners, etc.)
                            page.evaluate("el => el.click()", tab)
                            page.wait_for_timeout(1200)
                            all_html += page.content()
                            log.info(f"    Clicked {i+1}/{tab_count}: {label!r}")
                        except Exception as e:
                            log.warning(f"    Click {i+1} failed: {e}")

            browser.close()
            return all_html
    except Exception as e:
        log.error(f"Playwright error for {url}: {e}")
        return None


SEC_HEADERS = {
    "User-Agent": "IR-Tracker aronoh2650@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}


def fetch_edgar(cik: str) -> str | None:
    """Fetch 20-F filings from SEC EDGAR submissions API and return synthetic HTML."""
    padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.error(f"EDGAR submissions fetch failed for CIK {cik}: {e}")
        return None

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    numeric_cik = int(cik)

    links = []
    for i, form in enumerate(forms):
        if form == "20-F":
            acc_clean = accessions[i].replace("-", "")
            primary = primary_docs[i] if i < len(primary_docs) else ""
            if primary:
                doc_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{numeric_cik}/{acc_clean}/{primary}"
                )
                links.append(
                    f'<a href="{doc_url}">Annual Report 20-F {dates[i]}</a>'
                )

    log.info(f"  EDGAR: found {len(links)} 20-F filing(s) for CIK {cik}")
    return "<html><body>" + "\n".join(links) + "</body></html>"


def fetch_url_template(template: str, start_year: int) -> str | None:
    """Probe a URL template with {year} for each year from start_year to now+1."""
    current_year = datetime.now(timezone.utc).year
    links = []
    for year in range(start_year, current_year + 2):
        url = template.replace("{year}", str(year))
        try:
            resp = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                log.info(f"  url_template: found {url}")
                links.append(f'<a href="{url}">Annual Report {year}</a>')
            else:
                log.info(f"  url_template: {year} → {resp.status_code}, skipping")
        except requests.RequestException as e:
            log.warning(f"  url_template: HEAD failed for {year}: {e}")
    return "<html><body>" + "\n".join(links) + "</body></html>"


def fetch_page(url: str, fetch_type: str, tab_selector: str | None = None,
               wait_until: str = "networkidle",
               open_selector: str | None = None,
               tab_text_filter: str | None = None,
               cik: str | None = None,
               url_template: str | None = None,
               start_year: int = 2020) -> str | None:
    if fetch_type == "edgar":
        return fetch_edgar(cik or "")
    if fetch_type == "url_template":
        return fetch_url_template(url_template or url, start_year)
    if fetch_type == "js":
        return fetch_js(url, tab_selector=tab_selector, wait_until=wait_until,
                        open_selector=open_selector, tab_text_filter=tab_text_filter)
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
        allowed_exts = {".pdf", ".xlsx", ".xls", ".zip", ".docx"}
        if parsed.netloc.endswith("sec.gov"):
            allowed_exts.add(".htm")
        if ext not in allowed_exts:
            continue

        # Deduplicate
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # Decode URL for pattern matching / classification
        decoded_url = unquote(full_url)

        # Apply optional regex filter
        if link_pattern:
            if not re.search(link_pattern, decoded_url + " " + text, re.IGNORECASE):
                continue

        doc_type = classify_document(decoded_url, text)
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
    tab_selector = company.get("tab_selector")
    open_selector = company.get("open_selector")
    tab_text_filter = company.get("tab_text_filter")
    wait_until = company.get("wait_until", "networkidle")
    notify_filter = company.get("notify_filter", "all")
    cik = company.get("cik")
    url_template = company.get("url_template")
    start_year = company.get("start_year", 2020)

    log.info(f"Scanning: {name} ({ticker})")

    html = fetch_page(url, fetch_type, tab_selector=tab_selector, wait_until=wait_until,
                      open_selector=open_selector, tab_text_filter=tab_text_filter,
                      cik=cik, url_template=url_template, start_year=start_year)
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
