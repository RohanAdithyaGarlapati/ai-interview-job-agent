"""Minimal demo UI: paste a LinkedIn job URL, see the resolved company job-listing page.

Run locally:
    python webapp.py
    -> open http://localhost:8000

To make this reachable by someone outside your machine (e.g. for the take-home
reviewer to test their own 10 URLs), deploy this FastAPI app to any host you
control (Render/Railway/Fly.io/a VM) -- see README.md for a ready-to-use
Dockerfile/Procfile. Creating a hosting account isn't something this assistant
does on your behalf, so that last step is on you.
"""
import asyncio
import os
import re

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from agent.pipeline import resolve_job_source

app = FastAPI()

PAGE = """
<!doctype html>
<html>
<head>
<title>LinkedIn Job Source Agent</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }}
  input[type=text] {{ width: 100%; padding: 10px; font-size: 15px; box-sizing: border-box; }}
  button {{ padding: 10px 20px; font-size: 15px; margin-top: 10px; cursor: pointer; }}
  .result {{ margin-top: 24px; padding: 16px; border-radius: 8px; }}
  .ok {{ background: #e6f6ec; border: 1px solid #34a853; }}
  .fail {{ background: #fdecea; border: 1px solid #d93025; }}
  .row {{ margin: 6px 0; }}
  .label {{ color: #555; font-size: 13px; }}
  a {{ word-break: break-all; }}
</style>
</head>
<body>
  <h2>LinkedIn Job Source Agent</h2>
  <p>Paste a LinkedIn job posting URL. The agent resolves the company, then finds
  the actual job-listing page on the company's own site (Greenhouse, Lever,
  Ashby, Workday, or their in-house careers page).</p>
  <form method="post">
    <input type="text" name="url" placeholder="https://www.linkedin.com/jobs/view/..." value="{url}">
    <button type="submit">Resolve</button>
  </form>
  {result_html}
</body>
</html>
"""


def render_result(r: dict) -> str:
    if r["success"]:
        return f"""
        <div class="result ok">
          <div class="row"><b>Company:</b> {r.get('company_name')}</div>
          <div class="row"><b>Job title:</b> {r.get('job_title') or '(unknown)'}</div>
          <div class="row"><b>Job listing page:</b> <a href="{r['final_url']}" target="_blank">{r['final_url']}</a></div>
          <div class="row label">ATS detected: {r.get('ats_detected') or 'none (in-house careers page)'}</div>
          <div class="row label">Method: {r.get('method')}</div>
        </div>
        """
    return f"""
    <div class="result fail">
      <div class="row"><b>Could not resolve this posting.</b></div>
      <div class="row">{r.get('error')}</div>
    </div>
    """


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE.format(url="", result_html="")


LINKEDIN_JOB_RE = re.compile(
    r"^https?://([a-z0-9-]+\.)*linkedin\.com/jobs/view/", re.I
)


def validate_job_url(raw: str) -> str | None:
    """Return a complaint about `raw`, or None if it looks usable.

    Checked up front so an unusable input says so plainly. Without this the
    pipeline dutifully fetches whatever it was given and fails several steps
    later with "could not find company name on guest job page", which reads as
    the agent being broken rather than as the URL being wrong.
    """
    url = (raw or "").strip()
    if not url:
        return "Please paste a LinkedIn job URL."
    if not url.lower().startswith(("http://", "https://")):
        return "That does not look like a URL. It should start with https://"
    if "linkedin.com" not in url.lower():
        return (
            "That is not a LinkedIn URL. This tool takes a LinkedIn job posting, "
            "like https://www.linkedin.com/jobs/view/4413378187"
        )
    if not LINKEDIN_JOB_RE.match(url):
        return (
            "That is a LinkedIn URL but not a job posting. Job postings look like "
            "linkedin.com/jobs/view/<id> - a company page or a search results page "
            "will not work."
        )
    return None


@app.post("/", response_class=HTMLResponse)
async def submit(url: str = Form(...)):
    complaint = validate_job_url(url)
    if complaint:
        return PAGE.format(
            url=url,
            result_html=render_result({"success": False, "error": complaint}),
        )

    # Hand the pipeline to a worker thread explicitly. It drives Playwright's
    # *sync* API, which refuses to run on a thread that has a live asyncio event
    # loop ("Please use the Async API instead") - and when it refuses, the
    # pipeline swallows the error and quietly returns the careers page instead of
    # the ATS listing, so the failure looks like a weak result rather than a bug.
    # FastAPI does route plain `def` handlers to a threadpool, but that was not
    # holding once deployed, so this does not rely on it.
    result = await asyncio.to_thread(lambda: resolve_job_source(url).to_dict())
    return PAGE.format(url=url, result_html=render_result(result))


if __name__ == "__main__":
    import uvicorn

    # Render (and most PaaS) inject the port to bind on via $PORT.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
