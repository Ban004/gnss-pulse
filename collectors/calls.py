"""Calls for papers: conference deadlines (AI-extracted from official pages) and journal special issues."""
import datetime as dt
import hashlib
import re
import feedparser
import requests
from bs4 import BeautifulSoup
from collectors.sources import UA, TIMEOUT, _crossref_get

MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"

CONF_PROMPT = """From this official conference web page, extract the details of the NEXT upcoming edition.
Use ONLY dates written on the page; never guess. Dates as YYYY-MM-DD (null if not stated).
Return ONE JSON object in a JSON array: [{
 "edition": "e.g. ION GNSS+ 2027",
 "start": date, "end": date, "location": "City, Country" or null,
 "submission_opens": date, "abstract_deadline": date, "full_paper_deadline": date,
 "notification": date, "camera_ready": date,
 "submission_type": "abstract" | "full paper" | "both" | null,
 "publication": "where accepted papers appear (e.g. IEEE Xplore, ION proceedings)" or null,
 "notes": "one short line with anything else important (fees, tracks, student awards)" or null
}]

Today is %s. Page (%s):
%s"""


def page_text(url):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    head = "\n".join(lines)[:2500]
    dated = [l for l in lines if re.search(MONTHS, l, re.I) and re.search(r"\b20\d\d\b|\b\d{1,2}\b", l)]
    return (head + "\n...\n" + "\n".join(dated))[:9000]


def conferences(cfg, previous, llm_json):
    """Re-extract a conference only when its page text changed (saves free LLM quota)."""
    prev = {c["short"]: c for c in previous}
    out = []
    for c in cfg:
        entry = {k: c.get(k) for k in ("name", "short", "url", "rank", "area")}
        old = prev.get(c["short"], {})
        texts = []
        for u in c.get("pages") or [c["url"]]:
            try:
                texts.append(page_text(u))
            except Exception as ex:
                print(f"  {c['short']}: page failed ({type(ex).__name__})")
        text = "\n\n".join(texts)
        digest = hashlib.sha1(text.encode()).hexdigest()[:12] if text else None
        if text and digest != old.get("page_hash"):
            res = llm_json(CONF_PROMPT % (dt.date.today().isoformat(), c["url"], text))
            if res:
                entry |= res[0] | {"page_hash": digest, "checked": dt.date.today().isoformat()}
                print(f"  {c['short']}: extracted {entry.get('edition')}")
            else:
                entry |= {k: v for k, v in old.items() if k not in entry or entry[k] is None}
        else:
            entry |= {k: v for k, v in old.items() if k not in entry or entry[k] is None}
        out.append(entry)
    return out


GNSS_TOPIC = (r"gnss|gps|galileo|beidou|navic|satellite navigation|navigation|positioning|localization|localisation|pnt|"
              r"timing|geodes|ionospher|space weather|reflectometry|spoofing|jamming|interference|integrity|inertial|leo")


def ieee_special_issues(journals, quartile_of, days=365):
    """IEEE publishes each 'Call for Papers: Special Issue on …' as a DOI document; find them via Crossref."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    for j in journals:
        try:
            r = _crossref_get(f"https://api.crossref.org/journals/{j['issn']}/works", {
                "filter": f"from-created-date:{since}", "query.bibliographic": "call for papers special issue section",
                "rows": 20, "select": "DOI,title,created,container-title"})
        except Exception as ex:
            print(f"  {j['name']}: CFP lookup failed ({type(ex).__name__})")
            continue
        for w in r.json()["message"]["items"]:
            title = (w.get("title") or [""])[0]
            # only open calls ("Guest Editorial…" = already published issue), and only GNSS-type topics
            if not re.search(r"call for (papers|submissions|contributions)", title, re.I) or re.search(r"conference|symposium|meeting|workshop", title, re.I):
                continue
            if not re.search(GNSS_TOPIC, title, re.I):
                continue
            q = quartile_of(j["issn"])
            if q not in ("Q1", "Q2"):
                continue
            out.append({"title": re.sub(r"\s+", " ", title), "journal": j["name"], "tier": q,
                        "url": f"https://doi.org/{w['DOI']}", "posted": w["created"]["date-time"][:10],
                        "deadline": None, "source": "IEEE (Crossref)"})
    return out


def wikicfp(categories, keywords):
    """WikiCFP feeds: keep upcoming items that mention a GNSS term."""
    out, today = [], dt.date.today()
    for cat in categories:
        try:
            feed = feedparser.parse(requests.get(f"http://www.wikicfp.com/cfp/rss?cat={cat}", headers=UA, timeout=TIMEOUT).content)
        except Exception as ex:
            print(f"  WikiCFP {cat}: failed ({type(ex).__name__})")
            continue
        for e in feed.entries:
            text = f"{e.title} {e.get('summary', '')}"
            dates = re.findall(r"\[([A-Z][a-z]{2} \d{1,2}, 20\d\d)", text)
            parsed = [dt.datetime.strptime(d, "%b %d, %Y").date() for d in dates]
            if parsed and max(parsed) < today:
                continue  # past event
            years = [int(y) for y in re.findall(r"\b(20\d\d)\b", e.title)]
            if years and max(years) < today.year:
                continue  # old edition (e.g. "Special Issue MDPI Sensors 2019")
            if not any(k in text.lower() for k in keywords):
                continue
            kind = "journal" if re.search(r"special issue|journal|transactions", text, re.I) else "conference"
            out.append({"title": e.title, "summary": re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:300],
                        "url": e.link, "kind": kind, "when": dates[0] if dates else None, "source": "WikiCFP"})
    return list({i["url"]: i for i in out}.values())


def my_calls(entries, quartile_of):
    """Calls you add by hand in sources.yaml (calls.my_calls). Hidden after their deadline."""
    today = dt.date.today().isoformat()
    out = []
    for e in entries:
        deadline = str(e.get("deadline") or "") or None
        if deadline and deadline < today:
            continue
        out.append({"title": e["title"], "journal": e["journal"], "tier": quartile_of(str(e.get("issn", ""))) if e.get("issn") else e.get("tier"),
                    "url": e.get("url"), "deadline": deadline, "guest_editors": e.get("guest_editors"),
                    "posted": None, "source": "Added by you"})
    return out
