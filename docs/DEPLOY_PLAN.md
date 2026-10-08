# DEPLOY0 — Public read-only demo at ₹0 (plan only)

Written 8 October 2026. Nothing here is built, signed up for or installed. Every provider limit below was read on
8 October 2026 from the provider's own page, which is cited beside it. Free tiers change; DEPLOY1 re-reads them.

**Goal.** A public, read-only demo of Career Agent that costs ₹0 and needs no card. Real user data (the CV, real
applications, Gmail tokens, `.env`) never leaves the laptop: the demo is a separate deployment with synthetic data and
public job posts only.

## 1. What already exists (so this is not built twice)

| Piece | Where | State |
|---|---|---|
| `DEMO_MODE` | `app/core/config.py` (`_demo_isolation`), `app/main.py` (`demo_mode_boundary`), `tests/test_demo_mode.py` | Blanks every credential in `DEMO_BLANKED` (Gemini's key included), moves `data_dir` to a separate directory, sets `job_providers=demo` and `chat_model_provider=none`, answers 404 on `/auth/*`, `/email/drafts/{id}/send` and `/radar/sync` |
| Synthetic profile and jobs | `app/demo/profile.json`, `app/demo/jobs.json` | Exist; there are no synthetic tracker applications yet |
| Demo image | `Dockerfile`, `.dockerignore`, the `demo-image` job in `.github/workflows/ci.yml` | Builds on every push and is smoke-tested; `.env`, `data/`, `uploads/`, `CVs/` and every `*.sqlite3` are kept out of the build context |
| Tracker on Postgres | `app/tracker/store.py` takes a URL; migration `0001_tracker_v1` | The up/down migration test passed on Postgres 16 on 8 October 2026 (throwaway container). `default_url()` has no environment override yet, and `psycopg` is installed locally but is not in `requirements.txt` |
| Origin rule | `app/core/origin_security.py`, `TrustedHostMiddleware` in `app/main.py` | Changes need the exact `APP_ORIGIN` or `WEB_ORIGIN`; no CORS header is sent today |
| Web app | `web/` on Next.js 16.4.0; every page and component is a client component | Talks to its own `/api/v1`, which `next.config.ts` rewrites to FastAPI; one redirect rule sends other host names to `WEB_ORIGIN` |
| Radar index | `data/radar.sqlite3`, 14.8 MB, 2,491 jobs (`len()` of `radar_jobs`, 8 October 2026) | Public job posts only; `db.read_only(path)` already exists |

Not built yet, and the demo depends on them: requirement cards (Q10), sign-in and `AUTH_MODE` (TRK4), any rate
limiting, any CORS handling.

## 2. ADR: the stack

**Decision**

| Layer | Choice | Fallback |
|---|---|---|
| Web | `web/` as a Next.js static export, served as Cloudflare Workers static assets. The browser calls the API on Render directly | Workers through `@opennextjs/cloudflare`, keeping the `/api/v1` rewrite |
| API | The existing FastAPI app in Docker on one Render Free web service, `DEMO_MODE=true` plus a new `PUBLIC_DEMO=true` | Hugging Face Spaces, which the Dockerfile already targets (limits not checked here) |
| Tracker data | Synthetic applications in Supabase Postgres (Free), created by the existing Alembic migration | A SQLite file baked into the image |
| Job index | A read-only SQLite snapshot of the radar index inside the image, rebuilt by a daily GitHub Actions job | — |
| LLM | None at request time. Gemini's free tier only inside the daily job, only on public job-description text, and only once Q10 exists | Deterministic text, no LLM |
| Secrets | Render environment variables and GitHub Actions secrets only | — |

**Why**

- *Static export, not a Worker.* Every page in `web/` is a client component that fetches in the browser, so nothing
  needs a server. Cloudflare states "Requests to static assets are free and unlimited", while the Workers Free plan
  allows 10 ms of CPU per request and 100,000 requests a day. A server-rendered Next.js page can exceed 10 ms, and that
  cannot be measured without deploying. The static route has no such limit to hit.
- *It matches the CORS line in the brief.* With a static site the browser calls Render cross-origin, so the API sends
  one exact `Access-Control-Allow-Origin` for the Cloudflare URL. Through a Worker proxy no CORS header would be needed.
- *Cloudflare itself now recommends vinext over OpenNext* for new Next.js apps on Workers, and vinext is in beta.
  OpenNext stays a sound fallback (it supports every Next.js 16 minor), but not the default.
- *Render Free fits the API.* The demo API used about 100 MB of memory locally (section 5); the plan allows 512 MB.
- *Supabase holds only synthetic rows.* It exercises the Postgres path that the tracker was designed for and that is
  otherwise run only in a migration test.
- *No model call on a visitor's request.* It removes quota exhaustion and prompt abuse from the public surface, and it
  avoids a term in Google's free tier (section 4).

**Consequences**

- `web/` gains a second build mode. A static export supports no rewrites or redirects and no dynamic route without
  `generateStaticParams()`, so `/applications/[id]` needs a fixed list of demo ids or a query-string page, and the two
  rules in `next.config.ts` apply only to the local build.
- FastAPI sends a CORS header for the first time, for one exact origin, in the demo only.
- A new `PUBLIC_DEMO` switch and a read-only rule on every mutating route. It must be impossible to turn on against
  real data.
- Two new places to keep working: a daily GitHub Actions job and a Render deploy hook.
- Things to approve before they are added (the runbook requires asking): `psycopg[binary]` in `requirements.txt`, and
  `@opennextjs/cloudflare` plus `wrangler` only if the fallback is used.
- The demo is a second deployment, not the product. The laptop keeps SQLite and local mode.

## 3. Limits, verified

Read 8 October 2026. "Not stated" means the provider's page does not say it, and it must be checked at sign-up.

### Cloudflare (web)

| Fact | Value | Source |
|---|---|---|
| Static asset requests | "free and unlimited" | https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/ (updated 23 Apr 2026) |
| Workers Free: requests | 100,000 a day, reset at midnight UTC; error 1027 beyond that | https://developers.cloudflare.com/workers/platform/limits/ (updated 5 Sep 2026) |
| Workers Free: CPU per request | 10 ms | same |
| Workers Free: memory, subrequests | 128 MB; 50 per request | same |
| Static assets per Worker version | 20,000 files, 25 MiB each | same |
| Worker size | 64 MiB uncompressed on Free and Paid; "There is no compressed size limit" | same |
| OpenNext: Next.js versions | "All minor and patch versions of Next.js 16" (ours is 16.4.0) | https://opennext.js.org/cloudflare |
| OpenNext: gaps | Node middleware is not supported (we have none). Windows "full support is not guaranteed", so it builds in CI on Linux | same |
| OpenNext: rewrites | Rules in `next.config` run when the Worker handles the request first; no page documents Next 16 rewrite behaviour specifically | https://opennext.js.org/cloudflare/howtos/assets |
| Cloudflare's recommendation | vinext "as the default way to run Next.js applications on Cloudflare Workers"; vinext is in beta; `output: "export"` is supported | https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/ (updated 25 Aug 2026) |
| Static export: unsupported | Rewrites, redirects, headers, proxy, dynamic routes without `generateStaticParams()` | https://nextjs.org/docs/app/guides/static-exports (version 16.4.0, updated 9 Aug 2026) |
| Card required | Not stated | — |

One disagreement: OpenNext's page says the Worker size limit is 3 MiB on Free and 10 MiB on Paid. Cloudflare's own
limits page, which is newer, says 64 MiB uncompressed and no compressed limit. Cloudflare's page governs; it matters
only for the fallback.

### Render (API)

| Fact | Value | Source |
|---|---|---|
| Free web service | 0.1 CPU, 512 MB RAM | https://render.com/docs/compute-plans |
| Idle | Spun down after 15 minutes without inbound traffic; waking "takes about one minute" and Render shows a loading page to browsers | https://render.com/docs/free |
| Hours | 750 Free instance hours per workspace per month; when they run out every Free web service is suspended until next month | same |
| Disk | No persistent disk; file changes are lost on every redeploy, restart or spin-down | same |
| Restarts | "Render might restart a Free web service at any time" | same |
| Outbound traffic | May be suspended for "an uncommonly high volume"; ports 25, 465 and 587 are blocked | same |
| Bandwidth and build minutes | Shared monthly allowances; no number on the page. Without a payment method, Render suspends Free services (bandwidth) or disables builds (minutes) for the rest of the month | same |
| Port | Bind `0.0.0.0`; `PORT` defaults to 10000 | https://render.com/docs/web-services |
| Prebuilt image | Public images from any registry; compressed size at most 10 GB; `linux/amd64`. No automatic redeploy on a new tag: a deploy hook does it | https://render.com/docs/deploying-an-image |
| Card required | Not stated in the docs. A Render blog post says a free deploy needs none | — |

One month of one service that never sleeps is 744 hours, under the 750. A second Free service in the same workspace
would break that.

### Supabase (demo tracker)

| Fact | Value | Source |
|---|---|---|
| Free plan | 2 active projects; 500 MB database each; 5 GB egress; shared CPU, 500 MB RAM; no automatic backups; logs kept 1 day | https://supabase.com/pricing |
| Pause | "Free projects are paused after 1 week of inactivity" | same |
| What counts | Inactive means not enough "user database activity over the past week"; "Typically a few user requests to the database each day over the previous week is enough" | https://supabase.com/docs/guides/platform/free-project-pausing |
| Warning and restore | A warning email about a week before; restore from the dashboard within 1 year (an older changelog entry says 90 days) | same |
| Connections | Direct connections are IPv6; the shared pooler is IPv4 on every plan. A persistent server on an IPv4 network uses session mode, port 5432. Transaction mode (6543) has no prepared statements | https://supabase.com/docs/guides/database/connecting-to-postgres |
| Nano compute | 60 direct connections, 200 pooler clients | https://supabase.com/docs/guides/platform/compute-and-disk |
| Card required | Not stated | — |

### Gemini (job-description text only)

| Fact | Value | Source |
|---|---|---|
| Free tier exists for | Gemini 3.8 Flash (the model named in `app/core/config.py`), among others | https://ai.google.dev/gemini-api/docs/pricing (updated 7 Oct 2026) |
| Free-tier numbers | Not published on the docs page; shown per project in AI Studio. "Specified rate limits are not guaranteed"; limits are per project; daily quotas reset at midnight Pacific | https://ai.google.dev/gemini-api/docs/rate-limits (updated 2 Sep 2026) |
| Data use, unpaid | See section 4 | https://ai.google.dev/gemini-api/terms (effective 23 Mar 2026, updated 28 Apr 2026) |

The free-tier request numbers could not be verified without an account, and this plan forbids sign-ups. DEPLOY6
reads them in AI Studio before it sizes the batch.

### GitHub Actions (daily job)

| Fact | Value | Source |
|---|---|---|
| Cost | Free for public repositories on standard hosted runners (the repository is public) | https://docs.github.com/en/billing/concepts/product-billing/github-actions |
| Schedule | Can be delayed under load; runs on the default branch only | https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows |
| Auto-disable | "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days" | same |

## 4. Privacy and the LLM boundary

- The CV file and any text extracted from it are never in the image, the database or a prompt. This is CLAUDE.md's
  rule and the demo adds no exception.
- **Google's terms for unpaid Gemini use**, quoted: Google "uses the content you submit to the Services and any
  generated responses to provide, improve" its products; "human reviewers may read, annotate, and process your API
  input and output"; "Do not submit sensitive, confidential, or personal information to the Unpaid Services."
- So the only text that may be sent is a job description that is already public. Recruiter names and email addresses
  are stripped before any call, as they are personal information even when a posting prints them.
- The same terms say: "You may use only Paid Services when making API Clients available to users in the European
  Economic Area", Switzerland or the United Kingdom. A public website is reachable from there. The plan therefore
  never calls Gemini in response to a visitor: cards are computed in the daily job and shipped as data.
- CLAUDE.md says "Gemini stays gated to synthetic evaluation", and `app/llm/factory.py` enforces it. Sending real
  public job descriptions is a change to that rule. It needs the owner's explicit approval (open question 4) and it is
  not needed for the first demo.
- Labels. Every page of the web app and the vanilla dashboard carries a fixed banner: "Demo data. The profile and the
  applications are synthetic. The job posts are real public listings, last updated <date>." Scores keep their claim
  level ("Heuristic fit 72/100"), and risk signals stay signals.

## 5. Measurements against Render's limits

| What | Result | Level |
|---|---|---|
| Memory of the API in `DEMO_MODE` | 100 MB working set after start, 104 MB after 120 requests to `/api/v1/applications`, `/jobs/relevance` and `/app/` | Measured, on Windows with Python 3.11.16, 8 October 2026. Not measured on Render's Linux |
| Limit | 512 MB | Render docs |
| Image size | Not measured: building it would install packages into a new image, which this item forbids | — |
| Image size, estimated | About 270 MB for a lean demo set (API, tracker, Postgres driver: 69 MB in 36 packages) or about 500 MB with every runtime dependency except the embedding stack (296 MB in 86 packages), each on the 200 MB `python:3.11.16-slim` base | Estimate from the sizes of the Windows packages installed in `job-agent`; Linux wheels differ |
| Limit | 10 GB compressed | Render docs |
| Embedding stack left out | 153 MB (FastEmbed, ONNX Runtime, NumPy and their dependencies); the app imports it only in `app/eval/semantic.py` | Measured locally |

SEM2's decision stands: v1 ranking stays, and the deployed API has no embeddings. DEPLOY1's gate replaces the two
estimates with a real `docker build` in CI and a memory reading from the running container.

## 6. What the visitor sees when something is cold, paused or used up

| Situation | What the visitor sees | Mitigation |
|---|---|---|
| Render is asleep (15 idle minutes) | The Cloudflare page loads at once with its banner and "Waking the demo server, about a minute"; it retries and fills in | The daily job requests `/health` after its deploy. No keep-alive pinger: one always-on service already uses 744 of the 750 hours |
| Render restarts or redeploys | The same waking message for a short time | Nothing is stored on the instance |
| Supabase project is paused | Job search still works (it reads the baked SQLite file). The tracker panel says "The demo tracker is paused. It will be back after the next daily refresh." | The daily job resets the synthetic rows, which is several real writes a day. Supabase's page says a few requests a day is "typically" enough; it is not a guarantee. The warning email arrives a week ahead and a dashboard visit clears it |
| Supabase pooler refuses connections | The tracker panel shows the paused message | One small pool (2 connections) in session mode |
| Render's 750 hours, bandwidth or build minutes run out | Render suspends the service; the page says "The demo is offline until <the first of next month>" | The image is built in GitHub Actions, so Render build minutes are not used. Responses are small JSON |
| A visitor sends too many requests | HTTP 429 with a plain message and a `Retry-After` header | An in-process limit per client address, which is enough because the Free plan runs one instance |
| The scheduled job was auto-disabled after 60 quiet days | The banner's "last updated" date grows old; job posts go stale | The page shows the snapshot date, and the freshness rule already hides stale posts. Re-enable by hand |
| Gemini's daily quota is used up | Nothing changes for visitors | Cards are precomputed; the job keeps yesterday's cards and records the shortfall |

## 7. Security of the public surface

- `PUBLIC_DEMO=true` is accepted only together with `DEMO_MODE=true`; start-up fails otherwise. In it, every route that
  writes answers 403 "This is a read-only demo", and CV upload, attachments, email drafts, chat actions and capture are
  off. One test walks every registered route and fails if a new mutating route is not covered.
- There is no sign-in. `AUTH_MODE=local` (TRK4) means "one trusted local user" and must be refused when
  `PUBLIC_DEMO` is on; the demo's owner is a fixed synthetic user.
- CORS: `Access-Control-Allow-Origin` is the one configured Cloudflare origin, compared exactly; never `*`, no
  credentials, `GET` only. `TrustedHostMiddleware` allows the Render host name.
- Secrets: the Supabase URL and the Render deploy hook live in Render environment variables and GitHub Actions
  secrets. Nothing goes in git, in the image or in the static site. A build step fails if the exported site contains a
  secret-shaped string.
- The Supabase database role used by the API can read the demo tables and nothing else. Only the daily job has a role
  that writes.

## 8. Open questions, each with a recommendation

1. **Static export or OpenNext?** Recommendation: static export. Use OpenNext only if the export cannot be made to
   work with the detail page.
2. **Does Supabase belong in the first demo at all?** A baked SQLite file would be simpler and cannot pause.
   Recommendation: keep Supabase as asked, but build the SQLite fallback first (DEPLOY2) so the demo never depends on it.
3. **Card requirement.** None of the three providers' pages states it. Recommendation: Rajdeep checks at sign-up and
   stops if any asks for a card.
4. **May Gemini see real public job descriptions?** CLAUDE.md limits it to synthetic evaluation. Recommendation: not
   for the first demo; decide when Q10 is planned, and if yes, only in the daily job.
5. **Is a daily batch job "making an API client available" to European users under Google's terms?** The plan's
   reading is no, because no visitor reaches the model. Recommendation: treat it as unsettled and keep the first demo
   free of Gemini.
6. **Should Supabase Auth replace the custom JWT in TRK4?** Recommendation: no. The demo has no sign-in, so it does
   not need either. Career Agent is local-first and must run with no hosted service, which Supabase Auth cannot do,
   and it would put users' email addresses at a third party. Keep TRK4 as planned, with token checking behind one
   function so a hosted identity provider can be added later if a multi-user hosted version is ever decided.
7. **Keeping the schedule alive.** Whether a workflow's own commit counts as "repository activity" is not documented.
   Recommendation: do not rely on it; normal development keeps the repository active.
8. **Which jobs go in the public snapshot?** Recommendation: radar index rows only (official ATS feeds and
   robots-allowed sitemaps). Alert-email jobs, bookmarklet captures and aggregator results stay on the laptop.
9. **Client address behind Render.** Render's docs page does not say how the client address is forwarded.
   Recommendation: DEPLOY3 verifies it on the running service before the rate limit trusts any header.

## 9. Build rows (added to the queue after approval)

Each row is one session and ends at its gate. Nothing is signed up for before DEPLOY4.

| ID | Item | Gate | YOU |
|---|---|---|---|
| DEPLOY1 | Demo image: a demo requirements file without the embedding stack; `PUBLIC_DEMO` switch (requires `DEMO_MODE`); read-only rule on every mutating route; demo banner on the vanilla dashboard | Full gate; route-walk test; `docker build` in CI with the image size and the container's memory after 100 requests recorded here | Approve `psycopg[binary]` |
| DEPLOY2 | Demo data: synthetic profile and about 12 synthetic applications across the statuses, seeded by a script; read-only radar snapshot path opened with `db.read_only`; snapshot builder that copies radar rows only | Tests: seed is idempotent; snapshot contains no alert, capture or aggregator rows; byte-identical live databases | – |
| DEPLOY3 | API hardening: exact-origin CORS for one origin, `TrustedHostMiddleware` host, in-process rate limit with `Retry-After`, tracker database URL from the environment (Postgres through the session pooler) | Tests for each; migration test on Postgres; 429 after the limit in a test with a fake clock | – |
| DEPLOY4 | Accounts and first deploy of the API: Render service from the GHCR image, Supabase project, secrets set | `/health` and one read route answer on the Render URL; a write answers 403; memory and cold-start time recorded | Create the Render and Supabase accounts; stop if a card is asked for |
| DEPLOY5 | Web: static-export build mode, API origin from the environment, demo banner on every page, waking and paused states, write controls hidden; deploy to Cloudflare | Component tests; Playwright smoke against the export served locally; the public URL loads and lists the demo applications | Create the Cloudflare account |
| DEPLOY6 | Daily GitHub Actions job: polite radar sync, snapshot, image build and push, Render deploy hook, reset of the Supabase demo rows, `/health` check | Three consecutive scheduled runs green; snapshot counts from `len()` in the run log | Add the repository secrets |
| DEPLOY7 | Docs: README "Demo" section with the URL, what is synthetic, the measured limits, and how to take the demo down | Docs only | – |

Requirement cards from Gemini are not a DEPLOY row. They belong to Q10 and to open questions 4 and 5.
