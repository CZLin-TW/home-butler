# Cross-version signing experiment

This is the prerequisite experiment for full macOS agent auto-update, not an
updater or a production migration. No production sender or credential is queried.

`run_probe.py` compiles two different Swift executables, signs both with one
disposable self-signed certificate and an explicit certificate-pinned designated
requirement, then tests A → B → wrong identifier → wrong signer → A at the same
executable path. All password values are public sentinels; native Keychain UI is
disabled. Negative controls must fail with an authorization error, not a missing
item or malformed executable. Password-item ACL metadata must remain unchanged.

Signing private key, PKCS#12 and dedicated keychains are temporary. The signing
key's application ACL permits `/usr/bin/codesign` specifically. No certificate is
trusted system-wide and no production password-item ACL/partition is changed.
Default Keychain and user search-list preservation are checked.

## Local experiment

Run as a normal user on macOS with Command Line Tools and Keychain access:

```sh
mkdir -p agent/macos_daemon/.build
python3 agent/macos_daemon/signing_probe/run_probe.py \
  --output agent/macos_daemon/.build/signing-probe
```

Output must not exist. The disposable certificate is valid for two days; never
use it for production. Interrupted experiments may leave their named fixtures
for inspection. Never use broad Keychain cleanup commands.

## System-domain experiment

`system_probe.py` consumes signed `versions/` and `manifest.json` from the local
experiment. Administrator execution is required. It verifies binary hashes and
signatures, creates a uniquely named temporary directory and one **public**
System Keychain item, then performs each read as the selected non-root user via
a temporary system LaunchDaemon. Jobs have no repeat trigger or KeepAlive and no
persistent LaunchDaemons plist. Results must report the expected UID and build.

The controller removes each job, restores A, deletes only its own public item,
and writes `system-result.json`. A successful run removes its temporary system
directory. Errors can retain a report/directory for inspection; do not rerun
blindly. SIGKILL/power loss cannot execute cleanup. Unique service/job/directory
collisions are refused. No API key, network or real telemetry is involved.

```sh
sudo /usr/bin/python3 -I agent/macos_daemon/signing_probe/system_probe.py \
  --package /absolute/path/to/signing-probe --user <non-root-user>
```

The prepared local `.command` verifies controller/manifest hashes before this
step. Enter the administrator password only at sudo's own Terminal prompt.

## Evidence and limits

2026-10-09: isolated-keychain and **System Keychain + non-root system
LaunchDaemon** A/B/A passed. Both wrong-identifier and wrong-signer controls were
denied (-25293), all reads used the selected UID, and ACL metadata was identical
before/after. The actual databases were **0x100**. The System test had one inert
creator-cdhash partition record; the isolated test had none. All temporary System
jobs, the public item and its directory were removed. Signing private material
and dedicated user keychains were removed; default/search list were preserved.

This establishes stable certificate-pinned trust for the tested 0x100 behavior;
it does not establish 0x101/0x200, reboot-before-login or production migration.
[auto_update](../auto_update/README.md) additionally gates first activation on a
public-item migration/rollback test using its actual native migration code.

Eight offline tests cover result interpretation, non-root one-shot jobs, service
scope, partial-creation cleanup and uncertain-bootstrap cleanup.

References: [Apple signing](https://developer.apple.com/library/archive/technotes/tn2206/),
[Keychain implementations](https://developer.apple.com/documentation/Technotes/tn3137-on-mac-keychains),
[Apple partition identity](https://github.com/apple-oss-distributions/Security/blob/main/securityd/src/clientid.cpp).
