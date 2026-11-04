# Lightsail infrastructure and deployment

This defines the restricted development/demo environment in
[the architecture plan](../docs/aws-architecture.md). Staging was deployed in
Oregon on November 1, 2026 by importing an existing Lightsail instance and running
the host bootstrap/release scripts over SSH. See the
[deployment record and import procedure](../docs/lightsail-staging.md) for the
current baseline, verification results, and remaining work.

The imported stack currently manages only the server. The full CDK definition
also includes a static IP, automatic snapshots, and a budget; those additions
have not been deployed. Budget threshold and notification email remain undecided.
Synthesis and local tests do not create AWS resources.

## Components

- Python CDK generates the planned full CloudFormation stack for one 2 GB Linux
  Lightsail instance in **us-west-2**, an attached static IP, daily automatic
  snapshots, and a monthly
  project-wide cost budget with email alerts at 80% and 100%. Credits and refunds
  are excluded from the budget calculation so gross usage remains visible.
- `import/lightsail-instance.template.json` records the imported server baseline.
  It preserves the operator `/32` and the existing Lightsail browser SSH aliases.
  Its parameters and resource mapping examples contain no live IPs or secrets.
- In the full CDK template, `AccessMode=ssh` is the default: only TCP 22 from the
  required operator IPv4 `/32` is allowed. No public HTTP, HTTPS, app, DB, or IPv6 ingress
  is opened. Optional `AccessMode=https` adds restricted 443 and public 80 for
  certificate validation and redirects. Do not add an AAAA DNS record.
- Bootstrap installs Docker/Compose and prepares root-owned directories. It
  contains no secrets and does not start the application.
- Compose runs PostgreSQL and FastAPI/Uvicorn. In SSH mode, `compose.ssh.yaml`
  publishes the app only on server `127.0.0.1:8000`; Caddy stays disabled. In HTTPS
  mode, Caddy publishes 80/443 and the app has no host port. Persistent volumes
  hold state. A one-off service runs Alembic migrations explicitly.
- The host release script builds a commit-tagged image, validates configuration,
  stops web ingress, backs up the database before migrations, waits for app
  readiness, then starts Caddy only in HTTPS mode. It installs a daily logical-backup timer.

