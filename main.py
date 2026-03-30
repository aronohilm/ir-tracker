#!/usr/bin/env python3
"""
main.py — Entry point for GitHub Actions.
Runs the full scan → notify pipeline.
"""

import sys
import json
import logging
from pathlib import Path

from ir_scraper import run_scan, COMPANIES_FILE
from notify import send_notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="IR Document Tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan only, no downloads or notifications")
    parser.add_argument("--no-download", action="store_true",
                        help="Detect but don't download new documents")
    parser.add_argument("--output-json", type=str,
                        help="Write new docs list to this JSON file")
    args = parser.parse_args()

    log.info("Starting IR document scan...")

    new_docs = run_scan(
        companies_file=COMPANIES_FILE,
        download=not args.no_download and not args.dry_run,
        dry_run=args.dry_run,
    )

    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(new_docs, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        log.info(f"Results written to {args.output_json}")

    if not args.dry_run:
        send_notifications(new_docs)

    # Exit code: 0 = success (even if nothing new), 1 = errors
    return 0


if __name__ == "__main__":
    sys.exit(main())
