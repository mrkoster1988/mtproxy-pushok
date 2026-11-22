"""Flow consent in Chromium with real API/authentication and disposable SQL.

Only the Telegram SDK shell and unrelated subscription UI are fixtures. Widget
signatures, Mini App signatures, cabinet sessions and Flow decisions are real.
"""
import hashlib
import hmac
import json
import os
import time
from contextlib import ExitStack
from unittest.mock import patch
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

    def widget_login(page, person):
        data = {"id": people[person][1], "first_name": people[person][2],
                "last_name": "Тест " + uuid4().hex[:8], "auth_date": int(time.time())}
        checked = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        data["hash"] = hmac.new(hashlib.sha256(fixture_bot.encode()).digest(),
                                checked.encode(), hashlib.sha256).hexdigest()
        page.evaluate("data => window.onTelegramAuth(data)", data)

    def visit(item=None, *, person=0, cabinet_token=None, anonymous=False, query=None,
              mini=False, before=None, width=390):
        context = browser.new_context(viewport={"width": width, "height": 844}, reduced_motion="reduce")
        contexts.append(context)
        if not anonymous:
            context.add_init_script("sessionStorage.setItem('pushok-session'," +
                                    json.dumps(cabinet_token or sessions[person]) + ")")
        if mini:
            raw = signed_data(token=fixture_bot, query_id="flow-ui-" + uuid4().hex,
                              user=json.dumps({"id": people[person][1], "first_name": people[person][2]}))
            context.add_init_script("""window.Telegram={WebApp:{
                initData:RAW,initDataUnsafe:{user:{id:1,first_name:'Untrusted'}},
                colorScheme:'dark',ready(){},expand(){},onEvent(){},
                BackButton:{show(){},hide(){},onClick(){}}
            }};""".replace("RAW", json.dumps(raw)))
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

    def public_only(page, item):
        assert "flow=" not in page.url
        assert page.evaluate("!new URL(location.href).searchParams.has('flow')")
        stored = page.evaluate("JSON.stringify([Object.entries(sessionStorage),Object.entries(localStorage)])")
        assert item["user_code"] not in stored
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
            public_only(page, first)

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
            public_only(lost_page, lost)
            assert not failures, failures
        finally:
            for context in reversed(contexts):
                for page in context.pages:
                    page.unroute_all(behavior="ignoreErrors")
                context.close()
