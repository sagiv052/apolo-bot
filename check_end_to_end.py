from pathlib import Path

from playwright.sync_api import sync_playwright

import apollo_monitor as monitor

monitor.send_whatsapp("✅ בדיקת מערכת: WhatsApp Web, התחברות לאתר וזרימת הסריקה נבדקים כעת.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(locale="he-IL", user_agent=monitor.USER_AGENT)
    page.goto("https://apollo-security.co.il", wait_until="networkidle", timeout=30000)
    username = page.locator('input[name="username"], input[placeholder="123456789"]').first
    password = page.locator('input[name="password"], input[placeholder="••••••••"]').first
    username.fill(monitor.SITE_USERNAME)
    password.fill(monitor.SITE_PASSWORD)
    page.locator('button[type="submit"]').first.click()
    page.wait_for_timeout(8000)
    login_url = page.url
    dashboard_text = " ".join(page.locator("body").inner_text().split())[:1000]
    page.goto("https://apollo-security.co.il/open-events", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)
    snapshot = monitor.page_snapshot(page)
    monitor.save_browser_screenshot(page)
    print({
        "login_url": login_url,
        "events_url": snapshot["url"],
        "state": monitor.classify_state(snapshot),
        "event_count": len(snapshot["events"]),
        "dashboard_mentions_open_events": "אירועים פתוחים להרשמה" in dashboard_text,
        "screenshot_bytes": Path(monitor.SCREENSHOT_FILE).stat().st_size,
    })
    browser.close()
