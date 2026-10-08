"""One-time administrator installation. No telemetry credential enters Python.

The native ACL migrator preserves the existing password value. A public System
Keychain migration/rollback experiment must pass before production is touched.
"""
import argparse
import copy
import fcntl
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
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u


def captured(args, timeout=30, check=True):
    r = subprocess.run([str(x) for x in args], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, env=u.ENV, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError("Command failed: " + Path(str(args[0])).name)
    return r


def signing_info(path):
    r = captured(["/usr/bin/codesign", "-d", "-r-", "--verbose=4", path])
    text = r.stdout + r.stderr
    return {"cdhash": re.search(r"^CDHash=(.+)$", text, re.M).group(1),
            "requirement": re.search(r"^(?:# )?designated => (.+)$", text, re.M).group(1)}


def read_sources(package):
    manifest = json.loads((package / "package.json").read_text())
    if set(manifest) != {"sha", "files"} or not u.SHA.fullmatch(manifest["sha"]):
        raise ValueError("Invalid prepared package")
    required = {"bootstrap.py", "update.py", "settings.py", "signer", "probe.swift", "deployment-template.json"}
    required.update("source/" + name for name in u.FILES)
    if set(manifest["files"]) != required:
        raise ValueError("Incomplete prepared package")
    for name, expected in manifest["files"].items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or (package / path).is_symlink():
            raise ValueError("Unsafe package entry")
        if u.digest((package / path).read_bytes()) != expected:
            raise ValueError("Prepared file changed")
    sources = {name: (package / "source" / name).read_bytes() for name in u.FILES}
    return manifest["sha"], sources


def provision_signer(root):
    signer = root / "updater/signer"
    private = root / "signer"
    private.mkdir(mode=0o700)
    u.call([signer, "create", private])
    conf = private / "openssl.cnf"
    conf.write_text("""[req]
distinguished_name=dn
x509_extensions=extensions
prompt=no
[dn]
CN=HomeButler Mac local update signing
[extensions]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
""")
    try:
        u.call(["/usr/bin/openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes", "-x509",
                "-days", "3650", "-config", conf, "-keyout", private / "identity.pem",
                "-out", private / "certificate.pem"])
        u.call(["/usr/bin/openssl", "pkcs12", "-export", "-inkey", private / "identity.pem",
                "-in", private / "certificate.pem", "-out", private / "identity.p12",
                "-passout", "pass:PUBLIC-LOCAL-P12-TRANSPORT"])
        (private / "identity.pem").chmod(0o600)
        (private / "identity.p12").chmod(0o600)
        u.call([signer, "import", private])
    finally:
        (private / "identity.pem").unlink(missing_ok=True)
        (private / "identity.p12").unlink(missing_ok=True)
        u.call([signer, "lock", private])
    line = captured(["/usr/bin/openssl", "x509", "-in", private / "certificate.pem",
                     "-noout", "-fingerprint", "-sha1"]).stdout
    fingerprint = line.strip().split("=")[-1].replace(":", "").lower()
    if not u.SHA.fullmatch(fingerprint):
        raise ValueError("Invalid local identity")
    return fingerprint


def one_shot(root, c, args, label):
    d = c["deployment"]
    log = root / "state/probe-output.txt"
    log.unlink(missing_ok=True)
    plist = root / "one-shot.plist"
    value = {"Label": label, "ProgramArguments": [str(x) for x in args],
             "UserName": d["user"], "GroupName": d["group"], "RunAtLoad": True,
             "ProcessType": "Background", "WorkingDirectory": str(root), "Umask": 0o077,
             "StandardOutPath": str(log), "StandardErrorPath": "/dev/null"}
    plist.write_bytes(plistlib.dumps(value))
    plist.chmod(0o644)
    target = "system/" + label
    if u.call(["/bin/launchctl", "print", target], check=False) == 0:
        raise ValueError("Probe job already exists")
    try:
        u.call(["/bin/launchctl", "bootstrap", "system", plist])
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if log.exists() and log.stat().st_size:
                if log.is_symlink() or log.stat().st_size > 8192:
                    raise ValueError("Unexpected probe output")
                state = captured(["/bin/launchctl", "print", target]).stdout
                if not re.search(r"^\s*pid = \d+", state, re.M):
                    if "last exit code = 0" not in state:
                        raise ValueError("Background probe failed")
                    return log.read_text()
            time.sleep(0.2)
        raise TimeoutError("Background probe timed out")
    finally:
        if u.call(["/bin/launchctl", "print", target], check=False) == 0:
            u.call(["/bin/launchctl", "bootout", target])


def public_migration_test(root, c, package, sha, sources):
    nonce = uuid.uuid4().hex
    test = Path("/Library/Application Support") / ("HomeButlerUpdateProbe-" + nonce)
    test.mkdir(mode=0o755)
    test.chmod(0o755)
    (test / "releases").mkdir(mode=0o755)
    (test / "releases").chmod(0o755)
    (test / "state").mkdir(mode=0o700)
    os.chown(test / "state", c["deployment"]["uid"], c["deployment"]["gid"])
    service = "org.homebutler.telemetry.update-probe." + nonce
    config = "enum ProbeConfig {\n" + "\n".join("static let " + name + " = " + json.dumps(value)
        for name, value in {"fixture": str(test / "unused.keychain-db"), "service": service, "build": "original"}.items()) + "\n}\n"
    # This reviewed public-fixture source uses the same item account as the
    # isolated production-sender build. It never references the real item.
    fixture = (package / "probe.swift").read_text().replace('"public-fixture"', '"192.0.2.20"')
    (test / "main.swift").write_text(config + fixture)
    original = test / "original"
    u.call(["/usr/bin/swiftc", "-module-cache-path", test / "cache", test / "main.swift", "-o", original], timeout=180)
    u.call(["/usr/bin/codesign", "--force", "--sign", "-", original])
    original.chmod(0o755)
    tc = copy.deepcopy(c)
    tc["deployment"].update(install=str(test), service=service, account="192.0.2.20",
                             origin="https://probe.example.invalid", label=service)
    tc["original"].update(sender=str(original), cdhash=signing_info(original)["cdhash"])
    u.build(test, tc, sha, sources, signer_root=root)
    u.select(test, sha)
    updated = test / "current/bin/mini-telemetry"
    attempted = False
    report = {"service": service, "passed": False, "public_test_only": True}
    try:
        absent = captured([original, "metadata", "system"], check=False)
        if json.loads(absent.stdout).get("error_status") != -25300:
            raise ValueError("Public test item already exists")
        attempted = True
        initial = json.loads(captured([original, "create", "system"]).stdout)["metadata"]
        u.call([updated, "migrate-access"])
        result = one_shot(test, tc, [updated, "public-access-test"], service + ".new")
        if result.strip() != "PUBLIC_ACCESS_PASSED":
            raise ValueError("New public-item reader failed")
        u.call([updated, "restore-access"])
        result = json.loads(one_shot(test, tc, [original, "read", "system"], service + ".old"))
        if result.get("uid") != tc["deployment"]["uid"] or result.get("matched") is not True or result.get("status") != 0:
            raise ValueError("Original public-item reader failed after rollback")
        final = json.loads(captured([original, "metadata", "system"]).stdout)["metadata"]
        if initial != final:
            raise ValueError("Public-item metadata not restored exactly")
        report.update(passed=True, metadata_restored=True, new_reader_passed=True, original_reader_passed=True)
    finally:
        # A migration may have committed before its validation returned. The
        # reverse transition accepts only this fixture's exact two identities.
        if attempted:
            exists = json.loads(captured([original, "metadata", "system"], check=False).stdout)
            if exists.get("error_status") != -25300:
                u.call([updated, "restore-access"])
            value = json.loads(captured([original, "delete", "system"], check=False).stdout)
            if value.get("deleted") is not True and value.get("error_status") != -25300:
                raise RuntimeError("Public fixture cleanup needs inspection at " + str(test))
        u.atomic_json(root / "public-migration-test.json", report)
    shutil.rmtree(test)
    if not report["passed"]:
        raise RuntimeError("Public migration experiment did not pass")


def put_plist(path, value):
    with path.open("xb") as out:
        out.write(plistlib.dumps(value))
    path.chmod(0o644)


def updater_job(root, c):
    return {"Label": c["deployment"]["label"] + ".updater",
            "ProgramArguments": [str(root / "runtime" / c["deployment"]["python"]), "-I",
                                 str(root / "updater/update.py"), "--install", str(root)],
            "UserName": "root", "RunAtLoad": True, "StartInterval": 300,
            "ProcessType": "Background", "WorkingDirectory": str(root), "Umask": 0o077,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def switch(root, c, sha):
    original = c["original"]
    old_plist = Path("/Library/LaunchDaemons") / (original["label"] + ".plist")
    new_plist = Path("/Library/LaunchDaemons") / (c["deployment"]["label"] + ".plist")
    updater_plist = Path(str(new_plist).replace(".plist", ".updater.plist"))
    if new_plist.exists() or updater_plist.exists():
        raise ValueError("New job already exists")
    if u.digest(old_plist.read_bytes()) != original["plist_sha256"]:
        raise ValueError("Original job changed")
    if u.digest(Path(original["sender"]).read_bytes()) != original["sha256"]:
        raise ValueError("Original sender changed")
    old_root = Path(original["sender"]).parent.parent
    updated = root / "current/bin/mini-telemetry"
    u.atomic_json(root / "installation.json", {"phase": "prepared", "sha": sha})
    old_stopped = False
    try:
        with u.telemetry_lock(old_root):
            old_stopped = True
            u.atomic_json(root / "installation.json", {"phase": "switching", "sha": sha})
            u.call(["/bin/launchctl", "bootout", "system/" + original["label"]])
            u.call([updated, "migrate-access"])
        u.user_call(c, "sample")
        u.user_call(c, "verify-access")
        before = u.acknowledgements(root, sha)
        put_plist(new_plist, u.job(c))
        u.start_job(c)
        u.wait_health(root, sha, before)
        # Keep exact old plist bytes for explicit rollback; remove its startup
        # entry so a reboot cannot start both senders against different ACLs.
        (root / "original.plist").write_bytes(old_plist.read_bytes())
        old_plist.unlink()
        put_plist(updater_plist, updater_job(root, c))
        u.call(["/bin/launchctl", "bootstrap", "system", updater_plist])
        u.atomic_json(root / "installation.json", {"phase": "active", "sha": sha})
    except BaseException:
        if updater_plist.exists():
            u.call(["/bin/launchctl", "bootout", "system/" + c["deployment"]["label"] + ".updater"], check=False)
            updater_plist.unlink()
        if new_plist.exists():
            u.stop_job(c)
            new_plist.unlink()
        if old_stopped:
            u.call([updated, "restore-access"])
            if not old_plist.exists():
                put_plist(old_plist, plistlib.loads((root / "original.plist").read_bytes()))
            if u.call(["/bin/launchctl", "print", "system/" + original["label"]], check=False) != 0:
                u.call(["/bin/launchctl", "bootstrap", "system", old_plist])
        u.atomic_json(root / "installation.json", {"phase": "rolled_back", "sha": sha})
        raise


def run(package):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError("Administrator execution required")
    sha, sources = read_sources(package)
    c = json.loads((package / "deployment-template.json").read_text())
    root = Path(c["deployment"]["install"])
    u.validate(c["deployment"])
    if root.exists():
        raise ValueError("Installation already exists; inspect before resuming")
    user = pwd.getpwnam(c["deployment"]["user"])
    if user.pw_uid != c["deployment"]["uid"] or user.pw_uid == 0:
        raise ValueError("Configured non-root user changed")
    original = Path(c["original"]["sender"])
    u.trusted(original)
    if u.digest(original.read_bytes()) != c["original"]["sha256"]:
        raise ValueError("Original sender changed")
    os.umask(0o077)
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    for name in ("updater", "releases"):
        (root / name).mkdir(mode=0o755)
        (root / name).chmod(0o755)
    for name in ("update.py", "settings.py", "signer"):
        shutil.copyfile(package / name, root / "updater" / name)
        (root / "updater" / name).chmod(0o755 if name == "signer" else 0o644)
    (root / "state").mkdir(mode=0o700)
    os.chown(root / "state", c["deployment"]["uid"], c["deployment"]["gid"])
    runtime = original.parent.parent / "runtime"
    u.trusted(runtime, True)
    for entry in runtime.rglob("*"):
        if entry.is_symlink() and (os.path.isabs(os.readlink(entry)) or not entry.resolve().is_relative_to(runtime.resolve())):
            raise ValueError("Runtime symlink escapes original installation")
        if not entry.is_symlink():
            u.trusted(entry, entry.is_dir())
    shutil.copytree(runtime, root / "runtime", symlinks=True)
    certificate = provision_signer(root)
    c["certificate_sha1"] = certificate
    c["requirement"] = 'identifier "org.homebutler.telemetry.sender" and certificate leaf = H"' + certificate + '"'
    u.atomic_json(root / "update.json", c)
    u.load_config(root)
    with (root / "update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("Validating public System Keychain migration and rollback...", flush=True)
        public_migration_test(root, c, package, sha, sources)
        print("PUBLIC_MIGRATION_PASSED. Building initial signed agent...", flush=True)
        u.build(root, c, sha, sources)
        u.select(root, sha)
        switch(root, c, sha)
    print("AUTO_UPDATE_ACTIVE: two natural heartbeats confirmed; checks every 5 minutes.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    for signum in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        run(args.package.resolve())
    except Exception as error:
        raise SystemExit("Installation stopped: " + str(error))
