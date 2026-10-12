# Workday JSON feed: dry run (WD1)

Claim level L0: counts are `len()` of the lists the feeds returned. Nothing was saved; no tenant is enabled.

## Decisions (Rajdeep, 12 October 2026)

1. **India discovery.** Use the tenant's country filter where the listing offers one, newest first; otherwise page
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
