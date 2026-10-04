"""Offline build only. Never installs, loads launchd, accesses Keychain or sends metrics."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from settings import load, swift_settings

SOURCE = Path(__file__).resolve().parent
SUPPORT = ("install.py", "resume.py", "settings.py", "diagnose.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(
                root.resolve()
            ):
                raise ValueError("Runtime symlink escapes bundle")
            result[name] = {"link": target}
        elif path.is_file():
            result[name] = {"sha256": digest(path)}
        elif not path.is_dir():
            raise ValueError("Unsupported bundle file")
    return result


def build(config_path, runtime, output, tests_only=False):
    config = load(config_path)
    if output.exists():
        raise ValueError(
            "Refuse existing output; retain any earlier deployment package"
        )
    output.mkdir(parents=True, mode=0o700)
    main = output / "main.swift"
    main.write_text(swift_settings(config) + (SOURCE / "sender.swift").read_text())
    cache = output / "module-cache"
    flags = ["-D", "TESTING"] if tests_only else []
    target = (
        output / "fake-tests" if tests_only else output / "bundle/bin/mini-telemetry"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "/usr/bin/swiftc",
            "-module-cache-path",
            str(cache),
            *flags,
            str(main),
            "-o",
            str(target),
        ],
        check=True,
    )
    if tests_only:
        subprocess.run([str(target.resolve())], check=True)
        return
    # No downloading or package-manager execution: operator supplies an audited,
    # self-contained runtime with psutil already installed. No venv external links.
    if not runtime or not (runtime / config["python"]).is_file():
        raise ValueError(
            "Provide standalone runtime with configured interpreter and psutil"
        )
    inventory(runtime)
    bundle = output / "bundle"
    shutil.copytree(runtime, bundle / "runtime", symlinks=True)
    (bundle / "collector").mkdir()
    for name in ("macos_metrics.py", "macos_temperature.py"):
        shutil.copy2(SOURCE.parent / name, bundle / "collector" / name)
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(target)], check=True
    )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(target)], check=True
    )
    info = subprocess.run(
        ["/usr/bin/codesign", "-d", "--verbose=4", str(target)],
        capture_output=True,
        text=True,
        check=True,
    )
    cdhash = next(
        line.split("=", 1)[1]
        for line in info.stderr.splitlines()
        if line.startswith("CDHash=")
    )
    for name in SUPPORT:
        shutil.copy2(SOURCE / name, output / name)
    (output / "deployment.json").write_text(json.dumps(config, indent=2) + "\n")
    manifest = {
        "bundle": inventory(bundle),
        "sender_cdhash": cdhash,
        "installer_sha256": digest(output / "install.py"),
        "support_files": {
            name: digest(output / name) for name in (*SUPPORT, "deployment.json")
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    wrapper = (SOURCE / "operator.command.in").read_text()
    wrapper = wrapper.replace("__MANIFEST_SHA256__", digest(output / "manifest.json"))
    wrapper = wrapper.replace(
        "__SUPPORT_CHECKS__",
        "\n".join(
            "check " + name + " " + sha
            for name, sha in manifest["support_files"].items()
        ),
    )
    (output / "operator.command").write_text(wrapper)
    (output / "operator.command").chmod(0o700)
    print(
        "Built local package only. Review manifest/config/runtime dependencies before user-run installation."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tests-only", action="store_true")
    args = parser.parse_args()
    build(args.config, args.runtime, args.output, args.tests_only)


if __name__ == "__main__":
    main()
