"""GNSS Pulse: collect → dedup → filter → classify → save → notify."""
import datetime as dt
import json
import os
import sys
from pathlib import Path
import yaml
from collectors.sources import collect_all
from pipeline import process, telegram

ROOT = Path(__file__).parent
DATA = ROOT / "web" / "data" / "items.json"
MAX_ITEMS = 5000


def main():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    store = json.loads(DATA.read_text()) if DATA.exists() else {"items": []}
    old = store["items"]

    print("Collecting...")
    raw, errors = collect_all(cfg)
    new = process.dedup(raw, {i["id"] for i in old}, [i["title"].lower() for i in old])
    print(f"{len(raw)} raw → {len(new)} new")
    new = [i for i in new if process.keyword_pass(i, cfg["keywords"])]
    print(f"{len(new)} pass keyword filter")

    process.gemini_classify(new)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    new = [process.finalize(i) | {"added": now} for i in new]
    new = [i for i in new if i["relevant"]]
    print(f"{len(new)} relevant after AI")

    store = {"updated": now, "failed_sources": errors,
             "items": sorted(new + old, key=lambda x: x["added"], reverse=True)[:MAX_ITEMS]}
    DATA.write_text(json.dumps(store, ensure_ascii=False, indent=1))

    telegram.urgent([i for i in new if i["importance"] >= 5])
    if "--digest" in sys.argv:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
        telegram.digest([i for i in store["items"] if i["added"] >= cutoff], os.environ.get("SITE_URL"))


if __name__ == "__main__":
    main()
