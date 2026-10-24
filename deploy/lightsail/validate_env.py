"""Check deployment configuration without printing secrets. Not a shell dotenv loader."""
import ipaddress
import os
from pathlib import Path
import re
import stat
import sys


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
    if set(values) - {"SITE_DOMAIN", "POSTGRES_PASSWORD", "OPENAI_API_KEY",
                      "OPENAI_DEFAULT_MODEL", "OPENAI_ALLOWED_MODELS"}:
        raise ValueError("Unexpected runtime configuration key")
    return values


if __name__ == "__main__":
    try:
        validate(Path(sys.argv[1]))
    except (ValueError, OSError, IndexError):
        sys.exit("Invalid runtime configuration; check file ownership, permissions, and documented format.")
    print("Runtime configuration checks passed.")
