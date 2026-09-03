"""Lever application auto-fill demo.

Scope, on purpose: this script fills every visible field on a Lever "apply" page
(standard fields + a company's custom questions + resume upload) using Playwright,
then STOPS before the final submit click.

It does not attempt to solve or bypass the hCaptcha challenge that gates Lever's
submit button (confirmed present on this posting: the form ships a hidden
`h-captcha-response` input and two hCaptcha checkbox widgets). Defeating CAPTCHA /
bot-detection is out of scope for this build regardless of how the request is
framed -- see README.md for the reasoning and for legitimate alternatives
(human-in-the-loop handoff, official ATS partner APIs).

Usage:
    python fill_application.py <lever_apply_url> [--profile profile.json] [--headed]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, Locator

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# (regex to match against the question's label text, profile.json key, kind)
# First matching rule wins. "kind" tells the filler how to interpret the value:
#   text     -> type into a text/textarea input
#   select   -> choose an <option> whose visible text matches the value
#   radio    -> click the radio button whose label text matches the value
QUESTION_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"notice period", re.I), "notice_period", "select"),
    (re.compile(r"start date", re.I), "start_month", "select"),
    (re.compile(r"expected salary", re.I), "expected_salary_range", "text"),
    (re.compile(r"how did you hear about", re.I), "how_heard", "select"),
    (re.compile(r"require a visa", re.I), "visa_status_label", "radio"),
    (re.compile(r"visa are you on", re.I), "visa_details", "text"),
    (re.compile(r"office 3x a week|open to working out of", re.I), "open_to_nyc_office", "radio"),
    (re.compile(r"preferred coding language", re.I), "preferred_coding_language", "radio"),
    (re.compile(r"^gender$", re.I), "dei_gender", "select"),
    (re.compile(r"^ethnicity$", re.I), "dei_ethnicity", "select"),
    (re.compile(r"^age bracket$", re.I), "dei_age_bracket", "select"),
    (re.compile(r"where did you hear about", re.I), "dei_how_heard", "select"),
]


def load_profile(path: str) -> dict:
    return json.loads(Path(path).read_text())


def fill_fixed_fields(page: Page, profile: dict, base_dir: Path):
    page.fill('input[name="name"]', profile["full_name"])
    page.fill('input[name="email"]', profile["email"])
    page.fill('input[name="phone"]', profile["phone"])

    resume_path = base_dir / profile["resume_path"]
    page.set_input_files('input[name="resume"]', str(resume_path))

    loc = page.locator('input[name="location"]')
    loc.click()
    loc.fill(profile["current_location"])
    page.wait_for_timeout(1200)
    suggestion = page.locator(".suggestion, .pac-item, li[role='option']").first
    if suggestion.count() and suggestion.is_visible():
        suggestion.click()
    else:
        page.keyboard.press("Escape")

    org = page.locator('input[name="org"]')
    if org.count():
        org.fill(profile["current_company"])


def _select_by_text(select_locator: Locator, wanted_text: str) -> bool:
    options = select_locator.locator("option").all_inner_texts()
    for opt in options:
        if opt.strip().lower() == wanted_text.strip().lower():
            select_locator.select_option(label=opt)
            return True
    return False


def _click_radio_by_label(question_li: Locator, wanted_text: str) -> bool:
    labels = question_li.locator("label")
    for i in range(labels.count()):
        label = labels.nth(i)
        text = label.inner_text().strip()
        if wanted_text.strip().lower() in text.lower() or text.lower() in wanted_text.strip().lower():
            label.locator("input[type=radio]").check(force=True)
            return True
    return False


def fill_custom_questions(page: Page, profile: dict) -> list[str]:
    unmatched = []
    questions = page.locator("li.application-question")
    count = questions.count()
    for i in range(count):
        question = questions.nth(i)
        label_el = question.locator(".application-label").first
        if not label_el.count():
            continue
        label_text = label_el.inner_text().strip()
        # Lever appends a "required" marker glyph on its own line after the label.
        primary_label = label_text.splitlines()[0].strip() if label_text else ""
        fixed_field_labels = {
            "linkedin profile", "resume/cv", "full name", "email", "phone",
            "current location", "current company",
        }
        if not primary_label or primary_label.lower() in fixed_field_labels:
            continue  # handled separately by fill_fixed_fields(), or N/A
        if len(primary_label) > 200:
            continue  # long intro/disclaimer text, not an actual question

        field = question.locator(".application-field")
        if not field.locator("input, select, textarea").count():
            continue  # purely informational text (e.g. the DEI intro paragraph), no input to fill

        matched = False
        for pattern, profile_key, kind in QUESTION_RULES:
            if not pattern.search(label_text):
                continue
            value = profile.get(profile_key)
            if value is None:
                continue
            field = question.locator(".application-field")
            if kind == "select":
                select = field.locator("select").first
                if select.count():
                    matched = _select_by_text(select, value)
            elif kind == "text":
                inp = field.locator("input[type=text], textarea").first
                if inp.count():
                    inp.fill(value)
                    matched = True
            elif kind == "radio":
                matched = _click_radio_by_label(question, value)
            break

        if not matched:
            # Not a fatal error -- just means this posting asked something our
            # rule table doesn't know about yet. Report it so a human can extend
            # QUESTION_RULES or answer it manually.
            unmatched.append(label_text)

    return unmatched


def check_consent(page: Page, profile: dict):
    if not profile.get("consent_data_retention"):
        return
    checkbox = page.locator('input[type=checkbox][name*="consent"]').first
    if checkbox.count():
        checkbox.check(force=True)


def detect_captcha(page: Page) -> bool:
    return page.locator("iframe[src*='hcaptcha'], iframe[title*='hCaptcha'], div.h-captcha").count() > 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("apply_url")
    parser.add_argument("--profile", default="profile.json")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--screenshot", default="filled_form.png")
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    profile = load_profile(str(base_dir / args.profile) if not Path(args.profile).is_absolute() else args.profile)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(args.apply_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Dismiss cookie banner if present.
        dismiss = page.locator("text=Dismiss").first
        if dismiss.count() and dismiss.is_visible():
            dismiss.click()

        fill_fixed_fields(page, profile, base_dir)
        unmatched = fill_custom_questions(page, profile)
        check_consent(page, profile)

        page.wait_for_timeout(500)
        page.screenshot(path=str(base_dir / args.screenshot), full_page=True)

        captcha_present = detect_captcha(page)

        print(f"Form filled. Screenshot saved to {args.screenshot}")
        if unmatched:
            print("\nQuestions this script did not know how to answer (extend QUESTION_RULES):")
            for q in unmatched:
                print(f"  - {q}")

        if captcha_present:
            print(
                "\nhCaptcha widget detected on the submit button. Stopping here on "
                "purpose -- this script fills the form but does not click Submit "
                "or attempt to solve/bypass the CAPTCHA. See README.md."
            )
        else:
            print(
                "\nNo CAPTCHA detected on this posting. Submit button was still NOT "
                "clicked -- this is a fill-only demo. Review the screenshot, then "
                "submit manually if you actually intend to apply."
            )

        browser.close()


if __name__ == "__main__":
    main()
