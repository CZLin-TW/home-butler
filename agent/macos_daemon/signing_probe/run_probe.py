"""Build/run a PUBLIC-data Keychain upgrade experiment, never a production updater.

All identities, passwords, keychains and item names are disposable test fixtures.
No real credential is requested, queried or copied. No trust-store changes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

SOURCE = Path(__file__).resolve().parent
PUBLIC_PASSWORD = "PUBLIC-THROWAWAY-KEYCHAIN-PASSWORD"
IDENTIFIER = "org.homebutler.telemetry.signing-probe"
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()), "LANG": "en_US.UTF-8"}


def call(args, *, timeout=30, check=True):
    result = subprocess.run([str(a) for a in args], env=ENV, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} failed ({result.returncode}): {result.stderr[-1500:]} {result.stdout[-1500:]}")
    return result


def search_list():
    return [line.strip().strip('"') for line in call(["/usr/bin/security", "list-keychains", "-d", "user"]).stdout.splitlines()]


def remove_test_search_entry(path):
    entries = search_list()
    retained = [p for p in entries if Path(p) not in (path, Path(str(path) + "-db"))]
    if retained != entries:
        call(["/usr/bin/security", "list-keychains", "-d", "user", "-s", *retained])


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def activate(root, name):
    active = root / "active/sender"
    stage = active.with_suffix(".next")
    shutil.copyfile(root / "versions" / name, stage)
    stage.chmod(0o700)
    stage.replace(active)
    return active


def command(binary, mode, scope="fixture"):
    return json.loads(call([binary, mode, scope], timeout=20).stdout)


def build(root):
    root.mkdir(mode=0o700)
    for name in ("active", "versions", "build"):
        (root / name).mkdir(mode=0o700)
    nonce = uuid.uuid4().hex
    (root / "probe-owner.json").write_text(json.dumps({"nonce": nonce, "public_test_only": True}))
    fixture = root / "fixture.keychain-db"
    service = "org.homebutler.telemetry.signing-probe." + nonce
    print("Compiling public-data probe versions", flush=True)
    for version in ("A", "B"):
        constants = "enum ProbeConfig {\n" + "\n".join(
            "static let " + key + " = " + json.dumps(value)
            for key, value in {"fixture": str(fixture), "service": service, "build": version}.items()
        ) + "\n}\n"
        main = root / "build/main.swift"
        main.write_text(constants + (SOURCE / "probe.swift").read_text())
        result = call(["/usr/bin/swiftc", "-module-cache-path", root / "build/cache",
                       main, "-o", root / "versions" / version], timeout=120, check=False)
        (root / "build" / (version + "-compiler.log")).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError("Swift compilation failed: " + result.stderr[-2500:])
    print("Creating disposable signing identity", flush=True)
    config = root / "build/openssl.cnf"
    config.write_text("""[req]
