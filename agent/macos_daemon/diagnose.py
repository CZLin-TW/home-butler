"""Read-only, allowlisted diagnostics. No password data, env dump or manual send."""

import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install
import resume


def main():
    resume.verify_package()
    install.verify_installed(
        json.loads((install.PACKAGE / "manifest.json").read_text())
    )
    snapshot = resume.snapshot()
    resume.validate_before(snapshot)
    print("Exact sender / unique System item / actual database format / ACL: verified")
    print("Database format:", hex(snapshot["database_version"]))
    domains = ["system/" + install.LABEL, "system/" + install.LABEL + ".resume"]
    if install.PREVIOUS:
        domains.append("gui/" + str(install.CONFIG["uid"]) + "/" + install.OLD_LABEL)
    for domain in domains:
        result = subprocess.run(
            ["/bin/launchctl", "print", domain],
            env=install.ENV,
            capture_output=True,
            text=True,
        )
        print(domain, "loaded=" + str(result.returncode == 0))
        for key in ("state", "last exit code", "username", "run interval"):
            match = re.search(
                r"^\s*" + re.escape(key) + r" = ([\w -]+)$", result.stdout, re.M
            )
            if match:
                print(key + "=" + match.group(1))
    for line in install.status_lines()[-3:]:
        print(line)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Read-only check failed; no automatic repair or credential output.")
        sys.exit(1)
