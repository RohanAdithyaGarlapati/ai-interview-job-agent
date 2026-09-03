# Lever Auto-Apply -- Form Filler (and where this deliberately stops)

Target: https://jobs.lever.co/ekimetrics/d9d64766-3d42-4ba9-94d4-f74cdaf20065

## What this builds

`fill_application.py` is a Playwright script that opens a Lever "apply" page
and fills in **every** field on it:

- Standard fields: name, email, phone, current location (handles Lever's
  location autocomplete widget), current company, resume upload (a real file,
  via `input[type=file]`).
- The company's custom application questions -- these are matched *generically*
  by regexing each question's visible label text against a small rule table
  (`QUESTION_RULES`) rather than being hardcoded to Ekimetrics' specific
  questions, so the same script mostly works against other Lever postings
  too; anything it doesn't recognize is reported by label text instead of
  silently skipped, so a human can extend the rule table.
- The data-retention consent checkbox.

It stops there. It takes a full-page screenshot as proof of what got filled
and **does not click Submit.**

Run it:

```bash
pip install -r requirements.txt   # playwright
playwright install chromium
python fill_application.py "https://jobs.lever.co/ekimetrics/d9d64766-3d42-4ba9-94d4-f74cdaf20065/apply" --headed
```

## Why it stops before Submit -- the actual "blocking issue"

Inspecting this posting's DOM directly (`document.querySelectorAll('input,select,textarea')`
in a real browser) turns up:

```
{"tag":"INPUT","type":"hidden","name":"h-captcha-response","id":"hcaptchaResponseInput"}
```

...plus two "Widget containing checkbox for hCaptcha security challenge"
elements next to the Submit button. That's not a generic bot-fingerprint
check that happens to trigger on headless Chrome -- it's hCaptcha,
purpose-built by Lever specifically to stop unattended/automated submissions.

The brief for this part asks for a way to "work around this blocking issue"
via a server-side Chrome extension so applications can be auto-submitted
without a human present. I'm not building that piece: automatically
defeating a CAPTCHA / bot-detection challenge is something I won't do
regardless of the stated purpose, including "it's just my own job
applications" or "the company wants this as a product feature." That's a
hard line, not a judgment call I'm making about Jobnova's product -- happy
to talk through the reasoning further if useful.

## What a compliant version of this feature would actually look like

If Jobnova wants "auto apply" without running into this wall on Lever/Workday/etc.,
the durable fixes are all on the *access method*, not the browser:

1. **Human-in-the-loop handoff.** Auto-fill everything (this script already
   does the hard part), then hand control to the actual candidate to solve
   the CAPTCHA and click Submit themselves in their own browser -- via a
   companion Chrome extension that *they* install and run, not one that
   operates unattended server-side. This is the same shape as every
   legitimate "autofill my job applications" product on the market
   (Simplify, LazyApply's browser-extension mode, etc.) that doesn't get
   compliance/ToS pushback: the extension assists a present human, it doesn't
   replace one.
2. **Official ATS partner integrations.** Greenhouse, Lever, and Workday all
   have partner/API programs (Greenhouse Job Board API, Lever Postings API,
   Workday's recruiting partner APIs) for *reading* postings and, for
   approved partners, *submitting* applications through a sanctioned channel
   that bypasses the public form (and its CAPTCHA) entirely because the ATS
   vendor has authorized it. That requires a formal partnership with each
   ATS vendor, not a scraping workaround -- but it's the only way to get
   "submit without a human present" without fighting anti-automation
   controls that are working as intended.
3. If neither is available for a given ATS, the honest fallback is: fill
   everything, queue it, and surface a "needs your 5 seconds to solve a
   CAPTCHA and click Submit" notification to the user -- still a big time
   save over filling the form by hand, without pretending the CAPTCHA isn't
   there.

## Files

- `fill_application.py` -- the filler (see above)
- `profile.json` -- sample applicant data used to fill the demo run
- `sample_resume.pdf` -- a tiny placeholder PDF for the resume upload field
- `filled_form.png` -- screenshot from the last run, showing the fully filled
  form with the hCaptcha widget still sitting in front of Submit
