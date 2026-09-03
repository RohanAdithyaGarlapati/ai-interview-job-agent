"""Step 1: pull company name + LinkedIn company slug from a public LinkedIn job posting.

LinkedIn serves an unauthenticated "guest" version of /jobs/view/<id> pages that is
fully readable without login (confirmed by hand). We rely only on that guest HTML,
never on the authenticated API, so this keeps working without a LinkedIn account.
"""
from __future__ import annotations

import re
import time
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


JSONLD_COUNTRY_RE = re.compile(r'"addressCountry"\s*:\s*"([^"]{2,60})"')

# LinkedIn serves the job title in two shapes, and the location sits in a
# different place in each:
#   "<Company> hiring <Job Title> in <Location> | LinkedIn"
#   "<Job Title> at <Company> · <Location> | LinkedIn Jobs"
# The separator in the second is a middle dot, which arrives mojibaked often
# enough that it is matched as "some non-word run" rather than a literal.
TITLE_LOCATION_RES = [
    re.compile(r"\sin\s+([^<|]{2,60}?)\s*\|\s*LinkedIn", re.I),
    re.compile(r"\sat\s+[^<|]{1,80}?[^\w\s|<]\s*([^<|]{2,60}?)\s*\|\s*LinkedIn", re.I),
]


@dataclass
class LinkedInJob:
    job_url: str
    job_title: str | None
    company_name: str | None
    company_slug: str | None
    company_li_url: str | None
    location: str | None = None


class LinkedInFetchError(RuntimeError):
    pass


RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def fetch_linkedin_job(job_url: str, timeout: int = 15, attempts: int = 4) -> LinkedInJob:
    """Fetch a LinkedIn guest job page, retrying through rate limits.

    LinkedIn throttles shared datacenter IPs far harder than residential ones,
    so a deployed copy sees intermittent 429s that never appear when running
    from a laptop. The throttling is per-burst rather than a standing block -
    waiting a moment and retrying gets through - so 429 and 5xx are retried
    while everything else fails immediately, no amount of waiting fixing a 404.
    """
    resp = None
    for attempt in range(attempts):
        resp = requests.get(job_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 or resp.status_code not in RETRYABLE_STATUS:
            break
        if attempt < attempts - 1:
            retry_after = (resp.headers.get("Retry-After") or "").strip()
            delay = float(retry_after) if retry_after.isdigit() else 1.5 * (2**attempt)
            time.sleep(min(delay, 12.0))

    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "no response"
        hint = (
            " - LinkedIn is rate-limiting this server's IP; trying again shortly usually works"
            if code == 429
            else ""
        )
        raise LinkedInFetchError(f"LinkedIn returned HTTP {code} for {job_url}{hint}")

    # LinkedIn's guest pages are UTF-8 but do not always say so in the header,
    # and requests then falls back to latin-1 - which mangles the middle dot the
    # title uses to separate company from location.
    resp.encoding = resp.encoding or "utf-8"
    if (resp.encoding or "").lower() in ("iso-8859-1", "latin-1"):
        resp.encoding = "utf-8"
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

    # Location disambiguates companies that share a name. LinkedIn exposes it as
    # a JSON-LD addressCountry, and failing that the page <title> ends with
    # "... in <Location> | LinkedIn".
    location = None
    loc_m = JSONLD_COUNTRY_RE.search(html)
    if loc_m:
        location = loc_m.group(1).strip()
    else:
        title_m = OG_TITLE_RE.search(html) or re.search(r"<title>([^<]+)</title>", html, re.I)
        if title_m:
            for pat in TITLE_LOCATION_RES:
                t_loc = pat.search(title_m.group(1))
                if t_loc:
                    location = t_loc.group(1).strip()
                    break

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
        location=location,
    )
