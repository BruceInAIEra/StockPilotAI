"""Disposable local Docker checks; never calls AWS or the OpenAI API."""
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    project = "stockpilot-smoke-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="stockpilot-smoke-") as temporary:
        env = Path(temporary) / "runtime.env"
        env.write_text("SITE_DOMAIN=demo.example.invalid\nPOSTGRES_PASSWORD=" + "a" * 64
                       + "\nOPENAI_API_KEY=sk-test-only\nAPP_IMAGE=stockpilot:infra-smoke\n")
        env.chmod(0o600)
        command = ["docker", "compose", "--env-file", str(env), "-p", project,
                   "-f", str(ROOT / "deploy/lightsail/compose.yaml")]

        def compose(*args, **kwargs):
            return subprocess.run(command + list(args), check=True, **kwargs)

        subprocess.run(["docker", "build", "--target", "runtime", "-t",
                        "stockpilot:infra-smoke", str(ROOT)], check=True)
        try:
            compose("config", "--quiet")
            compose("up", "-d", "--wait", "db")
            compose("run", "--rm", "migrate")
            compose("up", "-d", "--wait", "web")
            compose("exec", "-T", "web", "python", "-c",
                    "import urllib.request; assert urllib.request.urlopen('http://localhost:8000/ready').status == 200")
            compose("run", "--rm", "--no-deps", "caddy", "caddy", "validate",
                    "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
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
            print("PASS: migration, readiness, Caddy configuration, and logical backup/restore")
        finally:
            # Only this randomly named test project; never the deployed stockpilot project.
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
