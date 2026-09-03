# Deploying both services to Render

Two web services, defined in [`render.yaml`](render.yaml). Both are Docker-based
and both run on Render's free plan.

## 1. Push to GitHub

`.gitignore` already excludes `.env`, so credentials stay out of the repo.

```bash
cd Liba
git init
git add .
git commit -m "Jobnova take-home: mock interview + LinkedIn job source agent"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Before pushing, confirm no secrets are staged:

```bash
git ls-files | grep -E "\.env$" && echo "STOP - .env is staged" || echo "clean"
```

## 2. Create the services

In Render: **New → Blueprint**, point it at the repo. It reads `render.yaml`
and creates both services.

## 3. Set Part 1's credentials

`mock-interview` needs these set in the Render dashboard (Environment tab).
They are marked `sync: false` in the blueprint, which is why Render prompts for
them rather than reading them from the repo:

| Variable | Where it comes from | Required |
|---|---|---|
| `LIVEKIT_URL` | LiveKit Cloud → project → `wss://...` | yes |
| `LIVEKIT_API_KEY` | LiveKit Cloud → API Keys | yes |
| `LIVEKIT_API_SECRET` | LiveKit Cloud → API Keys | yes |
| `TAVUS_API_KEY` | tavus.io → API Keys | no |
| `TAVUS_REPLICA_ID` | Tavus → your PAL / face id | no |

Part 2 needs nothing: it reads LinkedIn as an unauthenticated guest page and
Clearbit's autocomplete endpoint is keyless.

Leave the Tavus pair unset to run the interview voice-only. That is a supported
mode, not a degraded one — `main.py` catches avatar failures and continues, so a
Tavus outage or an exhausted credit balance cannot take the interview down.

## 4. Check it came up

```bash
curl https://mock-interview.onrender.com/healthz
```

`{"ok": true, "agent_worker_running": true}` means the web service is up *and*
its LiveKit agent worker registered. A 503 means the page is being served but no
interviewer would join a room — check the logs for the worker's exit reason.

---

## Free-tier cold starts — read this before the interview

Render's free instances **sleep after ~15 minutes idle** and take **roughly 50
seconds** to wake. For Part 2 that is a slow first page load. For Part 1 it is
worse: the LiveKit agent worker only starts once the container does, so somebody
opening a cold link may reach the join page before an interviewer exists.

Options, cheapest first:

1. **Warm it yourself.** Open `/healthz` a minute before you share the link and
   wait for `agent_worker_running: true`.
2. **Keep it awake.** Point an uptime pinger (UptimeRobot's free tier, say) at
   `/healthz` every 10 minutes. Note this burns free instance-hours continuously.
3. **Upgrade Part 1 only.** Render's cheapest paid instance does not sleep. Part
   2 can stay free — a slow first load is survivable there in a way that a
   missing interviewer is not.

## Running locally

Part 1 (join page on <http://localhost:8080>, worker supervised automatically):

```bash
cd part1_mock_interview
pip install -r requirements.txt
cp .env.example .env      # then fill it in
python server.py
```

Part 2 (<http://localhost:8000>):

```bash
cd part2_linkedin_agent
pip install -r requirements.txt
playwright install chromium
python webapp.py
```

Batch-evaluate Part 2 against the sample URLs:

```bash
python test_batch.py test_urls.txt --workers 6
```
