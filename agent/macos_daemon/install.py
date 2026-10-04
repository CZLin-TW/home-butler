"""User-run administrator installer. No password/key is read by this Python code."""

import datetime
import fcntl
import hashlib
import json
import os
import plistlib
import pwd
import grp
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from settings import load

PACKAGE = Path(__file__).resolve().parent
CONFIG = load(
    PACKAGE
    / (
        "deployment.json"
        if (PACKAGE / "deployment.json").exists()
        else "config.example.json"
    )
)
DEST = Path(CONFIG["install"])
LABEL = CONFIG["label"]
PROBE_LABEL = LABEL + ".probe"
PLIST = Path("/Library/LaunchDaemons") / (LABEL + ".plist")
PROBE_PLIST = Path("/Library/LaunchDaemons") / (PROBE_LABEL + ".plist")
PREVIOUS = CONFIG["previous"]
OLD_LABEL = PREVIOUS["label"] if PREVIOUS else ""
OLD_PLIST = Path(CONFIG["home"]) / "Library/LaunchAgents" / (OLD_LABEL + ".plist")
BACKUP = DEST / "previous-launchagent.plist"
SENDER = DEST / "bin/mini-telemetry"
ENV = {
    "HOME": CONFIG["home"],
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "en_US.UTF-8",
}


class Stop(Exception):
    pass


def interrupted(signum, frame):
    raise KeyboardInterrupt()


def validate_identity():
    user = pwd.getpwnam(CONFIG["user"])
    if (
        user.pw_uid != CONFIG["uid"]
        or user.pw_dir != CONFIG["home"]
        or grp.getgrnam(CONFIG["group"]).gr_gid != CONFIG["gid"]
    ):
        raise Stop("Configured user/group/home does not match this host")


OPERATION_LOCKS = []


def lock_operation():
    """Keep one user-run mutating operation active; process exit releases the lock."""
    descriptor = os.open(
        DEST / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        if os.fstat(descriptor).st_uid != os.geteuid():
            raise Stop("Unexpected operation lock owner")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, Stop):
        os.close(descriptor)
        raise Stop(
            "Another operation is active or lock is unsafe; do not retry concurrently"
        )
    OPERATION_LOCKS.append(descriptor)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    result = {}
    for p in sorted(path.rglob("*")):
        relative = str(p.relative_to(path))
        if p.is_symlink():
            target = os.readlink(p)
            if Path(target).is_absolute() or not p.resolve().is_relative_to(
                path.resolve()
            ):
                raise Stop("Runtime symlink leaves package")
            result[relative] = {"link": target}
        elif p.is_file():
            result[relative] = {"sha256": digest(p)}
        elif not p.is_dir():
            raise Stop("Unexpected file kind")
    return result


def run(args, visible=False, check=True):
    r = subprocess.run(
        [str(a) for a in args],
        env=ENV,
        stdin=None if visible else subprocess.DEVNULL,
        stdout=None if visible else subprocess.DEVNULL,
        stderr=None if visible else subprocess.DEVNULL,
    )
    if check and r.returncode:
        raise Stop("Command failed: " + str(args[0]) + "; exit=" + str(r.returncode))
    return r.returncode


def launch(*args, check=True):
    return run(["/bin/launchctl", *args], check=check)


def loaded(domain):
    return launch("print", domain, check=False) == 0


def configuration(probe=False):
    return {
        "Label": PROBE_LABEL if probe else LABEL,
        "ProgramArguments": [str(SENDER), "probe" if probe else "run"],
        "UserName": CONFIG["user"],
        "GroupName": CONFIG["group"],
        "RunAtLoad": True,
        "ProcessType": "Background",
        "WorkingDirectory": str(DEST),
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "Umask": 0o077,
        **({} if probe else {"StartInterval": 60}),
    }


