"""One bounded System Keychain experiment using a PUBLIC sentinel and temporary jobs.

Consumes already-signed test executables. Never imports a signing identity, changes
trust settings, updates a password item or touches a production telemetry service.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shutil
import signal
import subprocess
import time

VERSIONS = ("A", "B", "wrong-identifier", "wrong-signer")
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"}
PREFIX = "org.homebutler.telemetry.signing-probe."
SYSTEM_PARENT = Path("/Library/Application Support")


def call(args, *, check=True, timeout=30):
    result = subprocess.run([str(x) for x in args], env=ENV, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
    if check and result.returncode:
        # Do not print launchctl's entire environment-bearing job description.
        raise RuntimeError(f"{Path(str(args[0])).name} failed ({result.returncode})")
    return result


def validate_manifest(manifest):
    if manifest.get("public_test_only") is not True:
        raise ValueError("Not a public-data experiment")
    service = manifest.get("service", "")
    if not re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{32}", service):
        raise ValueError("Invalid isolated service name")
    signatures = manifest.get("signatures", {})
    if set(signatures) != set(VERSIONS):
        raise ValueError("Missing test variants")
    for value in signatures.values():
        if not re.fullmatch(r"[0-9a-f]{64}", value.get("sha256", "")):
            raise ValueError("Invalid binary digest")
    if (signatures["A"]["requirement"] != signatures["B"]["requirement"]
            or signatures["A"]["cdhash"] == signatures["B"]["cdhash"]):
        raise ValueError("Not two versions of one signing identity")
    return service


def activate(root, name):
    stage = root / "active.next"
    shutil.copyfile(root / "versions" / name, stage)
    stage.chmod(0o755)
    stage.replace(root / "sender")


def probe(root, action):
    return json.loads(call([root / "sender", action, "system"]).stdout)


def remove_owned_item(root):
    result = call([root / "sender", "delete", "system"], check=False)
    value = json.loads(result.stdout)
    if value.get("deleted") is True:
        return
    if value.get("error_status") == -25300:
        return  # SecItemAdd did not create the fixture.
    raise RuntimeError("Test-item cleanup needs inspection; retain experiment directory")


def job_config(root, label, user, output):
    return {
        "Label": label, "ProgramArguments": [str(root / "sender"), "read", "system"],
        "UserName": user.pw_name, "GroupName": __import__("grp").getgrgid(user.pw_gid).gr_name,
        "RunAtLoad": True, "ProcessType": "Background", "Umask": 0o077,
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {**ENV, "HOME": user.pw_dir},
        "StandardOutPath": str(output), "StandardErrorPath": "/dev/null",
    }


def background_read(root, label, user):
    output = root / "state/read.json"
    output.unlink(missing_ok=True)
    plist = root / "probe.plist"
    plist.write_bytes(plistlib.dumps(job_config(root, label, user, output)))
    plist.chmod(0o644)
    target = "system/" + label
    if call(["/bin/launchctl", "print", target], check=False).returncode == 0:
        raise ValueError("Temporary job label already exists")
    attempted = False
    try:
        attempted = True
        call(["/bin/launchctl", "bootstrap", "system", plist])
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if output.exists() and output.stat().st_size:
                if output.is_symlink() or output.stat().st_size > 8192:
                    raise ValueError("Unexpected probe output")
                try:
                    value = json.loads(output.read_text())
                except json.JSONDecodeError:
                    time.sleep(0.1)
                    continue
                state = call(["/bin/launchctl", "print", target]).stdout
                if re.search(r"^\s*pid = \d+", state, re.M):
                    time.sleep(0.1)
                    continue
                if "last exit code = 0" not in state or value.get("uid") != user.pw_uid:
                    raise ValueError("Probe did not exit cleanly as selected non-root user")
                return value
            time.sleep(0.2)
        raise TimeoutError("Bounded background probe timed out")
    finally:
        if attempted and call(["/bin/launchctl", "print", target], check=False).returncode == 0:
            call(["/bin/launchctl", "bootout", target])
            if call(["/bin/launchctl", "print", target], check=False).returncode == 0:
                raise RuntimeError("Temporary job still loaded")


def assess(checks, before, after, uid):
    order = ["A", "B", "wrong-identifier", "wrong-signer", "A"]
    if [c.get("version") for c in checks] != order:
        return False
    for i, result in enumerate(checks):
        if result.get("uid") != uid or result.get("scope") != "system":
            return False
        expected_build = "B" if i == 1 else "A"
        if result.get("build") != expected_build:
            return False
        if i in (0, 1, 4):
            if result.get("status") != 0 or result.get("matched") is not True:
                return False
        elif result.get("status") not in (-25293, -25308) or result.get("matched") is not False:
            return False
    return before == after


def run(package, username):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError("System-domain experiment requires administrator execution")
    user = pwd.getpwnam(username)
    if user.pw_uid == 0:
        raise ValueError("Background reader must be non-root")
    if (package / "system-result.json").exists() or (package / "system-result.json").is_symlink():
        raise ValueError("Experiment already has a result; refuse repeat execution")
    manifest = json.loads((package / "manifest.json").read_text())
    service = validate_manifest(manifest)
    nonce = service[len(PREFIX):]
    root = SYSTEM_PARENT / ("HomeButlerSigningProbe-" + nonce)
    if root.exists():
        raise ValueError("Existing experiment requires inspection; refuse duplicate execution")
    # Validate all package bytes BEFORE creating anything in the system domain.
    binaries = {}
    for name in VERSIONS:
        path = package / "versions" / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Invalid probe executable")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["signatures"][name]["sha256"]:
            raise ValueError("Test executable changed")
        binaries[name] = data
    os.umask(0o077)
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    (root / "versions").mkdir(mode=0o755)
    (root / "versions").chmod(0o755)
    (root / "state").mkdir(mode=0o700)
    os.chown(root / "state", user.pw_uid, user.pw_gid)
    for name, data in binaries.items():
        path = root / "versions" / name
        path.write_bytes(data)
        path.chmod(0o755)
        call(["/usr/bin/codesign", "--verify", "--strict", path])
    (root / "manifest.json").write_text(json.dumps(manifest))
    report = {"public_test_only": True, "scope": "system_keychain_system_launchd",
              "uid": user.pw_uid, "checks": [], "passed": False}
    attempted_create = False
    try:
        activate(root, "A")
        initial = call([root / "sender", "metadata", "system"], check=False)
        if json.loads(initial.stdout).get("error_status") != -25300:
            raise ValueError("Test service already exists or absence could not be established")
        attempted_create = True
        first = probe(root, "create")
        report["initial_metadata"] = first["metadata"]
        for index, version in enumerate(("A", "B", "wrong-identifier", "wrong-signer", "A")):
            activate(root, version)
            label = service + ".read-" + str(index)
            print("Testing " + version + " in system launchd as non-root", flush=True)
            result = background_read(root, label, user)
            report["checks"].append({"version": version, **result})
        activate(root, "A")
        report["final_metadata"] = probe(root, "metadata")["metadata"]
        report["passed"] = assess(report["checks"], report["initial_metadata"],
                                  report["final_metadata"], user.pw_uid)
    finally:
        activate(root, "A")
        if attempted_create:
            remove_owned_item(root)
            report["test_item_removed"] = True
        else:
            report["test_item_removed"] = False
        (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        (root / "result.json").chmod(0o644)
        print("Result: " + str(root / "result.json"), flush=True)
    # Return the report to the invoking user without following an existing path/link.
    dest = package / "system-result.json"
    with dest.open("x") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    os.chown(dest, user.pw_uid, user.pw_gid)
    shutil.rmtree(root)
    print("SYSTEM_SIGNING_PROBE_PASSED" if report["passed"] else "SYSTEM_SIGNING_PROBE_FAILED", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Interrupted; cleaning up public test item")
    for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    try:
        result = run(args.package.resolve(), args.user)
    except Exception as error:
        raise SystemExit("System probe stopped: " + str(error))
    raise SystemExit(0 if result["passed"] else 1)
