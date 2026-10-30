"""Disposable local Docker checks; never calls AWS or the OpenAI API."""
from pathlib import Path
import argparse
import json
import subprocess
import tempfile
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ssh", "https"], default="ssh")
    mode = parser.parse_args().mode
    project = "stockpilot-smoke-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="stockpilot-smoke-") as temporary:
        env = Path(temporary) / "runtime.env"
        env.write_text("ACCESS_MODE=" + mode + "\nPOSTGRES_PASSWORD=" + "a" * 64
                       + "\nOPENAI_API_KEY=sk-test-only\n"
                       + ("SITE_DOMAIN=demo.example.invalid\n" if mode == "https" else ""))
        env.chmod(0o600)
        subprocess.run(["python3", str(ROOT / "deploy/lightsail/validate_env.py"), str(env)], check=True)
        release = Path(temporary) / "release.env"
        release.write_text("APP_IMAGE=stockpilot:infra-smoke\n")
        command = ["docker", "compose", "--profile", mode, "--env-file", str(env),
                   "--env-file", str(release), "-p", project,
                   "-f", str(ROOT / "deploy/lightsail/compose.yaml")]
        if mode == "ssh":
            command += ["-f", str(ROOT / "deploy/lightsail/compose.ssh.yaml")]
            config = json.loads(subprocess.run(command + ["config", "--format", "json"],
                                               check=True, stdout=subprocess.PIPE).stdout)
            assert config["services"]["web"]["ports"][0]["host_ip"] == "127.0.0.1"
            # Use an ephemeral test port so an existing localhost app is untouched.
            override = Path(temporary) / "test-ports.yaml"
            override.write_text('services:\n  web:\n    ports: !override ["127.0.0.1::8000"]\n')
            command += ["-f", str(override)]

        def compose(*args, **kwargs):
            return subprocess.run(command + list(args), check=True, **kwargs)

        subprocess.run(["docker", "build", "--target", "runtime", "-t",
                        "stockpilot:infra-smoke", str(ROOT)], check=True)
        try:
            compose("config", "--quiet")
            compose("up", "-d", "--wait", "db")
            compose("stop", "caddy", "web")
            compose("run", "--rm", "migrate")
            compose("up", "-d", "--wait", "web")
            compose("exec", "-T", "web", "python", "-c",
                    "import urllib.request; assert urllib.request.urlopen('http://localhost:8000/ready').status == 200")
            if mode == "https":
                compose("run", "--rm", "--no-deps", "caddy", "caddy", "validate",
                        "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
            else:
                published = compose("port", "web", "8000", stdout=subprocess.PIPE).stdout.decode().strip()
                assert published.startswith("127.0.0.1:")
                assert urllib.request.urlopen("http://" + published + "/ready", timeout=10).status == 200
                running = compose("ps", "--services", "--status", "running", stdout=subprocess.PIPE).stdout.decode().split()
                assert set(running) == {"web", "db"}, "Caddy must remain disabled in SSH mode"
            compose("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "stockpilot",
                    "-d", "stockpilot", "-c",
                    "CREATE TABLE smoke_probe (value text); INSERT INTO smoke_probe VALUES ('restore-ok');")
            dump = compose("exec", "-T", "db", "pg_dump", "-U", "stockpilot",
                           "-d", "stockpilot", "-Fc", stdout=subprocess.PIPE).stdout
            compose("exec", "-T", "db", "createdb", "-U", "stockpilot", "smoke_restore")
            compose("exec", "-T", "db", "pg_restore", "-U", "stockpilot", "-d",
                    "smoke_restore", "--exit-on-error", "--no-owner", input=dump)
            restored = compose("exec", "-T", "db", "psql", "-U", "stockpilot", "-d",
                               "smoke_restore", "-Atc", "SELECT value FROM smoke_probe;",
                               stdout=subprocess.PIPE).stdout.decode().strip()
            assert restored == "restore-ok", "Backup round trip lost the probe record"
            print(f"PASS ({mode}): migration, readiness, access configuration, and logical backup/restore")
        finally:
            # Only this randomly named test project; never the deployed stockpilot project.
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
