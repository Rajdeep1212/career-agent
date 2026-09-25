# Job-Sourcing Feasibility

Status date: 2026-09-25 · Branch baseline: `m1a-correctness` · Research only (no code changes)

**Question.** Can Career Agent reliably find many real, currently open, fresher-relevant jobs in India, with official apply links, on a ₹0 budget, without breaking any provider's terms?

**Answer: yes, if the design changes.** Today every search spends quota on keyword aggregators, and the free tiers are tiny: JSearch allows 200 requests a month, and Jooble 500 requests per key in total. The agent should instead keep its own **daily-synced index of company job boards**, built from official ATS APIs, robots-sanctioned sitemaps and schema.org JSON-LD. Searches then run against that local index at zero cost, and each job's live status comes from its source. Aggregators become a small top-up, not the backbone.

**Evidence from live checks today.** A one-request-per-board probe with a clear User-Agent and 1–1.5 s delays found **≈ 570 open India jobs across 16 verified boards**. Official APIs needed no keys, and **21 of the 23 seed companies** have a machine-readable source. Details are in §2–§3.

Legend. **Verified** means an official doc was read or a live endpoint was probed today (marked "probe"). **3rd-party** means the claim is from a non-official source. **Unverified** means it could not be confirmed.

---

## 1. Verdict on each idea

