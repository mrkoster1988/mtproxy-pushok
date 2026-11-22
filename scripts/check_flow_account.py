"""Flow consent in Chromium with real API/authentication and disposable SQL.

Only the Telegram SDK shell and unrelated subscription UI are fixtures. Widget
signatures, Mini App signatures, cabinet sessions and Flow decisions are real.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import psycopg
from playwright.sync_api import Error as BrowserError, expect


def check_flow_account(browser, base, output, failures, dsn):
    from app import main
    from test_miniapp_auth import signed_data

    fixture_bot = "flow-browser-fixture-only"
    people = [(uuid4(), 99884401, "Flow Первый"), (uuid4(), 99884402, "Flow Второй")]
    with psycopg.connect(dsn) as conn:
        for user_id, telegram_id, name in people:
            conn.execute("INSERT INTO users(id,telegram_user_id,first_name,role) VALUES(%s,%s,%s,'advertiser')",
                         (user_id, telegram_id, name))

    def session(person=0):
        session_id = uuid4()
        with psycopg.connect(dsn) as conn:
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",
                         (session_id, people[person][0]))
        return main._create_session_token(people[person][0], session_id), session_id

    sessions = [session(0)[0], session(1)[0]]
    contexts = []
    transport = browser.new_context()
    contexts.append(transport)

    def challenge():
        # Independent scenarios share one loopback address. Rate-limit behavior
        # has separate API tests; auth and decisions are never bypassed here.
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM flow_rate_windows")
        response = transport.request.post(base + "/api/v1/flow/device", data={})
        assert response.status == 201, "Could not create synthetic Flow challenge"
        return response.json()

    def decision_state(item):
        with psycopg.connect(dsn) as conn:
            row = conn.execute("SELECT state,decided_by FROM flow_device_requests WHERE user_code=%s",
                               (item["user_code"],)).fetchone()
        return row

    def widget_login(page, person, *, wait=True):
        data = {"id": people[person][1], "first_name": people[person][2],
                "last_name": "Тест " + uuid4().hex[:8], "auth_date": int(time.time())}
        checked = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        data["hash"] = hmac.new(hashlib.sha256(fixture_bot.encode()).digest(),
                                checked.encode(), hashlib.sha256).hexdigest()
        if wait:
            page.evaluate("data => window.onTelegramAuth(data)", data)
        else:
            page.evaluate("data => {(window.flowTestLogins ??= []).push(window.onTelegramAuth(data));}", data)

    def visit(item=None, *, person=0, cabinet_token=None, anonymous=False, query=None,
              mini=False, before=None, width=390, stored_intent=None, cached_mini=False):
        context = browser.new_context(viewport={"width": width, "height": 844}, reduced_motion="reduce")
        contexts.append(context)
        if not anonymous:
            context.add_init_script("sessionStorage.setItem('pushok-session'," +
                                    json.dumps(cabinet_token or sessions[person]) + ")")
        if stored_intent is not None:
            context.add_init_script("sessionStorage.setItem('pushok-flow-intent-v1'," +
                                    json.dumps(stored_intent) + ")")
        if mini:
            raw = signed_data(token=fixture_bot, query_id="flow-ui-" + uuid4().hex,
                              user=json.dumps({"id": people[person][1], "first_name": people[person][2]}))
            context.add_init_script("""window.Telegram={WebApp:{
                initData:RAW,initDataUnsafe:{user:{id:1,first_name:'Untrusted'}},
                colorScheme:'dark',ready(){},expand(){},onEvent(){},
                BackButton:{show(){},hide(){},onClick(){}}
            }};""".replace("RAW", json.dumps(raw)))
            if cached_mini:
                fingerprint = parse_qs(raw)["hash"][0]
                context.add_init_script("sessionStorage.setItem('pushok-miniapp-init-hash'," +
                    json.dumps(fingerprint) + ");sessionStorage.setItem('pushok-miniapp-session'," +
                    json.dumps(cabinet_token or sessions[person]) + ")")
        page = context.new_page()
        events = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on("request", lambda request: events.append(("request", request.method, request.url)))
        page.on("response", lambda response: events.append(("response", response.status, response.url)))

        def local_only(route):
            if not route.request.url.startswith(base + "/"):
                route.abort()
            elif route.request.url.partition("?")[0].endswith("/me/channel-subscription"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "joined": True, "channel_url": "https://t.me/pushok_proxy", "channel_title": "Тест"}))
            else:
                route.continue_()
        page.route("**/*", local_only)
        if before:
            before(page)
        path = "/miniapp" if mini else "/"
        query = query if query is not None else ("flow=" + item["user_code"] if item else "")
        response = page.goto(base + path + ("?" + query if query else ""), wait_until="domcontentloaded")
        assert response.headers.get("referrer-policy") == "no-referrer"
        return page, events

    def dialog(page, state="pending"):
        result = page.locator("#flow-account-dialog")
        expect(result).to_be_visible()
        expect(result).to_have_attribute("data-state", state)
        return result

    def decisions(events):
        return [event for event in events if event[:2] == ("request", "POST")
                and event[2].endswith("/flow/device/decision")]

    def public_only(page, item, *, active=True):
        assert "flow=" not in page.url
        assert page.evaluate("!new URL(location.href).searchParams.has('flow')")
        stored = page.evaluate("JSON.stringify([Object.entries(sessionStorage),Object.entries(localStorage)])")
        intent = page.evaluate("JSON.parse(sessionStorage.getItem('pushok-flow-intent-v1'))")
        if active:
            assert set(intent) == {"code", "receivedAt"}
            assert intent["code"] == item["user_code"]
            assert isinstance(intent["receivedAt"], int)
            assert page.evaluate("stamp => Date.now() >= stamp && Date.now()-stamp < 300000", intent["receivedAt"])
        else:
            assert intent is None and item["user_code"] not in stored
        assert page.evaluate("!localStorage.getItem('pushok-flow-intent-v1')")
        assert item["device_code"] not in stored
        assert "pfa_" not in stored

    def release(held):
        try:
            held["route"].fulfill(response=held["response"])
        except BrowserError as error:
            # Reset cancels the fetch. Some Chromium versions report the
            # already-aborted route when the old response is finally delivered.
            assert any(word in str(error).lower() for word in ("closed", "cancel", "abort", "handled"))

    def wait_held(page, held):
        # Locator.click finishes before the routed HTTP exchange necessarily
        # completes. Keep dispatching events until its real response is held.
        until = time.monotonic() + 5
        while not held.get("response") and time.monotonic() < until:
            page.wait_for_timeout(25)
        assert held.get("response") and held["response"].status == 200

    def stored(page):
        return page.evaluate("JSON.parse(sessionStorage.getItem('pushok-flow-intent-v1'))")

    with ExitStack() as stack:
        stack.enter_context(patch.object(main, "TELEGRAM_BOT_TOKEN", fixture_bot))
        stack.enter_context(patch.dict(os.environ, {"PUSHOK_MINIAPP_ENABLED": "1",
                                                   "TELEGRAM_BOT_USERNAME": "mtproxy_pushok_bot"}))
        try:
            # Consent must work after /me even if unrelated advertising fails.
            first = challenge()
            def broken_ads(page):
                page.route("**/api/v1/channels", lambda route: route.fulfill(
                    status=503, content_type="application/json", body='{"detail":"fixture_unavailable"}'))
            page, events = visit(first, before=broken_ads)
            panel = dialog(page)
            expect(panel.locator("#flow-account-identity")).to_contain_text(people[0][2])
            expect(panel.locator("#flow-account-identity")).to_contain_text(str(people[0][1]))
            expect(panel.locator("#flow-account-code")).to_have_text(" ".join(
                first["user_code"][i:i + 4] for i in range(0, 12, 4)))
            expect(panel).to_contain_text("Доступ только для просмотра")
            expect(panel).to_contain_text("баланса кабинета MTProxy")
            expect(panel.locator("#flow-account-deny")).to_be_focused()
            expect(panel.locator("#flow-account-deny")).to_have_text("Отмена")
            expect(panel.locator("#flow-account-approve")).to_have_text("Подключить")
            public_only(page, first)
            me_event = next(i for i, event in enumerate(events) if event[:2] == ("response", 200)
                            and event[2] == base + "/api/v1/me")
            review_event = next(i for i, event in enumerate(events) if event[:2] == ("request", "POST")
                                and event[2].endswith("/flow/device/review"))
            assert me_event < review_event
            assert decision_state(first) == ("pending", None) and not decisions(events)
            page.evaluate("location.hash='#/profile'")
            assert decision_state(first) == ("pending", None) and not decisions(events)
            for width in (320, 390, 1280):
                page.set_viewport_size({"width": width, "height": 844})
                assert panel.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
                assert panel.evaluate("e => {const r=e.getBoundingClientRect();return r.x>=0 && r.right<=innerWidth+1}")
                panel.screenshot(path=str(output / f"flow-consent-{width}.png"))
            page.locator("#flow-account-approve").click()
            dialog(page, "approved")
            assert len(decisions(events)) == 1
            assert decision_state(first) == ("approved", people[0][0])
            exchange = transport.request.post(base + "/api/v1/flow/device/token", data={"device_code": first["device_code"]})
            assert exchange.status == 200 and exchange.json()["status"] == "authorized"
            profile = transport.request.get(base + "/api/v1/flow/me", headers={
                "Authorization": "Bearer " + exchange.json()["access_token"]})
            assert profile.status == 200 and profile.json()["id"] == str(people[0][0])
            public_only(page, first, active=False)

            foreign, foreign_events = visit(first, person=1)
            dialog(foreign, "unavailable")
            expect(foreign.locator("#flow-account-identity")).to_have_count(0)
            assert not decisions(foreign_events)

            # Real signed Login Widget callback, then explicit denial.
            unsigned = challenge()
            guest, guest_events = visit(unsigned, anonymous=True)
            expect(guest.locator("#flow-account-intro")).to_be_visible()
            expect(guest.locator("#login-note")).to_contain_text("Вход подтверждается")
            assert not any(event[2].endswith("/flow/device/review") for event in guest_events)
            public_only(guest, unsigned)
            widget_login(guest, 0)
            dialog(guest)
            assert decision_state(unsigned) == ("pending", None)
            guest.locator("#flow-account-deny").click()
            dialog(guest, "denied")
            assert decision_state(unsigned) == ("denied", people[0][0])
            denied = transport.request.post(base + "/api/v1/flow/device/token", data={"device_code": unsigned["device_code"]})
            assert denied.status == 403

            # Real Mini App signature selects its owner, ignoring a web session
            # for another account and untrusted initDataUnsafe fields.
            mini_item = challenge()
            mini, mini_events = visit(mini_item, person=1, cabinet_token=sessions[0], mini=True)
            mini_panel = dialog(mini)
            expect(mini_panel.locator("#flow-account-identity")).to_contain_text(people[1][2])
            expect(mini_panel.locator("#flow-account-identity")).to_contain_text(str(people[1][1]))
            assert mini.evaluate("String(me.id)") == str(people[1][0])
            public_only(mini, mini_item)
            assert decision_state(mini_item) == ("pending", None) and not decisions(mini_events)
            mini.keyboard.press("Escape")
            expect(mini.locator("#flow-account-dialog")).to_have_count(0)
            assert decision_state(mini_item) == ("pending", None)

            for query in ("flow=123456789ABC", "flow=" + first["user_code"] + "&flow=" + first["user_code"],
                          "flow=" + first["user_code"] + "%2Fextra"):
                invalid, invalid_events = visit(query=query)
                dialog(invalid, "unavailable")
                expect(invalid.locator("#flow-account-approve")).to_have_count(0)
                assert "flow=" not in invalid.url
                assert not any(event[2].endswith("/flow/device/review") for event in invalid_events)

            expired = challenge()
            with psycopg.connect(dsn) as conn:
                conn.execute("UPDATE flow_device_requests SET created_at=now()-interval '301 seconds',expires_at=now()-interval '1 second' WHERE user_code=%s",
                             (expired["user_code"],))
            expired_page, _ = visit(expired)
            dialog(expired_page, "unavailable")
            public_only(expired_page, expired, active=False)

            revoked = challenge()
            revoke_token, revoke_id = session()
            revoked_page, _ = visit(revoked, cabinet_token=revoke_token)
            dialog(revoked_page)
            with psycopg.connect(dsn) as conn:
                conn.execute("UPDATE user_sessions SET revoked_at=now() WHERE id=%s", (revoke_id,))
            # A background cabinet request may observe revocation first. The
            # same current-session 401 must clear consent in either order.
            revoked_page.evaluate("async () => {try {await api('/me');} catch(error) {if(token) throw error;}}")
            expect(revoked_page.locator("#flow-account-dialog")).to_have_count(0)
            expect(revoked_page.locator("#login")).to_be_visible()
            assert not revoked_page.evaluate("token")
            public_only(revoked_page, revoked, active=False)
            assert decision_state(revoked) == ("pending", None)

            # A completed review response cannot re-open consent after logout.
            delayed = challenge()
            logout_token, _ = session()
            held = {}
            def hold_review(page):
                def capture(route):
                    held.update(route=route, response=route.fetch())
                page.route("**/api/v1/flow/device/review", capture)
            delayed_page, delayed_events = visit(delayed, cabinet_token=logout_token, before=hold_review)
            dialog(delayed_page, "loading")
            expect(delayed_page.locator("body")).to_have_attribute("data-cabinet-loaded", "true")
            wait_held(delayed_page, held)
            delayed_page.evaluate("""async () => {
                await api('/auth/logout',{method:'POST'});token='';
                sessionStorage.removeItem(sessionKey);window.cabinet.logout();
            }""")
            release(held)
            expect(delayed_page.locator("#flow-account-dialog")).to_have_count(0)
            assert not decisions(delayed_events) and decision_state(delayed) == ("pending", None)

            # An old decision may have reached the server, but cannot attach a
            # success screen to a different account that signs in meanwhile.
            switched = challenge()
            switched_page, _ = visit(switched)
            dialog(switched_page)
            expect(switched_page.locator("body")).to_have_attribute("data-cabinet-loaded", "true")
            held = {}
            def capture_decision(route):
                held.update(route=route, response=route.fetch())
            switched_page.route("**/api/v1/flow/device/decision", capture_decision)
            switched_page.locator("#flow-account-approve").click()
            wait_held(switched_page, held)
            widget_login(switched_page, 1)
            expect(switched_page.locator("#flow-account-dialog")).to_have_count(0)
            release(held)
            assert switched_page.evaluate("String(me.id)") == str(people[1][0])
            assert decision_state(switched) == ("approved", people[0][0])
            expect(switched_page.locator("#flow-account-dialog")).to_have_count(0)
            public_only(switched_page, switched, active=False)

            # Lost response: review server state; never silently repeat approve.
            lost = challenge()
            lost_page, lost_events = visit(lost, width=320)
            dialog(lost_page)
            def lose_decision(route):
                response = route.fetch()
                assert response.status == 200
                route.abort()
            lost_page.route("**/api/v1/flow/device/decision", lose_decision)
            lost_page.locator("#flow-account-approve").click()
            retry = dialog(lost_page, "retry")
            expect(lost_page.locator("#flow-account-approve")).to_have_count(0)
            assert retry.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
            assert decision_state(lost) == ("approved", people[0][0])
            lost_page.locator("#flow-account-retry").click()
            dialog(lost_page, "approved")
            assert len(decisions(lost_events)) == 1
            public_only(lost_page, lost, active=False)

            # A delayed /me from A must never be paired with B's current bearer.
            # Both identities and both held responses come from the real API.
            raced = challenge()
            old_me, new_me = {}, {}
            def hold_identities(page):
                def capture(route):
                    response = route.fetch()
                    target = old_me if response.json()["id"] == str(people[0][0]) else new_me
                    target.update(route=route, response=response)
                page.route("**/api/v1/me", capture)
            raced_page, raced_events = visit(raced, before=hold_identities)
            wait_held(raced_page, old_me)
            widget_login(raced_page, 1, wait=False)
            wait_held(raced_page, new_me)
            release(old_me)
            raced_page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            raced_page.wait_for_function("me === null")
            expect(raced_page.locator("#flow-account-dialog")).to_have_count(0)
            assert not any(event[2].endswith("/flow/device/review") for event in raced_events)
            release(new_me)
            raced_panel = dialog(raced_page)
            expect(raced_panel.locator("#flow-account-identity")).to_contain_text(people[1][2])
            expect(raced_panel.locator("#flow-account-identity")).not_to_contain_text(people[0][2])
            raced_page.locator("#flow-account-approve").click()
            dialog(raced_page, "approved")
            assert decision_state(raced) == ("approved", people[1][0])
            assert len(decisions(raced_events)) == 1
            print("PASS: delayed /me cannot pair one identity with another cabinet bearer")

            # The previous Widget callback must not store its token after a
            # newer callback has already completed authentication.
            callbacks = challenge()
            first_auth, second_auth = {}, {}
            def hold_auth(page):
                def capture(route):
                    target = first_auth if route.request.post_data_json["id"] == people[0][1] else second_auth
                    target.update(route=route, response=route.fetch())
                page.route("**/api/v1/auth/telegram", capture)
            callbacks_page, callbacks_events = visit(callbacks, anonymous=True, before=hold_auth)
            expect(callbacks_page.locator("#flow-account-intro")).to_be_visible()
            widget_login(callbacks_page, 0, wait=False)
            wait_held(callbacks_page, first_auth)
            widget_login(callbacks_page, 1, wait=False)
            wait_held(callbacks_page, second_auth)
            release(second_auth)
            expect(dialog(callbacks_page).locator("#flow-account-identity")).to_contain_text(people[1][2])
            current_bearer = callbacks_page.evaluate("token")
            release(first_auth)
            callbacks_page.evaluate("window.flowTestLogins[0]")
            assert callbacks_page.evaluate("token") == current_bearer
            assert callbacks_page.evaluate("me.id") == str(people[1][0])
            assert decision_state(callbacks) == ("pending", None) and not decisions(callbacks_events)

            # Exercise the actual logout listener while a real signed callback
            # is still pending. Its late response cannot restore a local login.
            signing_out = challenge()
            late_auth = {}
            def hold_late_auth(page):
                page.route("**/api/v1/auth/telegram", lambda route: late_auth.update(
                    route=route, response=route.fetch()))
            signing_out_page, signing_out_events = visit(signing_out, anonymous=True, before=hold_late_auth)
            widget_login(signing_out_page, 0, wait=False)
            wait_held(signing_out_page, late_auth)
            signing_out_page.evaluate("document.getElementById('logout').click()")
            release(late_auth)
            signing_out_page.evaluate("window.flowTestLogins[0]")
            assert signing_out_page.evaluate("!token && me === null")
            assert stored(signing_out_page) is None
            expect(signing_out_page.locator("#flow-account-dialog")).to_have_count(0)
            assert decision_state(signing_out) == ("pending", None) and not decisions(signing_out_events)

            # Logout revokes its captured old session; a late success must not
            # reload or clear the next account that has already signed in.
            old_logout = challenge()
            logout_response = {}
            def hold_logout(page):
                page.route("**/api/v1/auth/logout", lambda route: logout_response.update(
                    route=route, response=route.fetch()))
            logout_page, logout_events = visit(old_logout, cabinet_token=session()[0], before=hold_logout)
            dialog(logout_page)
            expect(logout_page.locator("body")).to_have_attribute("data-cabinet-loaded", "true")
            logout_page.evaluate("document.getElementById('logout').click()")
            # Logout is HTTP 204 rather than the HTTP 200 expected by wait_held.
            until = time.monotonic() + 5
            while not logout_response.get("response") and time.monotonic() < until:
                logout_page.wait_for_timeout(25)
            assert logout_response.get("response") and logout_response["response"].status == 204
            assert logout_page.evaluate("!token && me === null") and stored(logout_page) is None
            widget_login(logout_page, 1)
            new_bearer = logout_page.evaluate("token")
            release(logout_response)
            logout_page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            assert logout_page.evaluate("token") == new_bearer
            assert logout_page.evaluate("me.id") == str(people[1][0])
            assert stored(logout_page) is None and not decisions(logout_events)
            print("PASS: newer Widget login and logout discard late authentication responses")

            # The first /me may reject an old stored cabinet session before
            # any identity was bound. Keep only the public login intent.
            recovery = challenge()
            expired_bearer, expired_id = session()
            with psycopg.connect(dsn) as conn:
                conn.execute("UPDATE user_sessions SET revoked_at=now() WHERE id=%s", (expired_id,))
            recovery_page, recovery_events = visit(recovery, cabinet_token=expired_bearer)
            recovery_page.wait_for_function("typeof token !== 'undefined' && token === ''")
            expect(recovery_page.locator("#flow-account-intro")).to_be_visible()
            assert not any(event[2].endswith("/flow/device/review") for event in recovery_events)
            receipt = stored(recovery_page)
            widget_login(recovery_page, 0)
            dialog(recovery_page)
            assert stored(recovery_page) == receipt
            assert decision_state(recovery) == ("pending", None) and not decisions(recovery_events)
            public_only(recovery_page, recovery)
            # The same initial-401 path in a cached Mini App retains no bearer
            # and asks for a fresh Telegram launch, keeping only public intent.
            mini_recovery = challenge()
            mini_recovery_page, mini_recovery_events = visit(mini_recovery, cabinet_token=expired_bearer,
                mini=True, cached_mini=True)
            mini_recovery_page.wait_for_function("typeof token !== 'undefined' && token === '' && !sessionStorage.getItem('pushok-miniapp-session')")
            expect(mini_recovery_page.locator("#miniapp-login")).to_contain_text("Сессия завершена")
            assert not any(event[2].endswith("/flow/device/review") for event in mini_recovery_events)
            public_only(mini_recovery_page, mini_recovery)
            print("PASS: initial expired sessions retain only an unbound public Flow intent")

            # Reload the guest page and then an authenticated consent. The same
            # receipt time survives; every consent is reviewed again by API.
            reload_item = challenge()
            reload_page, reload_events = visit(reload_item, anonymous=True)
            expect(reload_page.locator("#flow-account-intro")).to_be_visible()
            receipt = stored(reload_page)
            reload_page.reload(wait_until="domcontentloaded")
            expect(reload_page.locator("#flow-account-intro")).to_be_visible()
            assert stored(reload_page) == receipt
            widget_login(reload_page, 0)
            dialog(reload_page)
            reload_page.reload(wait_until="domcontentloaded")
            dialog(reload_page)
            assert stored(reload_page) == receipt
            assert sum(event[:2] == ("request", "POST") and event[2].endswith("/flow/device/review")
                       for event in reload_events) == 2
            assert decision_state(reload_item) == ("pending", None) and not decisions(reload_events)
            # Reopening the same URL cannot extend its original local TTL.
            reload_page.goto(base + "/?flow=" + reload_item["user_code"], wait_until="domcontentloaded")
            dialog(reload_page)
            assert stored(reload_page) == receipt
            # A new URL replaces the public intent; an invalid URL clears it.
            replacement = challenge()
            reload_page.goto(base + "/?flow=" + replacement["user_code"], wait_until="domcontentloaded")
            expect(dialog(reload_page).locator("#flow-account-code")).to_have_text(" ".join(
                replacement["user_code"][i:i+4] for i in range(0, 12, 4)))
            assert stored(reload_page)["code"] == replacement["user_code"]
            assert decision_state(reload_item) == ("pending", None)
            reload_page.goto(base + "/?flow=" + replacement["user_code"] + "&flow=" + replacement["user_code"],
                             wait_until="domcontentloaded")
            dialog(reload_page, "unavailable")
            public_only(reload_page, replacement, active=False)
            print("PASS: reload re-reviews the same bounded intent; a new or invalid URL replaces it safely")

            storage_item = challenge()
            stamp = int(time.time() * 1000)
            malformed = [
                "not json", "[1]", " " * 101,
                json.dumps({"code": storage_item["user_code"], "receivedAt": stamp - 300000}),
                json.dumps({"code": storage_item["user_code"], "receivedAt": stamp + 60000}),
                json.dumps({"code": storage_item["user_code"], "receivedAt": str(stamp)}),
                json.dumps({"code": storage_item["user_code"], "receivedAt": stamp + .5}),
                json.dumps({"code": storage_item["user_code"], "receivedAt": stamp, "userId": str(people[0][0])}),
                json.dumps({"code": "123456789ABC", "receivedAt": stamp}),
            ]
            for value in malformed:
                storage_page, storage_events = visit(query="", stored_intent=value)
                expect(storage_page.locator("body")).to_have_attribute("data-cabinet-loaded", "true")
                expect(storage_page.locator("#flow-account-dialog")).to_have_count(0)
                assert stored(storage_page) is None
                assert not any(event[2].endswith("/flow/device/review") for event in storage_events)
                assert not decisions(storage_events)
                storage_page.close()
            assert decision_state(storage_item) == ("pending", None)
            print("PASS: malformed, expired, future and identity-bearing stored intents are discarded")

            # The Widget script is blocked by the same external-network guard
            # used throughout this suite. Its failure must not hide the explicit
            # existing-bot path or turn opening the page into a decision.
            fallback_item = challenge()
            fallback_page, fallback_events = visit(fallback_item, anonymous=True, width=320)
            fallback = fallback_page.locator("#flow-account-telegram")
            expect(fallback).to_be_visible()
            destination = "https://t.me/mtproxy_pushok_bot?start=flow_" + fallback_item["user_code"]
            expect(fallback).to_have_attribute("href", destination)
            assert fallback.evaluate("e => {const r=e.getBoundingClientRect();return r.x>=0 && r.right<=innerWidth+1}")
            assert not any(event[2].startswith("https://t.me/") for event in fallback_events)
            assert not decisions(fallback_events)
            for invalid_bot in ("other_valid_bot", "mtproxy_pushok_bot?x=1", "https://t.me/mtproxy_pushok_bot", ""):
                fallback_page.evaluate("name => window.pushokFlowAccount.configure({telegram_bot_username:name})", invalid_bot)
                expect(fallback_page.locator("#flow-account-telegram")).to_have_count(0)
            fallback_page.evaluate("window.pushokFlowAccount.configure({telegram_bot_username:'mtproxy_pushok_bot'})")
            expect(fallback_page.locator("#flow-account-telegram")).to_have_attribute("href", destination)
            # Execute the bot's pure update handler, not any Telegram transport.
            with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]), *sys.path]):
                from bot.main import response_for
            with patch.dict(os.environ, {"PUSHOK_MINIAPP_URL": "https://pushok.example/miniapp"}):
                reply, callback = response_for({"message": {
                    "chat": {"id": people[1][1], "type": "private"},
                    "from": {"id": people[1][1], "is_bot": False},
                    "text": "/start " + parse_qs(urlsplit(destination).query)["start"][0],
                }})
            assert callback is None
            mini_url = reply["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
            assert parse_qs(urlsplit(mini_url).query) == {"flow": [fallback_item["user_code"]]}
            fallback_mini, fallback_mini_events = visit(fallback_item, mini=True, person=1)
            expect(dialog(fallback_mini).locator("#flow-account-identity")).to_contain_text(people[1][2])
            assert decision_state(fallback_item) == ("pending", None) and not decisions(fallback_mini_events)
            fallback_mini.locator("#flow-account-deny").click()
            dialog(fallback_mini, "denied")
            public_only(fallback_mini, fallback_item, active=False)
            print("PASS: unavailable Widget retains an explicit public-code bot/Mini App confirmation path")
            assert not failures, failures
        finally:
            for context in reversed(contexts):
                for page in context.pages:
                    page.unroute_all(behavior="ignoreErrors")
                context.close()
