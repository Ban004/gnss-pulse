"""Fetch raw items from free APIs and RSS feeds. Each returns a list of dicts:
{title, url, summary, source, date, kind_hint, doi, venue}"""
import datetime as dt
import re
import feedparser
import requests

UA = {"User-Agent": "GNSS-Pulse/0.1 (personal research aggregator; mailto:none@example.com)"}
TIMEOUT = 30


def _clean(text):
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _item(title, url, summary, source, date, kind_hint, doi=None, venue=None):
    return {"title": _clean(title), "url": url, "summary": _clean(summary)[:1500],
            "source": source, "date": date, "kind_hint": kind_hint,
            "doi": (doi or "").lower().replace("https://doi.org/", "") or None, "venue": venue}


def openalex(query, since):
    r = requests.get("https://api.openalex.org/works", headers=UA, timeout=TIMEOUT, params={
        "search": query, "filter": f"from_publication_date:{since}", "per-page": 50,
        "sort": "publication_date:desc"})
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        abstract = ""
        if inv := w.get("abstract_inverted_index"):
            pos = {p: word for word, ps in inv.items() for p in ps}
            abstract = " ".join(pos[i] for i in sorted(pos))
        src = (w.get("primary_location") or {}).get("source") or {}
        out.append(_item(w.get("title"), w.get("doi") or w.get("id"), abstract, "OpenAlex",
                         w.get("publication_date"), w.get("type") or "paper",
                         w.get("doi"), src.get("display_name")))
    return out


def arxiv(query, since):
    feed = feedparser.parse(requests.get("http://export.arxiv.org/api/query", headers=UA, timeout=TIMEOUT,
        params={"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending",
                "max_results": 50}).text)
    return [_item(e.title, e.link, e.summary, "arXiv", e.published[:10], "preprint", venue="arXiv")
            for e in feed.entries if e.published[:10] >= since]


def rss(name, url, kind_hint, since):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        date = dt.date(*t[:3]).isoformat() if t else dt.date.today().isoformat()
        if date >= since:
            out.append(_item(e.get("title"), e.get("link"), e.get("summary", ""), name, date,
                             kind_hint, e.get("prism_doi") or e.get("dc_identifier"), name))
    return out


def collect_all(cfg):
    since = (dt.date.today() - dt.timedelta(days=cfg.get("lookback_days", 3))).isoformat()
    jobs = [(f"OpenAlex '{q}'", openalex, (q, since)) for q in cfg.get("openalex_queries", [])]
    jobs += [(f"arXiv '{q}'", arxiv, (q, since)) for q in cfg.get("arxiv_queries", [])]
    for kind, feeds in cfg.get("rss", {}).items():
        jobs += [(f["name"], rss, (f["name"], f["url"], {"papers": "paper", "jobs": "job", "news": "news"}.get(kind, kind), since)) for f in feeds]
    items, errors = [], []
    for label, fn, args in jobs:
        try:
            got = fn(*args)
            print(f"  {label}: {len(got)}")
            items += got
        except Exception as ex:  # one bad source must not stop the run
            print(f"  {label}: FAILED ({ex})")
            errors.append(label)
    return items, errors
