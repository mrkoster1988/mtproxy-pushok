"""Навигация по настоящим раскрывающимся разделам настроек."""
def open_settings(page):
    page.wait_for_function("document.querySelector('#settings-system [data-settings-group]') !== null")
    for details in page.locator('.settings-panel:not([hidden]) details.settings-category, .settings-panel:not([hidden]) details.settings-section').all():
        if not details.evaluate('(d)=>d.open'):
            details.locator(':scope > summary').click()