def put_plist(path, value):
    if path.exists() or path.is_symlink():
        raise Stop("Refuse to overwrite existing plist")
    with path.open("xb") as f:
        f.write(plistlib.dumps(value))
    path.chmod(0o644)
    os.chown(path, 0, 0)


def old_bytes():
    if not PREVIOUS:
        return None
    if OLD_PLIST.is_symlink():
        raise Stop("Unexpected old plist link")
    data = OLD_PLIST.read_bytes()
    p = plistlib.loads(data)
    expected = PREVIOUS["sender"]
    if (
        p.get("Label") != OLD_LABEL
        or p.get("ProgramArguments") != [expected, "run"]
        or p.get("StartInterval") != 60
    ):
        raise Stop("Old agent configuration changed")
    if digest(Path(expected)) != PREVIOUS["sender_sha256"]:
        raise Stop("Old sender changed")
    return data


def audit(message):
    # Root-owned log with fixed messages only; never write subprocess output, inputs or env.
    p = DEST / "installation-audit.log"
    if p.is_symlink():
        raise Stop("Unexpected audit link")
    with p.open("a") as f:
        f.write(
            datetime.datetime.now(datetime.timezone.utc).isoformat()
            + " "
            + message
            + "\n"
        )
    p.chmod(0o600)


def verify_installed(manifest):
    directories = [DEST]
    for tree_root in [DEST / "bin", DEST / "runtime", DEST / "collector"]:
        directories.extend(
            [
                tree_root,
                *(p for p in tree_root.rglob("*") if p.is_dir() and not p.is_symlink()),
            ]
        )
    for directory in directories:
        if directory.is_symlink() or not directory.is_dir():
            raise Stop("Unsafe installed directory")
        st = directory.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise Stop("Unsafe installed directory permissions")
    for name, expected in manifest["bundle"].items():
        p = DEST / name
        if "link" in expected:
            if (
                not p.is_symlink()
                or os.readlink(p) != expected["link"]
                or not p.resolve().is_relative_to(DEST)
            ):
                raise Stop("Installed link mismatch")
        elif p.is_symlink() or digest(p) != expected["sha256"]:
            raise Stop("Installed file mismatch")
        st = p.lstat()
        if st.st_uid != 0 or (not p.is_symlink() and st.st_mode & 0o022):
            raise Stop("Installed code ownership/permissions mismatch")
    run(["/usr/bin/codesign", "--verify", "--strict", SENDER])


def prepare(manifest):
    if DEST.exists() or DEST.is_symlink():
        if json.loads((DEST / "package-manifest.json").read_text()) != manifest:
            raise Stop("Existing install differs; no overwrite")
        verify_installed(manifest)
        return
    if (
        PLIST.exists()
        or PROBE_PLIST.exists()
        or loaded("system/" + LABEL)
        or loaded("system/" + PROBE_LABEL)
    ):
        raise Stop("Unexpected existing system job")
    if tree(PACKAGE / "bundle") != manifest["bundle"]:
        raise Stop("Staged package checksum mismatch")
    # Runtime/collectors are root-owned and never run as root.
    shutil.copytree(PACKAGE / "bundle", DEST, symlinks=True)
    for p in [DEST, *DEST.rglob("*")]:
        if p.is_symlink():
            os.lchown(p, 0, 0)
            continue
        mode = 0o755 if p.is_dir() or p.stat().st_mode & 0o111 else 0o644
        p.chmod(mode)
        os.chown(p, 0, 0)
    (DEST / "package-manifest.json").write_text(json.dumps(manifest))
    (DEST / "package-manifest.json").chmod(0o600)
    state = DEST / "state"
    state.mkdir(mode=0o700)
    os.chown(state, CONFIG["uid"], CONFIG["gid"])
    verify_installed(manifest)
    audit("package_installed")


