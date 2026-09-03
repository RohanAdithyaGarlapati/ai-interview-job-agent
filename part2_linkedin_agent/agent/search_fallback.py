"""Fallback for when the company isn't in Clearbit's free autocomplete index at all
(confirmed gap: obscure/small companies like "Deeter Analytics" return zero results).

Renders a real search-engine results page with Playwright (a plain `requests` GET to
Bing/Google gets blocked; a real rendered browser is not) and reads the organic
result links. This is often even better than the Clearbit path: a "<company> careers"
search frequently returns the ATS listing page directly (e.g. jobs.ashbyhq.com/deeter-analytics)
with no need to separately crawl a homepage for a careers link at all.
"""
from __future__ import annotations

import base64
import re
from urllib.parse import urlparse, parse_qs

from .browser import page as browser_page
from .ats_crawler import ATS_RULES, ATS_SLUG_BLOCKLIST


def _decode_bing_redirect(href: str) -> str | None:
    """Bing wraps each result in a https://www.bing.com/ck/a?...&u=a1<base64>&...
    tracking redirect instead of a plain href. Decode it back to the real URL."""
    if not href:
        return None
    if "bing.com/ck/a" not in href:
        return href if href.startswith("http") else None
    qs = parse_qs(urlparse(href).query)
    payload = (qs.get("u") or [None])[0]
    if not payload:
        return None
    if payload.startswith("a1"):
        payload = payload[2:]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        return None


def _render_search_result_links(query: str, timeout_ms: int = 20000) -> list[str]:
    url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
    raw_links: list[str] = []
    with browser_page(timeout_ms) as page:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            raw_links = page.eval_on_selector_all(
                "#b_results a", "els => els.map(e => e.href)"
            )
        except Exception:
            pass

    seen = set()
    out = []
    for raw in raw_links:
        link = _decode_bing_redirect(raw)
        if link and link.startswith("http") and link not in seen:
            seen.add(link)
            out.append(link)
    return out


def _normalize_tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 2}


def find_via_search(company_name: str) -> tuple[str | None, str | None, str | None, str]:
    """Search "<company> careers" and see if a result already IS a known ATS
    listing page (e.g. jobs.ashbyhq.com/deeter-analytics).

    Returns (direct_ats_url, ats_name, None, evidence). The third slot is kept
    for API stability but is always None: an earlier version also fell back to
    "the top non-job-board result is probably the company's own site" and used
    that to seed the normal career-page crawl. That produced confidently wrong
    answers for obscure/generic company names -- e.g. searching "Good AI
    careers" surfaced a dictionary page, and the crawler then dutifully found
    *that site's* real careers page. A clean "couldn't resolve" is better than
    a wrong answer that looks legitimate, so that path was removed; only an
    unambiguous direct ATS match is trusted.
    """
    # Two phrasings, not four. A previous version also tried "<name> hiring" and a
    # quoted-exact variant; measured over the 20-URL sample those two recovered
    # exactly zero additional companies while adding a browser render each to
    # every failing lookup - roughly 20s of pure cost per miss. Cheap breadth is
    # worth having, breadth that never pays out is not.
    queries = [
        f"{company_name} careers",
        f"{company_name} jobs",
    ]

    name_tokens = _normalize_tokens(company_name)

    for query in queries:
        links = _render_search_result_links(query)

        for link in links:
            for rule in ATS_RULES:
                m = rule.pattern.search(link)
                if not m:
                    continue
                gd = m.groupdict()
                slug = gd.get("slug") or gd.get("sub") or ""
                if slug.lower() in ATS_SLUG_BLOCKLIST:
                    continue
                # Sanity check: the matched ATS org slug should share at least one
                # meaningful token with the company name, so a coincidental ATS-shaped
                # link for an unrelated company doesn't get accepted.
                slug_tokens = _normalize_tokens(slug.replace("-", " ").replace("_", " "))
                if name_tokens and slug_tokens and not (name_tokens & slug_tokens):
                    continue
                return rule.canonical(m), rule.name, None, f"web search result matched {rule.name}"

    return None, None, None, "web search found no direct ATS listing link"
