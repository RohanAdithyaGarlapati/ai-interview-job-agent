"""Steps 3 & 4: company website -> career page -> the actual ATS job-listing page.

Generic technique (works across arbitrary company site designs, not hardcoded per
company):
  1. Try to locate a "careers" link on the homepage (static HTML first, since most
     sites still server-render their nav; a handful of common URL paths are probed
     as a fallback).
  2. Render that career page in a real (headless) browser with Playwright, because
     most modern career pages fetch their job list client-side via JS (confirmed:
     harvey.ai/careers renders nothing job-related in the raw HTML -- the list only
     appears after the browser executes a fetch() to /api/ashby/jobs).
  3. While rendering, record every network request URL, every JSON response body,
     and every anchor href in the final DOM. Regex-match all of that against a
     table of known ATS URL shapes (Greenhouse, Lever, Ashby, Workday, ...). This
     is how we catch cases like Harvey, where the ATS link never appears as a
     plain <a href> -- it only shows up inside a JSON payload the page fetched.
  4. If a known ATS is detected, return its canonical org-level listing URL
     (e.g. https://jobs.ashbyhq.com/harvey, not a single job's deep link).
  5. If no known ATS is detected, fall back to the career page itself -- many
     companies run their own in-house job board with no third-party ATS.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from .browser import page as browser_page
from .linkedin import HEADERS, USER_AGENT

CAREER_KEYWORDS = [
    "career", "careers", "jobs", "job openings", "open positions", "open roles",
    "join us", "join our team", "we're hiring", "were hiring", "work with us",
    "life at",
]

COMMON_CAREER_PATHS = [
    "/careers", "/careers/", "/jobs", "/jobs/", "/company/careers",
    "/about/careers", "/about-us/careers", "/en/careers", "/company/jobs",
    "/careers.html", "/join-us", "/hiring", "/careers-hub", "/culture",
    "/team", "/apply", "/positions", "/open-positions", "/working-here",
    "/join", "/recruitment", "/employment",
]

NAV_NOISE = ("linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com")

# Path segments that look like an org slug to a naive regex but are actually a
# static asset / API route of the ATS's own site (e.g. a company embedding
# Lever's widget loader script whose URL happens to be https://jobs.lever.co/js).
ATS_SLUG_BLOCKLIST = {
    "js", "css", "api", "static", "assets", "favicon.ico", "embed", "widget",
    "public", "img", "images", "fonts", "sitemap.xml", "robots.txt",
}


@dataclass
class AtsRule:
    name: str
    pattern: re.Pattern
    canonical: callable


def _gh(m):
    return f"https://{m.group('host')}/{m.group('slug')}"


ATS_RULES: list[AtsRule] = [
    AtsRule(
        "Greenhouse",
        re.compile(r"https?://(?P<host>job-boards\.greenhouse\.io|boards\.greenhouse\.io)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "Lever",
        re.compile(r"https?://(?P<host>jobs\.lever\.co)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "Ashby",
        re.compile(r"https?://(?P<host>jobs\.ashbyhq\.com)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "SmartRecruiters",
        re.compile(r"https?://(?P<host>careers\.smartrecruiters\.com)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "Workable",
        re.compile(r"https?://(?P<host>apply\.workable\.com)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "Jobvite",
        re.compile(r"https?://(?P<host>jobs\.jobvite\.com)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "BambooHR",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.bamboohr\.com/(?:careers|jobs)"),
        lambda m: f"https://{m.group('sub')}.bamboohr.com/careers",
    ),
    AtsRule(
        "Breezy",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.breezy\.hr"),
        lambda m: f"https://{m.group('sub')}.breezy.hr",
    ),
    AtsRule(
        "Recruitee",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.recruitee\.com"),
        lambda m: f"https://{m.group('sub')}.recruitee.com",
    ),
    AtsRule(
        "Teamtailor",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.teamtailor\.com"),
        lambda m: f"https://{m.group('sub')}.teamtailor.com",
    ),
    AtsRule(
        "iCIMS",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.icims\.com"),
        lambda m: f"https://{m.group('sub')}.icims.com",
    ),
    AtsRule(
        "Workday",
        re.compile(
            r"https?://(?P<host>[a-zA-Z0-9\-]+\.wd\d*\.myworkdayjobs\.com)/(?P<path>[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)?)"
        ),
        lambda m: f"https://{m.group('host')}/{m.group('path')}",
    ),
    AtsRule(
        "Workday (wd4/wd5 variants)",
        re.compile(r"https?://(?P<host>[a-zA-Z0-9\-]+\.wd[45]\.myworkdayjobs\.(?:com|eu))/"),
        lambda m: f"https://{m.group('host')}/",
    ),
    AtsRule(
        "Rippling ATS",
        re.compile(r"https?://(?P<host>ats\.rippling\.com)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "Pinpoint",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.pinpointhq\.com"),
        lambda m: f"https://{m.group('sub')}.pinpointhq.com",
    ),
    AtsRule(
        "Personio",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.jobs\.personio\.(?:com|de)"),
        lambda m: m.group(0),
    ),
    AtsRule(
        "Jazz (Quicken Loans/Intuit)",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.jazz\.co"),
        lambda m: f"https://{m.group('sub')}.jazz.co",
    ),
    AtsRule(
        "Cornerstone OnDemand",
        re.compile(r"https?://(?P<sub>[a-zA-Z0-9\-]+)\.csod\.com"),
        lambda m: f"https://{m.group('sub')}.csod.com",
    ),
    AtsRule(
        "Talentdesk",
        re.compile(r"https?://(?P<host>jobs\.talentdesk\.io)/(?P<slug>[a-zA-Z0-9_.\-]+)"),
        _gh,
    ),
    AtsRule(
        "Lever (alt domain)",
        re.compile(r"https?://jobs-(?P<slug>[a-zA-Z0-9\-]+)\.lever\.co"),
        lambda m: f"https://jobs.lever.co/{m.group('slug')}",
    ),
]


@dataclass
class CrawlResult:
    career_page: str | None = None
    final_url: str | None = None
    ats_detected: str | None = None
    method: str = ""
    evidence: str = ""
    warnings: list[str] = field(default_factory=list)


def _score_links(links: list[tuple[str, str]], home: str) -> str | None:
    """links: list of (href, visible_text). Returns best resolved career URL, if any."""
    scored: list[tuple[int, str]] = []
    for href, text in links:
        if not href:
            continue
        text = (text or "").strip().lower()
        hay = f"{href.lower()} {text}"
        if any(n in hay for n in NAV_NOISE):
            continue
        for kw in CAREER_KEYWORDS:
            if kw in hay:
                score = 10 if kw in text else 5
                if href.strip("/").rstrip("/").split("/")[-1].lower() in ("careers", "jobs"):
                    score += 10
                scored.append((score, href))
                break
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    best_href = scored[0][1]
    return best_href if best_href.startswith("http") else requests.compat.urljoin(home, best_href)


def _render_homepage_links(home: str, timeout_ms: int = 20000) -> tuple[list[tuple[str, str]], str]:
    """Loads the homepage in a real browser and returns (links, final_url).
    Used as a fallback when a plain `requests` fetch is blocked (403/etc) --
    some corporate sites front their homepage with bot-detection (e.g. Akamai)
    that a real browser passes but a bare HTTP client does not."""
    with browser_page(timeout_ms) as page:
        try:
            page.goto(home, wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            try:
                page.goto(home, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass
        final_url = page.url
        try:
            links = page.eval_on_selector_all(
                "a", "els => els.map(e => [e.href, e.innerText || e.textContent || ''])"
            )
        except Exception:
            links = []
    return [(h, t) for h, t in links], final_url


def find_career_page(domain: str, timeout: int = 12) -> tuple[str | None, str]:
    """Returns (career_page_url, method_description)."""
    home = f"https://{domain}"
    blocked = False

    # Try both with and without www for initial fetch
    home_variants = [home]
    if not domain.startswith("www."):
        home_variants.append(f"https://www.{domain}")

    for home_attempt in home_variants:
        try:
            resp = requests.get(home_attempt, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if resp.status_code < 400:
                home = resp.url  # follow any redirect (e.g. to www.)
                soup = BeautifulSoup(resp.text, "lxml")
                links = [(a["href"], a.get_text()) for a in soup.find_all("a", href=True)]
                found = _score_links(links, home)
                if found:
                    return found, "found careers link in homepage HTML"
                blocked = False
                break
            else:
                blocked = True
        except requests.RequestException:
            blocked = True

    if blocked:
        # Plain HTTP fetch was blocked -- retry with a real rendered browser.
        try:
            links, final_home = _render_homepage_links(home)
            found = _score_links(links, final_home)
            if found:
                return found, "found careers link via rendered homepage (plain fetch was blocked)"
            home = final_home
        except Exception:
            pass

    # Fallback: probe the common paths. Done concurrently because these are ~22
    # independent HEAD requests against one host, and sequentially they dominated
    # every failed lookup - a domain with no careers link in its homepage HTML
    # spent about 28s here, mostly waiting on timeouts for paths that do not
    # exist. The probes still resolve in list order: COMMON_CAREER_PATHS is a
    # priority list (/careers before /team), so the earliest hit wins regardless
    # of which request happened to return first.
    def _probe(path: str) -> str | None:
        url = requests.compat.urljoin(home, path)
        for attempt in range(2):  # one retry, for transient timeouts only
            try:
                r = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
                if r.status_code == 405:  # some servers reject HEAD outright
                    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
                return r.url if r.status_code < 400 else None
            except requests.Timeout:
                if attempt == 1:
                    return None
            except requests.RequestException:
                return None
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        hits = list(pool.map(_probe, COMMON_CAREER_PATHS))

    for path, hit in zip(COMMON_CAREER_PATHS, hits):
        if hit:
            return hit, f"guessed common path {path}"

    return None, "no careers page found"


# Resource types this crawler never reads. It looks at request URLs, JSON bodies
# and anchors - never a pixel - so fetching hero images, video and webfonts is
# pure latency. Stylesheets are deliberately still allowed: some career pages
# only render their job list once CSS has settled, and blocking them changed
# what the DOM contained.
_SKIP_RESOURCE_TYPES = {"image", "media", "font"}

# Once a known ATS URL has been seen there is nothing left to wait for, so the
# render stops early instead of sitting out a fixed delay.
_EARLY_EXIT_POLL_MS = 100
_MAX_SETTLE_MS = 1500


def _render_and_collect(url: str, timeout_ms: int = 25000) -> dict:
    urls_seen: set[str] = set()
    bodies: list[str] = []
    anchors: list[str] = []
    html = ""
    found_ats = False

    def _is_ats(candidate: str) -> bool:
        for rule in ATS_RULES:
            m = rule.pattern.search(candidate)
            if not m:
                continue
            gd = m.groupdict()
            slug = (gd.get("slug") or gd.get("sub") or "").lower()
            if slug not in ATS_SLUG_BLOCKLIST:
                return True
        return False

    with browser_page(timeout_ms) as page:
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in _SKIP_RESOURCE_TYPES
                else route.continue_()
            ),
        )

        def on_response(response):
            nonlocal found_ats
            try:
                urls_seen.add(response.url)
                if _is_ats(response.url):
                    found_ats = True
                ctype = response.headers.get("content-type", "")
                if ("json" in ctype) and int(response.headers.get("content-length", "0") or 0) < 400_000:
                    body = response.text()
                    bodies.append(body)
                    if _is_ats(body):
                        found_ats = True
            except Exception:
                pass

        page.on("response", on_response)
        try:
            # domcontentloaded, not networkidle. networkidle waits for 500ms of
            # total network silence, which analytics beacons and polling widgets
            # can hold off for many seconds on a page that was usable almost
            # immediately. The settle loop below covers the late XHR that
            # actually matters here.
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass

        try:
            waited = 0
            while waited < _MAX_SETTLE_MS and not found_ats:
                page.wait_for_timeout(_EARLY_EXIT_POLL_MS)
                waited += _EARLY_EXIT_POLL_MS
            html = page.content()
            anchors = page.eval_on_selector_all("a", "els => els.map(e => e.href)")
        except Exception:
            pass

    return {"urls": urls_seen, "bodies": bodies, "anchors": anchors, "html": html}


def find_ats_listing(career_url: str) -> tuple[str | None, str | None, str]:
    """Renders the career page and looks for a known ATS. Returns
    (listing_url, ats_name, evidence_description)."""
    collected = _render_and_collect(career_url)
    haystacks = [
        ("network request", u) for u in collected["urls"]
    ] + [
        ("page link", u) for u in collected["anchors"] if u
    ] + [
        ("embedded JSON response", b) for b in collected["bodies"]
    ]

    for rule in ATS_RULES:
        for source, text in haystacks:
            for m in rule.pattern.finditer(text):
                gd = m.groupdict()
                slug = gd.get("slug") or gd.get("sub") or ""
                if slug.lower() in ATS_SLUG_BLOCKLIST:
                    continue
                return rule.canonical(m), rule.name, f"{rule.name} URL found in {source}"

    return None, None, "rendered page + network traffic; no known ATS pattern matched"
