"""Prepare a local, hash-pinned administrator installer from a clean commit.

Only public deployment settings belong in --config. Private signing material is
generated on the target Mac at installation and never included in this package.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE.parent))
from settings import validate

# This small verifier executes before importing any package Python as root. It
# copies the verified bytes to a root-owned temporary directory; installation
# never imports code again from the user's mutable output folder.
STAGE = r'''
import hashlib,json,os,pathlib,shutil,subprocess,sys,tempfile
package=pathlib.Path(sys.argv[1]); expected=sys.argv[2]
manifest_bytes=(package/'package.json').read_bytes()
if hashlib.sha256(manifest_bytes).hexdigest()!=expected: raise SystemExit('Package manifest changed')
manifest=json.loads(manifest_bytes)
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='homebutler-update-') as directory:
    stage=pathlib.Path(directory)
    for name,digest in manifest['files'].items():
        relative=pathlib.Path(name)
        if relative.is_absolute() or '..' in relative.parts: raise SystemExit('Unsafe package path')
        data=(package/relative).read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest: raise SystemExit('Package file changed: '+name)
        target=stage/relative; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o700 if name=='signer' else 0o600)
    (stage/'package.json').write_bytes(manifest_bytes)
    result=subprocess.run(['/usr/bin/python3','-I',str(stage/'bootstrap.py'),'--package',str(stage)],
                          env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LANG':'en_US.UTF-8'})
    sys.exit(result.returncode)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    validate(config["deployment"])
    repo = SOURCE.parents[2]
    git = lambda *parts: subprocess.check_output(["git", "-C", str(repo), *parts]).decode().strip()
    if git("status", "--porcelain"):
        raise SystemExit("Commit reviewed source before preparing an installer")
    sha = git("rev-parse", "HEAD")
    # Import updater after its settings dependency has been resolved above.
    sys.path.insert(0, str(SOURCE))
    import update
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.launcher.exists():
        raise SystemExit("Launcher already exists")
    if args.launcher.resolve().parent != output.parent:
        raise SystemExit("Launcher and package must share one parent directory")
    for name, source in {
        "bootstrap.py": SOURCE / "bootstrap.py",
        "update.py": SOURCE / "update.py",
        "settings.py": SOURCE.parent / "settings.py",
        "probe.swift": SOURCE.parent / "signing_probe/probe.swift",
        "deployment-template.json": args.config,
    }.items():
        shutil.copyfile(source, output / name)
    for name in update.FILES:
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    # No SIGNER_FIXTURE flag: the package's native helper enforces UID 0.
    subprocess.run(["/usr/bin/swiftc", "-module-cache-path", str(output / ".cache"),
                    str(SOURCE / "signer.swift"), "-o", str(output / "signer")], check=True)
    shutil.rmtree(output / ".cache")
    files = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in output.rglob("*") if p.is_file()}
    manifest = json.dumps({"sha": sha, "files": files}, indent=2, sort_keys=True) + "\n"
    (output / "package.json").write_text(manifest)
    checksum = hashlib.sha256(manifest.encode()).hexdigest()
    launcher = '''#!/bin/zsh
set -eu
finish() {
  local task_exit=$?
  if (( task_exit != 0 )); then
    print '啟用停止。請保留最後的錯誤文字並回到此聊天，不要重複執行。'
  fi
  if [[ -t 0 ]]; then
    read -r '?按 Enter 關閉視窗。' || true
  fi
  trap - EXIT
  exit "$task_exit"
}
trap finish EXIT
[[ -t 0 && -t 1 ]] || { print '請在 Mac mini 的 Terminal 開啟此檔案。'; exit 1; }
cd -P -- "${0:A:h}"
package="$PWD/"PACKAGE_NAME
[[ "$(/usr/bin/id -un)" == EXPECTED_USER ]] || { print '此包只適用於準備時的使用者。'; exit 1; }
print 'Home Butler：啟用完整 agent 自動更新'
print '先驗證公開測試金鑰的授權移交與回復，通過後切換正式 agent。'
print '沿用既有 API key；本機產生簽署身分。GitHub main 通過 CI 後，每五分鐘檢查更新。'
print '請在 sudo Password 提示輸入 Mac 管理員密碼；輸入時不顯示字元。'
/usr/bin/sudo -- /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 /usr/bin/python3 -I -c STAGE_CODE "$package" MANIFEST_HASH
print '啟用完成。請回到聊天，我會檢查背景更新與自然回報狀態。'
'''
    for name, value in {"PACKAGE_NAME": output.name, "EXPECTED_USER": config["deployment"]["user"],
                        "STAGE_CODE": STAGE, "MANIFEST_HASH": checksum}.items():
        launcher = launcher.replace(name, shlex.quote(value))
    args.launcher.write_text(launcher)
    args.launcher.chmod(0o755)
    print("Prepared commit " + sha)


if __name__ == "__main__":
    main()
