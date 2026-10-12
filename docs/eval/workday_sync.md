# Workday JSON feed: dry run (WD1)

Claim level L0: counts are `len()` of the lists the feeds returned. Nothing was saved; no tenant is enabled.

## Decisions (Rajdeep, 12 October 2026)

1. **India discovery.** Use the tenant's country filter where the listing offers one ("newest first" was dropped on
   12 October: no sort parameter is known, so each tenant's default order is kept); otherwise page
   through the listing and filter by location text. At most 40 detail requests per company per run, and a detail is
   requested only for a job not already in the index.
2. **Nike: excluded entirely.** Its robots.txt refuses `/nke/` and `/nke2/`, and the JSON feed would read the same
   jobs. No request is sent to Nike and it has no seed entry.
3. **NTT Data India: excluded.** The detected tenant is `nttglobaldatacenters` (NTT Global Data Centers), a name
   mismatch by the one rule.
4. **Red Hat.** robots.txt plus one list request with site `Jobs` (2 requests). If it fails or returns no jobs, Red
   Hat is link-only.
5. **Bank of America.** robots.txt plus one list request to `lateral-apac` (2 requests). `lateral-us` is dropped.
6. **Accenture, NXP, Target and Thomson Reuters stay on the sitemap source.** No `seed_v3.json` entry; their dry-run
   counts are in this document only.
7. **`seed_v3.json`: every entry `unofficial` and disabled.** No tenant is enabled in Rajdeep's local config. He
   decides per tenant from the results below.

Standing rule from the same day: ask before any live run over several tenants, even a dry run that saves nothing,
and name the tenants and the requests each will get. A saving sync needs a typed "go" of its own.

## The feed

`POST https://{host}/wday/cxs/{tenant}/{site}/jobs` with `{"appliedFacets": {}, "limit": 20, "offset": 0,
"searchText": ""}` is the search a tenant's own careers page runs. It is not a documented API, so:

- robots.txt is checked before every request (no tenant refused);
- an entry opts in with `source.cxs` on a `workday` source and must be `unofficial`; the sitemap reader stays the
  default (decision G);
- a page holds 20 jobs (a larger `limit` returns none); at most 10 list pages and 40 details per company per run,
  one request to a host every 1.5 seconds, and a 429 stops that host;
- `postedOn` ("Posted 14 Days Ago") is never turned into a date: the posted date is `startDate` from the detail,
  `GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}`;
