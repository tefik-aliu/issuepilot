"""Real-browser checks against isolated temporary databases and local servers."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from app.access import create_user
from app.db import initialise

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_ACCESS") != "1", reason="Opt-in browser suite"
)
PASSWORD = "isolated-browser-test-password"


@pytest.fixture(params=[False, True], ids=["demo", "private"])
def workspace(request, tmp_path):
    path = tmp_path / "browser.db"
    initialise(path)
    for role in ("admin", "editor", "viewer"):
        create_user(path, role, PASSWORD, role)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "ISSUEPILOT_DB": str(path),
        "ISSUEPILOT_AUTH_REQUIRED": "1" if request.param else "0",
        "ISSUEPILOT_COOKIE_SECURE": "0",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/api/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if process.poll() is not None:
                pytest.fail("Test server exited")
            time.sleep(0.1)
        else:
            pytest.fail("Test server did not start")
        yield url, request.param
    finally:
        process.terminate()
        process.wait(timeout=10)


def login(page, role):
    page.get_by_label("Username", exact=True).fill(role)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator("#access-status")).to_contain_text(f"{role} · {role}")


def test_browser_access_and_history(workspace):
    url, private = workspace
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PLAYWRIGHT_CHANNEL") or None
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            if private:
                expect(page.locator("main")).to_be_hidden()
                login(page, "editor")
            else:
                expect(page.locator("#access-status")).to_contain_text(
                    "Shared public demo"
                )
            page.get_by_label("Title", exact=True).fill("Browser history check")
            page.get_by_label("Description", exact=True).fill(
                '<img src=x onerror="window.injected=true">'
            )
            page.get_by_role("button", name="Create issue", exact=True).click()
            expect(page.locator(".issue-card")).to_have_count(1)
            page.get_by_label("Change issue status").select_option("resolved")
            expect(page.locator("#activity-list")).to_contain_text("updated")
            page.get_by_text("Change history", exact=True).click()
            expect(page.locator(".issue-history ol")).to_contain_text("open → resolved")
            expect(page.locator(".description")).to_contain_text("<img")
            assert page.evaluate("window.injected") is None
            if private:
                expect(
                    page.get_by_role("button", name="Delete", exact=True)
                ).to_be_hidden()
                page.get_by_role("button", name="Sign out").click()
                expect(page.locator("main")).to_be_hidden()
                login(page, "viewer")
                expect(page.locator(".form-panel")).to_be_hidden()
                expect(page.get_by_label("Change issue status")).to_be_disabled()
                expect(
                    page.get_by_role("button", name="Delete", exact=True)
                ).to_be_hidden()
                page.get_by_text("Change history", exact=True).click()
                expect(page.locator(".issue-history ol")).to_contain_text(
                    "open → resolved"
                )
                page.get_by_role("button", name="Sign out").click()
                login(page, "admin")
            for width in (375, 768, 1280):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
            screenshot_dir = os.getenv("BROWSER_SCREENSHOTS")
            if screenshot_dir:
                page.screenshot(
                    path=str(
                        Path(screenshot_dir)
                        / f"issuepilot-{'private' if private else 'demo'}.png"
                    ),
                    full_page=True,
                )
            if os.getenv("AXE_PATH"):
                page.add_script_tag(path=os.environ["AXE_PATH"])
                violations = page.evaluate(
                    "async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa']}})).violations"
                )
                assert violations == [], violations
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Delete", exact=True).click()
            expect(page.locator(".issue-card")).to_have_count(0)
            expect(page.locator("#activity-list")).to_contain_text("deleted")
            if private:
                page.get_by_role("button", name="Sign out").click()
                page.reload()
                expect(page.locator("#login-panel")).to_be_visible()
                expect(page.locator("main")).to_be_hidden()
            assert errors == []
        finally:
            browser.close()


def test_two_browsers_review_conflicts_before_retrying(workspace):
    url, private = workspace
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL') or None)
        first = browser.new_page(viewport={'width': 1280, 'height': 950})
        second = browser.new_page(viewport={'width': 1280, 'height': 950})
        try:
            first.goto(url)
            if private:
                login(first, 'editor')
            first.get_by_label('Title', exact=True).fill('Checkout timeout')
            first.get_by_label('Description', exact=True).fill('The payment request times out. Sample issue for a two-browser test.')
            first.get_by_role('button', name='Create issue', exact=True).click()
            expect(first.locator('.issue-card')).to_have_count(1)
            second.goto(url)
            if private:
                login(second, 'admin')
            expect(second.locator('.issue-card')).to_have_count(1)
            first.get_by_label('Change issue status').select_option('in_progress')
            expect(first.locator('#activity-list')).to_contain_text('open → in_progress')
            writes = []
            second.on('request', lambda r: writes.append(r.method) if r.method in ('PATCH', 'DELETE') else None)
            second.get_by_label('Change issue status').select_option('resolved')
            conflict = second.locator('.issue-conflict')
            expect(conflict).to_be_visible()
            expect(conflict).to_contain_text('Current status: in progress (version 2)')
            expect(conflict).to_contain_text('change to resolved was not saved')
            expect(second.get_by_label('Change issue status')).to_be_disabled()
            expect(second.get_by_role('button', name='Load latest issues')).to_be_focused()
            assert writes == ['PATCH']
            for width in (375, 768, 1280):
                second.set_viewport_size({'width': width, 'height': 950})
                assert second.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if os.getenv('AXE_PATH'):
                second.add_script_tag(path=os.environ['AXE_PATH'])
                assert second.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa']}})).violations") == []
            if os.getenv('BROWSER_SCREENSHOTS') and not private:
                directory = Path(os.environ['BROWSER_SCREENSHOTS'])
                second.screenshot(path=str(directory / 'issuepilot-conflict.png'), full_page=True)
                second.locator('.issues-panel').screenshot(path=str(directory / 'issuepilot-conflict-detail.png'))
            second.get_by_role('button', name='Load latest issues').press('Enter')
            expect(second.get_by_label('Change issue status')).to_have_value('in_progress')
            expect(second.get_by_label('Change issue status')).to_be_focused()
            assert writes == ['PATCH']  # Review alone must never resubmit the rejected edit.
            second.get_by_label('Change issue status').select_option('resolved')
            expect(second.locator('#activity-list')).to_contain_text('in_progress → resolved')
            assert writes == ['PATCH', 'PATCH']
            # The admin's now-stale delete must also stop, without losing the issue.
            first.get_by_role('button', name='Refresh', exact=True).click()
            expect(first.get_by_label('Change issue status')).to_have_value('resolved')
            first.get_by_label('Change issue status').select_option('open')
            expect(first.locator('#activity-list')).to_contain_text('resolved → open')
            second.on('dialog', lambda dialog: dialog.accept())
            second.get_by_role('button', name='Delete', exact=True).click()
            expect(conflict).to_contain_text('deletion was not saved')
            expect(second.locator('.issue-card')).to_have_count(1)
            second.get_by_role('button', name='Load latest issues').click()
            expect(second.get_by_label('Change issue status')).to_have_value('open')
            second.get_by_role('button', name='Delete', exact=True).click()
            expect(second.locator('.issue-card')).to_have_count(0)
        finally:
            browser.close()
