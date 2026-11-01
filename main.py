"""GNSS Pulse: collect → dedup → filter → classify → save → notify."""
import datetime as dt
import json
import os
import sys
from pathlib import Path
import yaml
from collectors import calls
from collectors.sources import collect_all
from pipeline import process, telegram

ROOT = Path(__file__).parent
DATA = ROOT / "web" / "data" / "items.json"
CALLS = ROOT / "web" / "data" / "calls.json"
MAX_ITEMS = 5000


def update_calls(cfg):
    """Conference deadlines + journal special issues (once a day is enough)."""
    old = json.loads(CALLS.read_text()) if CALLS.exists() else {}
    if old.get("updated", "")[:10] == dt.date.today().isoformat() and "--calls" not in sys.argv:
        return old
    print("Calls for papers...")
    c = cfg.get("calls", {})
    sjr_q = lambda issn: process.SJR.get(issn.replace("-", "").upper()) or "unknown"
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "conferences": calls.conferences(c.get("conferences", []), old.get("conferences", []), process.llm_json),
           "special_issues": calls.ieee_special_issues(cfg["crossref"]["journals"] + c.get("extra_journals", []), sjr_q),
           "wikicfp": calls.wikicfp(c.get("wikicfp_categories", []), c.get("wikicfp_keywords", []))}
    print(f"  {len(out['conferences'])} conferences, {len(out['special_issues'])} IEEE special issues, {len(out['wikicfp'])} WikiCFP")
    CALLS.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def main():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    process.QUARTILES = {k.lower(): v for k, v in (cfg.get("journal_quartiles") or {}).items()}
    process.SJR = process.load_sjr(ROOT / "data" / "scimagojr.csv")
    print(f"Journal rankings: {len(process.SJR)} ISSNs from SCImago, {len(process.QUARTILES)} names from sources.yaml")
    store = json.loads(DATA.read_text()) if DATA.exists() else {"items": []}
    old = store["items"]

    print("Collecting...")
    raw, errors = collect_all(cfg)
    new = process.dedup(raw, {i["id"] for i in old}, [i["title"].lower() for i in old])
    print(f"{len(raw)} raw → {len(new)} new")
    new = [i for i in new if process.keyword_pass(i, cfg["keywords"])]
    print(f"{len(new)} pass keyword filter")

    # retry recent items that missed the AI step (quota/timeouts), up to 3 times
    retry = [i for i in old if not i.get("ai_summary") and i.get("ai_tries", 3) < 3]
    old = [i for i in old if i not in retry]
    process.classify(new + retry)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    new = [process.finalize(i) | {"added": now} for i in new]
    retry = [process.finalize(i) for i in retry]
    new = [i for i in new if i["relevant"]]
    old = [i for i in retry if i["relevant"]] + old
    print(f"{len(new)} relevant after AI; {len(retry)} older items retried")

    store = {"updated": now, "failed_sources": errors,
             "items": sorted(new + old, key=lambda x: x["added"], reverse=True)[:MAX_ITEMS]}
    DATA.write_text(json.dumps(store, ensure_ascii=False, indent=1))

    for i in store["items"]:  # keep quartiles current if the ranking file changes
        i["tier"] = process.journal_tier(i)
    DATA.write_text(json.dumps(store, ensure_ascii=False, indent=1))

    cfps = update_calls(cfg)

    telegram.urgent([i for i in new if i["importance"] >= 5])
    if "--digest" in sys.argv:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
        telegram.digest([i for i in store["items"] if i["added"] >= cutoff], os.environ.get("SITE_URL"))
        telegram.deadlines(cfps, os.environ.get("SITE_URL"))


if __name__ == "__main__":
    main()