- a job is kept only when its own detail places it in India;
- a listing counts as the whole board only when as many jobs were read as the board reports, and the board reports
  fewer than 2,000 (Workday's ceiling). Otherwise a job's absence never closes it; its page is checked instead.

Not done: **"newest first" (decision 1).** The recorded requests carry no sort parameter and none was tried. The
adapter reads each tenant's default order. Four of the recorded tenants list "Posted Today" first; Uniphore's default
order is not by date (14 days, then 2 days).

## Dry runs

Nothing was saved in either run. "India jobs read" are jobs whose detail was read and placed in India; "more listed"
are listings that passed the India step (the tenant's country filter, or location text) and were not opened because
of the run's detail cap. The second number is therefore an upper bound, not a count of India jobs.

### 11 October 2026 (detail cap 40; stopped by Rajdeep's instruction during Genpact)

Started without asking first, which is why the standing rule above exists. No request reached Nike, Red Hat or Bank
of America. An earlier two-tenant check the same day sent 13 requests to DXC and 6 to Uniphore.

| Tenant | Site | Requests | Listed | India jobs read | More listed | Whole board read | In seed_v3 |
|---|---|---|---|---|---|---|---|
| Accenture | AccentureCareers | 52 | 200 | 40 | 160 | no | no: stays on the sitemap |
| Thomson Reuters | External_Career_Site | 45 | 45 | 40 | 5 | yes | no: stays on the sitemap |
| NXP Semiconductors | careers | 52 | 190 | 40 | 150 | yes | no: stays on the sitemap |
| Target | targetcareers | 45 | 59 | 40 | 19 | yes | no: stays on the sitemap |
| DXC Technology | DXCJobs | 50 | 151 | 40 | 111 | yes | yes, disabled |
| NTT Global Data Centers | External | 51 | 200 | 40 | 15 | no | no: name mismatch with NTT Data India |
| Genpact | External_Careers | not counted (run stopped), at most 52 | – | – | – | – | see below |

295 requests on the six finished tenants in 9.5 minutes; no 429 and no robots refusal.

### 12 October 2026 (detail cap 5; approved tenant by tenant)

| Tenant | Site | Requests | Listed | India by | India jobs read | More listed | Whole board read | Seconds |
|---|---|---|---|---|---|---|---|---|
| Genpact | External_Careers | 16 | 200 | location text | 5 | 137 | no | 30.0 |
| Fractal Analytics | Careers | 12 | 116 | location text | 5 | 60 | yes | 19.2 |
| Samsung R&D Institute India | Samsung_Careers | 9 | 31 | country filter | 5 | 26 | yes | 13.7 |
| Marvell Technology | MarvellCareers | 9 | 23 | country filter | 5 | 18 | yes | 13.7 |
| Airbus | Airbus | 17 | 200 | country filter | 5 | 195 | no | 26.4 |
| Stryker | StrykerCareers | 11 | 78 | country filter | 5 | 73 | yes | 17.7 |
| Uniphore | Uniphore | 7 | 34 | location text | 4 | 0 | yes | 11.2 |
| BrowserStack | External | 8 | 34 | location text | 5 | 27 | yes | 11.5 |
| Red Hat | Jobs | 2 | 20 | – | not read | – | no (one page by decision) | 3.4 |
| Bank of America | lateral-apac | 2 | 20 | – | not read | – | no (one page by decision) | 3.1 |

93 requests in 150 seconds; no 429, no robots refusal, no connection failure. Every one of the 39 details read was
placed in India (0 rejected) and none was malformed. "India by" is inferred from the counts (a filtered listing has
no listing outside India); the run did not record it directly.

- **Red Hat** answered HTTP 200 with 20 jobs, so it is not link-only. The board reports 159 jobs and its own country
  filter reports 5 in India (the tenant's numbers, not counted here); 2 of the 20 on the first page read as India by
  location text.
- **Bank of America** (`lateral-apac`) answered HTTP 200 with 20 jobs and reports 113. It offers no country filter,
  and none of the 20 reads as India by the location-text rule; one says "BANDRA (EAST)" (Mumbai), which the rule
  does not place. As it stands the adapter would find few or no India jobs there.
- The run was made on an IPv6-only connection, which needed NET1 (`64:ff9b::/96` addresses judged by the IPv4
  address inside them). Its first request was the first live use of that change.

## seed_v3.json

`app/core/radar/seed_v3.json` is seed v2 plus 11 entries, all `unofficial`, `reviewed` and disabled: DXC Technology,
Genpact, Fractal Analytics, Samsung R&D Institute India, Red Hat, Marvell Technology, Airbus, Stryker, Bank of
America, Uniphore and BrowserStack. Each entry's evidence holds the two `len()` counts of its dry run (listed, and
India jobs read). On the next start a local `data/company_radar.json` made from an older seed gains these 11 entries
once, after a backup; nothing in it is enabled or changed. No "pollable" verdict by the one rule was made: that
needs the newest posting date of a whole board, which a 5-detail run does not give.

## The one pollable rule, from the dry-run evidence (12 October 2026, no new requests)

`board_rule.judge` with today = 12 October 2026. "Jobs" is the number of listings read, "in India" the details read
and placed in India (so a floor, capped by the run), and the newest posting is the latest `startDate` among those
details. The name is taken as matching because each tenant was found as a link or redirect on the company's own
careers page (`ats_detection.md`); the feed's own organisation name was not compared. Samsung's tenant (`sec`) is
all of Samsung Electronics, not only the India R&D institute.

| Tenant | Jobs | In India | Newest posting read | Verdict |
|---|---|---|---|---|
| DXC Technology | 151 | 40 | 2026-10-09 (3 dates kept from the first check) | pollable |
| Genpact | 200 | 5 | 2026-10-12 | pollable |
| Fractal Analytics | 116 | 5 | 2026-10-06 | pollable |
| Samsung R&D Institute India | 31 | 5 | 2026-09-24 | pollable |
| Marvell Technology | 23 | 5 | 2026-10-10 | pollable |
| Airbus | 200 | 5 | 2026-10-12 | pollable |
| Stryker | 78 | 5 | 2026-10-12 | pollable |
| Uniphore | 34 | 4 | 2026-09-24 | pollable |
| BrowserStack | 34 | 5 | 2026-10-09 | pollable |
| Red Hat | 20 | 2 by location text | none: no detail was read, and "Posted Today" is not a date | not pollable on this evidence (`stale_no_india`) |
| Bank of America | 20 | 0 | none | not pollable (`stale_no_india`) |
