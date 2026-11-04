# Accounts and private research

StockPilotAI requires a verified Cognito identity for all stock research pages and
APIs. Only login, signup, recovery, the OAuth callback, static assets, and minimal
health endpoints are public. There is no guest analysis mode or default admin.

The app uses Cognito managed login for email/password registration, email
verification, sign-in, and recovery. The app has its own profile and password
change page, a private dashboard, and paginated history. Social login, billing,
and a browser administration console are outside this release.

## What is implemented and what still needs setup

The application, migration, tests, and Cognito template are implemented in the
repository. The Oregon Cognito stack `stockpilot-staging-auth` was provisioned on
November 3, 2026. Its managed sign-in, signup, and recovery pages are available.
Application commit `6fd049e4cc9f3ef78e2cc814c3dbd3762f965944` was deployed to
Lightsail on the same date. Its runtime is connected to Cognito, its session key
was generated on the server, and the database is at migration `20261102_0002`.
Missing authentication configuration locks private features and makes `/ready`
return 503. Real-user email verification and signed-in analysis still need a
user smoke test.

`infra/auth/cognito.yaml` is a separate CloudFormation stack; it does not modify
the imported Lightsail stack or open its firewall. Deploy only in the confirmed
selected Region, **us-west-2 (Oregon)**. Confirm the AWS project's plan using
`aws freetier get-account-plan-state --profile stockpilot --region us-west-2` and
check spend status in AWS Settings > Billing before deployment. The template uses
Cognito Essentials for managed login v2; review current Cognito pricing separately
from the AWS project's free/paid plan. Keeping Lightsail on its existing SSH-only
access means invited users still need a tunnel; public access also requires an
HTTPS domain and a deliberately reviewed network configuration.

## Cognito configuration

Review `infra/auth/cognito.yaml` and a change set before applying it. Parameters:

- `AppBaseUrl`: exact origin, no trailing slash. Examples: `http://localhost:18000`
  for the existing SSH tunnel or `https://research.example.com` for HTTPS.
- `DomainPrefix`: a unique prefix for the Cognito-managed domain.

The template enables self-registration with email verification, a 12-character
password policy, authorization code flow, token revocation and refresh rotation.
It creates a managed-login branding style, which v2 requires per client. The
client has no secret; this backend still uses PKCE, state, and nonce. No identity
pool, AWS credentials for end users, or application IAM permissions are needed.
The user pool is retained and deletion-protected: do not replace it casually,
because a new pool does not inherit existing identities or report ownership.

The default Cognito email sender is appropriate only for limited testing. Check
its delivery limits and configure production email sending through SES before a
public launch. Test verification, recovery, and delivery to real addresses in
staging. The pool permits optional authenticator-app MFA; email/password sign-in
remains available. A self-service MFA enrollment screen and additional abuse
controls are future work.

## Application configuration

Add these values to `.env` locally or to the server's root-owned mode-0600
`/etc/stockpilot/runtime.env`. Never commit the actual values:

```dotenv
APP_BASE_URL=http://localhost:18000
COGNITO_USER_POOL_ID=us-west-2_REPLACE
COGNITO_CLIENT_ID=REPLACE
COGNITO_DOMAIN=https://REPLACE.auth.us-west-2.amazoncognito.com
SESSION_ENCRYPTION_KEY=REPLACE
```

`COGNITO_REGION` defaults to and is restricted to `us-west-2`.
Use the CloudFormation outputs for pool ID, client ID, and domain. Generate the
session encryption key in a trusted terminal with the project's virtualenv:

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use `http://localhost:8000` for direct development or `http://localhost:8001` for
the default local Docker Compose port. Set `APP_BASE_URL` explicitly; the Cognito
callback must be exactly `APP_BASE_URL/auth/callback` and the allowed sign-out URL
must be exactly `APP_BASE_URL/login`. For public access use HTTPS; cookies become
Secure automatically. Trust only the application's controlled reverse proxy.

