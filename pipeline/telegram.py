import os
import requests

ICON = {"job": "💼", "internship": "🎓", "phd": "🎓", "postdoc": "🔬", "paper": "📄", "preprint": "📝",
        "conference": "🎤", "book_chapter": "📚", "product": "🛰️", "news": "📰", "cfp": "📢", "event": "📅"}


def _chat_id(token):
    """Use TELEGRAM_CHAT_ID if set, else the latest chat that messaged the bot."""
    if chat := os.environ.get("TELEGRAM_CHAT_ID"):
        return chat
    try:
        updates = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()["result"]
        chat = next(u["message"]["chat"]["id"] for u in reversed(updates) if "message" in u)
        os.environ["TELEGRAM_CHAT_ID"] = str(chat)
        print(f"  Telegram chat id detected: {chat}")
        return chat
    except Exception:
        print("  Telegram: no chat found. Send any message to your bot first.")


def send(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = token and _chat_id(token)
    if not (token and chat):
        print("  Telegram not configured; skipping")
        return
    for i in range(0, len(text), 4000):
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json={
            "chat_id": chat, "text": text[i:i + 4000], "parse_mode": "HTML",
            "disable_web_page_preview": True})


def fmt(it):
    from html import escape
    s = it.get("ai_summary") or ""
    return (f"{ICON.get(it['category'], '•')} <a href=\"{escape(it['url'] or '')}\">{escape(it['title'][:200])}</a>"
            f"\n<i>{escape(it.get('venue') or it['source'])}</i>" + (f"\n{escape(s)}" if s else ""))


def digest(items, site_url=None):
    if not items:
        send("🛰️ GNSS Pulse: no new items in the last 24h.")
        return
    items = sorted(items, key=lambda x: -x["importance"])
    jobs = [i for i in items if i["category"] in ("job", "internship", "phd", "postdoc")]
    papers = [i for i in items if i not in jobs]
    parts = [f"🛰️ <b>GNSS Pulse daily digest</b>: {len(items)} new items"]
    if jobs:
        parts += ["\n<b>💼 Positions</b>"] + [fmt(i) for i in jobs[:15]]
    if papers:
        parts += ["\n<b>📄 Papers & news</b>"] + [fmt(i) for i in papers[:25]]
    if site_url:
        parts.append(f"\nSee all: {site_url}")
    send("\n\n".join(parts))


def urgent(items):
    for it in items:
        send("🚨 <b>High priority</b>\n" + fmt(it))


def deadlines(cfps, site_url=None, days=14):
    """Remind about conference deadlines in the next `days` days."""
    import datetime as dt
    from html import escape
    today, soon = dt.date.today().isoformat(), (dt.date.today() + dt.timedelta(days=days)).isoformat()
    rows = []
    for c in cfps.get("conferences", []):
        for key, label in (("submission_opens", "submissions open"), ("abstract_deadline", "abstract deadline"),
                           ("full_paper_deadline", "full-paper deadline"), ("camera_ready", "camera-ready")):
            d = c.get(key)
            if d and today <= d <= soon:
                rows.append((d, f"⏰ <b>{escape(d)}</b>: <a href=\"{escape(c['url'])}\">{escape(c.get('edition') or c['name'])}</a> {label}"))
    if rows:
        send("📢 <b>Upcoming deadlines (next 14 days)</b>\n\n" + "\n".join(r for _, r in sorted(rows))
             + (f"\n\nAll calls: {site_url}#calls" if site_url else ""))
