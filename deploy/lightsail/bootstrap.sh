#!/usr/bin/env bash
# Cloud-init only prepares the host. No credentials or application releases here.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 python3 unattended-upgrades
systemctl enable --now docker
install -d -m 0700 /etc/stockpilot /var/lib/stockpilot/backups
install -d -m 0755 /opt/stockpilot/releases
cat >/etc/docker/daemon.json <<'JSON'
{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"3"}}
JSON
systemctl restart docker
# Explicit inbound firewall lives in CloudFormation. Never expose Docker or DB ports.
# No docker-group membership: container management requires sudo.
touch /var/lib/stockpilot/bootstrap-complete