A preexisting confidential Cognito app client can also use
`COGNITO_CLIENT_SECRET` locally. The provided infrastructure and Lightsail runtime
use the generated client without a secret. Never configure a different client or
pool by guessing its identifiers.

## Migration and existing reports

Back up the database before upgrading. Install the updated dependencies, then run:

```bash
.venv/bin/python -m pip install -e '.[cloud,dev]'
.venv/bin/alembic upgrade head
```

For an old local SQLite database created with `create_all` and no Alembic version,
first confirm it has exactly the original analyses schema. Back it up and stamp
`20261005_0001` only after that verification, then upgrade. Do not stamp a populated
database blindly. New empty development SQLite databases are created automatically;
PostgreSQL always requires migrations.

Existing reports keep their data but have no owner and remain hidden. After you
sign in, an operator can deliberately assign unowned history:

```bash
.venv/bin/python -m app.manage_accounts list-users
.venv/bin/python -m app.manage_accounts assign-legacy --user-id INTERNAL_USER_ID
.venv/bin/python -m app.manage_accounts assign-legacy --user-id INTERNAL_USER_ID --apply
```

The first assignment command is a dry run. Assignment never changes reports
already owned by another user. Run these commands against the intended database
only; the internal user ID comes from `list-users`, not an email or Cognito subject.
No user is automatically granted historical reports on registration.

The existing Lightsail release script takes a backup before migrating. Populate
the new runtime settings before releasing: the validator rejects missing or
mismatched settings. A rollback to older code would restore unauthenticated access
and cannot safely preserve multi-user privacy. Keep the app restricted, retain the
backup, and prefer a forward fix over deploying pre-authentication code.

## Security and session behavior

- Profiles are keyed by a unique `(issuer, subject)` pair, with an internal UUID.
  Matching email addresses never merge identities.
- Login attempts are single-use and expire after ten minutes. State is bound to
  an HTTP-only browser cookie. PKCE, nonce, signature, issuer, audience, token type,
  expiry, and verified email are checked before creating a session.
- Browsers receive only a random opaque session cookie. Its SHA-256 digest is
  stored in the database; provider tokens are encrypted with Fernet. Tokens and
  passwords are never placed in browser storage or server-rendered HTML.
- Sessions expire at the earlier of the Cognito ID token expiry or one hour.
  There is no automatic token refresh in this first release. Users sign in again
  after expiry. Expired session and login-attempt rows are removed on login starts.
- Every private request rechecks the session and local disabled flag. User-facing
  reads always filter by the authenticated user, including API polling and history
  pagination. Foreign or unassigned report IDs return 404.
- Every authenticated write requires a CSRF token. HTML forms include it. API
  clients using the session cookie must send `X-CSRF-Token` from their own rendered
  page. If an Origin header is supplied, it must match `APP_BASE_URL`.
- Logout deletes the app session first, attempts refresh-token revocation, and
  redirects through Cognito logout to clear its separate hosted-login cookie.
  Password changes invalidate all of the user's app sessions. Every new sign-in
  requests credentials rather than silently trusting a leftover Cognito cookie.
- App sessions are locally revocable. External Cognito administrator changes and
  password recovery do not immediately revoke an already-issued app session;
  it lasts at most one hour unless the operator disables the local user or deletes
  their app sessions. Immediate event-driven provider revocation is future work.
- Private responses are `no-store`; HTML receives CSP and anti-framing headers.
  Password validation errors omit submitted values. Uvicorn callback access logs
  redact query strings; any upstream proxy must also omit callback query strings.

Keep the encryption key with protected runtime secrets and backups. To rotate it,
remove existing app sessions during a maintenance window, then deploy the new key;
users sign in again. Do not rotate it while leaving old encrypted sessions active.
Do not retain failed-request bodies or secrets in application/proxy logs.

## Verification

The tests exercise verified-token handling with generated RSA keys and mock HTTP,
not real Cognito users or paid stock analysis. Integration tests cover anonymous
access, CSRF, cross-user isolation, callback replay, session expiration/revocation,
password changes, private pagination, and preservation/reassignment of legacy
reports. The PostgreSQL integration test upgrades/downgrades migrations in an
isolated schema and verifies history survives an app restart.

