from playwright.sync_api import sync_playwright
import apollo_monitor as m

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context('/tmp/rosh-profile-test', headless=True, locale='he-IL', user_agent=m.USER_AGENT)
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto(m.EVENTS_URL, wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(2000)
    first = (page.url, page.title(), ' '.join(page.locator('body').inner_text().split())[:300])
    snapshot = m.open_events_from_dashboard(page)
    print({'first': first, 'events_url': snapshot['url'], 'state': m.classify_state(snapshot), 'event_count': len(snapshot['events']), 'body': snapshot['body_text'][:500]})
    browser.close()
