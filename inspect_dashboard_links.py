from playwright.sync_api import sync_playwright
import apollo_monitor as m

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context('/tmp/rosh-profile-test', headless=True, locale='he-IL', user_agent=m.USER_AGENT)
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto('https://rosh-shalom-system.lovable.app', wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(3000)
    print('url=', page.url)
    print('title=', page.title())
    links = page.locator('a')
    for i in range(links.count()):
        link = links.nth(i)
        print('LINK', i, repr(' '.join(link.inner_text().split())), link.get_attribute('href'))
    buttons = page.locator('button')
    for i in range(buttons.count()):
        button = buttons.nth(i)
        text=' '.join(button.inner_text().split())
        if text:
            print('BUTTON', i, repr(text))
    browser.close()