The imported instance has both deletion and replacement retention policies,
and the deployed stack has termination protection. The full CDK stack also
retains its planned static IP and requests termination protection.
These reduce accidental deletion; they
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
export AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2
pnpm exec cdk synth --strict --quiet
.venv/bin/cfn-lint cdk.out/StockPilotLightsail.template.json
.venv/bin/cfn-lint --regions us-west-2 --template import/lightsail-instance.template.json
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
python3 infra/smoke_test.py --mode ssh
python3 infra/smoke_test.py --mode https
```

The smoke check builds the image, runs migrations and readiness checks, validates
SSH loopback access or Caddy configuration without issuing a certificate, and restores a logical dump
into a separate test database. It uses a fake API key; SSH tests publish an ephemeral loopback port,
and removes its randomly named test containers and volumes afterward. Docker
must be running; no AWS or paid provider calls are made.

Schema validation, focused safety tests, and local container tests do not verify
live permissions, quotas, blueprint availability, DNS, or certificate issuance.
Run account-aware CloudFormation pre-deployment checks when actual parameters
and the intended project are available. Run `cfn-guard` compliance checks if the
optional checker is available; they are a separate layer from schema validation.

## Decisions before deploying the full CDK stack

1. Confirm the intended AWS project permits Oregon before creating resources.
   Changing the console Region selector alone does not change the assigned Region
   of a simplified-experience project; other Regions require advanced access.
   Check AWS Settings > Projects > Overview > Additional Info > Region, and recheck plan
   and credit/spend status in AWS Settings > Billing. Confirm Lightsail and the
   selected Ubuntu blueprint/bundle remain available before creating anything.
2. Choose an existing Lightsail SSH key pair in Oregon and the trusted operator's
   public IPv4 address (`/32`). Keep the private key on the operator's machine.
3. Choose a monthly gross-cost threshold and budget notification email. This
   budget covers all AWS usage in the project, not just StockPilotAI. Alerts can
   lag usage and are not a hard spending cap; manage spend limits in AWS Settings.
4. Keep `AccessMode=ssh` to defer a domain. Use `ACCESS_MODE=ssh` in the runtime
   file as well. For optional HTTPS later, choose a DNS hostname, set both access
   modes to `https`, configure `SITE_DOMAIN`, and create an A record to the static
   IP. Caddy uses public port 80 for HTTP-01; HTTPS remains IP-restricted.
5. Choose backup recovery/retention requirements. The planned baseline combines
   daily dumps and automatic snapshots; current staging has local dumps only.
   Test a restore before storing valuable data.

Copy `parameters.example.json` to ignored `deploy.parameters.json` and fill in
the values. The example deliberately fails validation; do not deploy it as-is.
There are no password or API-key CloudFormation parameters.

Review `cdk diff` and the change set before deployment. CDK's standard deployment
workflow may require a one-time toolkit bootstrap, which is a separate reviewed
AWS action; synthesis does not bootstrap. Use standard CloudFormation deployment,
not hotswap or Express mode. If deploying the generated template directly, enable
stack termination protection explicitly. Do not change resource logical IDs or
physical names casually: a replacement does not transfer PostgreSQL volumes.

For the existing imported staging environment, first compare against the
versioned import baseline. The CDK app's stack ID is `StockPilotLightsail`, while
the deployed stack is `stockpilot-staging`; deliberately target the existing
stack when preparing an update. The full template would add the static IP,
snapshots, budget, and launch script, and remove the preserved browser SSH
aliases. Review those differences before applying it. The bootstrap script was
already run manually over SSH; importing a resource does not run its launch script.

## Host configuration and first release (after provisioning is approved)

1. Verify the server host key independently before using SSH. Bootstrap must
   complete: check `/var/lib/stockpilot/bootstrap-complete` and cloud-init logs
   (or the bootstrap command output for a manually created instance).
   Use `ubuntu` with the selected key pair; container operations require sudo.
2. Transfer `deploy/lightsail/runtime.env.example` securely to the server, edit
   it there, and install it as `/etc/stockpilot/runtime.env`, owned by root with
   mode `0600`. Never place the actual file in a source archive or Git.
   Generate a password with `python3 -c 'import secrets; print(secrets.token_hex(32))'`.
   Use that 64-character hex string as `POSTGRES_PASSWORD` and set the real
   `OPENAI_API_KEY`. Keep `ACCESS_MODE=ssh` and omit `SITE_DOMAIN` for tunnel access.
   HTTPS mode requires `ACCESS_MODE=https` and a real `SITE_DOMAIN`. Values must be plain, single-line, unquoted
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
5. From the allowed IP, open the SSH tunnel below (or verify HTTPS in HTTPS mode),
   check `/ready`, and run one complete stock analysis. Test that unapproved
   networks cannot reach SSH or optional HTTPS, and that port 8000 is not public. Confirm
   `systemctl list-timers stockpilot-backup.timer` and snapshot creation if enabled.

App and database image pulls/builds must be tested for the target server's CPU
architecture. The local build follows the server architecture; ECR/Fargate later
requires an explicit matching build platform. Review PostgreSQL/Caddy image
versions and committed image digests regularly for security updates; upgrades require local validation and
backups. Constrain the model allowlist and avoid unnecessary paid analyses.

## Test without a domain using SSH

After provisioning and running the release script in SSH mode, run this on your
Mac (replace the key path and server address):

```bash
chmod 400 /path/to/your-key.pem
ssh -i /path/to/your-key.pem -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18000:127.0.0.1:8000 ubuntu@SERVER_PUBLIC_IPV4
```

Keep that terminal open and visit `http://localhost:18000/`; check
`http://localhost:18000/ready` too. Port 18000 on your Mac forwards over encrypted
SSH to port 8000 on the server's loopback interface. Nothing should listen on a
public server interface at port 8000. Stop the tunnel with Ctrl+C. This command
will not work until the AWS server, SSH key, and app deployment exist.

To enable HTTPS later, update CloudFormation `AccessMode` and runtime
`ACCESS_MODE` to `https`, add `SITE_DOMAIN`, configure DNS, and release again.
The SSH overlay is then omitted, and Caddy is started. To return to SSH-only,
set both modes to `ssh` and release again; the script stops any existing Caddy
container before starting the web app on loopback. Changing regions creates a
new regional stack; it does not move or delete any resources in another region.

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

The imported Lightsail staging server and SSH-only application release are live.
Static IP, automatic snapshots, budget alerts, durable SQS jobs, email
subscriptions, GitHub OIDC CI/CD, and Fargate/RDS remain later work.

The account-system application code and a separate Cognito template are now
available in `auth/cognito.yaml`; see [account setup](../docs/accounts.md).
The separate `stockpilot-staging-auth` stack was provisioned on November 3, 2026;
its managed account pages are available. The application release is pending
SSH access to the existing host. Populate the new runtime settings and run the
migration during that release before treating application authentication as live.
