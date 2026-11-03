"""Check deployment configuration without printing secrets. Not a shell dotenv loader."""
import ipaddress
import os
from pathlib import Path
import re
import stat
import sys
import base64
from urllib.parse import urlsplit


def validate(path: Path) -> dict[str, str]:
    mode = path.stat()
    if mode.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("Runtime configuration must have mode 0600")
    if mode.st_uid != os.geteuid():
        raise ValueError("Runtime configuration must be owned by the invoking user (root on server)")
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError("Invalid or duplicate configuration key")
        if not value or re.search(r"[\s\x00-'\x7f]", value) or "`" in value or "\\" in value:
            raise ValueError("Values must be unquoted, nonempty, single-line values without interpolation")
        values[key] = value
    access_mode = values.get("ACCESS_MODE", "ssh")
    if access_mode not in {"ssh", "https"}:
        raise ValueError("ACCESS_MODE must be ssh or https")
    if access_mode == "https" or "SITE_DOMAIN" in values:
        domain = values.get("SITE_DOMAIN", "")
        if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
            raise ValueError("SITE_DOMAIN must be a real lowercase DNS hostname")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError("Use a DNS name for public certificates")
    if not re.fullmatch(r"[0-9a-f]{64}", values.get("POSTGRES_PASSWORD", "")):
        raise ValueError("POSTGRES_PASSWORD must be 64 lowercase hexadecimal characters")
    if not values.get("OPENAI_API_KEY", "").startswith("sk-"):
        raise ValueError("Set OPENAI_API_KEY to a provider key")
    for key in ["OPENAI_DEFAULT_MODEL", "OPENAI_ALLOWED_MODELS"]:
        if key in values and not re.fullmatch(r"[A-Za-z0-9.,_-]+", values[key]):
            raise ValueError("Invalid model configuration")
    base = urlsplit(values.get("APP_BASE_URL", ""))
    if (base.username or base.password or base.path or base.query or base.fragment
            or not base.hostname):
        raise ValueError("APP_BASE_URL must be an origin without a path or trailing slash")
    if access_mode == "https":
        if values["APP_BASE_URL"] != f"https://{values['SITE_DOMAIN']}":
            raise ValueError("APP_BASE_URL must match the HTTPS site domain")
    elif base.scheme != "http" or base.hostname != "localhost" or not base.port:
        raise ValueError("SSH mode requires an explicit http://localhost:PORT application origin")
    if not re.fullmatch(r"us-west-2_[A-Za-z0-9]+", values.get("COGNITO_USER_POOL_ID", "")):
        raise ValueError("Cognito must be in us-west-2")
    if not re.fullmatch(r"[a-z0-9]{1,128}", values.get("COGNITO_CLIENT_ID", "")):
        raise ValueError("Set the Cognito app client ID")
    if not re.fullmatch(r"https://[a-z0-9-]+\.auth\.us-west-2\.amazoncognito\.com",
                        values.get("COGNITO_DOMAIN", "")):
        raise ValueError("Set the Cognito managed domain URL in us-west-2")
    key = values.get("SESSION_ENCRYPTION_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}=", key) or len(base64.urlsafe_b64decode(key)) != 32:
        raise ValueError("Set a generated Fernet session encryption key")
    if set(values) - {"ACCESS_MODE", "SITE_DOMAIN", "POSTGRES_PASSWORD", "OPENAI_API_KEY",
                      "OPENAI_DEFAULT_MODEL", "OPENAI_ALLOWED_MODELS", "APP_BASE_URL",
                      "COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "COGNITO_DOMAIN",
                      "SESSION_ENCRYPTION_KEY"}:
        raise ValueError("Unexpected runtime configuration key")
    return values


if __name__ == "__main__":
    try:
        args = sys.argv[1:]
        print_mode = args[0] == "--print-mode"
        values = validate(Path(args[1] if print_mode else args[0]))
    except (ValueError, OSError, IndexError):
        sys.exit("Invalid runtime configuration; check file ownership, permissions, and documented format.")
    print(values.get("ACCESS_MODE", "ssh") if print_mode else "Runtime configuration checks passed.")
