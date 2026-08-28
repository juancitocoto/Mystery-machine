# Deploying to Railway

A step-by-step walkthrough for getting this API live on Railway
(railway.app) - a hosting platform that builds straight from your
Dockerfile, so you don't need to manage a server yourself. Written for
someone doing this for the first time; skip steps you've already done.

**Before you start:** make sure the real end-to-end test works locally
first (`uvicorn app.main:app --reload`, real keys in `.env`,
`MOCK_MODE=false`) - deploying something you haven't confirmed works is
just moving the debugging somewhere slower and more awkward to reach.

## 1. Get this project into a GitHub repository

Railway's simplest deploy path is "connect a GitHub repo and it builds
automatically" - so this project needs to be one first. If you already
have this in a GitHub repo, skip to step 2.

If you don't have a GitHub account yet, create one for free at
github.com, then:

```bash
cd path\to\mystery-shop-api\mystery-shop-api
git init
git add .
git commit -m "Initial commit"
```

`git add .` respects `.gitignore` (already in this folder), so `.env`
and your real secrets are never included - only check `git status`
before committing if you ever want to double-check what's about to be
tracked.

Then on github.com: click "New repository," give it a name (e.g.
`mystery-shop-api`), leave it empty (no README/license - you already
have files), and create it. GitHub will show you commands like:

```bash
git remote add origin https://github.com/YOUR-USERNAME/mystery-shop-api.git
git branch -M main
git push -u origin main
```

Run those (GitHub may prompt you to sign in). Once it finishes, refresh
the GitHub page - your files should be there.

## 2. Create a Railway project from that repo

1. Go to railway.com/new/github and sign in (GitHub sign-in is easiest,
   since you'll be connecting a GitHub repo anyway).
2. Authorize Railway to access your GitHub account if prompted, then
   select the `mystery-shop-api` repo you just pushed.
3. Railway detects the `Dockerfile` at the repo root automatically and
   builds from it - no extra configuration needed for the build itself.

## 3. Set your environment variables

In your new Railway service: Settings -> **Variables** tab -> **RAW
Editor**. Paste all of these at once (one `KEY=value` per line), filling
in your real values in place of the placeholders:

```
API_KEY=make-up-your-own-secret-here
ASSEMBLYAI_API_KEY=your-real-assemblyai-key
ANTHROPIC_API_KEY=your-real-anthropic-key
MOCK_MODE=false
JOBS_DB_PATH=/app/data/jobs.db
```

That last one, `JOBS_DB_PATH`, matters - see step 4 before you deploy,
not after.

Everything else (`RATE_LIMIT_CREATE_SHOP`, `AGENT_MAX_ITERATIONS`,
`RETAIN_AUDIO_FILES`, etc.) has a sensible default from `app/config.py`
and only needs to go here if you want to override it - see
`.env.example` for the full list.

## 4. Attach a persistent volume for your job database

**Don't skip this.** Without it, every redeploy wipes `jobs.db` -
including the recording-consent audit trail that's the whole point of
that feature (see the README's "Recording consent compliance" section).
A plain container's filesystem is thrown away and rebuilt fresh on every
deploy; a volume is a separate disk that survives redeploys.

1. In your Railway project's canvas view, right-click (or press Cmd/Ctrl
   + K) and choose to create a **Volume**.
2. Attach it to your `mystery-shop-api` service.
3. Set its **mount path** to `/app/data` (this matches `WORKDIR /app` in
   the Dockerfile - the volume shows up as a folder inside the running
   container at that path).
4. Make sure `JOBS_DB_PATH=/app/data/jobs.db` is set in your environment
   variables (step 3) - that's what tells the app to put its database
   file *inside* the volume instead of the container's throwaway disk.

`uploaded_audio/` deliberately does NOT need a volume - audio files are
meant to be deleted right after processing anyway (see
`RETAIN_AUDIO_FILES` in the README), so losing an in-progress upload on
a redeploy just means that one job fails and can be resubmitted, not
lost history.

## 5. Deploy and verify

Railway deploys automatically once the Dockerfile builds and your
variables are set. When it's done:

1. Railway gives your service a public URL (Settings -> Networking ->
   Generate Domain if one isn't there yet) - something like
   `mystery-shop-api-production.up.railway.app`.
2. Visit `https://your-url/docs` - you should see the same interactive
   docs page you've been testing locally.
3. Visit `https://your-url/health` - should return `{"status": "ok"}`.
4. Run one real shop through the public URL (same steps as your local
   real-run test) to confirm transcription, report generation, and the
   consent gate all work the same way they did locally.

If the deploy fails or the health check doesn't pass, check Railway's
build/deploy logs first (Deployments tab) - a missing environment
variable or a typo in one is the most common first-deploy issue.

## Cost

Railway currently offers a free trial ($1/month of usage credit) and a
Hobby plan around $5/month beyond that for a small always-on service
like this one - worth checking railway.com's current pricing page
directly, since platform pricing changes over time and this document
won't stay current on that specific number.

## After this works

- Your `POST /shops` and `POST /support/ask` rate limits (see the
  README's "Rate limiting" section) reset if Railway ever restarts your
  service, and won't share state if you scale to multiple instances -
  fine for a single instance, worth revisiting if you outgrow that.
- Consider whether a single shared `API_KEY` is still right once you
  have more than one real client hitting this API - see the "Next steps
  / ideas" section of the README.
- Every push to your GitHub repo's main branch auto-redeploys by
  default - useful once you're iterating, but worth knowing so an
  unfinished change doesn't accidentally go live.
