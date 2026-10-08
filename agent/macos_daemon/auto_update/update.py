"""Installed root-owned update controller; GitHub source never executes as root.

Only fixed swiftc/codesign invocations build the native sender. Application code
runs through the existing non-root telemetry job. Signing credentials stay local.
"""
import argparse
import ast
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from settings import validate, swift_settings

REPO = "CZLin-TW/home-butler"
FILES = (
    "agent/macos_daemon/sender.swift",
    "agent/macos_daemon/auto_update/trust.swift",
    "agent/macos_metrics.py",
    "agent/macos_temperature.py",
    "agent/requirements-macos.txt",
)
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"}
SHA = re.compile(r"[a-f0-9]{40}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    temp = path.with_suffix(".next")
    with temp.open("w") as out:
        json.dump(value, out, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    if path.name != "update.json":
        temp.chmod(0o644)  # Version/journal/result files contain no credentials.
    temp.replace(path)


def trusted(path, directory=False):
    st = path.lstat()
    if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
        raise ValueError("Untrusted installed path")
    if directory != path.is_dir() or not directory and not path.is_file():
        raise ValueError("Unexpected installed entry")


def load_config(root):
    if root.parent != Path("/Library/Application Support") or root.name in ("", ".", ".."):
        raise ValueError("Dedicated installation required")
    for p in reversed(root.parents):
        trusted(p, True)
    trusted(root, True)
    trusted(root / "update.json")
    c = json.loads((root / "update.json").read_text())
    if set(c) != {"deployment", "certificate_sha1", "requirement", "original", "requirements_sha256"}:
        raise ValueError("Invalid update configuration")
    validate(c["deployment"])
    if c["deployment"]["install"] != str(root):
        raise ValueError("Wrong installation")
    if not SHA.fullmatch(c["certificate_sha1"]):
        raise ValueError("Invalid signing identity")
    expected = 'identifier "org.homebutler.telemetry.sender" and certificate leaf = H"' + c["certificate_sha1"] + '"'
    if c["requirement"] != expected:
        raise ValueError("Signing requirement changed")
    original = c["original"]
    if set(original) != {"label", "sender", "sha256", "cdhash", "plist_sha256"}:
        raise ValueError("Invalid original deployment")
    if not SHA.fullmatch(original["cdhash"]):
        raise ValueError("Invalid original identity")
    for name in ("sha256", "plist_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", original[name]):
            raise ValueError("Invalid original digest")
    if not re.fullmatch(r"[a-f0-9]{64}", c["requirements_sha256"]):
        raise ValueError("Invalid dependency pin")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", original["label"]):
        raise ValueError("Invalid original label")
    if not Path(original["sender"]).is_absolute() or ".." in Path(original["sender"]).parts:
        raise ValueError("Invalid original sender")
    return c


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Unexpected GitHub redirect")


def github(path):
    request = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "HomeButler-Mac-Updater"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Oversized GitHub response")
    return json.loads(data)


def download(fetch=github, known_shas=()):
    ref = fetch("/git/ref/heads/main")
    sha = ref["object"]["sha"]
    if ref.get("ref") != "refs/heads/main" or not SHA.fullmatch(sha):
        raise ValueError("Invalid main reference")
    if sha in known_shas:
        return sha, None
    query = urllib.parse.urlencode({"head_sha": sha, "event": "push", "per_page": 20})
    runs = fetch("/actions/workflows/ci.yml/runs?" + query)["workflow_runs"]
    matches = [r for r in runs if r.get("head_sha") == sha and r.get("head_branch") == "main"
               and r.get("event") == "push" and r.get("path") == ".github/workflows/ci.yml"
               and r.get("repository", {}).get("full_name") == REPO]
    if not matches:
        return None
    latest = max(matches, key=lambda r: (r["id"], r.get("run_attempt", 1)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        return None
    result = {}
    for path in FILES:
        item = fetch("/contents/" + path + "?ref=" + sha)
        if item.get("path") != path or item.get("type") != "file" or item.get("encoding") != "base64":
            raise ValueError("Unexpected source entry")
        data = base64.b64decode("".join(item["content"].split()), validate=True)
        if not 0 < len(data) <= 256 * 1024:
            raise ValueError("Invalid source size")
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != item["sha"]:
            raise ValueError("Git blob mismatch")
        if path.endswith(".py"):
            ast.parse(data, filename=path)
        result[path] = data
    return sha, result


def call(args, *, check=True, timeout=30, log=None):
    with (log.open("wb") if log else contextlib.nullcontext(subprocess.DEVNULL)) as output:
        result = subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL,
                                stdout=output, stderr=output, env=ENV, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("Local command failed: " + Path(str(args[0])).name)
    return result.returncode


def user_call(c, mode):
    root = Path(c["deployment"]["install"])
    return call(["/usr/bin/sudo", "-n", "-u", c["deployment"]["user"], "/usr/bin/env", "-i",
                 "PATH=" + ENV["PATH"], "LANG=en_US.UTF-8", root / "current/bin/mini-telemetry", mode])


def source_hashes(sources):
    if set(sources) != set(FILES):
        raise ValueError("Source package is not complete")
    return {p: digest(data) for p, data in sources.items()}


def build(root, c, sha, sources, signer_root=None):
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid release identity")
    hashes = source_hashes(sources)
    if hashes["agent/requirements-macos.txt"] != c["requirements_sha256"]:
        raise ValueError("Dependency change requires reviewed runtime upgrade")
    target = root / "releases" / sha
    if target.exists():
        verify_release(root, c, sha)
        if json.loads((target / "release.json").read_text())["sources"] != hashes:
            raise ValueError("Existing release does not match source")
        return
    stage = root / "build"
    if stage.exists():
        trusted(stage, True)
        shutil.rmtree(stage)
    stage.mkdir(mode=0o700)
    bundle = stage / "release"
    (bundle / "bin").mkdir(parents=True, mode=0o755)
    (bundle / "collector").mkdir(mode=0o755)
    constants = {"requirement": c["requirement"], "version": sha,
                 "originalSender": c["original"]["sender"], "originalCDHash": c["original"]["cdhash"]}
    swift = swift_settings(c["deployment"]) + "\nenum UpdateTrust {\n" + "\n".join(
        "static let " + key + " = " + json.dumps(value) for key, value in constants.items()) + "\n}\n"
    swift += sources[FILES[0]].decode() + "\n" + sources[FILES[1]].decode()
    (stage / "main.swift").write_text(swift)
    # Fixed compiler command only: no repo build script, package manager or plugin
    # path is executed. Source is copied to a root-owned directory before compile.
    call(["/usr/bin/swiftc", "-D", "AUTO_UPDATE", "-module-cache-path", stage / "cache",
          stage / "main.swift", "-o", bundle / "bin/mini-telemetry"], timeout=180, log=stage / "compiler.log")
    for path in FILES[2:4]:
        (bundle / "collector" / Path(path).name).write_bytes(sources[path])
    signer_root = signer_root or root
    signer = signer_root / "updater/signer"
    try:
        call([signer, "unlock", signer_root / "signer"])
        call(["/usr/bin/codesign", "--force", "--sign", c["certificate_sha1"],
              "--keychain", signer_root / "signer/signing.keychain-db", "--timestamp=none",
              "--identifier", "org.homebutler.telemetry.sender", "--requirements", "=designated => " + c["requirement"],
              bundle / "bin/mini-telemetry"], log=stage / "signing.log")
    finally:
        call([signer, "lock", signer_root / "signer"])
    for path in bundle.rglob("*"):
        path.chmod(0o755 if path.is_dir() or path.name == "mini-telemetry" else 0o644)
    bundle.chmod(0o755)
    files = {str(p.relative_to(bundle)): digest(p.read_bytes()) for p in bundle.rglob("*") if p.is_file()}
    atomic_json(bundle / "release.json", {"sha": sha, "sources": hashes, "files": files})
    bundle.rename(target)
    verify_release(root, c, sha)


def verify_release(root, c, sha):
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid release")
    release = root / "releases" / sha
    trusted(release, True)
    trusted(release / "release.json")
    data = json.loads((release / "release.json").read_text())
    expected = {"bin/mini-telemetry", "collector/macos_metrics.py", "collector/macos_temperature.py"}
    if data["sha"] != sha or set(data["files"]) != expected:
        raise ValueError("Invalid release manifest")
    for name in expected:
        path = release / name
        trusted(path.parent, True)
        trusted(path)
        if digest(path.read_bytes()) != data["files"][name]:
            raise ValueError("Installed release changed")
    call(["/usr/bin/codesign", "--verify", "--strict", "-R", "=" + c["requirement"],
          release / "bin/mini-telemetry"])


def current(root):
    value = os.readlink(root / "current")
    if not re.fullmatch(r"releases/[a-f0-9]{40}", value):
        raise ValueError("Unexpected active release")
    return value.split("/")[1]


def select(root, sha):
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid release pointer")
    stage = root / "current.next"
    if stage.is_symlink():
        stage.unlink()
    elif stage.exists():
        raise ValueError("Unexpected pending pointer")
    stage.symlink_to("releases/" + sha)
    stage.replace(root / "current")


def job(c):
    d = c["deployment"]
    return {"Label": d["label"], "ProgramArguments": [str(Path(d["install"]) / "current/bin/mini-telemetry"), "run"],
            "UserName": d["user"], "GroupName": d["group"], "RunAtLoad": True, "StartInterval": 60,
            "ProcessType": "Background", "WorkingDirectory": d["install"], "Umask": 0o077,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def check_job(c):
    path = Path("/Library/LaunchDaemons") / (c["deployment"]["label"] + ".plist")
    trusted(path)
    if plistlib.loads(path.read_bytes()) != job(c):
        raise ValueError("Telemetry job changed")
    return path


def stop_job(c):
    check_job(c)
    target = "system/" + c["deployment"]["label"]
    if call(["/bin/launchctl", "print", target], check=False) == 0:
        call(["/bin/launchctl", "bootout", target])


def start_job(c):
    path = check_job(c)
    call(["/bin/launchctl", "bootstrap", "system", path])


@contextlib.contextmanager
def telemetry_lock(root):
    # Never create/truncate a lock owned by the telemetry user. Native sender
    # creates it before the automatic updater is enabled.
    fd = os.open(root / "state/run.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Telemetry still busy")
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def acknowledgements(root, sha):
    path = root / "state/status.log"
    if not path.exists():
        return set()
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 262144))
        data = stream.read(262144)
    suffix = ("heartbeat acknowledged version=" + sha).encode()
    return {line for line in data.splitlines() if line.endswith(suffix)}


def wait_health(root, sha, before):
    deadline = time.monotonic() + 155
    while time.monotonic() < deadline:
        if len(acknowledgements(root, sha) - before) >= 2:
            return
        time.sleep(5)
    raise RuntimeError("New-version natural heartbeats not confirmed")


def recover(root, c):
    path = root / "pending.json"
    if not path.exists():
        return False
    pending = json.loads(path.read_text())
    if set(pending) != {"before", "after"} or not all(SHA.fullmatch(v) for v in pending.values()):
        raise ValueError("Invalid recovery journal")
    verify_release(root, c, pending["before"])
    with telemetry_lock(root):
        stop_job(c)
        select(root, pending["before"])
    start_job(c)
    atomic_json(root / "failed.json", {"sha": pending["after"]})
    path.unlink()
    return True


def deploy(root, c, sha):
    before_sha = current(root)
    verify_release(root, c, sha)
    before_acks = acknowledgements(root, sha)
    atomic_json(root / "pending.json", {"before": before_sha, "after": sha})
    try:
        with telemetry_lock(root):
            stop_job(c)
            select(root, sha)
            user_call(c, "sample")
            user_call(c, "verify-access")
        start_job(c)
        wait_health(root, sha, before_acks)
        (root / "pending.json").unlink()
    except BaseException:
        recover(root, c)
        raise


def event(root, value, sha=""):
    path = root / "update.log"
    if path.exists() and path.stat().st_size > 131072:
        path.replace(root / "update.log.1")
    with path.open("a") as out:
        out.write(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()) + value + " " + sha + "\n")
    path.chmod(0o644)


def run(root):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError("Installed controller requires administrator identity")
    c = load_config(root)
    trusted(root / "updater", True)
    trusted(root / "releases", True)
    with (root / "update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if recover(root, c):
            event(root, "interrupted_update_restored")
            return
        if (root / "paused").exists():
            event(root, "updates_paused")
            return
        active = current(root)
        verify_release(root, c, active)
        known = {active}
        for name in ("checked.json", "failed.json"):
            path = root / name
            if path.exists():
                value = json.loads(path.read_text())["sha"]
                if not SHA.fullmatch(value):
                    raise ValueError("Invalid check history")
                known.add(value)
        try:
            found = download(known_shas=known)
        except Exception:
            event(root, "github_check_failed")
            raise
        if found is None:
            event(root, "waiting_for_successful_ci")
            return
        sha, sources = found
        if sources is None:
            event(root, "already_checked", sha)
            return
        previous = json.loads((root / "releases" / active / "release.json").read_text())
        if previous["sources"] == source_hashes(sources):
            atomic_json(root / "checked.json", {"sha": sha})
            event(root, "agent_unchanged", sha)
            return
        failed = root / "failed.json"
        if failed.exists() and json.loads(failed.read_text())["sha"] == sha:
            event(root, "failed_version_held", sha)
            return
        try:
            build(root, c, sha, sources)
            deploy(root, c, sha)
            event(root, "update_confirmed", sha)
            atomic_json(root / "checked.json", {"sha": sha})
        except Exception:
            atomic_json(failed, {"sha": sha})
            event(root, "update_failed", sha)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        run(args.install)
    except Exception:
        raise SystemExit("Update not completed; see the installed fixed-event update.log")
