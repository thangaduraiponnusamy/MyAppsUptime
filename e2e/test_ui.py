from playwright.sync_api import expect

from conftest import call, sign_in


def test_sign_in(page, server):
    base, _ = server
    page.goto(f"{base}/login")
    page.locator("input[name=u]").fill("admin")
    page.locator("input[name=p]").fill("wrong-password")
    page.locator("button.btn.primary").click()
    expect(page.locator(".auth-error")).to_be_visible()
    sign_in(page, base)
    expect(page).to_have_url(f"{base}/")


def test_dashboard_lists_monitors(page, server):
    base, token = server
    call(base, "POST", "/api/monitors", {"name": "E2E dashboard", "type": "http", "target": f"{base}/health"}, token)
    sign_in(page, base)
    expect(page.locator("a.mrow", has_text="E2E dashboard")).to_be_visible()


def test_monitor_detail(page, server):
    base, token = server
    m = call(base, "POST", "/api/monitors", {"name": "E2E detail", "type": "http", "target": f"{base}/health"}, token)
    sign_in(page, base)
    page.locator("a.mrow", has_text="E2E detail").click()
    expect(page).to_have_url(f"{base}/monitors/{m['id']}")
    expect(page.get_by_role("heading", name="E2E detail")).to_be_visible()
    expect(page.get_by_role("button", name="Check now")).to_be_enabled()
    expect(page.get_by_role("heading", name="Response time")).to_be_visible()


PAGES = ["/", "/activity", "/incidents", "/maintenance", "/monitors/new", "/status-pages", "/settings/general",
         "/settings/notifications", "/settings/projects", "/settings/users", "/settings/tenants", "/settings/locations",
         "/settings/security", "/settings/audit", "/settings/backup", "/settings/about"]


def test_pages_render_without_errors(page, server):
    import os
    base, _ = server
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    sign_in(page, base)
    shots = os.environ.get("E2E_SHOTS")
    for path in PAGES:
        page.goto(f"{base}{path}")
        page.wait_for_timeout(800)
        expect(page.locator("main.main")).to_be_visible()
        if shots:
            page.screenshot(path=os.path.join(shots, path.strip("/").replace("/", "_") or "dashboard") + ".png", full_page=True)
    assert errors == []


def test_audit_log_has_sign_ins(page, server):
    base, _ = server
    sign_in(page, base)
    page.goto(f"{base}/settings/audit")
    page.locator(".toolbar app-select .ms-box").click()
    page.locator(".ms-row", has_text="Sign-ins & account").click()
    expect(page.locator("td", has_text="signed in").first).to_be_visible()
