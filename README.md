# Apollo Security Telegram Monitor

This project logs into the Apollo Security site, checks the open-events flow, and sends Telegram notifications when the visible event list changes.

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in your Telegram bot token and chat ID.
3. Install dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run once

```bash
python apollo_monitor.py --once
```

## Run continuously every hour

```bash
python apollo_monitor.py
```

## Notes

- The monitor intentionally treats `/onboarding` as a blocker state and will not try to force the app past it.
- The site can redirect users into an onboarding questionnaire, so the bot waits for a valid event page before sending alerts.
- The event detection is based on comparing page text snapshots between polls, which is robust when the site structure changes a little. The event filter keeps every event containing `סדרן ללא תעודה`, regardless of its location.
- Each eligible shift is parsed separately at the `הרשמה / הגש מועמדות` boundary, so one Telegram message contains one shift and its time/place fields do not include button text.
- A persistent `sent_events` journal is stored with the monitor state. It records sent event identities and their last reported time, preventing repeat alerts while still allowing a one-time time-change update.
- `/start` displays Telegram buttons for status, an immediate scan, a test alert, and sending the latest browser screenshot.
- Browser screenshots are saved to `latest_browser.png` after each important page transition and can be requested from the bot.
- If the site opens the onboarding questionnaire instead of the login flow, the monitor closes the browser and retries indefinitely with a fresh browser instance. The delay is controlled by `RETRY_DELAY_SECONDS`.
- Text commands `/status`, `/scan`, `/screenshot`, and `/test` are available in addition to the buttons.
- The `🔎 בדיקת משמרת חדשה` button and `/new_shift` command run an immediate scan without changing the hourly schedule. A scan lock prevents a manual scan and the hourly scan from running simultaneously.
- Event deduplication uses title and date. Changes to available seats are deliberately ignored.
- Events are identified by title and date. If the time of an existing event changes, the bot sends an update containing both the previous and new times.
- The browser User-Agent is configurable with `USER_AGENT` and defaults to a current Chrome-like desktop User-Agent.
- The login flow verifies that both fields contain values and clicks the exact `button[type="submit"]` login button, avoiding the similarly named navigation tab. A live check confirmed successful navigation to `/dashboard`; the site then redirected to its onboarding questionnaire.
- The browser now uses a persistent Chromium profile. Cookies, local storage, and session data are kept in `BROWSER_PROFILE_DIR`, so normal scans reuse the login session instead of filling credentials every hour.
- The recovery flow opens `DASHBOARD_URL` first and clicks the visible `אירועים פתוחים להרשמה` link in the Dashboard. It does not navigate directly to `/open-events`. If the onboarding questionnaire appears it closes the persistent browser context and reopens the same cached profile before trying again.

## GitHub Actions

The repository includes `.github/workflows/apollo-monitor.yml` for a scheduled one-shot scan. Add these repository secrets under **Settings → Secrets and variables → Actions**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SITE_USERNAME`, and `SITE_PASSWORD`. The workflow installs Chromium, runs one scan, and preserves `.apollo_state.json` plus the latest screenshot through the Actions cache so event notifications are not duplicated between runs.

GitHub Actions is suitable for the hourly scan, but it is not a continuously running host. The Telegram listener required for `/start`, buttons, `/status`, `/scan`, and `/screenshot` is therefore not kept alive between scheduled runs. To use those interactive controls continuously, run the same project on an always-on Python service or virtual machine.