After deployment, perform a real staging smoke test: register two users, verify
both emails, test recovery/password change, create reports, verify isolation via
both HTML and API URLs, sign out, and check that replaying the old cookie fails.
Do not treat mocked provider tests or template linting as proof of email delivery
or live sign-in availability.

References: [Cognito managed login](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-managed-login.html),
[authorization code and PKCE](https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html),
[managed login endpoints](https://docs.aws.amazon.com/cognito/latest/developerguide/managed-login-endpoints.html).

Local infrastructure validation used cfn-lint and cfn-guard 3.2.1. The latter is
installed in the ignored `infra/.tools/` directory from the official ARM macOS
release, verified against SHA-256
`4c1eb10c061731159eaaf0e7dbd465db9fa4b767b82186a4ab489671cc00b7d0`.
AWS managed Cognito rules were pinned to registry revision
`7f7340c26ae5d5e8874651dbffeb12e0e9f505b6`: the MFA configuration rule passes;
the unauthenticated identity-pool rule is not applicable because this template
creates no identity pool. This is limited rule coverage, not a complete security
assessment. Account-aware CloudFormation change-set validation passed on
November 3, 2026. Real-user verification-email delivery and end-to-end sign-in
through the deployed application remain untested.

Live setup check, November 2, 2026 (America/Los_Angeles): AWS login for the
`stockpilot` profile was refreshed successfully, and the existing Oregon staging
stack remained accessible with termination protection enabled. Cognito
`ListUserPools` was denied by an explicit organization service control policy;
this role also could not read that policy. The plan-status API returned missing
project data, so the free/paid plan remains unconfirmed. No Cognito resources
were created and the running application was not changed. The project owner or
AWS Support must clarify the Cognito restriction before live setup continues.

November 3 update: the Cognito restriction was resolved and AWS login refreshed.
The approved `accounts-20261103` change set added only the user pool, app client,
managed domain, and branding; the separate stack reached `CREATE_COMPLETE`.
Stack termination protection, user-pool deletion protection, optional software
TOTP, the password policy, callback URLs, token revocation, and refresh rotation
were verified. Sign-in, signup, and recovery pages returned their expected forms;
OIDC discovery and signing keys were also verified. No users were created and no
email was sent by these checks. Retrieve identifiers from the stack outputs.

The plan-status API still returns missing project data; the user confirmed a
spend limit is configured. CloudTrail inspection remains denied, so durable
CloudFormation audit retention is unverified. macOS denied reading Downloads;
the user subsequently identified an accessible credentials folder and authorized
the existing Lightsail key there. It was used without copying or displaying the
private key, with strict checking against the previously trusted SSH host key.

The approved application commit was transferred as a checksum-verified Git
archive, built on the server, and deployed through the existing release script.
An isolated container with no runtime secrets or network access passed 115 tests;
the PostgreSQL integration test was skipped in that container and had passed
locally. Pre- and post-migration logical backups were created and validated with
`pg_restore --list`. Runtime settings were updated atomically, preserving the
database password and OpenAI key; the session encryption key was generated on
the server. The runtime file remains root-owned mode 0600.

The live schema matches the application models (`alembic check` passed). Both
preexisting reports survived with no owner, so they are hidden until deliberately
assigned. The app and database are healthy, the backup timer is active, the app
listens only on server loopback, and PostgreSQL has no published host port.
Anonymous research pages redirect to login; anonymous analysis API requests return
401. Signup and recovery redirect to the correct Cognito domain. The browser's
Create account link reached the expected Cognito signup form through the SSH
tunnel at `http://localhost:18000`. No real users were registered by these checks;
email delivery, verified-user sign-in, and signed-in stock analysis remain to be
tested with a user's own account. Temporary deployment files and the test-only
image were removed; previous releases, runtime backup, and database backups were
retained.
