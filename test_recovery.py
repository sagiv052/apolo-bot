import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Keep this test independent from optional runtime packages.
playwright = types.ModuleType("playwright")
playwright_sync = types.ModuleType("playwright.sync_api")
setattr(playwright_sync, "sync_playwright", object())
setattr(playwright, "sync_api", playwright_sync)
sys.modules["playwright"] = playwright
sys.modules["playwright.sync_api"] = playwright_sync

dotenv = types.ModuleType("dotenv")
setattr(dotenv, "load_dotenv", lambda: None)
sys.modules["dotenv"] = dotenv

spec = importlib.util.spec_from_file_location("apollo_monitor", "apollo_monitor.py")
if spec is None or spec.loader is None:
    raise ImportError("Unable to load apollo_monitor.py for the recovery test")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


Snapshot = dict[str, Any]


class FakeBrowser:
    def __init__(self, snapshots: list[Snapshot]) -> None:
        self.snapshots = snapshots
        self.closed = False
        self._page = FakePage(self.snapshots.pop(0))

    @property
    def pages(self) -> list["FakePage"]:
        return [self._page]

    def new_page(self, *args: Any, **kwargs: Any) -> "FakePage":
        return self._page

    def close(self) -> None:
        self.closed = True


class FakePage:
    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.url = snapshot["url"]

    def goto(self, *args: Any, **kwargs: Any) -> None:
        return None

    def fill(self, *args: Any, **kwargs: Any) -> None:
        return None

    def click(self, *args: Any, **kwargs: Any) -> None:
        return None

    def wait_for_timeout(self, *args: Any, **kwargs: Any) -> None:
        return None

    def screenshot(self, path: str | Path, full_page: bool = True) -> None:
        with open(path, "wb") as image:
            image.write(b"fake-png")

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(self.snapshot)

    def get_by_role(self, *args: Any, **kwargs: Any) -> "FakeLocator":
        return FakeLocator(self.snapshot)


class FakeLocator:
    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return 1

    def input_value(self) -> str:
        return "filled"

    def click(self) -> None:
        return None

    def inner_text(self, timeout: float | None = None) -> str:
        if "body" in self.snapshot:
            return str(self.snapshot["body"])
        return str(self.snapshot.get("title", ""))


def test_onboarding_is_retried_with_a_new_browser() -> None:
    onboarding: Snapshot = {"url": "https://apollo-security.co.il/onboarding", "title": "", "body": "שאלון קליטת עובד"}
    events: Snapshot = {"url": "https://apollo-security.co.il/open-events", "title": "אירועים", "body": "מקומות פנויים"}
    browsers: list[FakeBrowser] = []

    class Chromium:
        def launch_persistent_context(
            self,
            user_data_dir: str,
            headless: bool,
            locale: str,
            user_agent: str,
        ) -> FakeBrowser:
            browser = FakeBrowser([onboarding] if not browsers else [events])
            browsers.append(browser)
            return browser

    class Playwright:
        chromium = Chromium()

        def __enter__(self) -> "Playwright":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def snapshot_for_page(page: FakePage) -> dict[str, Any]:
        return {
            "url": page.snapshot["url"],
            "title": page.snapshot.get("title", ""),
            "body_text": page.snapshot.get("body", ""),
            "events": [],
        }

    with patch.object(module, "sync_playwright", return_value=Playwright()), \
         patch.object(module, "SITE_USERNAME", "user"), \
         patch.object(module, "SITE_PASSWORD", "pass"), \
         patch.object(module, "RETRY_DELAY_SECONDS", 0), \
         patch.object(module, "page_snapshot", side_effect=snapshot_for_page):
        result = module.login_and_capture()

    assert len(browsers) == 2, "the function should retry once with a fresh browser after onboarding"
    assert all(browser.closed for browser in browsers)
    assert result["url"].endswith("/open-events")


def test_event_time_update_message() -> None:
    previous: Snapshot = {"title": "אירוע לדוגמה", "date": "01.10.2026", "time": "08:00", "seats": 2}
    current: Snapshot = {"title": "אירוע לדוגמה", "date": "01.10.2026", "time": "10:00", "seats": 20, "place": "כיכר ספרא"}
    assert module.event_identity(previous) == module.event_identity(current)
    message = module.format_event_update_message(current, previous)
    assert "08:00" in message
    assert "10:00" in message
    assert "עדכון" in message


def test_parser_splits_shifts_and_removes_cta_text() -> None:
    card = (
        "יום שלישי, 29.09.2026 הופעה - עברי לידר - גבעת התחמושת "
        "סדרן ללא תעודה גבעת התחמושת ניווט שעת התייצבות: 16:30 — 23:30 "
        "מקומות פנויים: 4 הרשמה / הגש מועמדות "
        "הפועל ירושלים כדורסל סדרן ללא תעודה היכל הפיס ארנה ניווט "
        "שעת התייצבות: 17:30 — 22:00 מקומות פנויים: 11 הרשמה / הגש מועמדות"
    )
    events = module.parse_event_card_text(card)
    assert len(events) == 2
    assert all("הרשמה" not in event["time"] for event in events)
    assert all("מקומות פנויים" not in event["time"] for event in events)
    assert events[0]["seats"] == 4
    assert events[1]["seats"] == 11


if __name__ == "__main__":
    try:
        test_onboarding_is_retried_with_a_new_browser()
        test_event_time_update_message()
        test_parser_splits_shifts_and_removes_cta_text()
        print("recovery test passed")
    finally:
        Path(module.SCREENSHOT_FILE).unlink(missing_ok=True)
