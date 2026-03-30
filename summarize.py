import json, os, pathlib

f = pathlib.Path("new_docs.json")
if not f.exists():
    print("No results file found")
    raise SystemExit(0)

docs = json.loads(f.read_text())
print(f"Found {len(docs)} new document(s)")
for d in docs:
    ticker = d.get("ticker", "?")
    doc_type = d.get("doc_type", "?")
    text = d.get("text", "")
    url = d.get("url", "")
    print(f"  [{ticker}] {doc_type}: {text}")
    print(f"    {url}")
