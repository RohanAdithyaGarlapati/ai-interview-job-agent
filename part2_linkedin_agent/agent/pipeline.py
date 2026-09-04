from __future__ import annotations

from dataclasses import dataclass, asdict

from .linkedin import fetch_linkedin_job, LinkedInFetchError
from .domain_resolver import resolve_company_domain
from .ats_crawler import find_career_page, find_ats_listing, ATS_RULES, ATS_SLUG_BLOCKLIST
from .search_fallback import find_via_search


def _direct_ats_match(url: str):
    """If `url` already looks like a known ATS listing URL, return (canonical_url, ats_name)."""
    for rule in ATS_RULES:
        m = rule.pattern.search(url)
        if not m:
            continue
        gd = m.groupdict()
        slug = gd.get("slug") or gd.get("sub") or ""
        if slug.lower() in ATS_SLUG_BLOCKLIST:
            continue
        return rule.canonical(m), rule.name
    return None, None


@dataclass
class JobSourceResult:
    linkedin_url: str
    success: bool
    company_name: str | None = None
    job_title: str | None = None
    domain: str | None = None
    career_page: str | None = None
    final_url: str | None = None
    ats_detected: str | None = None
    method: str | None = None
    error: str | None = None

    def to_dict(self):
        return asdict(self)


def resolve_job_source(linkedin_url: str) -> JobSourceResult:
    result = JobSourceResult(linkedin_url=linkedin_url, success=False)

    try:
        job = fetch_linkedin_job(linkedin_url)
    except LinkedInFetchError as e:
        result.error = f"LinkedIn fetch failed: {e}"
        return result

    result.company_name = job.company_name
    result.job_title = job.job_title

    domain_result = resolve_company_domain(
        job.company_name, job.company_slug, location=job.location
    )
    domain = domain_result.domain

    if not domain:
        # Not in Clearbit's free index at all (common for smaller/newer companies).
        # Fall back to a real search-engine query for "<company> careers" -- this
        # only ever returns a result when a hit unambiguously IS a known ATS's
        # listing URL for this company (see search_fallback.py for why the fuzzier
        # "just guess the top result is their homepage" version of this was removed).
        direct_url, direct_ats, _, evidence = find_via_search(job.company_name)
        if not direct_url:
            # Bing occasionally blocks/rate-limits a request transiently; retry once.
            direct_url, direct_ats, _, evidence = find_via_search(job.company_name)
        if direct_url:
            result.final_url = direct_url
            result.ats_detected = direct_ats
            result.method = evidence
            result.success = True
            return result
        result.error = (
            f"Could not resolve official domain for company '{job.company_name}' "
            f"(Clearbit had nothing, and {evidence})"
        )
        return result

    result.domain = domain

    try:
        career_page, career_method = find_career_page(domain)
    except Exception as e:
        result.error = f"Error finding career page: {e}"
        return result

    if not career_page:
        result.error = f"Could not find a careers page on {domain}"
        return result
    result.career_page = career_page

    # If the "careers" link found on the homepage already points straight at a
    # known ATS (common case: the nav link goes directly to boards.greenhouse.io/x
    # or jobs.lever.co/x), we're done -- no need to render+crawl further.
    direct_url, direct_ats = _direct_ats_match(career_page)
    if direct_url:
        result.final_url = direct_url
        result.ats_detected = direct_ats
        result.method = f"{career_method} -> careers link is already the {direct_ats} listing page"
        result.success = True
        return result

    try:
        # Rendered once, not twice. This used to retry whenever no ATS was found,
        # on the grounds that a cold browser launch could miss late JS/XHR - but
        # agent/browser.py now keeps one warm browser for the process, so there
        # is no cold start to lose a request to. Meanwhile the retry fired on
        # exactly the case it could never help: a company that genuinely runs its
        # own job board has no ATS link to find, so the second render was
        # guaranteed to come back empty, having doubled the slowest step in the
        # pipeline for every such company.
        listing_url, ats_name, evidence = find_ats_listing(career_page)
    except Exception as e:
        # Browser rendering failed -- still report the career page as a partial success.
        result.final_url = career_page
        result.method = f"{career_method}; ATS detection failed ({e}), returning career page"
        result.success = True
        return result

    if listing_url:
        result.final_url = listing_url
        result.ats_detected = ats_name
        result.method = f"{career_method} -> rendered page -> {evidence}"
    else:
        result.final_url = career_page
        result.method = f"{career_method} -> no third-party ATS detected, career page is the listing page"

    result.success = True
    return result
