"""Step 1: pull company name + LinkedIn company slug from a public LinkedIn job posting.

LinkedIn serves an unauthenticated "guest" version of /jobs/view/<id> pages that is
fully readable without login (confirmed by hand). We rely only on that guest HTML,
never on the authenticated API, so this keeps working without a LinkedIn account.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

ORG_NAME_RE = re.compile(
    r'class="topcard__org-name-link[^"]*"[^>]*href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.S,
)
OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"')
COMPANY_SLUG_RE = re.compile(r"linkedin\.com/company/([a-zA-Z0-9\-%.]+)")


@dataclass
class LinkedInJob:
    job_url: str
    job_title: str | None
    company_name: str | None
    company_slug: str | None
    company_li_url: str | None


class LinkedInFetchError(RuntimeError):
    pass


def fetch_linkedin_job(job_url: str, timeout: int = 15) -> LinkedInJob:
    resp = requests.get(job_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    if resp.status_code != 200:
        raise LinkedInFetchError(f"LinkedIn returned HTTP {resp.status_code} for {job_url}")

    html = resp.text

    m = ORG_NAME_RE.search(html)
    company_name = None
    company_li_url = None
    if m:
        company_li_url = m.group(1).split("?")[0]
        company_name = m.group(2).strip()

    company_slug = None
    slug_m = COMPANY_SLUG_RE.search(company_li_url or html)
    if slug_m:
        company_slug = slug_m.group(1)

    job_title = None
    title_m = OG_TITLE_RE.search(html)
    if title_m:
        # og:title looks like "<Job Title> at <Company> — <Location>"
        raw = title_m.group(1).strip()
        job_title = raw.split(" at ")[0].strip() if " at " in raw else raw

    if not company_name:
        raise LinkedInFetchError(
            f"Could not find company name on guest job page {job_url} "
            "(page layout may have changed, or job requires login to view)"
        )

    return LinkedInJob(
        job_url=job_url,
        job_title=job_title,
        company_name=company_name,
        company_slug=company_slug,
        company_li_url=company_li_url,
    )
