import base64
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

SOURCE = Path(__file__).resolve().parents[1] / "agent/macos_daemon"
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location("macos_update", SOURCE / "auto_update/update.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)
sys.path.remove(str(SOURCE))

OLD, NEW = "a" * 40, "b" * 40


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.runs = [{"id": 4, "run_attempt": 1, "head_sha": NEW, "head_branch": "main",
                      "event": "push", "path": ".github/workflows/ci.yml",
                      "repository": {"full_name": u.REPO}, "status": "completed", "conclusion": "success"}]
        self.calls = []
        self.corrupt = False

    def fetch(self, path):
        self.calls.append(path)
        if path == "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": NEW}}
        if path.startswith("/actions/"):
            return {"workflow_runs": self.runs}
        name = path.removeprefix("/contents/").split("?")[0]
        data = b"# public source\n"
        return {"path": name, "type": "file", "encoding": "base64",
                "content": base64.b64encode(data).decode(),
                "sha": "bad" if self.corrupt else hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}

    def test_approved_exact_commit_and_blob(self):
        sha, sources = u.download(self.fetch)
        self.assertEqual(sha, NEW)
        self.assertEqual(set(sources), set(u.FILES))
        self.assertTrue(all(path.endswith("?ref=" + NEW) for path in self.calls if path.startswith("/contents/")))

    def test_new_failed_attempt_overrides_older_success(self):
        self.runs.append({**self.runs[0], "run_attempt": 2, "conclusion": "failure"})
        self.assertIsNone(u.download(self.fetch))
        self.assertFalse(any(path.startswith("/contents/") for path in self.calls))

    def test_foreign_pr_or_wrong_commit_never_deploys(self):
        good = dict(self.runs[0])
        for field, value in (("event", "pull_request"), ("head_sha", OLD), ("head_branch", "feature"),
                             ("repository", {"full_name": "other/repo"}), ("status", "in_progress")):
            self.runs = [{**good, field: value}]
            self.assertIsNone(u.download(self.fetch))

    def test_corrupt_blob_rejected(self):
        self.corrupt = True
        with self.assertRaisesRegex(ValueError, "Git blob mismatch"):
            u.download(self.fetch)

    def test_unchanged_head_uses_only_one_request(self):
        self.assertEqual(u.download(self.fetch, known_shas={NEW}), (NEW, None))
        self.assertEqual(self.calls, ["/git/ref/heads/main"])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "releases" / OLD).mkdir(parents=True)
        (self.root / "releases" / NEW).mkdir()
        (self.root / "current").symlink_to("releases/" + OLD)
        self.events = []
        self.c = {"deployment": {"install": str(self.root)}}
        patches = [patch.object(u, "verify_release"),
                   patch.object(u, "telemetry_lock", side_effect=lambda r: contextlib.nullcontext()),
                   patch.object(u, "stop_job", side_effect=lambda c: self.events.append("stop")),
                   patch.object(u, "start_job", side_effect=lambda c: self.events.append("start")),
                   patch.object(u, "user_call", side_effect=lambda c, mode: self.events.append(mode)),
                   patch.object(u, "wait_health", side_effect=lambda *args: self.events.append("healthy"))]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def test_success_selects_new_and_clears_journal(self):
        u.deploy(self.root, self.c, NEW)
        self.assertEqual(u.current(self.root), NEW)
        self.assertFalse((self.root / "pending.json").exists())
        self.assertEqual(self.events, ["stop", "sample", "verify-access", "start", "healthy"])

    def test_bad_sample_restores_before_starting_previous(self):
        self.mocks[4].side_effect = RuntimeError("bad sample")
        with self.assertRaises(RuntimeError):
            u.deploy(self.root, self.c, NEW)
        self.assertEqual(u.current(self.root), OLD)
        self.assertEqual(json.loads((self.root / "failed.json").read_text()), {"sha": NEW})
        self.assertFalse((self.root / "pending.json").exists())
        self.assertEqual(self.events[-2:], ["stop", "start"])

    def test_missing_new_heartbeats_rolls_back_without_forced_post(self):
        self.mocks[5].side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            u.deploy(self.root, self.c, NEW)
        self.assertEqual(u.current(self.root), OLD)
        self.assertNotIn("run", self.events)

    def test_interrupted_switch_recovers_without_network(self):
        u.atomic_json(self.root / "pending.json", {"before": OLD, "after": NEW})
        u.select(self.root, NEW)
        with patch.object(u, "github", side_effect=AssertionError("network must not be needed")):
            self.assertTrue(u.recover(self.root, self.c))
        self.assertEqual(u.current(self.root), OLD)
        self.assertFalse(u.recover(self.root, self.c))

    def test_foreign_recovery_target_rejected_before_stopping(self):
        u.atomic_json(self.root / "pending.json", {"before": "../../somewhere", "after": NEW})
        with self.assertRaises(ValueError):
            u.recover(self.root, self.c)
        self.assertEqual(self.events, [])

    def test_heartbeats_must_match_new_compiled_version(self):
        (self.root / "state").mkdir()
        (self.root / "state/status.log").write_text(
            "one heartbeat acknowledged version=" + OLD + "\n" +
            "two heartbeat acknowledged version=" + NEW + "\n" +
            "three sample/send not confirmed; no immediate retry\n")
        self.assertEqual(u.acknowledgements(self.root, NEW), {("two heartbeat acknowledged version=" + NEW).encode()})

    def test_dependency_change_fails_before_compiler_or_signer(self):
        sources = {name: b"public" for name in u.FILES}
        with patch.object(u, "call") as command:
            with self.assertRaisesRegex(ValueError, "Dependency change"):
                u.build(self.root, {"requirements_sha256": "wrong"}, NEW, sources)
            command.assert_not_called()


class PollTests(unittest.TestCase):
    def test_docs_only_changes_do_not_build_or_restart(self):
        self.run_poll(failed=False)

    def test_failed_version_does_not_loop(self):
        self.run_poll(failed=True)

    def run_poll(self, failed):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "releases" / OLD).mkdir(parents=True)
            sources = {name: b"new" for name in u.FILES}
            (root / "releases" / OLD / "release.json").write_text(json.dumps({
                "sources": {} if failed else u.source_hashes(sources)}))
            if failed:
                (root / "failed.json").write_text(json.dumps({"sha": NEW}))
            with patch.object(u.os, "getuid", return_value=0), patch.object(u.os, "geteuid", return_value=0), \
                 patch.object(u, "load_config", return_value={}), patch.object(u, "trusted"), \
                 patch.object(u, "recover", return_value=False), patch.object(u, "download", return_value=(NEW, sources)), \
                 patch.object(u, "current", return_value=OLD), patch.object(u, "verify_release"), \
                 patch.object(u, "build") as build, patch.object(u, "deploy") as deploy:
                u.run(root)
            build.assert_not_called()
            deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
