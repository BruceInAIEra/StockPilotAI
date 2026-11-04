# Lightsail staging deployment record

Verified November 1, 2026 (America/Los_Angeles). This records the initial
deployment; recheck AWS and the host before changing or recovering it. Public
addresses, operator IP, project identifiers, credentials, and private key paths
are intentionally kept outside this record.

## Deployed state

| Component | Verified state |
| --- | --- |
| Region | Oregon (`us-west-2`), Availability Zone `us-west-2a` |
| CloudFormation stack | `stockpilot-staging`; `IMPORT_COMPLETE` |
| Lightsail server | `stockpilot-staging-server`; created manually, then imported as logical ID `Server` |
| Image and bundle | Ubuntu 24.04 LTS, x86-64; `ubuntu_24_04`, `small_3_0` (2 GB RAM, selected $12 plan) |
| Protection | Stack termination protection enabled; instance deletion and replacement policies both `Retain` |
| Drift check | `IN_SYNC`; zero drifted resources after import |
| Application release | Commit `e52200b87e17e022368a182f8e23c36eca30122c` |
| Running services | FastAPI/Uvicorn and PostgreSQL 17 in Docker Compose; Caddy disabled |
| Application access | Server `127.0.0.1:8000`, reached through a laptop SSH tunnel at `http://localhost:18000/` |
| Firewall | TCP 22 from the operator's IPv4 `/32`, plus preserved `lightsail-connect` and `lightsail-connect-ipv6` browser SSH aliases; no HTTP/HTTPS/app/DB rules |
| Secrets | `/etc/stockpilot/runtime.env`, root-owned, mode `0600`; SSH mode and `gpt-5-mini` model allowlist |
| Database backups | Daily `stockpilot-backup.timer`; root-only local dumps retained seven days; fresh post-migration backup succeeded |

The versioned [import template](../infra/import/lightsail-instance.template.json)
matches the imported resource configuration. Actual parameter values remain
outside Git. Its stack-derived instance name requires a stack name matching the
existing server's `<stack-name>-server` name. Its blueprint, bundle, and browser
SSH aliases describe this particular import and must be checked before reuse.

## How the release was performed

Account-system update, November 3, 2026: the separate Cognito stack
`stockpilot-staging-auth` reached `CREATE_COMPLETE` in Oregon. Its approved change
set added a user pool, app client, managed login domain, and client branding.
Stack termination protection and user-pool deletion protection are enabled.
The managed sign-in, signup, and recovery forms, OIDC discovery, and signing keys
were verified; actual user signup, email delivery, and app sign-in remain untested.
Retrieve pool/client/domain identifiers from the stack outputs.

The account-system app release (`6fd049e4cc9f3ef78e2cc814c3dbd3762f965944`) is
waiting for access to the existing Lightsail SSH key. The running application
and database remain at the initial release recorded above. No server migration
or runtime-secret update has occurred. See [account setup](accounts.md) for the
remaining steps. CloudTrail inspection is still denied; durable audit logging
remains unverified.

1. AWS API calls prepared and reviewed an import-only change set, imported the
   existing instance, enabled termination protection, and checked stack drift.
   The import preserved the server and its firewall configuration.
2. SSH used the operator's local private key and the previously trusted server
   host key. The private key stayed on the laptop. `sudo` ran the committed
   `deploy/lightsail/bootstrap.sh` to install Docker/Compose, configure rotated
   logs, and create the protected directories.
3. `git archive` transferred only committed source, excluding the local `.env`.
   Source lives under `/opt/stockpilot/releases/<full-commit-sha>`; the
   `/opt/stockpilot/app` symlink selects the release. The runtime image was built
   on the server for its CPU architecture.
4. The OpenAI key was transferred through encrypted SSH without displaying it.
   A database password was generated on the server. Configuration was validated
   and saved atomically with root ownership and `0600` permissions. Updating an
   API key must preserve the initialized database password.
5. `deploy/lightsail/release.sh` started PostgreSQL, took a pre-migration dump,
   ran Alembic, started the private web service, and enabled the backup timer.
   A fresh backup was taken after migrations. A separate SSH connection supplied
   the localhost tunnel; that connection must remain open to use the UI.

The runtime configuration, database volumes, and dumps remain outside the source
tree and container image. See the [runbook](../infra/README.md) for later releases,
secret handling, the tunnel command, and recovery steps.

## Verification and limits

- The committed SSH-mode smoke test passed on the actual server: migrations,
  database readiness, loopback-only access, disabled Caddy, and logical
  backup/restore. Its temporary containers and volumes were removed.
- The deployed home page returned HTTP 200 and `/ready` returned `{"status":"ok"}`
  through the SSH tunnel. Both service containers were healthy. The app port
  listened only on loopback; PostgreSQL had no published host port.
- The backup timer was enabled and active, and its service completed successfully.
- Local `cfn-lint` and AWS template validation passed for the import. The change
  set contained one `Import` action and no reported validation errors.
- `cfn-guard` was skipped with explicit approval for this import. CloudTrail
  inspection was denied, so durable audit logging remains unverified.
- Live AI analysis and provider authentication have not yet been tested. Health
  checks and the smoke test do not validate OpenAI or market-data access.

## Reusing the import template

The recorded staging server is already imported. This procedure is for adopting
another manually created instance, after comparing its current properties with
the template. Importing does not run the host bootstrap or deploy the app.

From the repository root, copy and fill in the ignored local files:

```bash
cp infra/import/parameters.example.json infra/import/deploy.parameters.json
cp infra/import/resources.example.json infra/import/deploy.resources.json
```

Set the existing key name, operator `/32`, and actual instance name. Verify the
intended project and Region, validate the template, then prepare an `IMPORT`
change set targeting the chosen stack. For example:

```bash
aws cloudformation create-change-set \
  --stack-name stockpilot-staging \
  --change-set-name import-existing-server \
  --change-set-type IMPORT \
  --template-body file://infra/import/lightsail-instance.template.json \
  --parameters file://infra/import/deploy.parameters.json \
  --resources-to-import file://infra/import/deploy.resources.json \
  --profile stockpilot --region us-west-2
```

Use a unique change-set name for each attempt. Wait for creation to finish and
review its actions and validation events before executing it. Then verify
`IMPORT_COMPLETE`, enable termination protection explicitly, and run drift
detection. The saved import template deliberately has no outputs, launch script,
or new resources: the import operation rejected adding outputs, and new resources
must be introduced in a later stack update. A prepared import leaves a stack in
`REVIEW_IN_PROGRESS` until it is executed; review its status before any cleanup.

See [AWS's import procedure](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-new-stack.html)
for the review, execution, and drift-detection commands.

## Remaining infrastructure work

The full [CDK stack](../infra/lightsail/stack.py) is a later target, not the deployed
baseline. It includes a static IP, automatic snapshots, a budget, and launch
bootstrap, and its default SSH rules omit the preserved browser aliases. The
CDK app's stack ID is `StockPilotLightsail`; deliberately target the existing
`stockpilot-staging` stack for future updates and preserve logical ID `Server`.
Review changes rather than creating a second stack or replacing the server.

Static IP, automatic Lightsail snapshots, and budget alerts are not configured.
The current public IPv4 may change after a stop/start. Local database dumps do
not provide off-host protection against loss of the instance. Choose a budget
threshold/email, establish off-host backup retention, verify audit logging, and
test live analysis before treating this as more than a restricted demo.
