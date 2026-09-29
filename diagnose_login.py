import json
from typing import TypedDict

from playwright.sync_api import sync_playwright
import apollo_monitor as monitor


class InputInfo(TypedDict):
    index: int
    type: str | None
    name: str | None
    placeholder: str | None
    value_length: int


class ButtonInfo(TypedDict):
    index: int
    text: str
    type: str | None
    disabled: bool


class BeforeInfo(TypedDict):
    url: str
    username_value_length: int
    password_value_length: int


class AfterInfo(TypedDict):
    url: str
    title: str
    body: str


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(locale="he-IL", user_agent=monitor.USER_AGENT)
    page.goto("https://apollo-security.co.il", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    inputs = page.locator("input")
    input_info: list[InputInfo] = []
    for i in range(inputs.count()):
        item = inputs.nth(i)
        input_info.append({
            "index": i,
            "type": item.get_attribute("type"),
            "name": item.get_attribute("name"),
            "placeholder": item.get_attribute("placeholder"),
            "value_length": len(item.input_value()),
        })

    buttons = page.locator("button")
    button_info: list[ButtonInfo] = []
    for i in range(buttons.count()):
        item = buttons.nth(i)
        button_info.append({
            "index": i,
            "text": " ".join(item.inner_text().split()),
            "type": item.get_attribute("type"),
            "disabled": item.is_disabled(),
        })

    username = page.locator('input[placeholder="123456789"], input[type="text"]').first
    password = page.locator('input[placeholder="••••••••"], input[type="password"]').first
    username.fill(monitor.SITE_USERNAME)
    password.fill(monitor.SITE_PASSWORD)
    before: BeforeInfo = {
        "url": page.url,
        "username_value_length": len(username.input_value()),
        "password_value_length": len(password.input_value()),
    }
    monitor.save_browser_screenshot(page)

    button = page.get_by_role("button", name="התחבר")
    button_count = button.count()
    if button_count:
        button.first.click()
    else:
        page.locator('button:has-text("התחבר")').first.click()
    page.wait_for_timeout(8000)
    after: AfterInfo = {
        "url": page.url,
        "title": page.title(),
        "body": " ".join(page.locator("body").inner_text().split())[:2000],
    }
    monitor.save_browser_screenshot(page)
    print(json.dumps({"inputs": input_info, "buttons": button_info, "button_count": button_count, "before": before, "after": after}, ensure_ascii=False, indent=2))
    browser.close()
