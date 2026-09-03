# LinkedIn Job Source Agent

Given a LinkedIn job posting URL, resolves it down to the actual job-listing
page on the hiring company's own site (a Greenhouse/Lever/Ashby/Workday/etc.
board, or their in-house careers page).

Example: `https://www.linkedin.com/jobs/view/4427787182/` -> `https://jobs.ashbyhq.com/harvey`

## Pipeline

1. **`agent/linkedin.py`** -- fetch the LinkedIn job posting as an unauthenticated
   "guest" page (no login/API key needed; confirmed to work reliably) and pull
   the company name + LinkedIn company slug out of the HTML.
2. **`agent/domain_resolver.py`** -- resolve the company name to its official
   website domain via Clearbit's free, keyless autocomplete endpoint. A bare
   name is often ambiguous ("Harvey" collides with Harvey Norman / Harvey
   Nichols), so we also query a disambiguated string built from the LinkedIn
   slug (`harvey-ai` -> `"Harvey ai"`), which resolves correctly in practice.
3. **`agent/ats_crawler.py`** step A (`find_career_page`) -- fetch the
   company's homepage and look for a "careers"/"jobs" nav link (plain HTTP
   first; falls back to rendering with a headless browser if the plain fetch
   is blocked, since some corporate sites front their homepage with bot
   detection that only a real browser passes).
4. **`agent/ats_crawler.py`** step B (`find_ats_listing`) -- if the careers
   link doesn't already point straight at a known ATS, render the careers page
   with Playwright and inspect *everything* that happens during load: every
   network request URL, every JSON response body, and every rendered `<a>`
   href. This catches ATS integrations that never appear as a plain link --
   e.g. harvey.ai/careers renders nothing job-related in its raw HTML; the
   Ashby job list only appears after the browser runs a `fetch()` to
   `/api/ashby/jobs`, and the real `jobs.ashbyhq.com/harvey` URL is buried
   inside that JSON response, not in an `<a href>`.
5. A table of ~15 known ATS URL shapes (Greenhouse, Lever, Ashby, Workday,
   SmartRecruiters, Workable, BambooHR, iCIMS, Jobvite, Breezy, Recruitee,
   Teamtailor, Rippling, Pinpoint, Personio) turns whatever matched into the
   canonical org-level listing URL, not a single job's deep link.
6. If no known ATS is found, the careers page itself is returned -- many
   companies run their own in-house job board with no third-party ATS at all,
   and that page genuinely is the correct answer in that case.
