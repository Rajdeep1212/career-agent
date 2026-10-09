# Keka tenants: first sync (KEKA1)

Run on 9 October 2026. Claim level L0: counts are `len()` of the lists the feeds returned and rows counted in
`data/radar.sqlite3`. One sync, one day; nothing here says a company is hiring freshers.

## The feed

`GET https://{tenant}.keka.com/careers/api/jobs/default/active` returns every active job of a tenant as one JSON list,
with no authentication. It is the feed the tenant's own careers page reads, not a documented API, so:

- robots.txt is checked before every request, like for any crawled page;
- the entries ship disabled in `app/core/radar/seed_v2.json` and are marked `unofficial` (decision G: an undocumented
  feed is opt-in per company);
- one request per tenant per day, at most one request to a host every 1.5 seconds, and a 429 stops that host.

A job's public page is `https://{tenant}.keka.com/careers/jobdetails/{id}` (checked on one job: the page holds the
job's title). `publishedOn` is the posted date; `publishedSinceDays` is only a cross-check.

### robots.txt

A Keka tenant serves:

```
User-agent: *
Disallow: /
Allow: /careers
Allow: /careers/
```

RFC 9309 says the longest matching rule wins, so `/careers/api/jobs/default/active` is allowed. Python's
`urllib.robotparser` takes the first matching line and refused it. The fetcher now reads robots.txt as the RFC says
(`RobotsRules` in `app/sources/fetcher.py`). The change was checked against the sources already crawled: 548 stored job
and sitemap addresses on 38 hosts, read with both rules, gave the same answer every time.

## The 12 tenants

The seed list's `ats` column says `unknown` for all of them; the tenant was found by detection (`ats_detection.md`).

| Company | Tenant | How the tenant was found | Feed answered | Jobs | In India | Judged by the one rule |
|---|---|---|---|---|---|---|
| SatSure | satsure | linked from the company's careers page | yes | 30 | 27 | pollable |
| Teachmint | teachmint | linked from the company's careers page | yes | 24 | 15 | pollable |
| Bhanzu | exploringinfinities | linked from the company's careers page | yes | 15 | 13 | pollable |
| Zenwork | zenwork | linked from the company's careers page | yes | 11 | 1 | pollable |
| Deccan AI | deccanaiauto | linked from the company's careers page | yes | 15 | 15 | pollable |
| Perceptyne | perceptyne | linked from the company's careers page | yes | 10 | 9 | pollable |
| Disprz | disprz | the seed list's careers URL is the tenant's address | yes | 5 | 3 | pollable |
| Infinite Uptime | infiniteuptime | the seed list's careers URL is the tenant's address | yes | 5 | 4 | pollable |
| GoKwik | gokwik | the seed list's careers URL is the tenant's address | yes | 25 | 25 | pollable |
| Mintifi | mintifi | linked from the company's careers page | yes | 0 | 0 | not pollable: the board lists no job |
| MiHup | mihup | the seed list's careers URL is the tenant's address | yes | 4 | 4 | pollable |
| Turbolab | turbolabtech | linked from the company's careers page | yes | 0 | 0 | not pollable: the board lists no job |

The rule (`app/sources/board_rule.py`, `judge`) is unchanged: at least one job in India and a newest posting at most
180 days old. Keka only gained a reader.

## Result

| What | Before | After |
|---|---|---|
| Tenants whose feed answered | – | 12 of 12 |
| Pollable companies in the seed list (`ats_detection.md`) | 22 | 32 |
| Jobs in the radar index | 2,491 | 2,607 |
| New India jobs added by this sync | – | 116 (of 144 the feeds listed) |
| Records that could not be read; posted dates that disagree with the day count | – | 0; 0 |

Posted-date spread of the 116 jobs, counted on 9 October 2026 from `publishedOn`:

| 0-7 days | 8-30 days | 31-60 days | Over 60 days | No date |
|---|---|---|---|---|
| 15 | 35 | 21 | 45 | 0 |

45 of the 116 were posted more than 60 days ago. The freshness rule shows any job its feed listed within the last
48 hours; without that, a job posted 31 to 60 days ago goes to "check before applying" and an older one is hidden. A
job posted over 180 days ago is flagged as a possible evergreen listing even when it is listed.

Every job carries the source `Company Radar` (as all index jobs do) and the label `Keka` in its `sources` list.

Requests: 10 while recording the two fixtures and checking the job address (6 robots.txt, 2 feeds, 2 pages), 24 for
detection and 24 for the sync (a robots.txt and a feed per tenant each time), and 38 robots.txt files for the check of
the sources already crawled. No 429.

On Rajdeep's computer the 10 pollable tenants are enabled in `data/company_radar.json` and the daily sync now polls
them; Mintifi and Turbolab are disabled again. The file was backed up first
(`data/backups/20261009T020738545918Z/company_radar.json`). The radar database changed only through this sync
(sha256, first 16 hex: `b320ee7a7b81a6ce` before, `5764b35df7de0b10` after).
