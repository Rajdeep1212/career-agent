# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through the repository
host's security-advisory feature. If that is unavailable, contact the repository
owner privately. Do not include credentials, OAuth codes, access tokens, resume
contents, or other personal data in a public issue.

Include the affected component, reproduction steps using synthetic data, and
the expected impact. Maintainers should acknowledge a report before discussing
disclosure timing.

## Deployment boundary

Career Agent is a local, single-user application intended to bind to
`127.0.0.1:8010`. Browser mutations validate the exact local origin. A hosted or
multi-user deployment requires authentication, per-user authorization and
storage, HTTPS, secure cookies, rate limits, and a new threat review.

OAuth tokens are encrypted at rest using the locally supplied
`TOKEN_ENCRYPTION_KEY`. The key and provider credentials belong only in `.env`.
Changing the key makes existing encrypted records unreadable. Provider errors
and API responses must not be returned or logged verbatim.

Automated tests must use disposable storage, synthetic profile data, mocked
providers, and blocked external HTTP. Live validation requires explicit,
separately scoped authorization.
