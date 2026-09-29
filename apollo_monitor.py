from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

from playwright.sync_api import sync_playwright
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler


load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SITE_USERNAME = os.getenv("SITE_USERNAME", "")
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))
HEARTBEAT_MINUTES = int(os.getenv("HEARTBEAT_MINUTES", "5"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
RETRY_DELAY_SECONDS = int(os.getenv("RETRY_DELAY_SECONDS", "5"))
TELEGRAM_MESSAGE_DELAY_SECONDS = float(os.getenv("TELEGRAM_MESSAGE_DELAY_SECONDS", "2"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
APOLLO_URL = "https://apollo-security.co.il"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", f"{APOLLO_URL}/dashboard")
EVENTS_URL = os.getenv("EVENTS_URL", "https://rosh-shalom-system.lovable.app")
BROWSER_PROFILE_DIR = Path(os.getenv("BROWSER_PROFILE_DIR", str(Path(__file__).with_name(".browser-profile"))))
STATE_FILE = Path(os.getenv("STATE_FILE", str(Path(__file__).with_name(".apollo_state.json"))))
SCREENSHOT_FILE = Path(os.getenv("SCREENSHOT_FILE", str(Path(__file__).with_name("latest_browser.png"))))
SCAN_LOCK = threading.Lock()
_startup_success_logged = False
_startup_baseline_captured = False


def console_log(message: str) -> None:
    """Print a short status message immediately to the terminal."""
    print(message, flush=True)


def log_first_success() -> None:
    global _startup_success_logged
    if not _startup_success_logged:
        console_log("✅ ההתחברות הצליחה — הבוט רץ!")
        _startup_success_logged = True


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Telegram not configured. Message would be: {text}")
        return

    async def _send() -> None:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=text, disable_web_page_preview=True)

    asyncio.run(_send())


async def handle_start(update: Any, context: Any) -> None:
    await update.message.reply_text(
        "🤖 *Apollo Security Monitor*\n\n"
        "בוט ששולח התראות בזמן אמת על אירועים חדשים באתר Apollo Security.\n"
        "פותח והועלה על ידי *אלון נושם*.\n\n"
        "🎯 הבוט מיועד אך ורק לאירועי *סדרן ללא תעודה* — כי אנחנו פשוטי עם.\n\n"
        "בחר פעולה מהתפריט 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def handle_status(update: Any, context: Any) -> None:
    state = load_previous_state()
    url = state.get("snapshot", {}).get("url", "לא ידוע")
    event_count = len(state.get("snapshot", {}).get("events", []))
    await update.message.reply_text(
        "📡 מצב הבוט\n"
        f"URL אחרון: {url}\n"
        f"אירועים נוכחיים: {event_count}\n"
        "הסריקה מתבצעת כל שעה (או לפי התצורה).",
        reply_markup=main_keyboard(),
    )


async def handle_test(update: Any, context: Any) -> None:
    await update.message.reply_text("🧪 שולח בדיקת הודעה…")
    await asyncio.to_thread(send_test_alert)


async def handle_scan(update: Any, context: Any) -> None:
    await update.message.reply_text("🔄 מתחיל סריקה עכשיו…")
    await asyncio.to_thread(run_scan_locked)
    await update.message.reply_text("✅ הסריקה הסתיימה.", reply_markup=main_keyboard())


async def handle_new_shift_scan(update: Any, context: Any) -> None:
    await update.message.reply_text("🔎 בודק עכשיו אם נוספה משמרת חדשה…")
    result = await asyncio.to_thread(run_scan_locked, False)
    await update.message.reply_text(format_new_shift_result(result), reply_markup=main_keyboard())


async def handle_screenshot(update: Any, context: Any) -> None:
    await send_latest_screenshot(update, context)


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 רשימת האירועים", callback_data="list_events")],
        [
            InlineKeyboardButton("🔎 בדיקת משמרת חדשה", callback_data="new_shift_scan"),
        ],
        [
            InlineKeyboardButton("📖 מדריך קצר", callback_data="guide"),
        ],
    ])


async def send_latest_screenshot(update: Any, context: Any) -> None:
    target = update.effective_chat.id if update.effective_chat else CHAT_ID
    if not SCREENSHOT_FILE.exists():
        await context.bot.send_message(
            chat_id=target,
            text="📸 עדיין אין צילום מסך. הפעל סריקה אחת לפחות ואז נסה שוב.",
            reply_markup=main_keyboard(),
        )
        return

    with SCREENSHOT_FILE.open("rb") as image:
        await context.bot.send_photo(
            chat_id=target,
            photo=image,
            caption="📸 צילום המסך האחרון של הדפדפן",
            reply_markup=main_keyboard(),
        )


async def handle_button(update: Update, context: Any) -> None:
    query = update.callback_query
    if query is None or not isinstance(query.message, Message):
        return
    await query.answer()
    message = query.message

    if query.data == "status":
        state = load_previous_state()
        snapshot = state.get("snapshot", {})
        await message.reply_text(
            "📡 מצב הבוט\n"
            f"URL אחרון: {snapshot.get('url', 'לא ידוע')}\n"
            f"אירועים נוכחיים: {len(snapshot.get('events', []))}\n"
            "הסריקה מתבצעת כל שעה (או לפי התצורה).",
            reply_markup=main_keyboard(),
        )
    elif query.data == "screenshot":
        await send_latest_screenshot(update, context)
    elif query.data == "list_events":
        result = await asyncio.to_thread(run_scan_locked, False)
        events = result.get("events", [])
        messages = format_event_list_messages(events)
        for index, text in enumerate(messages):
            await message.reply_text(
                text,
                reply_markup=main_keyboard() if index == len(messages) - 1 else None,
            )
    elif query.data == "guide":
        await message.reply_text(
            "📖 *מדריך קצר*\n\n"
            "🔎 *בדיקת משמרת חדשה*\n"
            "מבצע סריקה חדשה באתר ובודק אם נוספו אירועים חדשים.\n\n"
            "📋 *רשימת האירועים*\n"
            "מבצע סריקה חדשה ושולח את כל האירועים העדכניים של סדרן ללא תעודה.\n\n"
            "⏱️ הבוט מבצע סריקות אוטומטיות ושולח התראה רק על אירועים חדשים.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
    elif query.data == "scan":
        await message.reply_text("🔄 מתחיל סריקה עכשיו…")
        await asyncio.to_thread(run_scan_locked)
        await message.reply_text("✅ הסריקה הסתיימה.", reply_markup=main_keyboard())
    elif query.data == "new_shift_scan":
        await message.reply_text("🔎 בודק עכשיו אם נוספה משמרת חדשה…")
        result = await asyncio.to_thread(run_scan_locked, False)
        await message.reply_text(format_new_shift_result(result), reply_markup=main_keyboard())
    elif query.data == "test":
        await message.reply_text("🧪 שולח בדיקת הודעה…")
        await asyncio.to_thread(send_test_alert)


def run_telegram_listener() -> None:
    if not BOT_TOKEN:
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("test", handle_test))
    app.add_handler(CommandHandler("scan", handle_scan))
    app.add_handler(CommandHandler("new_shift", handle_new_shift_scan))
    app.add_handler(CommandHandler("screenshot", handle_screenshot))
    app.add_handler(CallbackQueryHandler(handle_button))

    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        loop.close()


def load_previous_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(data: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


EVENT_TITLE_VENUES = (
    "איצטדיון טדי",
    "אצטדיון בלומפילד",
    "בלומפילד",
    "בריכת הסולטן",
    "גבעת התחמושת",
    "היכל הפיס ארנה",
    "שער יפו",
    "פארק הירקון",
)


def clean_event_title(title: str) -> str:
    """Remove venue names that the website repeats in the event title."""
    cleaned = title
    venue_removed = False
    for venue in EVENT_TITLE_VENUES:
        venue_pattern = r"\s+".join(re.escape(part) for part in venue.split())
        updated = re.sub(rf"(?:\s+|[-–—:|]\s*)ב?{venue_pattern}", "", cleaned)
        venue_removed = venue_removed or updated != cleaned
        cleaned = updated
    # ירושלים is also part of legitimate team names such as ביתר ירושלים.
    if venue_removed:
        cleaned = re.sub(r"(?:\s+|[-–—:|]\s*)ב?ירושלים(?=\s*$|\s*[-–—:|,])", "", cleaned)
    return normalize_space(cleaned).strip(" -–—|:,") or "אירוע ללא שם"


def extract_relevant_open_events(page: Any) -> list[dict[str, Any]]:
    try:
        cards = page.locator("div.rounded-lg.border.bg-card.overflow-visible")
        card_count = cards.count()
    except Exception:
        return []

    matches: list[dict[str, Any]] = []
    for i in range(card_count):
        matches.extend(parse_event_card_text(cards.nth(i).inner_text()))

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in matches:
        key = f"{item['title']}|{item['date']}|{item['place']}|{item['time']}"
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:10]


def parse_event_card_text(card_text: str) -> list[dict[str, Any]]:
    """Parse each eligible shift in a card, without leaking CTA text into fields."""
    text = normalize_space(card_text)
    if "סדרן ללא תעודה" not in text or "מקומות פנויים" not in text:
        return []

    date_match = re.search(r"יום\s*[\u05D0-\u05EA\s,]+\d{2}\.\d{2}\.\d{4}", text)
    date_value = date_match.group(0).strip() if date_match else "תאריך לא ידוע"
    chunks = re.split(r"\s+הרשמה\s*/\s*הגש מועמדות\s*", text)
    matches: list[dict[str, Any]] = []

    for chunk in chunks:
        role_match = re.search(r"סדרן ללא תעודה", chunk)
        if not role_match:
            continue
        seats_match = re.search(r"מקומות פנויים:\s*(\d+)", chunk)
        time_match = re.search(r"שעת התייצבות:\s*(.*?)\s+מקומות פנויים:", chunk)
        place_match = re.search(r"סדרן ללא תעודה\s+(.*?)\s+ניווט\s+שעת התייצבות:", chunk)
        if not seats_match or not time_match or not place_match:
            continue

        title = chunk[:role_match.start()]
        title = re.sub(r"יום\s*[\u05D0-\u05EA\s,]+\d{2}\.\d{2}\.\d{4}", "", title)
        title = clean_event_title(title)
        matches.append({
            "title": title,
            "date": date_value,
            "place": normalize_space(place_match.group(1)),
            "time": normalize_space(time_match.group(1)),
            "seats": int(seats_match.group(1)),
            "raw": normalize_space(chunk),
        })
    return matches


def page_snapshot(page: Any) -> dict[str, Any]:
    try:
        url = page.url
    except Exception:
        url = "unknown"

    try:
        title = page.locator("h1, h2, h3").first.inner_text(timeout=5000)
    except Exception:
        title = ""

    try:
        body_text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        body_text = ""

    return {
        "url": url,
        "title": title,
        "body_text": normalize_space(body_text)[:4000],
        "events": extract_relevant_open_events(page),
    }


def save_browser_screenshot(page: Any) -> None:
    try:
        page.screenshot(path=str(SCREENSHOT_FILE), full_page=True)
    except Exception as exc:
        print(f"Could not save browser screenshot: {exc}")


def open_events_from_dashboard(page: Any) -> dict[str, Any]:
    """Open events by clicking the Dashboard link, not by direct URL navigation."""
    event_link = page.get_by_role("link", name="אירועים פתוחים להרשמה", exact=True)
    if event_link.count() == 0:
        event_link = page.locator('a[href="/open-events"]').first
    if event_link.count() == 0:
        raise RuntimeError("Dashboard link 'אירועים פתוחים להרשמה' was not found")
    event_link.click()
    page.wait_for_timeout(3000)
    return page_snapshot(page)


def login_and_capture() -> dict[str, Any]:
    if not SITE_USERNAME or not SITE_PASSWORD:
        raise RuntimeError("SITE_USERNAME and SITE_PASSWORD must be set in .env")

    with sync_playwright() as p:
        last_snapshot: dict[str, Any] = {"url": APOLLO_URL, "title": "", "body_text": "", "events": []}

        attempt = 0
        while True:
            attempt += 1
            console_log(f"🌐 מתחבר לאתר ההתחברות… (ניסיון {attempt})")
            browser = None
            try:
                BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    headless=HEADLESS,
                    locale="he-IL",
                    user_agent=USER_AGENT,
                )
                page = browser.pages[0] if browser.pages else browser.new_page()

                # First use the cached session and open the Dashboard. Events
                # are opened by clicking the Dashboard link below.
                page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                snapshot = page_snapshot(page)
                last_snapshot = snapshot
                save_browser_screenshot(page)
                state_name = classify_state(snapshot)
                if state_name == "event_page":
                    log_first_success()
                    return snapshot
                if state_name == "dashboard":
                    state = open_events_from_dashboard(page)
                    last_snapshot = state
                    save_browser_screenshot(page)
                    if classify_state(state) == "event_page":
                        log_first_success()
                        return state
                    if classify_state(state) == "onboarding":
                        print(f"Onboarding appeared on events route; closing browser and reopening cached profile (cycle {attempt}).")
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue

                # If the cached session is absent/expired, authenticate once
                # in the same persistent profile and then revisit the Dashboard.
                page.goto(APOLLO_URL, wait_until="networkidle", timeout=30000)
                login_snapshot = page_snapshot(page)
                login_state = classify_state(login_snapshot)
                if login_state == "onboarding":
                    print(f"Onboarding detected; closing persistent browser and reopening with cached profile (cycle {attempt}).")
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                if login_state == "dashboard":
                    state = open_events_from_dashboard(page)
                    last_snapshot = state
                    save_browser_screenshot(page)
                    if classify_state(state) == "event_page":
                        log_first_success()
                        return state
                    if classify_state(state) == "onboarding":
                        print(f"Onboarding appeared after cached login; closing browser and reopening cached profile (cycle {attempt}).")
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue

                page.fill('input[placeholder="123456789"], input[type="text"]', SITE_USERNAME)
                page.fill('input[placeholder="••••••••"], input[type="password"]', SITE_PASSWORD)
                username_field = page.locator('input[name="username"], input[placeholder="123456789"]').first
                password_field = page.locator('input[name="password"], input[placeholder="••••••••"]').first
                if not username_field.input_value() or not password_field.input_value():
                    raise RuntimeError("Login fields were not filled")

                submit_button = page.locator('button[type="submit"]').first
                if submit_button.count() == 0:
                    submit_button = page.get_by_role("button", name="התחבר", exact=True).first
                submit_button.click()
                page.wait_for_timeout(8000)

                login_snapshot = page_snapshot(page)
                last_snapshot = login_snapshot
                save_browser_screenshot(page)
                if classify_state(login_snapshot) == "onboarding":
                    print(f"Onboarding detected after login; closing persistent browser and reopening with cached profile (cycle {attempt}).")
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue

                page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                state = page_snapshot(page)
                last_snapshot = state
                save_browser_screenshot(page)
                if classify_state(state) == "dashboard":
                    state = open_events_from_dashboard(page)
                    last_snapshot = state
                    save_browser_screenshot(page)
                if classify_state(state) == "onboarding":
                    print(f"Onboarding appeared on direct events URL; closing browser and reopening cached profile (cycle {attempt}).")
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                if classify_state(state) == "event_page":
                    log_first_success()
                    return state

            except Exception:
                console_log(f"❌ ההתחברות נכשלה — מנסה שוב בעוד {RETRY_DELAY_SECONDS} שניות…")
                time.sleep(RETRY_DELAY_SECONDS)
            finally:
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass

        return last_snapshot


def classify_state(snapshot: dict[str, Any]) -> str:
    url = snapshot["url"].lower()
    body = snapshot["body_text"].lower()
    title = snapshot["title"].lower()

    if "/onboarding" in url or "שאלון קליטת עובד" in title or "קליטת עובד" in body:
        return "onboarding"
    if "/dashboard" in url:
        return "dashboard"
    if "/open-events" in url or "/event-list" in url or url.startswith(EVENTS_URL.lower().rstrip("/")):
        return "event_page"
    if "/login" in url or "התחברות" in title or "תעודת זהות" in body:
        return "login"
    return "unknown"


def format_event_message(event: dict[str, Any]) -> str:
    return (
        "🚨 אירוע חדש להרשמה\n"
        f"📌 שם אירוע: {event['title']}\n"
        f"📍 מקום: {event['place']}\n"
        f"🗓️ תאריך: {event['date']}\n"
        f"⏰ זמנים: {event['time']}"
    )


def event_identity(event: dict[str, Any]) -> str:
    """Identify an event independently of its time, place, and seat availability."""
    return f"{event['title']}|{event['date']}"


def format_event_update_message(event: dict[str, Any], previous_event: dict[str, Any]) -> str:
    return (
        "🔔 עדכון לאירוע קיים\n"
        f"📌 שם אירוע: {event['title']}\n"
        f"🗓️ תאריך: {event['date']}\n"
        f"⏰ שעות קודמות: {previous_event.get('time', 'לא ידוע')}\n"
        f"⏰ שעות חדשות: {event.get('time', 'לא ידוע')}\n"
        f"📍 מקום: {event['place']}"
    )


def send_heartbeat_if_needed(snapshot: dict[str, Any], current_state: dict[str, Any]) -> None:
    now = int(time.time())
    last_heartbeat = int(current_state.get("last_heartbeat", 0))
    if now - last_heartbeat >= HEARTBEAT_MINUTES * 60:
        current_state["last_heartbeat"] = now


def run_scan_locked(notify: bool = True) -> dict[str, Any]:
    """Run one scan; an hourly and manual scan cannot overlap."""
    with SCAN_LOCK:
        return one_scan(notify=notify)


def format_new_shift_result(result: dict[str, Any]) -> str:
    new_events = result.get("new_events", [])
    if not new_events:
        return "ℹ️ לא נמצאו משמרות חדשות כרגע."

    lines = [f"✅ נמצאו {len(new_events)} משמרות חדשות:"]
    for event in new_events:
        lines.extend([
            f"\n📌 שם אירוע: {event['title']}",
            f"📍 מקום: {event['place']}",
            f"🗓️ תאריך: {event['date']}",
            f"⏰ זמנים: {event['time']}",
        ])
    return "\n".join(lines)


def format_event_list_messages(events: list[dict[str, Any]], max_length: int = 3900) -> list[str]:
    if not events:
        return ["ℹ️ לא נמצאו כרגע אירועים מתאימים של סדרן ללא תעודה."]

    event_blocks: list[str] = []
    for index, event in enumerate(events, start=1):
        event_blocks.append("\n".join([
            f"\n{index}. 📌 {event['title']}",
            f"📍 מקום: {event['place']}",
            f"🗓️ תאריך: {event['date']}",
            f"⏰ זמנים: {event['time']}",
        ]))

    chunks: list[list[str]] = [[]]
    for block in event_blocks:
        candidate = "\n".join(chunks[-1] + [block])
        if chunks[-1] and len(candidate) > max_length:
            chunks.append([block])
        else:
            chunks[-1].append(block)

    total_parts = len(chunks)
    messages: list[str] = []
    for part_index, chunk in enumerate(chunks, start=1):
        part_label = f" — חלק {part_index}/{total_parts}" if total_parts > 1 else ""
        header = f"📋 רשימת האירועים ({len(events)}){part_label} — נכון לסריקה האחרונה:"
        messages.append(header + "\n" + "\n".join(chunk))
    return messages


def one_scan(notify: bool = True) -> dict[str, Any]:
    global _startup_baseline_captured
    try:
        snapshot = login_and_capture()
    except Exception as exc:
        send_telegram(f"⚠️ Apollo monitor failed to log in: {exc}")
        return {"new_events": [], "updated_events": []}

    state = classify_state(snapshot)
    prev = load_previous_state()

    if state == "onboarding":
        print("Blocked by onboarding questionnaire")
        send_telegram("🛑 Apollo Security: המשתמש עדיין בשאלון קליטת עובד. אין סריקה של אירועים עד לסיום השאלון.")
        prev["state"] = "onboarding"
        prev["snapshot"] = snapshot
        save_state(prev)
        return {"new_events": [], "updated_events": []}

    if state == "login":
        print("Login page is active")
        send_telegram("🔐 Apollo Security: דף הכניסה פעיל, נדרשת התחברות מחדש.")
        prev["state"] = "login"
        prev["snapshot"] = snapshot
        save_state(prev)
        return {"new_events": [], "updated_events": []}

    if state != "event_page":
        print(f"Unknown page state: {snapshot['url']}")
        prev["state"] = "unknown"
        prev["snapshot"] = snapshot
        save_state(prev)
        return {"new_events": [], "updated_events": []}

    current_events = snapshot["events"]
    prev_events = prev.get("snapshot", {}).get("events", [])
    sent_events: dict[str, dict[str, Any]] = prev.setdefault("sent_events", {})
    # Migrate the previous snapshot into the explicit sent-event journal once.
    # This prevents the first run after an upgrade from re-alerting everything.
    if not sent_events:
        sent_events.update({event_identity(event): event for event in prev_events})

    # The first scan after a process restart establishes the current list as
    # the baseline, so existing events are not re-sent as "new" alerts.
    startup_baseline = notify and not _startup_baseline_captured
    _startup_baseline_captured = True

    # Seat availability is intentionally excluded. Name + date identify the
    # event; a time change is handled separately as an event update.
    previous_by_identity = sent_events or {event_identity(item): item for item in prev_events}
    new_events: list[dict[str, Any]] = []
    updated_events: list[tuple[dict[str, Any], dict[str, Any]]] = []

    if startup_baseline:
        sent_events.update({event_identity(event): event for event in current_events})
    else:
        for event in current_events:
            previous_event = previous_by_identity.get(event_identity(event))
            if previous_event is None:
                new_events.append(event)
            elif previous_event.get("time") != event.get("time"):
                updated_events.append((event, previous_event))

    if new_events:
        for index, event in enumerate(new_events):
            if notify:
                if index > 0:
                    time.sleep(TELEGRAM_MESSAGE_DELAY_SECONDS)
                send_telegram(format_event_message(event))
            sent_events[event_identity(event)] = event

    if updated_events:
        for event, previous_event in updated_events[:5]:
            if notify:
                send_telegram(format_event_update_message(event, previous_event))
            sent_events[event_identity(event)] = event

    prev["state"] = state
    prev["snapshot"] = snapshot
    send_heartbeat_if_needed(snapshot, prev)
    save_state(prev)
    print(f"Checked Apollo Security. URL: {snapshot['url']} | Events: {len(current_events)}")
    return {
        "new_events": new_events,
        "updated_events": updated_events,
        "events": current_events,
    }


def send_test_alert() -> None:
    try:
        snapshot = login_and_capture()
    except Exception as exc:
        send_telegram(f"⚠️ Test alert failed: {exc}")
        return

    if snapshot.get("events"):
        event = random.choice(snapshot["events"])
        send_telegram("🧪 בדיקת סריקה חיה\n\n" + format_event_message(event))
        print(f"Test alert sent for: {event.get('title', 'unknown')}")
    else:
        send_telegram("🧪 בדיקת סריקה חיה: לא נמצאו אירועים מתאימים כרגע ב-כיכר ספרא / סדרן ללא תעודה.")
        print("Test alert sent, but no matching events found.")


def run_loop() -> None:
    while True:
        run_scan_locked()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apollo Security Telegram monitor")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--test-alert", action="store_true", help="Send one real test alert using the current filtered event list")
    args = parser.parse_args()

    if not BOT_TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN in .env")
        sys.exit(1)

    if not CHAT_ID:
        print("TELEGRAM_CHAT_ID is empty; running in dry-run mode until a valid chat is configured.")

    console_log("🚀 מפעיל את הבוט…")

    if args.test_alert:
        if not CHAT_ID:
            print("No valid TELEGRAM_CHAT_ID configured, so the test alert is skipped.")
            raise SystemExit(0)
        send_test_alert()
    elif args.once:
        one_scan()
    else:
        if BOT_TOKEN:
            listener = threading.Thread(target=run_telegram_listener, daemon=True)
            listener.start()
        run_loop()
