"""Навигация по разделам управления и настоящим раскрывающимся формам."""
from playwright.sync_api import expect


def open_settings(page):
    page.evaluate('async () => { try { await adminSettingsLoad; } catch {} }')
    for details in page.locator('.settings-panel:not([hidden]) details.settings-section:visible').all():
        if not details.evaluate('(d)=>d.open'):
            details.locator(':scope > summary').click()


def open_management(page, section):
    page.evaluate('section => window.cabinet.go("management/"+section)', section)
    expect(page.locator('[data-page=management]')).to_be_visible()
    expect(page.locator('#settings')).to_have_attribute('data-management-section', section)
    open_settings(page)
