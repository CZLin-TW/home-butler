"""Non-secret deployment settings. No environment or credential discovery."""

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def validate(value):
    expected = {
        "label",
        "service",
        "account",
        "hostname",
        "origin",
        "user",
        "group",
        "uid",
        "gid",
        "home",
        "install",
        "python",
        "previous",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ConfigurationError(
            "Unexpected configuration fields; never put credentials here"
        )
    for key in expected - {"uid", "gid", "previous"}:
        text = value[key]
        if (
            not isinstance(text, str)
            or not text
            or len(text) > 256
            or any(ord(c) < 32 for c in text)
        ):
            raise ConfigurationError("Invalid configuration string: " + key)
    for key in ("label", "service", "user", "group"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value[key]):
            raise ConfigurationError("Invalid identifier: " + key)
    for key in ("uid", "gid"):
        if type(value[key]) is not int or not 1 <= value[key] < 2**31:
            raise ConfigurationError("Require a non-root user/group")
    ipaddress.ip_address(value["account"])
    origin = urlsplit(value["origin"])
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
        or origin.port not in (None, 443)
    ):
        raise ConfigurationError(
            "Require an explicit HTTPS origin without credentials/path/query"
        )
    for key, parent in [
        ("home", "/Users"),
        ("install", "/Library/Application Support"),
    ]:
        path = Path(value[key])
        if path.parent != Path(parent) or path.name in (".", ".."):
            raise ConfigurationError("Require a dedicated direct child: " + key)
    python = Path(value["python"])
    if (
        python.is_absolute()
        or ".." in python.parts
        or len(python.parts) != 2
        or python.parts[0] != "bin"
    ):
        raise ConfigurationError("Runtime interpreter must be bin/<executable>")
    previous = value["previous"]
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {
            "label",
            "sender",
            "sender_sha256",
        }:
            raise ConfigurationError("Invalid previous-agent description")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", previous["label"])
            or previous["label"] == value["label"]
        ):
            raise ConfigurationError("Invalid previous label")
        if (
            not Path(previous["sender"]).is_absolute()
            or ".." in Path(previous["sender"]).parts
        ):
            raise ConfigurationError("Invalid previous sender")
        if not re.fullmatch(r"[a-f0-9]{64}", previous["sender_sha256"]):
            raise ConfigurationError("Require reviewed previous sender SHA256")
    return value


def load(path):
    return validate(json.loads(Path(path).read_text()))


def swift_settings(config):
    # JSON string escaping is valid Swift for this ASCII-only deployment subset.
    # Reject Unicode rather than incorrectly rendering JSON \u escapes in Swift.
    lines = ["enum Deployment {"]
    for name, value in config.items():
        if name == "previous":
            continue
        if isinstance(value, str):
            if not value.isascii() or "\\" in value:
                raise ConfigurationError(
                    "Build settings must use ASCII without backslashes"
                )
            lines.append(f"    static let {name} = {json.dumps(value)}")
        else:
            lines.append(f"    static let {name}: UInt32 = {value}")
    return "\n".join(lines + ["}", ""])
