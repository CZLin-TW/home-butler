"""User-run continuation; never creates, updates, deletes or reads credential data."""

import datetime
import hashlib
import json
import os
import plistlib
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
PACKAGE = Path(__file__).resolve().parent
import install as deployment

CONFIG = deployment.CONFIG
LABEL = CONFIG["label"] + ".resume"
PLIST = Path("/Library/LaunchDaemons") / (LABEL + ".plist")
DEST = Path(CONFIG["install"])
MARKER = DEST / "resume-attempt.json"
ENV = deployment.ENV


class Stop(Exception):
    pass


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_package():
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    for name, expected in manifest["support_files"].items():
        if digest(PACKAGE / name) != expected:
            raise Stop("Support file checksum mismatch")
    if digest(PACKAGE / "install.py") != manifest["installer_sha256"]:
        raise Stop("Installer checksum mismatch")


def installer():
    return deployment


def snapshot():
    r = subprocess.run(
        [str(deployment.SENDER), "metadata"], capture_output=True, env=ENV
    )
    if r.returncode:
        raise Stop("唯讀 sender/item/ACL 預檢失敗；停止，不讀 key。")
    return json.loads(r.stdout)


def expected_cdhash():
    return json.loads((PACKAGE / "manifest.json").read_text())["sender_cdhash"]


def validate_before(s):
    signer = expected_cdhash()
    if (
        s.get("count") != 1
        or s.get("sender_cdhash") != signer
        or not s.get("acl")
        or s.get("keychain_path") != "/Library/Keychains/System.keychain"
    ):
        raise Stop("Unique item, exact sender or owning keychain mismatch")
    partitions = s.get("partitions")
    version = s.get("database_version")
    if version in (256, 257) and partitions == []:
        return
    if version == 512 and partitions == [["cdhash:" + signer]]:
        return
    raise Stop("Keychain format/partition mismatch; no automatic ACL repair")


def validate_after(before, after):
    validate_before(after)
    if before != after:
        raise Stop("sender 或授權 metadata 改變；停止，不自動修 ACL。")


def metadata_ready_flow(read, send):
    before = read()
    validate_before(before)
    send(before)
    after = read()
    validate_after(before, after)
    return after


def job_plist(m):
    d = dict(m.configuration())
    d["Label"] = LABEL
    d.pop("StartInterval")
    return d


def parse_timestamp(line):
    return datetime.datetime.fromisoformat(
        line.split(" ", 1)[0].replace("Z", "+00:00")
    ).timestamp()


def ensure_attempt(m, before):
    expected = job_plist(m)
    if MARKER.exists():
        attempt = json.loads(MARKER.read_text())
        if attempt.get("version") != 1 or attempt.get("snapshot") != before:
            raise Stop("已有不同的續接紀錄；停止，請只讀診斷。")
        if attempt.get("acknowledged") is True:
            return None
        if not PLIST.exists() or plistlib.loads(PLIST.read_bytes()) != expected:
            raise Stop("既有續接 job 狀態不完整；停止，不再送。")
        if not m.loaded("system/" + LABEL):
            raise Stop("先前續接 job 未載入；停止，不自動重送。")
        return attempt["started"]
    if PLIST.exists() or m.loaded("system/" + LABEL):
        raise Stop("未知同名續接 job；停止。")
    started = time.time()
    # Durable non-secret marker prevents blind resend after a closed Terminal.
    MARKER.write_text(
        json.dumps({"version": 1, "started": started, "snapshot": before})
    )
    MARKER.chmod(0o600)
    m.put_plist(PLIST, expected)
    m.launch("bootstrap", "system", PLIST)
    m.audit("resume_single_system_job_started")
    return started


def cleanup_job(m):
    if not PLIST.exists() and m.loaded("system/" + LABEL):
        raise Stop("Unknown continuation job without owned plist; stop")
    if PLIST.exists() and plistlib.loads(PLIST.read_bytes()) != job_plist(m):
        raise Stop("續接 plist 變更，拒絕移除。")
    if m.loaded("system/" + LABEL):
        m.launch("bootout", "system/" + LABEL)
    if PLIST.exists():
        PLIST.unlink()
    # Marker retained as evidence; no credential item is removed.


