#!/usr/bin/env python3
"""Проверка локального выбора в Chromium: только искусственные ID/изображения."""
import argparse
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.button_icons import ALLOWED
from bot.pushok_emoji_catalog import build_catalog
from bot.pushok_emoji_picker import render_picker
from scripts.prepare_pushok_emoji_ids import validate


def main():
    from PIL import Image, ImageDraw
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable")
    parser.add_argument("--out", type=Path, default=Path(".build/emoji-picker-check"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    ids = ["9007199254740993123", "9007199254740993124", "9007199254740993125"]
    raw = {"name": "PushokIcons", "sticker_type": "custom_emoji", "stickers": [
        {"type": "custom_emoji", "custom_emoji_id": icon_id, "emoji": "🐱", "file_id": "fixture_" + str(i)}
        for i, icon_id in enumerate(ids)]}
    raw["stickers"][1]["emoji"] = '</script><img src=x onerror="alert(1)">'
    catalog = build_catalog(raw, 3)
    previews = {}
    for icon_id, color in zip(ids[:2], ("#57e3cb", "#ffb675")):
        image = Image.new("RGBA", (100, 100))
        ImageDraw.Draw(image).ellipse((15, 15, 85, 85), fill=color)
        buffer = io.BytesIO(); image.save(buffer, format="PNG"); previews[icon_id] = buffer.getvalue()
    html = render_picker(catalog, previews).replace('<header>', '<header><p class="note">ТЕСТОВЫЙ КАТАЛОГ: искусственные ID и изображения.</p>', 1)
    page_path = args.out / "fixture.html"
    page_path.write_text(html, encoding="utf-8")
    manifest = {"schema": "pushok-telegram-emoji-manifest-v1", "actions": dict.fromkeys(ALLOWED, {})}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser_executable)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        errors, external = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        def route(request):
            if request.request.url.startswith(("http:", "https:")):
                external.append(request.request.url); request.abort()
            else:
                request.continue_()
        page.route("**/*", route)
        page.goto(page_path.resolve().as_uri())
        page.wait_for_selector(".card")
        assert page.locator("#action option").count() == 6
        assert page.locator("#download").is_disabled()
        assert page.locator(f'.card[data-id="{ids[2]}"] button').is_disabled()
        assert page.locator("#chosen img").count() == 0
        assert page.locator('img[src="x"]').count() == 0
        page.locator(f'.card[data-id="{ids[0]}"] button').click()
        page.locator("#action").select_option("help")
        page.locator(f'.card[data-id="{ids[1]}"] button').click()
        assert page.locator("#count").inner_text() == "Выбрано 2 из 6"
        with page.expect_download() as download:
            page.locator("#download").click()
        download.value.save_as(args.out / "ids.json")
        exported = json.loads((args.out / "ids.json").read_text())
        assert exported == {"advertise": ids[0], "help": ids[1]}
        assert validate(manifest, exported, catalog) == exported
        for width in (1280, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator(".picture img").evaluate_all("imgs => imgs.every(i => i.complete && i.naturalWidth > 0)")
            page.screenshot(path=str(args.out / f"picker-{width}.png"), full_page=True)
        for key in ALLOWED:
            page.locator("#action").select_option(key)
            page.locator(f'.card[data-id="{ids[0]}"] button').click()
        assert page.locator("#count").inner_text() == "Выбрано 6 из 6"
        page.get_by_role("button", name="Убрать иконку: Помощь", exact=True).click()
        assert page.locator("#count").inner_text() == "Выбрано 5 из 6"
        page.locator("#reset").click()
        assert page.locator("#download").is_disabled()
        page.locator("#action").focus(); page.keyboard.press("Tab")
        assert page.locator(f'.card[data-id="{ids[0]}"] button').evaluate("button => button === document.activeElement")
        page.keyboard.press("Enter")
        assert page.locator("#count").inner_text() == "Выбрано 1 из 6"
        assert not errors, errors
        assert not external, external
        # Все изображения недоступны: обычный emoji не становится заменой картинки.
        page_path.write_text(render_picker(catalog, {}), encoding="utf-8")
        page.reload()
        assert page.locator("#unavailable").is_visible()
        assert page.locator(".card button:disabled").count() == 3
        assert page.locator("#download").is_disabled()
        assert not errors, errors
        browser.close()
    print("PASS: выбор шести действий, точные строковые ID, частичный экспорт, очистка, клавиатура, 320/390/1280 px, без сети")


if __name__ == "__main__":
    main()