def status_lines():
    p = DEST / "state/status.log"
    if not p.exists():
        return []
    if p.is_symlink():
        raise Stop("Unexpected status log link")
    # Only fixed status tokens survive into installer decisions or reports.
    return [
        line
        for line in p.read_text().splitlines()
        if re.fullmatch(
            r"[0-9TZ:.-]+ (heartbeat acknowledged|sample/send not confirmed; no immediate retry|keychain unavailable status=-?\d+; no heartbeat sent|probe passed expected_uid format_aware_acl no_network|probe failed validation|probe failed keychain status=-?\d+)",
            line,
        )
    ]


def wait_probe(start):
    for tick in range(35):
        lines = status_lines()[start:]
        if any(
            "probe passed expected_uid format_aware_acl no_network" in x for x in lines
        ):
            return
        if any("probe failed" in x for x in lines):
            raise Stop(
                "System-domain configured UID probe failed; old agent remains active"
            )
        time.sleep(1)
    raise Stop("System-domain probe timed out; old agent remains active")


def real_probe():
    if (DEST / "probe-ok").exists():
        return
    if (
        PROBE_PLIST.exists()
        or loaded("system/" + PROBE_LABEL)
        or (DEST / "probe-owned").exists()
    ):
        raise Stop("Prior probe remains; request read-only diagnosis before retry")
    try:
        run([SENDER, "probe-create"], visible=True)
        start = len(status_lines())
        put_plist(PROBE_PLIST, configuration(True))
        launch("bootstrap", "system", PROBE_PLIST)
        if not loaded("system/" + PROBE_LABEL):
            raise Stop("Probe not in system domain")
        wait_probe(start)
        # Capture only selected launchd metadata, not environment or full arguments.
        for _ in range(10):
            r = subprocess.run(
                ["/bin/launchctl", "print", "system/" + PROBE_LABEL],
                capture_output=True,
                text=True,
                env=ENV,
            )
            if (
                r.returncode == 0
                and re.search(r"last exit code = 0\b", r.stdout)
                and "state = not running" in r.stdout
            ):
                break
            time.sleep(0.2)
        else:
            raise Stop("Probe did not exit successfully")
        audit("probe_verified_system_domain_configured_uid_format_aware_acl_no_network")
    finally:
        # Only this new temporary job and the public non-secret item created above.
        if loaded("system/" + PROBE_LABEL):
            launch("bootout", "system/" + PROBE_LABEL)
        if PROBE_PLIST.exists():
            PROBE_PLIST.unlink()
        if (DEST / "probe-owned").exists():
            run([SENDER, "probe-delete"], visible=True)
    (DEST / "probe-ok").write_text("verified")
    (DEST / "probe-ok").chmod(0o600)
    print(
        "PROBE_OK：真 system domain、configured UID、無互動、限定 sender 與格式相符的 ACL 與本機採樣均通過；測試 item 已清除。",
        flush=True,
    )


def restore():
    if PLIST.exists() and (
        PLIST.is_symlink() or plistlib.loads(PLIST.read_bytes()) != configuration()
    ):
        raise Stop("Unexpected system plist; refuse stop/removal")
    if not PLIST.exists() and loaded("system/" + LABEL):
        raise Stop("Unknown system job without owned plist")
    if loaded("system/" + LABEL):
        launch("bootout", "system/" + LABEL)
    if PLIST.exists():
        if plistlib.loads(PLIST.read_bytes()) != configuration():
            raise Stop("Unexpected system plist; refuse removal")
        PLIST.unlink()
    if PREVIOUS and BACKUP.exists():
        data = BACKUP.read_bytes()
        if OLD_PLIST.exists() and OLD_PLIST.read_bytes() != data:
            raise Stop("Old plist conflict; refuse overwrite")
        if not OLD_PLIST.exists():
            with OLD_PLIST.open("xb") as f:
                f.write(data)
            OLD_PLIST.chmod(0o600)
            os.chown(OLD_PLIST, CONFIG["uid"], CONFIG["gid"])
        if loaded("gui/" + str(CONFIG["uid"])) and not loaded(
            "gui/" + str(CONFIG["uid"]) + "/" + OLD_LABEL
        ):
            launch("bootstrap", "gui/" + str(CONFIG["uid"]), OLD_PLIST)
    audit("rollback_old_agent_restored_system_key_retained")


