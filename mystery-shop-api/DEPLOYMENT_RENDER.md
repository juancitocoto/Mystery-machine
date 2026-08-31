# Deploying to Render

An alternative to `DEPLOYMENT.md` (Railway) for getting this API live on
Render (render.com) - also builds straight from the `Dockerfile`, so
there's no server to manage yourself. Written for someone doing this for
the first time; skip steps you've already done.

**Before you start:** make sure the real end-to-end test works locally
first (`uvicorn app.main:app --reload`, real keys in `.env`,
`MOCK_MODE=false`) - deploying something you haven't confirmed works is
just moving the debugging somewhere slower and more awkward to reach.

## 1. Push this repo to GitHub

Render's simplest deploy path is "connect a GitHub repo and it builds
automatically." Unlike the Railway walkthrough, you don't need to pull
`mystery-shop-api/` out into its own repo - the root-level `render.yaml`
in this monorepo already knows to build from the `mystery-shop-api/`
subdirectory (its `rootDir`), so just push the whole repo as-is.

## 2. Create a Blueprint from that repo

1. Go to your Render dashboard and click **New > Blueprint**.
2. Connect your GitHub account if prompted, then select this repo.
3. Render finds `render.yaml` at the repo root and shows you the
   services it defines - `mystery-shop-api` (this API) and
   `allophilism-connects-website` (the marketing site in `website/`).
   You can deselect the website here if you only want the API for now.
4. Click **Apply** - Render creates the service(s) and starts a build
   from `mystery-shop-api/Dockerfile`.

## 3. Set your environment variables

`render.yaml` already declares `API_KEY`, `ASSEMBLYAI_API_KEY`, and
`ANTHROPIC_API_KEY` as secrets (`sync: false`), so Render prompts you for
real values during the Blueprint setup - fill those in there, or add/edit
them later under the service's **Environment** tab.

`MOCK_MODE=false` is already set in `render.yaml` so the deployed
instance uses the real services once those keys are filled in.

Everything else (`RATE_LIMIT_CREATE_SHOP`, `AGENT_MAX_ITERATIONS`,
`RETAIN_AUDIO_FILES`, etc.) has a sensible default from `app/config.py`
and only needs to go here if you want to override it - see
`.env.example` for the full list.

## 4. Attach a persistent disk for your job database

**Don't skip this if you care about job history.** Without it, every
redeploy wipes `jobs.db` - including the recording-consent audit trail
that's the whole point of that feature (see the README's "Recording
consent compliance" section). A plain container's filesystem is thrown
away and rebuilt fresh on every deploy; a disk is a separate volume that
survives redeploys.

Render's free plan does **not** support persistent disks - you'll need
to upgrade the service to at least the Starter plan first. Once you have:

1. In `render.yaml`, uncomment the `disk:` block under `mystery-shop-api`
   and set `plan: starter` (or higher) for that service, then commit and
   push - Render picks up the change on the next deploy.
   (Or do the same from the dashboard: service -> **Disks** tab -> **Add
   Disk**, mount path `/app/data`.)
2. Add an env var `JOBS_DB_PATH=/app/data/jobs.db` so the app writes its
   database file *inside* the disk instead of the container's throwaway
   filesystem.

`uploaded_audio/` deliberately does NOT need a disk - audio files are
meant to be deleted right after processing anyway (see
`RETAIN_AUDIO_FILES` in the README), so losing an in-progress upload on
a redeploy just means that one job fails and can be resubmitted, not
lost history.

## 5. Deploy and verify

1. Render gives your service a free subdomain automatically, something
   like `https://mystery-shop-api.onrender.com` - find it at the top of
   the service page once the build finishes.
2. Visit `https://your-url/docs` - you should see the same interactive
   docs page you've been testing locally.
3. Visit `https://your-url/health` - should return `{"status": "ok"}`.
4. Run one real shop through the public URL (same steps as your local
   real-run test) to confirm transcription, report generation, and the
   consent gate all work the same way they did locally.

If the deploy fails or the health check doesn't pass, check Render's
build/deploy logs first (the service's **Logs** tab) - a missing
environment variable or a typo in one is the most common first-deploy
issue.

## 6. Point your real domain at it

This project's plan is `api.allophilismconnects.com` for this API and
`allophilismconnects.com` (root) for the marketing site in `website/` -
matching the links already in `website/index.html`.

For each service, in its Render dashboard page: **Settings > Custom
Domains > Add Custom Domain**, enter the domain (`api.allophilismconnects.com`
for this service, `allophilismconnects.com` for the website), then add
the CNAME (or A/ALIAS for an apex domain) record Render shows you at
your domain registrar's DNS settings. HTTPS is provisioned automatically
once DNS propagates.

## Cost

Render's free plan works for testing but spins the service down after a
period of inactivity and cold-starts on the next request - and, as noted
above, doesn't support persistent disks. A small always-on service with
a disk needs at least the Starter plan; check render.com's current
pricing page directly, since it changes over time and this document
won't stay current on a specific number.

## After this works

- Your `POST /shops` and `POST /support/ask` rate limits (see the
  README's "Rate limiting" section) reset if Render ever restarts your
  service, and won't share state if you scale to multiple instances -
  fine for a single instance, worth revisiting if you outgrow that.
- Consider whether a single shared `API_KEY` is still right once you
  have more than one real client hitting this API - see the "Next steps
  / ideas" section of the README.
- Render auto-redeploys on every push to the branch you connected by
  default - useful once you're iterating, but worth knowing so an
  unfinished change doesn't accidentally go live.