distinguished_name=dn
x509_extensions=extensions
prompt=no
[dn]
CN=HomeButler DISPOSABLE signing probe
[extensions]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
""")
    key = root / "build/disposable.key"
    cert = root / "certificate.pem"
    p12 = root / "build/disposable.p12"
    call(["/usr/bin/openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-x509",
          "-days", "2", "-config", config, "-keyout", key, "-out", cert])
    key.chmod(0o600)
    call(["/usr/bin/openssl", "pkcs12", "-export", "-inkey", key, "-in", cert, "-out", p12,
          "-passout", "pass:" + PUBLIC_PASSWORD])
    p12.chmod(0o600)
    chain = root / "signing.keychain-db"
    try:
        call(["/usr/bin/security", "create-keychain", "-p", PUBLIC_PASSWORD, chain])
        remove_test_search_entry(chain)
        call(["/usr/bin/security", "set-keychain-settings", chain])
        call(["/usr/bin/security", "unlock-keychain", "-p", PUBLIC_PASSWORD, chain])
        call(["/usr/bin/security", "import", p12, "-k", chain, "-P", PUBLIC_PASSWORD,
              "-T", "/usr/bin/codesign"])
        # Only the disposable private signing key is targeted. The application ACL
        # permits codesign specifically; no change to password-item partitions.
        call(["/usr/bin/security", "set-key-partition-list", "-s", "-t", "private",
              "-S", "apple-tool:", "-k", PUBLIC_PASSWORD, chain])
        fingerprint = call(["/usr/bin/openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha1"]).stdout
        certificate_hash = fingerprint.strip().split("=")[-1].replace(":", "").lower()
        if not re.fullmatch(r"[0-9a-f]{40}", certificate_hash):
            raise ValueError("Invalid test certificate fingerprint")
        shutil.copyfile(root / "versions/A", root / "versions/wrong-identifier")
        shutil.copyfile(root / "versions/A", root / "versions/wrong-signer")
        for version in ("A", "B", "wrong-identifier"):
            identifier = IDENTIFIER + ".wrong" if version == "wrong-identifier" else IDENTIFIER
            requirement = f'designated => identifier "{identifier}" and certificate leaf = H"{certificate_hash}"'
            call(["/usr/bin/codesign", "--force", "--sign", certificate_hash, "--keychain", chain,
                  "--timestamp=none", "--identifier", identifier, "--requirements", "=" + requirement,
                  root / "versions" / version])
        call(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", IDENTIFIER,
              root / "versions/wrong-signer"])
        signatures = {}
        for version in ("A", "B", "wrong-identifier", "wrong-signer"):
            path = root / "versions" / version
            path.chmod(0o700)
            call(["/usr/bin/codesign", "--verify", "--strict", path])
            result = call(["/usr/bin/codesign", "-d", "-r-", "--verbose=4", path])
            info = result.stdout + result.stderr
            signatures[version] = {
                "sha256": sha256(path),
                "cdhash": re.search(r"^CDHash=(.+)$", info, re.M).group(1),
                "requirement": re.search(r"^(?:# )?designated => (.+)$", info, re.M).group(1),
            }
        if signatures["A"]["cdhash"] == signatures["B"]["cdhash"]:
            raise ValueError("Versions must differ")
        if signatures["A"]["requirement"] != signatures["B"]["requirement"]:
            raise ValueError("Stable designated requirement missing")
        manifest = {"service": service, "fixture": str(fixture), "signatures": signatures,
                    "certificate_sha1": certificate_hash, "public_test_only": True}
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest
    finally:
        # Explicit paths only. Never change default/trusted keychains or identities.
        if chain.exists():
            call(["/usr/bin/security", "delete-keychain", chain])
        remove_test_search_entry(chain)
        key.unlink(missing_ok=True)
        p12.unlink(missing_ok=True)


def experiment(root, manifest):
    report = {"public_test_only": True, "scope": "isolated_user_keychain", "checks": []}
    active = activate(root, "A")
    created = False
    fixture_created = False
    try:
        command(active, "create-fixture")
        fixture_created = True
        first = command(active, "create")
        created = True
        before = first["metadata"]
        report["initial_metadata"] = before
        for name in ("A", "B", "wrong-identifier", "wrong-signer", "A"):
            active = activate(root, name)
            result = command(active, "read")
            report["checks"].append({"version": name, **result})
        active = activate(root, "A")
        after = command(active, "metadata")["metadata"]
        report["final_metadata"] = after
        report["acl_unchanged"] = before == after
        checks = report["checks"]
        report["upgrade_and_rollback_passed"] = all(checks[i]["matched"] and checks[i]["status"] == 0 for i in (0, 1, 4))
        report["negative_controls_denied"] = all(not checks[i]["matched"] and checks[i]["status"] in (-25293, -25308) for i in (2, 3))
        report["passed"] = all(report[k] for k in ("acl_unchanged", "upgrade_and_rollback_passed", "negative_controls_denied"))
        return report
    finally:
        if created:
            active = activate(root, "A")
            command(active, "delete")
        if fixture_created:
            call(["/usr/bin/security", "delete-keychain", manifest["fixture"]])
        report["test_keychain_removed"] = not Path(manifest["fixture"]).exists()
        (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("Run the isolated signing experiment as a normal user")
    root = args.output.resolve()
    if root.exists():
        raise SystemExit("Refuse existing experiment directory")
    os.umask(0o077)
    before = search_list()
    default_before = call(["/usr/bin/security", "default-keychain", "-d", "user"]).stdout
    manifest = build(root)
    print("Running A -> B -> negative controls -> A with all Keychain UI disabled", flush=True)
    report = experiment(root, manifest)
    report["search_list_unchanged"] = before == search_list()
    report["default_keychain_unchanged"] = default_before == call(["/usr/bin/security", "default-keychain", "-d", "user"]).stdout
    report["passed"] = report["passed"] and report["search_list_unchanged"] and report["default_keychain_unchanged"]
    (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
