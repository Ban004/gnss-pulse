"""Dedup, keyword filter, Gemini classification."""
import hashlib
import json
import os
import re
import time
import requests
from rapidfuzz import fuzz

CATEGORIES = ["paper", "conference", "book_chapter", "preprint", "thesis", "job", "internship",
              "phd", "postdoc", "product", "news", "event", "cfp", "dataset", "software", "other"]


def item_id(it):
    key = it.get("doi") or re.sub(r"\W+", "", (it["title"] or "").lower())
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def dedup(new, existing_ids, existing_titles):
    out, seen = [], set(existing_ids)
    titles = list(existing_titles)
    for it in new:
        if not it["title"]:
            continue
        it["id"] = item_id(it)
        if it["id"] in seen or any(fuzz.ratio(it["title"].lower(), t) > 92 for t in titles[-3000:]):
            continue
        seen.add(it["id"])
        titles.append(it["title"].lower())
        out.append(it)
    return out


def keyword_pass(it, kw):
    text = f"{it['title']} {it['summary']}".lower()
    if any(x in text for x in kw.get("exclude", [])):
        return False
    if any(x in text for x in kw.get("strong", [])):
        return True
    weak = sum(1 for x in kw.get("weak", []) if re.search(rf"\b{re.escape(x)}", text))
    ctx = sum(1 for x in kw.get("context", []) if x in text)
    return weak >= 1 and ctx >= 1 and weak + ctx >= 3


PROMPT = """You classify items for a GNSS (satellite navigation) researcher's news feed.
For each item return a JSON object with:
 "i": the item index,
 "relevant": true only if it is genuinely about GNSS / satellite navigation / PNT,
 "category": one of %s,
 "tier": one of "Q1","Q2","Q3","Q4","conference","book","preprint","unknown" (journal quality guess for papers, else "unknown"),
 "summary": a plain 2-line summary (max 40 words),
 "importance": 1-5 (5 = must-see: major result, open position matching GNSS, big launch/product),
 "deadline": "YYYY-MM-DD" or null,
 "location": country/city or null.
Return ONLY a JSON array.

Items:
%s"""


def gemini_classify(items, batch=10):
    key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    if not key:
        print("  No GEMINI_API_KEY: keyword-only mode")
        return False
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        listing = "\n".join(
            f"[{i}] ({it['kind_hint']}, {it.get('venue') or it['source']}) {it['title']} :: {it['summary'][:600]}"
            for i, it in enumerate(chunk))
        body = {"contents": [{"parts": [{"text": PROMPT % (CATEGORIES, listing)}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
        for attempt in range(3):
            try:
                r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
                if r.status_code == 429:
                    print("  Gemini quota hit; rest stay keyword-only")
                    return True
                r.raise_for_status()
                results = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                for res in results:
                    if 0 <= int(res.get("i", -1)) < len(chunk):
                        chunk[int(res["i"])]["ai"] = res
                break
            except Exception as ex:  # key is in a header, so it never appears in this message
                print(f"  Gemini batch {start // batch} attempt {attempt + 1} failed: {type(ex).__name__} {getattr(ex, 'response', None) and ex.response.status_code}")
                time.sleep(15 * (attempt + 1))
        time.sleep(4)  # stay under free-tier rate limit
    return True


TYPE_MAP = {"article": "paper", "review": "paper", "letter": "paper", "conference-paper": "conference",
            "conference-abstract": "conference", "proceedings-article": "conference", "book-chapter": "book_chapter",
            "book": "book_chapter", "dissertation": "thesis", "posted-content": "preprint"}


def finalize(it):
    it["kind_hint"] = TYPE_MAP.get(it["kind_hint"], it["kind_hint"])
    ai = it.pop("ai", None)
    if ai:
        it.update(relevant=bool(ai.get("relevant")), category=ai.get("category") if ai.get("category") in CATEGORIES else it["kind_hint"],
                  tier=ai.get("tier", "unknown"), ai_summary=ai.get("summary"),
                  importance=int(ai.get("importance") or 2), deadline=ai.get("deadline"),
                  location=ai.get("location"))
    else:
        it.update(relevant=True, category=it["kind_hint"], tier="unknown", ai_summary=None,
                  importance=2, deadline=None, location=None)
    return it
