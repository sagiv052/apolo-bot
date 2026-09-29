from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

from playwright.sync_api import sync_playwright


load_dotenv()

SITE_USERNAME = os.getenv("SITE_USERNAME", "")
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))
HEARTBEAT_MINUTES = int(os.getenv("HEARTBEAT_MINUTES", "5"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
RETRY_DELAY_SECONDS = int(os.getenv("RETRY_DELAY_SECONDS", "5"))
WHATSAPP_MESSAGE_DELAY_SECONDS = float(os.getenv("WHATSAPP_MESSAGE_DELAY_SECONDS", "2"))
WHATSAPP_BRIDGE_PORT = int(os.getenv("WHATSAPP_BRIDGE_PORT", "3021"))
WHATSAPP_COMMAND_PORT = int(os.getenv("WHATSAPP_COMMAND_PORT", "3022"))
WHATSAPP_STARTUP_TIMEOUT_SECONDS = int(os.getenv("WHATSAPP_STARTUP_TIMEOUT_SECONDS", "180"))
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
SUBSCRIBERS_FILE = Path(os.getenv("SUBSCRIBERS_FILE", str(Path(__file__).with_name(".whatsapp_subscribers.json"))))
SCAN_LOCK = threading.Lock()
SUBSCRIBERS_LOCK = threading.Lock()
_startup_success_logged = False
_startup_baseline_captured = False
_whatsapp_bridge_process: subprocess.Popen[bytes] | None = None
_command_server: ThreadingHTTPServer | None = None


def console_log(message: str) -> None:
    """Print a short status message immediately to the terminal."""
    print(message, flush=True)


def log_first_success() -> None:
    global _startup_success_logged
    if not _startup_success_logged:
        console_log("✅ ההתחברות הצליחה — הבוט רץ!")
        _startup_success_logged = True


def send_whatsapp_to(text: str, chat_id: str | None = None) -> None:
    """Send a WhatsApp message through the local bridge, optionally to one chat."""
    payload_data: dict[str, str] = {"text": text}
    if chat_id:
        payload_data["chat_id"] = chat_id
    payload = json.dumps(payload_data).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{WHATSAPP_BRIDGE_PORT}/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                print(f"WhatsApp message was not sent: {result.get('error', 'unknown error')}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"WhatsApp is not connected; notification was not sent ({exc}).")


def load_whatsapp_subscribers() -> list[dict[str, str]]:
    with SUBSCRIBERS_LOCK:
        try:
            data = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            subscribers = data.get("subscribers", []) if isinstance(data, dict) else data
            return [item for item in subscribers if isinstance(item, dict) and item.get("chat_id")]
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            return []


def save_whatsapp_subscribers(subscribers: list[dict[str, str]]) -> None:
    SUBSCRIBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = SUBSCRIBERS_FILE.with_name(SUBSCRIBERS_FILE.name + ".tmp")
    temporary_file.write_text(
        json.dumps({"subscribers": subscribers}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_file.replace(SUBSCRIBERS_FILE)


def subscribe_whatsapp_sender(chat_id: str, phone: str = "") -> bool:
    """Persist one opt-in subscriber; return True only for a new subscription."""
    chat_id = str(chat_id).strip()
    if not re.fullmatch(r"\d+@(c\.us|s\.whatsapp\.net|lid)", chat_id, flags=re.IGNORECASE):
        raise ValueError("לא הצלחתי לזהות מספר WhatsApp פרטי לצורך ההרשמה.")
    phone = re.sub(r"\D", "", str(phone or ""))
    if not phone and chat_id.lower().endswith(("@c.us", "@s.whatsapp.net")):
        phone = chat_id.split("@", 1)[0]

    with SUBSCRIBERS_LOCK:
        try:
            existing = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            subscribers = existing.get("subscribers", []) if isinstance(existing, dict) else existing
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            subscribers = []
        if not isinstance(subscribers, list):
            subscribers = []
        subscribers = [item for item in subscribers if isinstance(item, dict) and item.get("chat_id")]
        current = next((item for item in subscribers if item.get("chat_id") == chat_id), None)
        if current:
            if phone:
                current["phone"] = phone
            save_whatsapp_subscribers(subscribers)
            return False
        subscribers.append({"chat_id": chat_id, "phone": phone})
        save_whatsapp_subscribers(subscribers)
        return True


def unsubscribe_whatsapp_sender(chat_id: str, phone: str = "") -> bool:
    phone = re.sub(r"\D", "", str(phone or ""))
    with SUBSCRIBERS_LOCK:
        try:
            existing = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            subscribers = existing.get("subscribers", []) if isinstance(existing, dict) else existing
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            return False
        if not isinstance(subscribers, list):
            return False
        kept = [
            item for item in subscribers
            if not (
                isinstance(item, dict)
                and (item.get("chat_id") == chat_id or (phone and item.get("phone") == phone))
            )
        ]
        if len(kept) == len(subscribers):
            return False
        save_whatsapp_subscribers(kept)
        return True


def send_whatsapp(text: str) -> None:
    """Send a monitor message to its normal destination."""
    send_whatsapp_to(text)


def notify_whatsapp_subscribers(text: str) -> None:
    """Send event announcements and event-time updates to opted-in chats."""
    for subscriber in load_whatsapp_subscribers():
        send_whatsapp_to(text, subscriber["chat_id"])


def handle_whatsapp_command(command: str, chat_id: str = "", phone: str = "") -> dict[str, Any]:
    """Run one of the text commands sent from the linked WhatsApp account."""
    action = command.strip().lower()
    if action in {"subscribe", "updates_on"}:
        if not chat_id:
            return {"messages": ["לא זיהיתי את המספר שלך. שלח הודעה פרטית לחשבון ונסה שוב."]}
        is_new = subscribe_whatsapp_sender(chat_id, phone)
        if is_new:
            return {"messages": ["✅ התקבל! שמרתי את מספר WhatsApp שלך. מעכשיו אשלח לך עדכונים על אירועים חדשים ושינויים בשעות."]}
        return {"messages": ["✅ את/ה כבר רשום/ה לעדכונים. אמשיך לשלוח לך אירועים חדשים ושינויים בשעות."]}
    if action in {"unsubscribe", "updates_off"}:
        if unsubscribe_whatsapp_sender(chat_id, phone):
            return {"messages": ["קיבלתי. הסרתי אותך מרשימת העדכונים."]}
        return {"messages": ["לא מצאתי הרשמה פעילה למספר הזה."]}

    action = action or "help"
    if action == "help":
        return {"messages": [
            "📋 הפקודות הזמינות:\n"
            "חיפוש משמרת חדשה\n"
            "רשימת פקודות\n"
            "צילום מסך\n"
            "שלח לי עדכונים — הרשמה לאירועים חדשים\n"
            "הפסק עדכונים — הסרה מרשימת העדכונים"
        ]}
    if action == "scan":
        result = run_scan_locked()
        return {"messages": ["✅ הסריקה הסתיימה.", format_new_shift_result(result)]}
    if action == "new_shift":
        result = run_scan_locked()
        return {"messages": [format_new_shift_result(result)]}
    if action == "screenshot":
        if not SCREENSHOT_FILE.exists():
            return {"messages": ["📸 עדיין אין צילום מסך. הרץ סריקה אחת ואז נסה שוב."]}
        return {
            "messages": ["📸 צילום המסך האחרון של הדפדפן"],
            "image_base64": base64.b64encode(SCREENSHOT_FILE.read_bytes()).decode("ascii"),
            "image_filename": SCREENSHOT_FILE.name,
        }
    return {"messages": ["לא זיהיתי את הפקודה. שלח: רשימת פקודות"]}


class WhatsAppCommandHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/command":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise ValueError("invalid request size")
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            result = handle_whatsapp_command(
                str(request.get("command", "")),
                str(request.get("chat_id", "")),
                str(request.get("phone", "")),
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"messages": [f"הפקודה נכשלה: {exc}"]}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_whatsapp_bridge() -> bool:
    global _whatsapp_bridge_process, _command_server
    if _command_server is None:
        _command_server = ThreadingHTTPServer(("127.0.0.1", WHATSAPP_COMMAND_PORT), WhatsAppCommandHandler)
        threading.Thread(target=_command_server.serve_forever, daemon=True).start()

    root = Path(__file__).resolve().parent
    if not (root / "node_modules" / "whatsapp-web.js").exists():
        console_log("חסרות ספריות WhatsApp. הרץ npm install בתיקיית הפרויקט.")
        return False
    if _whatsapp_bridge_process is None or _whatsapp_bridge_process.poll() is not None:
        _whatsapp_bridge_process = subprocess.Popen(
            ["node", str(root / "whatsapp_bridge.cjs")], cwd=str(root), env=os.environ.copy()
        )
    return True


def wait_for_whatsapp_ready(timeout_seconds: int = WHATSAPP_STARTUP_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + max(0, timeout_seconds)
    while time.monotonic() < deadline:
        if _whatsapp_bridge_process is not None and _whatsapp_bridge_process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{WHATSAPP_BRIDGE_PORT}/health", timeout=2) as response:
                if json.loads(response.read().decode("utf-8")).get("ready"):
                    return True
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(1)
    return False


def stop_whatsapp_bridge() -> None:
    global _whatsapp_bridge_process, _command_server
    if _whatsapp_bridge_process is not None and _whatsapp_bridge_process.poll() is None:
        _whatsapp_bridge_process.terminate()
        try:
            _whatsapp_bridge_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _whatsapp_bridge_process.kill()
    _whatsapp_bridge_process = None
    if _command_server is not None:
        _command_server.shutdown()
        _command_server.server_close()
        _command_server = None


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
        send_whatsapp(f"⚠️ Apollo monitor failed to log in: {exc}")
        return {"new_events": [], "updated_events": []}

    state = classify_state(snapshot)
    prev = load_previous_state()

    if state == "onboarding":
        print("Blocked by onboarding questionnaire")
        send_whatsapp("🛑 Apollo Security: המשתמש עדיין בשאלון קליטת עובד. אין סריקה של אירועים עד לסיום השאלון.")
        prev["state"] = "onboarding"
        prev["snapshot"] = snapshot
        save_state(prev)
        return {"new_events": [], "updated_events": []}

    if state == "login":
        print("Login page is active")
        send_whatsapp("🔐 Apollo Security: דף הכניסה פעיל, נדרשת התחברות מחדש.")
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
                    time.sleep(WHATSAPP_MESSAGE_DELAY_SECONDS)
                message = format_event_message(event)
                send_whatsapp(message)
                notify_whatsapp_subscribers(message)
            sent_events[event_identity(event)] = event

    if updated_events:
        for event, previous_event in updated_events[:5]:
            if notify:
                message = format_event_update_message(event, previous_event)
                send_whatsapp(message)
                notify_whatsapp_subscribers(message)
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
        send_whatsapp(f"⚠️ Test alert failed: {exc}")
        return

    if snapshot.get("events"):
        event = random.choice(snapshot["events"])
        send_whatsapp("🧪 בדיקת סריקה חיה\n\n" + format_event_message(event))
        print(f"Test alert sent for: {event.get('title', 'unknown')}")
    else:
        send_whatsapp("🧪 בדיקת סריקה חיה: לא נמצאו אירועים מתאימים כרגע ב-כיכר ספרא / סדרן ללא תעודה.")
        print("Test alert sent, but no matching events found.")


def seconds_until_next_scan(now: datetime | None = None) -> float:
    """Wait until the next clock boundary (the default is every local full hour)."""
    if POLL_INTERVAL_SECONDS == 3600:
        current = now or datetime.now()
        next_hour = (current + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return max(0.0, (next_hour - current).total_seconds())

    interval = max(1, POLL_INTERVAL_SECONDS)
    timestamp = time.time()
    return max(0.0, interval - (timestamp % interval))


def run_loop() -> None:
    # Scan once at startup, then align the recurring checks to the clock.
    run_scan_locked()
    while True:
        time.sleep(seconds_until_next_scan())
        run_scan_locked()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apollo Security WhatsApp monitor")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--test-alert", action="store_true", help="Send one real test alert using the current filtered event list")
    args = parser.parse_args()

    if not start_whatsapp_bridge():
        raise SystemExit(1)
    console_log("🚀 מפעיל את Apollo Monitor ופותח WhatsApp Web…")
    if not wait_for_whatsapp_ready():
        console_log("⌛ WhatsApp עדיין לא התחבר. סרוק את ה-QR שמופיע במסוף; ההתראות יתחילו לאחר החיבור.")

    try:
        if args.test_alert:
            send_test_alert()
        elif args.once:
            one_scan()
        else:
            run_loop()
    except KeyboardInterrupt:
        console_log("⏹️ עוצר את Apollo Monitor…")
    finally:
        stop_whatsapp_bridge()
