# Job providers

JSearch through RapidAPI is the only enabled live job provider. Provider
selection is controlled by `JOB_PROVIDERS`; unavailable names are not treated
as working integrations.

## JSearch configuration

Set these values in the local `.env` file:

```dotenv
JOB_PROVIDERS=jsearch
RAPIDAPI_KEY=
RAPIDAPI_HOST=jsearch.p.rapidapi.com
SEARCH_COUNTRY=in
```

Keep the key empty for offline development. Automated tests mock provider HTTP
and must not consume live quota.

## Provider boundary

Each adapter accepts one planned query and returns normalized job records.
Search orchestration controls the total request budget and does not retry or
fan out implicitly. A malformed record or URL is skipped or normalized without
aborting the rest of a batch.

Normalization produces stable source identity, title, company, location, work
mode, employment and experience data, dates, skills, salary, description, and a
usable application URL when available. Raw provider payloads, authorization
headers, and provider response bodies are not exposed through public APIs.

Provider results then pass through canonical deduplication, safe application
page verification, eligibility, and ranking. A provider listing is not proof
that an application page is active; verification records uncertainty instead
of inventing an active or closed state.

## Adding a provider

Add an adapter under `app/providers/`, register it explicitly, and cover its
normalization and error handling with synthetic fixtures. Keep credentials in
settings, return sanitized errors, preserve the global query budget, and test
the full pipeline with external HTTP blocked. Any live validation requires a
separately approved, bounded session.
