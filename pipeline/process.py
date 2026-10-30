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
    if it.get("trusted"):
        return True
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


class QuotaExhausted(Exception):
    pass


def _gemini(prompt, key):
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": key}, timeout=180, json={
                          "contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}})
    return r, lambda j: j["candidates"][0]["content"]["parts"][0]["text"]


def _openai_compatible(url, model):
    def call(prompt, key):
        r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=180, json={
            "model": model, "temperature": 0.1, "messages": [{"role": "user", "content": prompt}]})
        return r, lambda j: j["choices"][0]["message"]["content"]
    return call


# Tried in order; a provider is skipped if its key is missing, and dropped once its quota runs out.
PROVIDERS = [
    ("Gemini", "GEMINI_API_KEY", _gemini),
    ("GitHub Models", "GITHUB_TOKEN", _openai_compatible(
        "https://models.github.ai/inference/chat/completions", os.environ.get("GITHUB_MODEL", "openai/gpt-4.1-mini"))),
    ("Groq", "GROQ_API_KEY", _openai_compatible(
        "https://api.groq.com/openai/v1/chat/completions", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))),
    ("OpenRouter", "OPENROUTER_API_KEY", _openai_compatible(
        "https://openrouter.ai/api/v1/chat/completions", os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free"))),
]


def _ask(name, fn, key, prompt):
    for attempt in range(3):
        try:
            r, extract = fn(prompt, key)
            if r.status_code == 429:
                if attempt == 2 or "quota" in r.text.lower() and "day" in r.text.lower():
                    raise QuotaExhausted(r.text[:200])
                print(f"  {name} rate limit; waiting 60s")
                time.sleep(60)
                continue
            if r.status_code in (401, 403):
                raise QuotaExhausted(f"auth {r.status_code}")
            r.raise_for_status()
            text = extract(r.json())
            return json.loads(text[text.index("["):text.rindex("]") + 1])  # tolerate ```json fences
        except QuotaExhausted:
            raise
        except Exception as ex:  # keys are in headers, so they never appear in this message
            print(f"  {name} attempt {attempt + 1} failed: {type(ex).__name__}")
            time.sleep(15 * (attempt + 1))
    raise QuotaExhausted("3 failed attempts")


def classify(items, batch=10):
    """Fill item["ai"] using the first provider that still has quota."""
    providers = [(n, fn, os.environ[k]) for n, k, fn in PROVIDERS if os.environ.get(k)]
    if not providers:
        print("  No LLM keys: keyword-only mode")
        return
    print("  LLM chain: " + " → ".join(n for n, _, _ in providers))
    used = {}
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        listing = "\n".join(
            f"[{i}] ({it['kind_hint']}, {it.get('venue') or it['source']}) {it['title']} :: {it['summary'][:600]}"
            for i, it in enumerate(chunk))
        prompt = PROMPT % (CATEGORIES, listing)
        while providers:
            name, fn, key = providers[0]
            try:
                results = _ask(name, fn, key, prompt)
                break
            except QuotaExhausted as ex:
                print(f"  {name} out of quota/unauthorized ({ex}); switching provider")
                providers.pop(0)
        else:
            print("  All LLM providers exhausted; remaining items retried next run")
            break
        for res in results or []:
            if isinstance(res, dict) and 0 <= int(res.get("i", -1)) < len(chunk):
                chunk[int(res["i"])]["ai"] = res | {"by": name}
        used[name] = used.get(name, 0) + 1
        time.sleep(7)  # stay under free-tier per-minute limits
    print(f"  LLM batches: {used}")


TYPE_MAP = {"article": "paper", "review": "paper", "letter": "paper", "conference-paper": "conference",
            "conference-abstract": "conference", "proceedings-article": "conference", "book-chapter": "book_chapter",
            "book": "book_chapter", "dissertation": "thesis", "posted-content": "preprint"}


def finalize(it):
    it["kind_hint"] = TYPE_MAP.get(it["kind_hint"], it["kind_hint"])
    it.pop("trusted", None)
    ai = it.pop("ai", None)
    if not ai and it.get("ai_tries") is not None:  # retried old item, still no AI: keep as is
        it["ai_tries"] += 1
        return it
    if ai:
        it.update(relevant=bool(ai.get("relevant")), category=ai.get("category") if ai.get("category") in CATEGORIES else it["kind_hint"],
                  tier=ai.get("tier", "unknown"), ai_summary=ai.get("summary"),
                  importance=int(ai.get("importance") or 2), deadline=ai.get("deadline"),
                  location=ai.get("location"))
        it.pop("ai_tries", None)
    else:
        it.update(ai_tries=1, relevant=True, category=it["kind_hint"], tier="unknown", ai_summary=None,
                  importance=2, deadline=None, location=None)
    return it
