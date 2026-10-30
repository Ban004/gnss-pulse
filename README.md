# 🛰️ GNSS Pulse

Free daily GNSS aggregator covering papers, jobs and news. It runs on GitHub Actions, is served on GitHub Pages, and sends alerts through Telegram.

## Setup (one time)
1. **Gemini key:** sign in at https://aistudio.google.com, then click *Get API key*.
2. **Telegram bot:** message @BotFather, send `/newbot`, and copy the token. Send any message to your bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}`.
3. **GitHub:** create a public repo named `gnss-pulse`, then:
   ```
   git remote add origin https://github.com/<you>/gnss-pulse.git
   git push -u origin main
   ```
4. Repo **Settings → Secrets and variables → Actions**: add `GEMINI_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
5. Repo **Settings → Pages → Source: GitHub Actions**.
6. **Actions → Collect GNSS updates → Run workflow** (tick *digest* to test Telegram).

The app is served at `https://<you>.github.io/gnss-pulse/`. On your phone, open it and choose *Add to Home screen*.

## Schedule
Collects 4×/day; sends the daily digest at 08:00 IST. Importance-5 items are sent instantly.

## Customize
Edit `sources.yaml` (keywords, queries, RSS feeds). No code changes needed.

## Run locally
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
GEMINI_API_KEY=... .venv/bin/python main.py      # add --digest to send Telegram
cd web && python3 -m http.server   # open http://localhost:8000
```