def one_attempt(m, before):
    started = ensure_attempt(m, before)
    if started is None:
        cleanup_job(m)
        return
    for tick in range(40):
        lines = [x for x in m.status_lines() if parse_timestamp(x) >= int(started)]
        if any("not confirmed" in x or "keychain unavailable" in x for x in lines):
            cleanup_job(m)
            raise Stop("原 sender 單次驗證失敗，原版（若有）仍運作；停止，不重送。")
        acknowledged = any(x.endswith("heartbeat acknowledged") for x in lines)
        r = subprocess.run(
            ["/bin/launchctl", "print", "system/" + LABEL],
            capture_output=True,
            text=True,
            env=ENV,
        )
        exited = r.returncode == 0 and "state = not running" in r.stdout
        zero = bool(re.search(r"last exit code = 0\b", r.stdout))
        if acknowledged and exited and zero:
            attempt = json.loads(MARKER.read_text())
            attempt["acknowledged"] = True
            temporary = MARKER.with_suffix(".tmp")
            temporary.write_text(json.dumps(attempt))
            temporary.chmod(0o600)
            temporary.replace(MARKER)
            m.audit("resume_single_system_configured_uid_acknowledged")
            cleanup_job(m)
            return
        if exited and re.search(r"last exit code = [1-9]\d*\b", r.stdout):
            cleanup_job(m)
            raise Stop("原 sender exit 非零；停止，不重送。")
        if tick % 10 == 0:
            print("等待原 sender 的單次 system／configured UID 驗證…", flush=True)
        time.sleep(1)
    cleanup_job(m)
    raise Stop("單次驗證未確認，已停續接 job；原版（若有）保留，不自動重送。")


def main():
    if sys.argv[1:] not in ([], ["--check"]):
        raise Stop("不支援參數。")
    verify_package()
    m = installer()
    checks = json.loads((PACKAGE / "manifest.json").read_text())
    m.verify_installed(checks)
    s = snapshot()
    validate_before(s)
    if sys.argv[1:] == ["--check"]:
        print(
            "CHECK_OK: existing item unique, installed sender and application ACL match; partition="
            + (
                "not_applicable_legacy"
                if s["database_version"] < 512
                else "exact_sender"
            )
            + "; no secret read or mutation."
        )
        return
    if os.geteuid() != 0 or not all(os.isatty(fd) for fd in (0, 1, 2)):
        raise Stop("請使用者親自在 Terminal 執行管理員入口。")
    m.validate_identity()
    m.lock_operation()
    if m.loaded("system/" + m.LABEL):
        if plistlib.loads(m.PLIST.read_bytes()) != m.configuration():
            raise Stop("既有正式 system job 設定不符。")
        print(
            "正式 system job 已載入；不重做 CREATE 或 SWITCH。請回報以便只讀驗證最近兩次回報。"
        )
        return
    original = m.old_bytes()
    if m.PREVIOUS and not m.loaded("gui/" + str(CONFIG["uid"]) + "/" + m.OLD_LABEL):
        raise Stop("原 GUI agent 未載入；停止，不自動改動。")
    print(
        "保留原監控（若有）。使用已建立的新 item，不再輸入 API key、不 CREATE、不改 sender。"
    )
    print("已核對實際 Keychain 格式、唯一 item 與原 sender 限定 application ACL。")
    print(
        "Single system-domain send to "
        + CONFIG["origin"]
        + "/api/computers/heartbeat; device "
        + CONFIG["account"]
    )
    print(
        "資料為本機 CPU、GPU、記憶體百分比與可取得的溫度及既有裝置識別；load average 不外送；成功後才可 SWITCH。"
    )
    print("不讀舊 key、不改 ACL、不刪任何 Keychain item；不重開機／登出。")
    if input("輸入 RESUME 繼續；其他輸入取消：") != "RESUME":
        return
    metadata_ready_flow(snapshot, lambda before: one_attempt(m, before))
    # Clean up a finished attempt after a previously closed window, without resending.
    if MARKER.exists() and (PLIST.exists() or m.loaded("system/" + LABEL)):
        cleanup_job(m)
    ready = DEST / "key-ready"
    if not ready.exists():
        ready.write_text("ready")
        ready.chmod(0o600)
    m.audit("resume_existing_key_metadata_verified")
    print(
        "EXISTING_KEY_READY：原 sender 背景回報與未變更的 application ACL 已驗證；未重輸或改寫 key。",
        flush=True,
    )
    if input("輸入 SWITCH 才切換；失敗自動恢復原版（若有）：") != "SWITCH":
        return
    m.activate(original)


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, deployment.interrupted)
    signal.signal(signal.SIGTERM, deployment.interrupted)
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("已中止；可回報階段，續接紀錄會防止盲目重送。")
        sys.exit(2)
    except Exception as e:
        print(str(e) if isinstance(e, Stop) else "續接未確認；停止，不重輸 key。")
        sys.exit(1)
