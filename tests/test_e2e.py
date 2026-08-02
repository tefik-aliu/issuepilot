"""Optional browser test.

Run the app first with `uvicorn app.main:app`, install a Playwright browser,
and then execute `pytest tests/test_e2e.py -m e2e`.
"""

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    os.getenv("RUN_E2E") != "1",
    reason="Set RUN_E2E=1 after starting the application locally.",
)
def test_user_can_create_and_resolve_issue(page: Page):
    page.goto(os.getenv("BASE_URL", "http://127.0.0.1:8000"))
    page.get_by_label("Title").fill("Search freezes after submit")
    page.get_by_label("Description").fill("The loading indicator never disappears.")
    page.get_by_label("Priority").select_option("critical")
    page.get_by_role("button", name="Create issue").click()

    card = page.locator(".issue-card", has_text="Search freezes after submit")
    expect(card).to_be_visible()
    card.get_by_label("Change issue status").select_option("resolved")
    expect(page.locator("#stat-resolved")).to_have_text("1")
