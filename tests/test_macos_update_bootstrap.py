"""Fake OS regression for initial activation; no Keychain or network access."""
import contextlib
import importlib.util
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "agent/macos_daemon"
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location("update_bootstrap", SOURCE / "auto_update/bootstrap.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
sys.path.remove(str(SOURCE))


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.old = self.jobs / "old.plist"
        self.original_bytes = plistlib.dumps({"Label": "old"})
        self.old.write_bytes(self.original_bytes)
        sender = self.root / "old/bin/mini-telemetry"
        sender.parent.mkdir(parents=True)
        sender.write_bytes(b"original sender")
        self.c = {"deployment": {"label": "new"}, "original": {"label": "old", "sender": str(sender),
                  "sha256": b.u.digest(sender.read_bytes()), "plist_sha256": b.u.digest(self.original_bytes)}}
        self.calls = []
        self.loaded = {"system/old"}

        def call(args, **kwargs):
            args = [str(x) for x in args]
            self.calls.append(args)
            if args[0] == "/bin/launchctl":
                if args[1] == "print":
                    return 0 if args[2] in self.loaded else 1
                if args[1] == "bootout":
                    self.loaded.discard(args[2])
                if args[1] == "bootstrap":
                    self.loaded.add("system/" + Path(args[3]).stem)
            return 0

        patches = [patch.object(b, "Path", side_effect=lambda value: self.jobs if value == "/Library/LaunchDaemons" else Path(value)),
                   patch.object(b.u, "call", side_effect=call),
                   patch.object(b.u, "telemetry_lock", side_effect=lambda r: contextlib.nullcontext()),
                   patch.object(b.u, "user_call"), patch.object(b.u, "acknowledgements", return_value=set()),
                   patch.object(b.u, "wait_health"), patch.object(b.u, "job", return_value={"Label": "new"}),
                   patch.object(b.u, "start_job"), patch.object(b.u, "stop_job"),
                   patch.object(b, "updater_job", return_value={"Label": "new.updater"})]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def test_two_natural_acks_enable_updater_and_preserve_original(self):
        b.switch(self.root, self.c, "a" * 40)
        self.assertFalse(self.old.exists())
        self.assertEqual((self.root / "original.plist").read_bytes(), self.original_bytes)
        self.assertTrue((self.jobs / "new.updater.plist").exists())
        self.assertEqual(json.loads((self.root / "installation.json").read_text())["phase"], "active")
        self.assertEqual([c[-1] for c in self.calls if c[0].endswith("mini-telemetry")], ["migrate-access"])

    def test_missing_acks_restore_original_acl_and_job(self):
        self.mocks[5].side_effect = RuntimeError("no new acks")
        with self.assertRaisesRegex(RuntimeError, "no new acks"):
            b.switch(self.root, self.c, "a" * 40)
        self.assertEqual(self.old.read_bytes(), self.original_bytes)
        self.assertFalse((self.jobs / "new.plist").exists())
        self.assertFalse((self.jobs / "new.updater.plist").exists())
        self.assertIn("system/old", self.loaded)
        self.assertEqual([c[-1] for c in self.calls if c[0].endswith("mini-telemetry")], ["migrate-access", "restore-access"])
        self.mocks[3].assert_any_call(self.c, "sample")
        self.assertFalse(any(call.args[-1] == "run" for call in self.mocks[3].call_args_list))

    def test_changed_original_job_refused_before_any_command(self):
        self.old.write_bytes(plistlib.dumps({"Label": "someone-else"}))
        with self.assertRaisesRegex(ValueError, "Original job changed"):
            b.switch(self.root, self.c, "a" * 40)
        self.assertEqual(self.calls, [])

    def test_failure_after_original_plist_removed_restores_it(self):
        real_put = b.put_plist
        def fail_updater(path, value):
            if path.name == "new.updater.plist":
                raise OSError("cannot install updater")
            return real_put(path, value)
        with patch.object(b, "put_plist", side_effect=fail_updater):
            with self.assertRaisesRegex(OSError, "cannot install updater"):
                b.switch(self.root, self.c, "a" * 40)
        self.assertEqual(plistlib.loads(self.old.read_bytes()), {"Label": "old"})
        self.assertIn("system/old", self.loaded)
        self.assertEqual(json.loads((self.root / "installation.json").read_text())["phase"], "rolled_back")


class PackageTests(unittest.TestCase):
    def test_retry_cleanup_refuses_unrelated_service(self):
        with patch.object(b.u, "call") as command:
            with self.assertRaisesRegex(ValueError, "Unexpected failed public probe scope"):
                b.clear_failed_probe(Path("/unused"), {"service": "production"}, Path("/unused"))
            command.assert_not_called()

    def test_retry_cleanup_requires_confirmed_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            package.mkdir()
            (package / "probe.swift").write_text("// public-only fixture")
            library = root / "library"
            test = library / ("HomeButlerUpdateProbe-" + "a" * 32)
            test.mkdir(parents=True)
            marker = test / "keep-until-absence-confirmed"
            marker.touch()
            report = {"service": "org.homebutler.telemetry.update-probe." + "a" * 32}
            with patch.object(b, "Path", side_effect=lambda x: library if x == "/Library/Application Support" else Path(x)), \
                 patch.object(b.u, "trusted"), patch.object(b.u, "call", return_value=1), \
                 patch.object(b, "captured", return_value=Mock(stdout='{"error_status":-25293}', returncode=1)) as query:
                with self.assertRaisesRegex(ValueError, "metadata query failed"):
                    b.clear_failed_probe(root, report, package)
                self.assertTrue(marker.exists())
                query.return_value = Mock(stdout='{"error_status":-25300}', returncode=1)
                b.clear_failed_probe(root, report, package)
                self.assertFalse(test.exists())
                self.assertEqual(query.call_args.args[0][-2:], ["metadata", "system"])

    def test_absence_is_distinct_from_probe_failure_or_existing_item(self):
        b.ensure_absent(Mock(stdout='{"error_status":-25300}', returncode=1))
        for value, code, message in (({"error": "probe_validation_failed"}, 1, "metadata query failed"),
                                     ({"error_status": -25293}, 1, "metadata query failed"),
                                     ({"metadata": {}}, 0, "already exists")):
            with self.assertRaisesRegex(ValueError, message):
                b.ensure_absent(Mock(stdout=json.dumps(value), returncode=code))

    def test_resume_refuses_any_evidence_of_production_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c = {"deployment": {}, "original": {}, "certificate_sha1": "fake", "requirement": "fake"}
            template = {"deployment": {}, "original": {}}
            for name in ("installation.json", "current", "pending.json", "original.plist"):
                (root / name).touch()
                with patch.object(b.u, "load_config", return_value=c), patch.object(b, "public_migration_test") as probe:
                    with self.assertRaisesRegex(ValueError, "Production switch may have started"):
                        b.resume_preflight(root, template, root, "a" * 40, {})
                    probe.assert_not_called()
                (root / name).unlink()

    def test_resume_refuses_different_deployment(self):
        with patch.object(b.u, "load_config", return_value={"deployment": {"service": "original"}}):
            with self.assertRaisesRegex(ValueError, "differs"):
                b.resume_preflight(Path("/unused"), {"deployment": {"service": "other"}}, Path("/unused"), "a" * 40, {})

    def test_missing_manifest_coverage_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / "package.json").write_text(json.dumps({"sha": "a" * 40, "files": {}}))
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                b.read_sources(package)

    def test_signer_package_cannot_enable_fixture_mode(self):
        source = (SOURCE / "auto_update/prepare.py").read_text()
        import ast
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "run"]
        compiler = next(n for n in calls if isinstance(n.args[0], ast.List)
                        and any(isinstance(x, ast.Constant) and x.value == "/usr/bin/swiftc" for x in n.args[0].elts))
        self.assertFalse(any(isinstance(x, ast.Constant) and x.value in ("-D", "SIGNER_FIXTURE") for x in compiler.args[0].elts))


if __name__ == "__main__":
    unittest.main()
