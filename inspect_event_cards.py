from playwright.sync_api import sync_playwright
import apollo_monitor as m

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context('/tmp/rosh-profile-test', headless=True, locale='he-IL', user_agent=m.USER_AGENT)
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto(m.DASHBOARD_URL, wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(2000)
    m.open_events_from_dashboard(page)
    cards = page.locator('div.rounded-lg.border.bg-card.overflow-visible')
    print('card_count=', cards.count())
    for i in range(min(cards.count(), 5)):
        print(f'--- CARD {i} ---')
        print(repr(cards.nth(i).inner_text()))
    browser.close()
