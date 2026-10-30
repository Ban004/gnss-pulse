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
