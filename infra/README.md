# Lightsail infrastructure and deployment

This prepares the restricted development/demo environment in
[the architecture plan](../docs/aws-architecture.md). It does not create AWS
resources during synthesis or tests. Domain, operator IP, budget threshold,
notification email, and SSH key selection remain undecided.

## Components

- Python CDK generates CloudFormation for one 2 GB Linux Lightsail instance in
  **us-east-2**, an attached static IP, daily automatic snapshots, and a monthly
  project-wide cost budget with email alerts at 80% and 100%. Credits and refunds
  are excluded from the budget calculation so gross usage remains visible.
- A required operator IPv4 `/32` restricts SSH and HTTPS. Port 80 is public for
  ACME HTTP-01 certificate validation and redirects only. No database, app,
  Docker API, or IPv6 port is opened by the stack. Do not add an AAAA DNS record.
- Bootstrap installs Docker/Compose and prepares root-owned directories. It
  contains no secrets and does not start the application.
- Compose runs PostgreSQL, FastAPI/Uvicorn, and Caddy. Only Caddy publishes
  ports. Separate persistent volumes hold the database, market cache, and Caddy
  certificate state. A one-off service runs Alembic migrations explicitly.
- The host release script builds a commit-tagged image, validates configuration,
  backs up an existing database, stops web ingress for migrations, waits for app
  readiness, then starts Caddy. It installs a daily logical-backup timer.

The instance and static IP have both deletion and replacement retention policies.
The CDK stack has termination protection. These reduce accidental deletion; they
do not migrate data during replacement. A retained instance can continue billing,
and an orphaned IP may incur charges. Never use `docker compose down -v` on this
environment. Review backups and restore plans before replacement or cleanup.

## Local preparation and validation

Use Python 3.11+ and a supported Node.js version (22+ recommended). Node runs CDK's
underlying engine even though infrastructure definitions are Python. Dependencies
are isolated from the app environment and locked in this directory.

```bash
cd infra
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
pnpm install --frozen-lockfile
export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2
pnpm exec cdk synth --strict --quiet
.venv/bin/cfn-lint cdk.out/StockPilotLightsail.template.json
.venv/bin/python -m pytest tests -q
```

`package.json` pins the CDK CLI; `pnpm-lock.yaml` locks it. `requirements.txt`
records direct development dependencies; `requirements.lock` includes transitive
dependencies. In a restricted filesystem, set
`JSII_RUNTIME_PACKAGE_CACHE=disabled` to avoid writes to the global jsii cache.
Synthesis needs no real parameter values and performs no resource lookups.
The result is `infra/cdk.out/StockPilotLightsail.template.json` (ignored by Git).
The CDK app rejects another selected Region, and the template includes a Region
rule for direct CloudFormation use.

Shell checks from the repository root:

```bash
bash -n deploy/lightsail/{bootstrap,common,release,backup}.sh
python3 infra/smoke_test.py
```

The smoke check builds the image, runs migrations and readiness checks, validates
Caddy configuration without issuing a certificate, and restores a logical dump
into a separate test database. It uses a fake API key, publishes no host ports,
and removes its randomly named test containers and volumes afterward. Docker
must be running; no AWS or paid provider calls are made.

Schema validation, focused safety tests, and local container tests do not verify
live permissions, quotas, blueprint availability, DNS, or certificate issuance.
Run account-aware CloudFormation pre-deployment checks when actual parameters
and the intended project are available. Run `cfn-guard` compliance checks if the
optional checker is available; they are a separate layer from schema validation.

## Decisions required before any AWS deployment

1. Confirm the intended AWS project and Ohio Region in AWS Settings; recheck plan
   and credit/spend status in AWS Settings > Billing. Confirm Lightsail and the
   selected Ubuntu blueprint/bundle remain available before creating anything.
2. Choose an existing Lightsail SSH key pair in Ohio and the trusted operator's
   public IPv4 address (`/32`). Keep the private key on the operator's machine.
3. Choose a monthly gross-cost threshold and budget notification email. This
   budget covers all AWS usage in the project, not just StockPilotAI. Alerts can
   lag usage and are not a hard spending cap; manage spend limits in AWS Settings.
4. Choose a DNS hostname. Create an A record pointing to the stack's static IP
   after provisioning. Caddy requires public DNS and inbound port 80 for HTTP-01;
   HTTPS stays IP-restricted. DNS is managed separately in this first version.
5. Choose backup recovery/retention requirements. Daily dumps plus automatic
   snapshots are an initial baseline; test a restore before storing valuable data.