def activate(original):
    if PREVIOUS and OLD_PLIST.read_bytes() != original:
        raise Stop("Old plist changed before switch")
    if BACKUP.exists() and BACKUP.read_bytes() != original:
        raise Stop("Rollback backup mismatch")
    if PREVIOUS and not BACKUP.exists():
        BACKUP.write_bytes(original)
        BACKUP.chmod(0o600)
    start = len(status_lines())
    try:
        if PREVIOUS:
            launch("bootout", "gui/" + str(CONFIG["uid"]) + "/" + OLD_LABEL)
        if PREVIOUS:
            OLD_PLIST.unlink()  # backup retained; prevents duplicate at next GUI login
        put_plist(PLIST, configuration())
        launch("bootstrap", "system", PLIST)
        audit("switched_to_system_daemon_configured_uid")
        for tick in range(100):
            lines = status_lines()[start:]
            if any("not confirmed" in x or "keychain unavailable" in x for x in lines):
                raise Stop("New heartbeat failed; restoring old agent")
            good = [x for x in lines if x.endswith("heartbeat acknowledged")]
            if len(good) >= 2:
                audit("two_natural_heartbeat_acknowledgements_verified")
                print(
                    "BOOT_DAEMON_READY：兩次自然回報成功。尚未驗證重開機未登入；不要現在重開機。",
                    flush=True,
                )
                print("\n".join(good[-2:]), flush=True)
                return
            if tick % 15 == 0:
                print("等待新 system job 的兩次自然回報…", flush=True)
            time.sleep(1)
        raise Stop("Two acknowledgements not confirmed; restoring old agent")
    except BaseException:
        restore()
        raise


def main():
    if sys.argv[1:] not in ([], ["--rollback"]):
        raise Stop("Unsupported argument")
    if os.geteuid() != 0 or not all(os.isatty(fd) for fd in (0, 1, 2)):
        raise Stop(
            "Must be personally run through the administrator wrapper in Terminal"
        )
    validate_identity()
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    if digest(PACKAGE / "install.py") != manifest["installer_sha256"]:
        raise Stop("Installer checksum mismatch")
    for name, expected in manifest["support_files"].items():
        if digest(PACKAGE / name) != expected:
            raise Stop("Support file checksum mismatch")
    if sys.argv[1:] == ["--rollback"]:
        verify_installed(manifest)
        if (
            input("輸入 ROLLBACK：停用開機版、恢復原登入版；兩份正式 key 都保留：")
            == "ROLLBACK"
        ):
            restore()
            print("原登入版已恢復；未刪除正式 key。")
        return
    original = old_bytes()
    if PREVIOUS and not loaded("gui/" + str(CONFIG["uid"]) + "/" + OLD_LABEL):
        raise Stop("Current agent not loaded; stop before installation")
    print("將安裝開機版並做真 OS 無秘密 probe；原監控持續運作。", flush=True)
    print(
        "probe 新增與清除僅限 本設定 service + .probe 的測試 item；不讀舊 key，不改其他服務。",
        flush=True,
    )
    if input("輸入 INSTALL 開始安裝與 probe：") != "INSTALL":
        return
    prepare(manifest)
    lock_operation()
    real_probe()
    if not (DEST / "key-ready").exists():
        run([SENDER, "setup"], visible=True)
    print(
        "Key setup complete. Run resume.py personally to validate the formal item in system domain and switch."
    )


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("已取消；請回報目前階段。不要重輸 key 或重跑。")
        sys.exit(2)
    except Exception as e:
        print(str(e) if isinstance(e, Stop) else "安裝未確認；停止，請回報目前階段。")
        sys.exit(1)