| # | Idea | Verdict | Why | Terms risk | Effort | Touches |
|---|---|---|---|---|---|---|
| 1 | Company Radar via official ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters) | **Feasible** | Public, documented, key-free GET endpoints (§2). Probe: 16 boards gave ≈570 India jobs | **Low**: documented public job-board APIs [G][L][A][S] | M | new `app/sources/`, `app/storage/radar_store.py`, `data/company_radar.json`; a `RadarProvider(JobProvider)` in `app/providers/` searches the local index |
| 1b | Workday `…/wday/cxs/<tenant>/<site>/jobs` | **Feasible with caveats. Don't enable by default** | Undocumented internal JSON (POST, 20 per page, ≈2,000-result ceiling) [W1][W2]. A better path exists: robots.txt on every Workday tenant **allows** the career site and publishes `siteMap.xml`, and job pages serve **JobPosting JSON-LD** (probe) | cxs: **medium** (undocumented, not listed under robots `Allow`); sitemap + JSON-LD: **low–medium** | M | `app/sources/sitemap_jsonld.py` (default); `app/sources/workday_cxs.py` (opt-in only) |
| 1c | JSON-LD, sitemaps and RSS for other ATSs (SuccessFactors, Oracle, Eightfold, Phenom, custom) | **Feasible with caveats** | Phenom (Cisco, P&G) and Publicis Sapient job pages have full JSON-LD. Cognizant, Walmart and Oracle's SPA pages don't (probe) | **Low–medium**: robots-allowed pages, one fetch per job per day at most | M | same sitemap source; per-company `parser` hints in the seed file |
| 1d | Source verification (present in live list = active; gone = closed) | **Feasible** for API boards. **Caveat for sitemaps:** Accenture's and Gartner's Workday sitemaps return exactly 100 URLs (likely capped), so absence ≠ closed; confirm with the job page (404, or `validThrough` in the past) | — | S | `application_verifier.py` gains a `SOURCE_LISTED` path; `career_store` records `last_seen_in_source` |
| 2 | Seed companies with verified ATS and India URLs | **Feasible**: done for 23 companies (§3). Extending to ≈100 is a curation task | Low | M | `data/company_radar.json` (reviewed by you); detector script in `tools/` |
| 3 | JSearch efficiency (one OR-query does the work of four) | **Feasible with caveats** | `date_posted` accepts `all/today/3days/week/month` [J3]. `job_requirements` exists (values seen: `more_than_3_years_experience`, `no_degree` [J3]; `no_experience` and `under_3_years_experience` **unverified**). **`num_pages` billing unverified**: the official docs render client-side and couldn't be read; one 3rd-party source says each page costs a credit [J3]. **OR syntax unverified.** The app already reads `x-ratelimit-requests-remaining`, so **one test request settles each question** | Low | S | `jsearch_provider.py`, `search_planner.py` |
| 4 | Result cache (12 h) and per-provider usage display | **Feasible** | `provider_usage.py` already counts Adzuna/Jooble; add JSearch's monthly count from headers and a normalized-query cache | Low | S | new `app/storage/search_cache.py`; `career_agent.py`; `app.js` Connections |
| 5 | Job-alert email ingestion (separate alerts inbox) | **Feasible with caveats** | `gmail.readonly` is **restricted** and there is **no per-label scope** [GS]. Apps in *Testing* status get **refresh tokens that expire after 7 days** [GO] (this also affects today's `gmail.send`, which is "sensitive"). Better: **IMAP with an app password on the dedicated alerts account**, or a manual `.eml`/`.mbox` drop folder. No OAuth verification, no 7-day expiry, and only a throwaway mailbox is exposed | **Low** (your own mail; links stored, never fetched) | M | new `app/sources/alerts_mail.py`; `career_store` relevance labels |
| 6 | "Save to Career Agent" browser button | **Feasible only in a reduced form** | LinkedIn's User Agreement §8.2.2 forbids "browser plugins and add-ons… to scrape or copy the Services" [LI]. So **no DOM extraction** on LinkedIn/Naukri/Indeed. A bookmarklet may open `http://localhost:8010/capture?url=…&title=…` in a new tab; you confirm and paste details there. This also keeps the exact-origin check intact | Extension scraping DOM: **high**. URL + title + your paste: **low** | S | `app/static/capture.html`, `GET /capture` (form), `POST /capture` (local origin) |
| 7 | Cross-source dedup with multi-source evidence | **Feasible** | `job_identity.py` already dedups by URL/source ID. Add fuzzy company + title + city matching and keep a `sources[]` list per card, preferring the official URL. Multi-source presence becomes an M3 signal | Low | M | `job_identity.py`, `career_store.upsert_job`, `app.js` card |

---

## 2. Verified facts

| Topic | Fact | Status | Source |
|---|---|---|---|
| JSearch pricing | Free 200 req/month (hard limit); Pro $25/10k; Ultra $75/50k; Mega $150/200k; PAYG $0.005/req | Verified | [J1] |
| JSearch coverage | Aggregates LinkedIn, Indeed, Glassdoor, ZipRecruiter and "all public job sites via Google for Jobs" | Verified | [J1] |
| JSearch `date_posted` | `all`, `today`, `3days`, `week`, `month` | 3rd-party | [J3] |
| JSearch `job_requirements` | Exists; `more_than_3_years_experience`, `no_degree` seen; `no_experience`, `under_3_years_experience` not confirmed | Partly unverified | [J3] |
| JSearch `num_pages` billing / OR queries | Not stated on readable official pages; "each page costs one credit" (3rd-party) | **Unverified** | [J1][J3] |
| JSearch pagination | `/search-v2` with cursor recommended beyond page 1 | Verified | [J1] |
| Adzuna limits | 25/min, 250/day, 1,000/week, 2,500/month; "Jobs by Adzuna" label ≥116×23 px with "Adzuna" as the logo image; no aggregate use beyond 14-day trial without consent | Verified | [AZ] |
| Jooble | `POST https://<cc>.jooble.org/api/<key>`; key per country site; 500 requests per key lifetime; snippets only | Verified (full ToU text not reachable) | [JB] |
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs[?content=true]`; "authentication is not required for any GET endpoints"; no stated rate limit | Verified + probe | [G] |
| Lever | `GET api.lever.co/v0/postings/{site}?mode=json` (EU: `api.eu.lever.co`); listing needs no key; filters `location`, `commitment`, `team`, `department` | Verified + probe | [L] |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{name}[?includeCompensation=true]`; fields incl. `isRemote`, `workplaceType`, `publishedAt`, `applyUrl` | Verified + probe | [A] |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{id}/postings?country=in`; key needed only for *internal* postings. Probe: `BoschGroup` gave 502 India postings with no key | Verified + probe | [S] |
| Workday cxs | POST JSON `{appliedFacets, limit≤20, offset, searchText}`; larger limits return empty; ≈2,000 ceiling | 3rd-party (undocumented) | [W1][W2] |
| Workday robots/sitemap | Each tenant's `robots.txt` lists `…/<site>/siteMap.xml` and `Allow: /<site>/`. Sitemaps have `<loc>` only (no `lastmod`). Some return exactly 100 URLs (Accenture, Gartner); Cisco returned 1,332 (294 India) | Probe | robots.txt of 11 tenants |
| Workday job JSON-LD | Job pages embed `JobPosting` with `title`, `datePosted`, `employmentType`, `jobLocation`, `identifier`, `hiringOrganization`; `validThrough` on some (Gartner, Salesforce) | Probe | NVIDIA, Accenture, Gartner, Salesforce, Cisco pages |
| JSON-LD elsewhere | Present: Phenom (Cisco, P&G), Publicis Sapient. Absent in sampled pages: Cognizant, Walmart | Probe | job pages sampled from sitemaps |
| Amazon | `amazon.jobs/en/search.json?normalized_country_code[]=IND` returns JSON (2,354 India hits); robots.txt disallows only `/internal` paths | Probe; **undocumented** | [AM] |
| Oracle Recruiting Cloud | `…oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions` answers publicly (Oracle; Dell's site is also ORC) | Probe; **undocumented** | careers.oracle.com |
| Gmail scopes | `gmail.send` sensitive; `gmail.readonly` / `gmail.metadata` / `gmail.modify` restricted; no per-label read scope | Verified | [GS] |
| OAuth Testing apps | Refresh token expires after 7 days unless scopes are only name/email/profile | Verified | [GO] |
| LinkedIn UA | §8.2.2 bans scraping via "crawlers, browser plugins and add-ons"; §8.2.4 bans copying content without consent | Verified | [LI] |
| Himalayas | `himalayas.app/jobs/api`, `/jobs/api/search?country=…`; cached every 24 h; must link back and credit; "do not submit Himalayas jobs to third-party websites" | Verified | [HI] |
| Remotive | `remotive.com/api/remote-jobs`; credit and link back; max ≈4 calls/day; jobs delayed 24 h; paid API from $5k/month | Probe (legal notice in response) | [RM] |
| HN "Who is hiring" | Algolia API `hn.algolia.com/api/v1/search?tags=comment,story_<id>`; Sept 2026 thread `49522897`: 11 comments mention India | Probe | [HN] |
| Careerjet | `search.api.careerjet.net/v4/query`, needs a publisher key plus the end user's `user_ip` and `user_agent`; `en_IN` locale | Partly verified (cost and display terms unclear) | [CJ] |
| National Career Service | Only aggregate vacancy datasets on data.gov.in; "API… does not currently exist" | Verified | [NCS] |

---

## 3. Seed companies (all 23 checked today)

MR means machine-readable. **API** = official ATS API; **SJ** = robots-allowed sitemap plus JSON-LD job pages; **U** = undocumented JSON (opt-in only); **—** = none found. India job counts are *URL-slug matches* in one sitemap page or API call: approximate, and they exclude "Indiana" false positives.

| Company | Detected ATS (evidence) | India job-list source | MR | Tag | Conf. |
|---|---|---|---|---|---|
| Accenture | Workday `accenture.wd103` / `AccentureCareers` (linked from accenture.com; robots) | `accenture.wd103.myworkdayjobs.com/AccentureCareers/siteMap.xml` (100 URLs, 10 India; likely capped) | SJ | it_services | High |
| Alphabet/Google | Custom Google Careers; no ATS link, no job sitemap found | none machine-readable; Google's jobs reach JSearch via Google for Jobs | — | big_tech | Medium |
| Amazon | Custom amazon.jobs (iCIMS IDs in JSON) | `amazon.jobs/en/search.json?normalized_country_code[]=IND` (2,354) | U | big_tech | High |
| Boeing | Radancy front end, Workday `boeing.wd1` / `EXTERNAL_CAREERS` (+`INTERN`) | `boeing.wd1…/EXTERNAL_CAREERS/siteMap.xml` | SJ | gcc | High |
| Bosch | SmartRecruiters `BoschGroup` | `api.smartrecruiters.com/v1/companies/BoschGroup/postings?country=in` (502) | API | gcc | High |
| Capgemini | Custom site; front end calls `cg-jobstream-api.azurewebsites.net/api` | undocumented API only | U | it_services | Medium |
| Cisco | Phenom `careers.cisco.com` plus Workday `cisco.wd5` / `Cisco_Careers` | `cisco.wd5…/Cisco_Careers/siteMap.xml` (1,332; 294 India) | SJ | big_tech | High |
| Coca-Cola | Workday `coke.wd1` / `coca-cola-careers` (+`coca_cola_college`); careers site redirects there | tenant sitemaps | SJ | gcc | Medium (tag) |
| Cognizant | Custom `careers.cognizant.com` | `careers.cognizant.com/sitemap.xml` (≈2,290 `/india-en/jobs/` URLs); **no JSON-LD** | Partial (sitemap + HTML) | it_services | Medium |
| Comcast | Radancy front end, Workday `comcast.wd115` / `Comcast_Careers` | tenant sitemap | SJ | gcc | High |
| Dell | Oracle Recruiting Cloud (`enterpriseplatform.dell.com/hcmUI/CandidateExperience`) | ORC REST (undocumented) | U | big_tech | Medium |
| Gartner | Workday `gartner.wd5` / `EXT` | `gartner.wd5…/EXT/siteMap.xml` (100; 15 India; JSON-LD incl. `validThrough`) | SJ | gcc | High |
| HSBC | Avature (`mycareer.hsbc.com`; `hsbc.avature.net` in robots) | sitemap index exists, but sampled `en_GB` sitemap has no job URLs | — (unverified) | gcc | Medium |
| IBM | Avature-backed `careers.ibm.com` (`ibmglobal.avature.net` in robots) | sitemap index exists; job entries not parsed | — (unverified) | big_tech | Medium |
| Infosys | Custom SPA `career.infosys.com` | none found | — | it_services | Medium |
| Maersk | Workday `maersk.wd3` / `Maersk_Careers` | tenant sitemap | SJ | gcc | High |
| NVIDIA | Eightfold `jobs.nvidia.com` over Workday `nvidia.wd5` / `NVIDIAExternalCareerSite` | tenant sitemap (JSON-LD verified) | SJ | big_tech | High |
| Oracle | Oracle Recruiting Cloud (`eeho.fa.us2.oraclecloud.com`) | ORC REST answered without auth (undocumented) | U | big_tech | High |
| P&G | Phenom `pgcareers.com` plus Workday `pg.wd5` (`1000`, `1001`) | `pgcareers.com/in/en/sitemap_index.xml` (JSON-LD yes; includes non-India jobs) | SJ | gcc | Medium (tag) |
| Salesforce | Workday `salesforce.wd12` (`External_Career_Site`, **`Futureforce_NewGradRoles`**, `Futureforce_Internships`) | tenant sitemaps (JSON-LD incl. `validThrough`) | SJ | big_tech | High |
| Publicis Sapient | Custom AEM site | `careers.publicissapient.com/careersJobDetailSitemap.xml` (377; ≈50 India; JSON-LD yes) | SJ | it_services | High |
| Voith | SAP SuccessFactors (`career5.successfactors.eu`, `jobs.voith.com`) | no sitemap in robots; feed unverified | — (unverified) | gcc | Medium |
| Walmart | Custom `careers.walmart.com` (robots disallows `/api`) | sitemap of about 16k jobs, all sampled URLs US, no JSON-LD; India (Walmart Global Tech) Workday tenant **not found** at `walmart.wd5` | — | big_tech | Low |

**Verified extension boards (official APIs, probe today; India count / total):**
- **Greenhouse:** Databricks 97/884, MongoDB 77/401, Rubrik 33/140, Razorpay (`razorpaysoftwareprivatelimited`) 20/25, HackerRank 17/27, Elastic 15/386, Druva 12/35, Groww 7/7.
- **Lever:** Paytm 133/174, Meesho 51/54, Zeta 20/22, CRED 11/12.
- **Ashby:** Sarvam AI (`sarvam`) 61/61, OpenAI 10/827, Atlan 6/8, Composio 2/28.
- **Not found on the probed ATS** (don't guess; detect another way): Postman, PhonePe and Gojek on Greenhouse; Upstox and Jupiter on Lever; Krutrim and Zepto on Ashby.

---

## 4. Other approaches, assessed bluntly

| Idea | Verdict | Notes |
|---|---|---|
| **Generic "sitemap + JSON-LD" source** | **Best new idea** | One adapter covers Workday, Phenom and many custom sites legally (robots-sanctioned, one fetch per new job). It supplies `datePosted`/`validThrough` for freshness and liveness |
| **Fresher-page watch** | Good | For unsupported pages you list yourself (Infosys, Capgemini, TCS NextStep and similar), fetch once a day if robots allows, hash the visible text, and alert "page changed: open it". No parsing, no crawling |
| HN "Who is hiring" (Algolia) | Minor top-up | Free and official, but ≈11 India-mentioning comments a month; free text needs parsing. One request a day |
| Himalayas / Remotive | Low value for India | Remote-worldwide, mostly US; strict credit terms; Remotive allows ≈4 calls/day with a 24 h delay. Optional, off by default |
| Careerjet | Maybe later | Covers India, but it's a publisher-affiliate API: registration plus the end user's IP/UA, and display terms are unclear. Ask Careerjet before use |
| National Career Service | **Not feasible** | No job API; aggregates only [NCS]. Its listings are a JS site with no sanctioned feed |
| Google Alerts RSS for "careers" | Weak | Surfaces pages, not jobs. Redundant with JSearch (Google for Jobs) |
| GitHub fresher lists | Weak for India | Popular new-grad lists are US-centric (not verified for India); maintenance varies |
| Google for Jobs directly | **Not feasible** | No public API; scraping Google Search results breaks its terms. JSearch is the lawful proxy |

---

## 5. Recommended sourcing architecture

```text
daily sync (one run a day, per-host delay ≥1.5 s, robots-checked, stop on 429, UA "CareerAgent/x (+contact)")
  ├─ P1 official ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters) ─┐
  ├─ P2 sitemap + JSON-LD (Workday, Phenom, custom)                     ├─► radar_store (jobs, first_seen, last_seen_in_source, sources[])
  ├─ P6 fresher-page watch (hash only)                                  │
  └─ P7 HN monthly thread (Algolia)                                     ┘
