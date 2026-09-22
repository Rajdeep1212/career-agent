# LinkedIn connection

Start the app with `02_START_WINDOWS.ps1`, then open
http://localhost:8010/app/ and select **Connections**.

Keep the existing root `.env`. It needs these names (values stay private):

```dotenv
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=http://localhost:8010/auth/linkedin/callback
TOKEN_ENCRYPTION_KEY=
```

Use an existing valid Fernet encryption key if Gmail already uses it. Changing
the key makes existing encrypted credentials unreadable. Never copy
`.env.example` over your real `.env` or put credentials in frontend files.
`check_config.py` reports readiness as booleans without printing these values.

1. Restart the backend after configuration/code changes.
2. Open **http://localhost:8010/app/**, then **Connections**.
3. Click **Connect LinkedIn**. Check that LinkedIn requests profile/email access.
   Sign in and approve manually. The app requests only `openid profile email`.
4. Confirm the browser returns to the localhost dashboard and LinkedIn shows
   **Connected**, with your available name/email. Refresh to check persistence.
5. Click **Disconnect LinkedIn** and confirm it returns to **Connect LinkedIn**.
6. Optionally connect again and cancel consent: the dashboard should show a
   cancellation message. An expired sign-in can be retried from Connections.
7. Check job search, Profile & CV, and the existing Gmail status as usual.

## Architecture and limits

The four `/auth/linkedin/*` routes use the existing settings and encrypted
SQLite token store. The `linkedin` provider row holds only allowlisted token,
expiry, and profile fields. The `oauth_bound_states` table in that same database
holds hashed random state/browser bindings with a ten-minute expiry. Consuming
state is transactional and single-use, including on denial and failed exchange.
An atomic connection-generation check prevents an in-flight callback from
reconnecting after Disconnect succeeds.
The browser binding uses an HttpOnly, SameSite=Lax cookie. HTTP is allowed only
for the fixed localhost development origin, so this local cookie is not Secure.

The backend exchanges the code and reads the fixed HTTPS UserInfo endpoint.
It never decodes or trusts the returned ID token and does not persist it.
This is a LinkedIn account connection for the existing single-user local app,
not a new multi-user authentication/session system. Member claims are obtained
from UserInfo; email may be absent. See the
[official LinkedIn OIDC documentation](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2).

Tokens/profile metadata are encrypted together with Fernet. Status uses an
allowlist and never returns tokens. State and token data stay out of browser
storage. Callback indicators are fixed strings; errors never include provider
responses. Standard Uvicorn and httpx logs redact OAuth query strings. If you
add a reverse proxy or custom request logger, configure its query redaction too.

Disconnect deletes local LinkedIn tokens and pending LinkedIn states; it does
not revoke consent at LinkedIn. Remove access in LinkedIn's account settings if
you also want provider-side revocation. Token expiry requires reconnection;
automatic refresh and posting (`w_member_social`) are outside this integration.
The connection indicator uses stored expiry, not a live revocation check.

Keep the app bound to `127.0.0.1:8010`. A hosted/multi-user deployment would need
HTTPS/Secure cookies and authenticated, per-user storage. Existing Gmail
configuration and its registered redirect remain unchanged. LinkedIn always
starts and finishes on `localhost:8010`.

## Verification

Run with the project's Conda Python:

```powershell
& "$env:USERPROFILE\anaconda3\envs\job-agent\python.exe" run_tests.py
& "$env:USERPROFILE\anaconda3\envs\job-agent\python.exe" -m compileall -q app run_tests.py test_linkedin.py test_regressions.py
Get-Content -Raw app/static/app.js | node --check
Get-Content -Raw test_frontend.cjs | node
```

`run_tests.py` runs the complete Python suite, including the existing smoke and
dashboard scripts, using disposable storage and blank real credentials. External
HTTP is blocked; LinkedIn and JSearch are mocked at the transport boundary.
Use this runner so the old smoke scripts do not create drafts in your real data.
