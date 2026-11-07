# Administration

This release adds application roles (`user`, `admin`), `/admin` model settings,
and an audit history. Role assignment is server-only. There is no public admin
signup, first-user promotion, email allowlist, or HTTP role-assignment endpoint.
New and existing users default to `user`.

## Deploy before using these commands

These changes are local source changes until deployed. Use the normal reviewed
Lightsail release process (`deploy/lightsail/release.sh`); it backs up the database,
stops the app, applies Alembic migrations, and starts the new image. The latest schema
revision is `20261106_0004`. Do not run these commands against the old image.
For local development, run `.venv/bin/alembic upgrade head` before restarting an
existing database. Automatic SQLite schema creation only handles empty databases;
it does not upgrade existing tables.

Before enabling production admin access, require authenticator-app MFA in Cognito,
test enrollment and recovery, and sign in again. The current infrastructure
template has **optional** MFA; this release does not change the deployed pool or
enforce an MFA claim in application sessions. Application roles alone do not add
MFA. Pool-wide required TOTP is the simplest deployment policy; it affects all
users, so plan and test that change separately. Protect AWS access with MFA too.

## Grant the first admin on Lightsail

1. Sign up, verify your email, and sign in to the app once.
2. SSH to Lightsail and enter the current release directory (normally
   `/opt/stockpilot/app`). Use a trusted terminal and protect its SSH credentials.
3. List identities:

   ```bash
   sudo bash deploy/lightsail/admin.sh list-users
   ```

   Columns are internal user ID, email, role, status, issuer, and subject. Verify
   the intended user. Email is displayed for review; assignment uses the exact
   Cognito issuer and immutable `sub` (stored as `subject`). Do not guess identity
   values or share the full identity listing publicly.

4. Preview the assignment, substituting the exact issuer and subject:

   ```bash
   sudo bash deploy/lightsail/admin.sh grant-admin \
     --issuer 'https://cognito-idp.us-west-2.amazonaws.com/us-west-2_REPLACE' \
     --subject 'REPLACE_WITH_COGNITO_SUB' \
     --reason 'Initial administrator setup'
   ```

5. Review the target shown, then repeat with `--apply` to commit. The command
   rejects other user pools, missing users, and disabled users. It invalidates the
   user's existing app sessions, so sign in again and open `/admin`.

The wrapper uses the deployment's Compose files and protected runtime environment
without printing credentials. For local Compose, the equivalent is
`docker compose exec web python -m app.manage ...`. For a local virtualenv, use
`.venv/bin/python -m app.manage ...` against the intended configured database.

To revoke access, use the same issuer and subject with `revoke-admin`, first as a
preview and then with `--apply`. Revocation returns the user to the regular role
and invalidates their sessions. Revoking the last admin is allowed: an authorized
server operator can restore access using this same process. Repeating an already
applied role assignment makes no additional change or audit entry.

## Model settings

Admins can enable models and choose a default from `OPENAI_ALLOWED_MODELS` plus
`OPENAI_DEFAULT_MODEL`. Those deployment values remain the ceiling. API keys,
database credentials, Cognito settings, and AWS permissions are not editable here.

Settings persist in the application database and are read for new requests by
all app processes. Each save requires a reason, CSRF validation, and a current
settings version. Stale saves return 409 rather than overwriting another admin's
changes. An unavailable model is rejected before a new analysis calls providers.
The home page and `/api/v1/models` use the same settings. Each saved analysis
already records its selected model; in-progress jobs keep that selected model.

If a deployment removes every enabled model, model-dependent requests return 503
until an admin selects an available model on `/admin`. The admin page remains
available for recovery. Quotas, spending caps, user-disable controls, and job
operations are not implemented by this release.

## Security boundaries

- Every request authenticates the opaque session and reloads the database role.
  Admin routes reject regular users. Settings writes recheck the role and disabled
  state within their database transaction.
- Role changes and model-setting changes commit with their audit entry. `/admin`
  shows the latest 50 entries, including actor, target, old/new values, and reason.
  Audit values exclude credentials and provider tokens. Operator entries identify
  the container OS user and hostname; shared SSH access is not individual attribution.
- The audit table has no application edit/delete endpoint, but is not tamper-proof:
  a database administrator or compromised database writer can alter it. External
  audit retention is a separate production improvement.
- Knowing a Cognito `sub` does not grant access. Running the operator command
  requires access to the server/container and its database credentials. A server or
  database compromise can bypass app roles; Docker is not an additional identity check.
- Keep the database, app port, and Docker socket private; restrict SSH access,
  use HTTPS for public access, patch dependencies, and retain protected backups.
  An app admin role never grants AWS or shell permissions.

This feature has automated authorization, CSRF, identity isolation, migration,
settings persistence, stale-update, and audit checks. It is not a penetration test
or an audit of the live Lightsail configuration.

## Invitation-only registration

Deploy schema revision `20261106_0004` using the normal release process before
running this version. For local existing databases, run `.venv/bin/alembic upgrade head`.
No Cognito configuration changes are needed.

`INVITE_ONLY_SIGNUP=true` is the default. In `/admin`, **Registration → Require
invitation to register** controls the current policy. Once an admin saves this
setting, the database value takes precedence over the environment default and
persists across restarts. Turn it off to reopen public registration. Concurrent
stale setting changes return 409; reload before saving again.

Existing users, including existing admins, can sign in without invitations.
For a brand-new installation without an admin, temporarily set
`INVITE_ONLY_SIGNUP=false` while access is restricted to a trusted operator
(for example, the SSH tunnel). Create the first user and grant admin using the
operator procedure above, then enable the invitation requirement in `/admin`
and restore the environment default to `true` before opening access.

Under **Invitations**, generate a code and copy it immediately. The full code
appears only in that response and is never stored. New codes have four characters
using uppercase letters and numbers, excluding I, O, 0, and 1. Short codes are
case-insensitive; existing long codes keep working with their original case.
The list identifies new invitations by their record ID, so it does not reveal
the short code. Existing long invitations still show their last eight characters.
The list includes creation time, creator email, status, recipient
email, and redemption time, with pages of 50 invitations. Available invitations
can be deactivated. Used/deactivated records remain visible, and deactivation
does not disable an already-registered user. All times are UTC.

Missing/invalid codes are rejected locally before redirecting to Cognito. The
code is bound to the server-side OAuth attempt and checked again after verified
Cognito authentication. Only a successful transaction creating a new app user
and session consumes it. Authentication errors, abandonment, expired attempts,
and failed database transactions leave the code available for a fresh signup.
Two concurrent users cannot redeem one code. An existing user's sign-in does
not consume a code. Deactivation takes effect even after registration has begun.
Code generation, deactivation, redemption, and policy changes are audited without
recording raw codes.

The invitation form uses an anonymous CSRF token and permits five submissions per
client address and 100 submissions globally per ten-minute window, shared across
app processes. A client already over its own limit does not consume the global
allowance. The global cap limits distributed guessing but can temporarily block
legitimate signup during an attack. Four-character codes have a smaller guessing
space than long tokens; keep the number of available invitations small and
deactivate unwanted invitations. Client IP attribution depends on trusted proxy configuration; do not expose the app directly
with untrusted forwarded headers. Raw codes are sent in POST bodies, never URLs;
keep request-body logging disabled at the proxy as well as the application.

This is an application access gate: Cognito's own registration can still create
an identity, but first login cannot create a StockPilotAI user or session without
a valid invitation while the requirement is enabled. It does not change Cognito
email verification, MFA, password recovery, or existing sessions.