search time: RadarProvider (local, 0 requests) ─► Adzuna (≤250/day) ─► JSearch (1 combined query/day, cached) ─► Jooble (reserve)
manual/opt-in: alerts inbox (IMAP/.eml), capture page (bookmarklet opens localhost form)
```

| Priority | Source | Verification | Budget/cost | Expected yield (estimate) |
|---|---|---|---|---|
| P1 | Official ATS APIs, 40–60 boards | **Source-listed**: active while its ID is in the latest list; CLOSED when it disappears for 2 consecutive syncs | 1 request per board per day; ₹0 | 16 boards now show ≈570 India jobs; 60 boards ≈1.5–3k open, of which perhaps 20–60 new a day |
| P2 | Sitemap + JSON-LD (Workday/Phenom/custom, ≈25 companies) | Page 200 + JSON-LD present + `validThrough` not passed = active. For **capped sitemaps**, absence triggers a page check rather than CLOSED | 1 sitemap per site per day, plus pages for *new* IDs only; ₹0 | Several hundred India jobs across seed MNCs |
| P3 | Adzuna | UNVERIFIED (tracked redirect) unless matched to a P1/P2 record, in which case it takes that status and official URL | ≤250/day (use ≤50); ₹0 | Tens a day |
| P4 | JSearch | As today; matched jobs inherit Radar status | ≈1 request/day (≈30/month of 200); ₹0 | 10 results per request, Google-for-Jobs breadth |
| P5 | Jooble | As Adzuna | Reserve (500 lifetime); weekly at most | Small |
| Opt-in | Alerts inbox, capture page | Links stored, never fetched; matched to Radar for status | ₹0 | Depends on your alerts |

**Expected monthly cost: ₹0.** A paid tier is needed only if you want more JSearch (Pro is $25/month, about ₹2,100 at the current rate; not recommended). The fresher-eligible share after eligibility filters is unknown; M2 will measure it. Treat every yield figure above as an estimate.

---

## 6. Revised M1C plan

Order: after M1B (CI, `tests/`, DEMO_MODE), because Radar needs the storage/migration helper and fixture-based tests. Each task is one or two small commits, tests written first against **recorded fixtures** (saved JSON/XML/HTML in `tests/fixtures/sources/`), with the live network blocked as today.

1. **Seed file and detector:** `data/company_radar.json` holds company, tag, ATS, board token or sitemap URL, India filter and `enabled`. A `tools/detect_ats.py` produces *suggestions* only; you review them. Tests: schema validation, and no entry without verified evidence.
2. **radar_store** (migration with backup, idempotent, tested): jobs, `first_seen`, `last_seen_in_source`, `missing_syncs`, `sources[]`.
3. **Greenhouse, Lever, Ashby, SmartRecruiters adapters:** a normalizer each (reuse `providers/common.build_posting`) and India filtering. Tests per adapter from fixtures, including a job that disappears and is marked CLOSED.
4. **Polite fetcher:** robots.txt cache, per-host delay, stop on 429 (reusing `ProviderError` reasons), ETag/If-Modified-Since, a clear UA, and `safe_http` for SSRF safety. Tests: disallowed path skipped, 429 stops the host.
5. **Sitemap + JSON-LD source,** including the capped-sitemap rule. Tests from recorded NVIDIA, Cisco and Publicis Sapient samples.
6. **`RadarProvider`** registered in `providers/registry.py` (configured when Radar has data); searches the local index; 0 requests. Tests: ranking pipeline unchanged.
7. **Sync command:** `python -m app.sources.sync` plus a "Sync now" button (local origin) and a "last synced" display. A Windows Task Scheduler recipe goes in the README.
8. **Cross-source dedup:** fuzzy company + title + city; `sources[]`; prefer the official URL; aggregator jobs inherit Radar status.
9. **JSearch diet:** one combined daily query with `date_posted=3days`, `num_pages=1`, plus a 12 h cache and a usage display ("JSearch 37/200 this month"). **First, one manual live test** to confirm OR syntax, `job_requirements` values and page billing (read the quota header before and after).
10. **Capture page:** bookmarklet opens `/capture?url=&title=`, you confirm; no DOM extraction.
11. *(Optional, after approval)* **alerts inbox** via IMAP app password or `.eml` drop folder; parser fixtures for LinkedIn, Naukri and Indeed alert emails; 👍/👎 stored as 0–3 labels for D4.
12. *(Optional)* HN monthly thread source; fresher-page watch.

**Definition of done:** a search returns ≥50 Radar jobs for "Python developer fresher India" from fixtures; zero provider requests at search time; every card shows its source(s) and verification basis.

---

## 7. Decisions (recorded 2026-09-25)

| # | Decision | Outcome |
|---|---|---|
| D1 | Workday source | **Decided:** sitemap + JSON-LD by default; `cxs` JSON opt-in only |
| D2 | Undocumented JSON (Amazon `search.json`, Oracle ORC REST, Capgemini API) | **Decided:** opt-in per company, at most 1 request/day, cached, cards labeled "undocumented source" |
| D3 | Alerts inbox | **Decided:** IMAP with an app password on a dedicated alerts account; `.eml` drop folder as fallback. No OAuth `gmail.readonly` |
| D4 | Browser capture | **Decided:** bookmarklet that opens a localhost form; no page scraping, no extension |
| D5 | Paid tiers | **Decided:** none; ₹0 budget |
| D6 | Seed list | **Decided:** approved (23 seeds + 16 verified boards); extend with India AI startups and GCCs; every entry reviewed by the owner |
| D7 | Gmail OAuth app | **Decided:** move the OAuth app to Production (avoids 7-day refresh-token expiry in Testing) |
| D8 | Extra sources | **Decided:** HN "Who is hiring" on; remote-job APIs (Himalayas, Remotive) off |

---

### Sources

- [J1] OpenWeb Ninja, JSearch: https://www.openwebninja.com/api/jsearch
- [J3] career-ops JSearch plugin: https://github.com/cbeaulieu-gt/career-ops-plugin-jsearch
- RapidAPI listing (client-rendered, not readable): https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
- [AZ] Adzuna API terms: https://developer.adzuna.com/docs/terms_of_service · search docs: https://developer.adzuna.com/docs/search
- [JB] Jooble REST API: https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation
- [G] Greenhouse Job Board API: https://docs.greenhouse.io/job-board.html
- [L] Lever Postings API: https://github.com/lever/postings-api
- [A] Ashby Job Postings API: https://developers.ashbyhq.com/docs/public-job-posting-api
- [S] SmartRecruiters Posting API: https://developers.smartrecruiters.com/docs/posting-api
- [W1] Workday cxs (3rd-party): https://dev.to/udaninn/workday-job-boards-have-a-json-api-too-its-just-better-hidden-23fl
- [W2] Workday 2,000-job ceiling (3rd-party): https://dev.to/dododata/scraping-workday-career-sites-without-a-browser-and-the-2000-job-ceiling-h2e
- [AM] amazon.jobs robots: https://www.amazon.jobs/robots.txt
- [GS] Gmail API scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- [GO] Google OAuth 2.0 (refresh-token expiry in Testing): https://developers.google.com/identity/protocols/oauth2
- [LI] LinkedIn User Agreement §8.2: https://www.linkedin.com/legal/user-agreement
- [HI] Himalayas API: https://himalayas.app/api · [RM] Remotive API: https://remotive.com/api/remote-jobs
- [HN] HN Algolia API: https://hn.algolia.com/api · [CJ] Careerjet API: https://www.careerjet.co.in/partners/api/
- [NCS] data.gov.in NCS catalog: https://www.data.gov.in/catalog/national-career-service-ncs
- Probes: one GET per URL on 2026-09-25 with UA `CareerAgentResearch/0.1`, 1–1.5 s delays: Workday tenant `robots.txt`/`siteMap.xml`, sampled job pages, ATS API boards listed in §3.