7. **`agent/search_fallback.py`** -- if step 2 can't resolve a domain at all
   (the company just isn't in Clearbit's free index -- common for smaller
   startups), render a real "`<company> careers`" search on Bing (a plain
   `requests` GET gets blocked; Playwright isn't) and check whether any result
   already IS a known ATS listing URL. This is deliberately narrow: an earlier
   version also treated "the top non-job-board result" as the company's own
   homepage and fed that into steps 3-4, which produced confident wrong
   answers for generic/ambiguous names (searching "Good AI careers" surfaced
   a dictionary page; the crawler then dutifully found *that site's* real
   careers page). That path was removed -- a clean "couldn't resolve" beats a
   wrong answer that looks legitimate.

Nothing above needs a LinkedIn account, a paid scraping API, or a browser
extension. It's plain Python + `requests`/`BeautifulSoup` for the cheap paths
and Playwright only where JS rendering (or evading a plain-HTTP block) is
actually required.

## Running it

```bash
pip install -r requirements.txt
playwright install chromium

python cli.py "https://www.linkedin.com/jobs/view/4427787182/"
```

Web demo:

```bash
python webapp.py
# open http://localhost:8000
```

Batch evaluation (concurrent by default):

```bash
python test_batch.py test_urls.txt --workers 6
```

## Performance

Resolving one URL fans out into several headless-browser renders: the homepage,
the careers page, an ATS-detection retry, and up to two search-fallback queries.
Two changes took the 20-URL batch from ~460s to **89s**:

- **One shared browser instead of one per call** (`agent/browser.py`). Each call
  site used to open its own `sync_playwright()` context and launch its own
  Chromium. Measured, that is ~6.5s per call against ~0.19s to open a page on a
  browser already running - the launch cost dominated the actual work. The
  browser is now started once, lazily, and held in thread-local storage, since
  Playwright's sync API cannot be shared across threads.
- **Concurrent URLs** (`test_batch.py --workers`). The work is almost entirely
  waiting on network and renders, so threads overlap it well: 4.6x on the sample.

Accuracy is unchanged at 9/20 - these are pure throughput changes.

A third change cut work rather than parallelising it: the search fallback used to
try four query phrasings, and over this sample the extra two recovered exactly
zero additional companies while adding a browser render to every failing lookup.
It now tries two.

## Test results (20 real, randomly-sampled LinkedIn job postings)

Sampled live from LinkedIn's public job search (`/jobs/search?keywords=software+engineer`),
spanning household names (ByteDance, Cargill, Applied Materials) down to
obscure seed-stage startups, run with `python test_batch.py`. See
`test_results.json` for the full machine-readable output.

**9/20 succeeded outright end-to-end (45%), with zero known wrong answers.**
That number is deliberately precision-first, not the highest number I could
report -- getting here involved finding and removing two different fuzzy-match
shortcuts, each of which briefly inflated the raw hit count while quietly
producing wrong answers:

- The domain-matching step originally accepted a "starts with" fuzzy match on
  company name. That pushed the count to 10/20, but two hits were confidently
  wrong ("Aven", a fintech, matched "Aventon", an e-bike brand, because
  `"aventon".startswith("aven")`; "HoundDog" similarly matched a barbershop).
  Tightened to exact-match-only.
- The search-fallback step (added later, for companies absent from Clearbit
  entirely) originally also treated "the top non-job-board search result" as
  the company's own homepage. That pushed the count to 15/20, but several
  were wrong in the same way -- e.g. searching "Good AI careers" surfaced a
  dictionary page, "HoundDog careers" surfaced IMDb, and the crawler then
  dutifully found *that unrelated site's* real careers page. Narrowed to only
  trust a search result that IS already a known ATS listing URL.

For a product that's about to hand someone a job-application link, a
wrong-but-confident answer is worse than an honest "I couldn't find it," so
both shortcuts were removed even though each one looked like a win in
isolation. Every pass below is hand-checked against the real company.

Breaking down the 11 misses:

| Cause | Count | Fixable without a paid data source? |
|---|---|---|
| Company name has no exact match in Clearbit's free autocomplete index, and no unambiguous ATS link turns up in a "&lt;company&gt; careers" search either -- either genuinely obscure ("Recover Systems", "Pioneer Software Solutions", "Voyatek", "MetAntz") or a common word/phrase that just isn't Clearbit's top hit and doesn't have an ATS-shaped search result ("Good AI", "HoundDog", "Chalk") | 7 | Partially -- a paid company-enrichment API (Clearbit Enrichment, People Data Labs) or an authenticated LinkedIn company-page lookup would close most of this; a couple (e.g. "Chalk", a name that collides with an unrelated well-known word) would still need human disambiguation |
| Correct domain resolved, but the homepage itself blocks both a plain HTTP fetch *and* headless-browser rendering with enterprise bot-management (Cargill, Applied Materials) | 2 | Not without browser-fingerprint evasion, which is intentionally out of scope here (see Part 3's README for why) |
| Company's real domain resolved correctly, but a "careers" link genuinely couldn't be found on the rendered homepage ("Genius AI" -> aiwithgai.com, "Autopilot" -> an EU research project's site -- both likely wrong companies for these particular common-word LinkedIn postings in the first place) | 2 | These look like company-name ambiguity upstream of the crawl, same fix as row 1 |

Separately verified against a real-world case a reviewer tried live: an
obscure company entirely absent from Clearbit ("Deeter Analytics") resolved
correctly via the search fallback to `jobs.ashbyhq.com/deeter-analytics` --
outside this fixed 20-URL sample, but real evidence the fallback generalizes.

None of the 9 successes were flukes specific to one company's site layout --
they span 5 different ATS providers (Lever, Greenhouse, Ashby, iCIMS,
BambooHR) plus 2 in-house career pages, which is the actual point: the
pipeline is generic across site architectures, not hardcoded per company.

The bottleneck is almost entirely step 2 (company name -> domain), which is
also the only piece running on a free, keyless data source. Swap it for a
paid company-enrichment API and I'd expect most of the 9 name-resolution
misses to clear, pushing this toward 80-90% on a sample like this one --
steps 3 and 4 (career-page discovery and ATS detection) are the parts doing
the actual "generic across site architectures" work this challenge asks for,
and they held up fine everywhere the domain was correct.

## Deploying it so it's reachable from outside this machine

This assistant doesn't create hosting accounts on your behalf, so the last
mile is on you. `webapp.py` is a plain FastAPI app -- point any of these at it:

- **Render / Railway / Fly.io**: `pip install -r requirements.txt && playwright install --with-deps chromium`
  as the build step, `python webapp.py` as the start command.
- **A VM/EC2 box you already have**: same two commands, put it behind nginx
  or just open port 8000.

A `Dockerfile` is included below if your host prefers containers:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "webapp.py"]
```