Copy `parameters.example.json` to ignored `deploy.parameters.json` and fill in
the values. The example deliberately fails validation; do not deploy it as-is.
There are no password or API-key CloudFormation parameters.

Review `cdk diff` and the change set before deployment. CDK's standard deployment
workflow may require a one-time toolkit bootstrap, which is a separate reviewed
AWS action; synthesis does not bootstrap. Use standard CloudFormation deployment,
not hotswap or Express mode. If deploying the generated template directly, enable
stack termination protection explicitly. Do not change resource logical IDs or
physical names casually: a replacement does not transfer PostgreSQL volumes.

## Host configuration and first release (after provisioning is approved)

1. Verify the server host key independently before using SSH. Bootstrap must
   complete: check `/var/lib/stockpilot/bootstrap-complete` and cloud-init logs.
   Use `ubuntu` with the selected key pair; container operations require sudo.
2. Transfer `deploy/lightsail/runtime.env.example` securely to the server, edit
   it there, and install it as `/etc/stockpilot/runtime.env`, owned by root with
   mode `0600`. Never place the actual file in a source archive or Git.
   Generate a password with `python3 -c 'import secrets; print(secrets.token_hex(32))'`.
   Use that 64-character hex string as `POSTGRES_PASSWORD` and set the real
   `SITE_DOMAIN` and `OPENAI_API_KEY`. Values must be plain, single-line, unquoted
   strings without whitespace, shell substitutions, or `$` interpolation.
   Rotate an initialized PostgreSQL password in the DB as well as the file;
   changing `POSTGRES_PASSWORD` alone does not update an existing database.
3. Transfer a **committed, reviewed source archive**, not a working directory
   containing `.env`. Use `git archive <full-commit-sha>` locally and SSH to copy
   it to the server. Extract as root under
   `/opt/stockpilot/releases/<full-commit-sha>` and point the
   `/opt/stockpilot/app` symlink at that directory. Keep earlier source releases
   and images for rollback. The supplied full SHA identifies the source archive;
   the release script does not fetch or verify Git history on the server.
4. Run on the server:

   ```bash
   sudo bash /opt/stockpilot/app/deploy/lightsail/release.sh <full-commit-sha>
   ```

   Use a full 40-character hexadecimal commit SHA. The script builds a local
   image and writes its reference to root-only `/etc/stockpilot/release.env`.
   The previous image reference is saved to `previous-release.env`. Do not print
   `docker compose config` without `--quiet`: resolved configuration has secrets.
5. From the allowed IP, verify HTTPS, `/ready`, and one complete stock analysis.
   Also test that an unapproved network cannot reach HTTPS or SSH. Confirm
   snapshot creation and `systemctl list-timers stockpilot-backup.timer`.

App and database image pulls/builds must be tested for the target server's CPU
architecture. The local build follows the server architecture; ECR/Fargate later
requires an explicit matching build platform. Review PostgreSQL/Caddy image
versions and committed image digests regularly for security updates; upgrades require local validation and
backups. Constrain the model allowlist and avoid unnecessary paid analyses.

## Backups, restore, and failed releases

Daily `pg_dump -Fc` files go to root-only `/var/lib/stockpilot/backups`; old dumps
are removed after seven days only following a successful backup. A pre-migration
dump is made when an existing DB container is running. Lightsail snapshots are
off-host machine recovery points, but are not a substitute for logical restore
testing; automatic snapshots have provider-managed retention. Keep independent
off-host copies of important logical dumps. For example, from a trusted operator
machine, copy a known dump via SSH with `sudo cat` into a protected local file;
never give the instance broad AWS credentials just to upload backups.

Test restore with a disposable PostgreSQL 17 database: create an empty database,
restore the custom-format dump using `pg_restore --exit-on-error --no-owner`,
then verify tables, saved reports, and application readiness. Never overwrite the
live database as a restore test. Production migration to RDS will use the same
logical dump/restore approach with an explicit write pause if retaining data.

A failed migration/readiness check stops the release. The script does not
automatically reverse schema changes or restart old code against an unknown
schema. Inspect migration and app logs, then either fix forward or restore into
a separate DB from the pre-release backup and deploy a compatible image. Restore
the source symlink and previous image reference together when appropriate.
Backups and rollback must account for writes after the backup. Initial releases
accept downtime and interrupted in-process analyses; durable jobs remain planned.

Only the initial infrastructure and release preparation is implemented here.
Cognito, durable SQS jobs, email subscriptions, GitHub OIDC CI/CD, and Fargate/RDS
remain later work. No AWS infrastructure is created by these instructions alone.
