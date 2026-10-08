"""Offline gates for the isolated public-data signing experiment."""
import copy
import importlib.util
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "agent/macos_daemon/signing_probe/system_probe.py"
spec = importlib.util.spec_from_file_location("system_signing_probe", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SigningProbeTests(unittest.TestCase):
    def setUp(self):
        self.checks = []
        for i, version in enumerate(("A", "B", "wrong-identifier", "wrong-signer", "A")):
            allowed = i in (0, 1, 4)
            self.checks.append({"version": version, "build": "B" if i == 1 else "A",
                                "uid": 502, "scope": "system", "matched": allowed,
                                "status": 0 if allowed else -25293})

    def test_upgrade_rollback_and_denials_required(self):
        self.assertTrue(m.assess(self.checks, {"acl": "same"}, {"acl": "same"}, 502))
        for index in (0, 1, 4):
            broken = copy.deepcopy(self.checks)
            broken[index].update(status=-25293, matched=False)
            self.assertFalse(m.assess(broken, {}, {}, 502))
        for index in (2, 3):
            broken = copy.deepcopy(self.checks)
            broken[index].update(status=0, matched=True)
            self.assertFalse(m.assess(broken, {}, {}, 502))

    def test_missing_item_is_not_valid_denial(self):
        for code in (-25300, -50, -34018):
            broken = copy.deepcopy(self.checks)
            broken[2]["status"] = code
            self.assertFalse(m.assess(broken, {}, {}, 502))

    def test_wrong_uid_old_binary_and_acl_change_rejected(self):
        self.assertFalse(m.assess(self.checks, {"acl": "old"}, {"acl": "new"}, 502))
        for key, value in (("uid", 0), ("build", "A"), ("scope", "fixture")):
            broken = copy.deepcopy(self.checks)
            broken[1][key] = value
            self.assertFalse(m.assess(broken, {}, {}, 502))

    def test_missing_or_reordered_results_rejected(self):
        self.assertFalse(m.assess(self.checks[:-1], {}, {}, 502))
        self.assertFalse(m.assess(self.checks[::-1], {}, {}, 502))

    def test_manifest_cannot_name_production_service(self):
        signatures = {v: {"sha256": "a" * 64, "cdhash": v, "requirement": "same"}
                      for v in m.VERSIONS}
        manifest = {"public_test_only": True, "service": m.PREFIX + "a" * 32, "signatures": signatures}
        self.assertEqual(m.validate_manifest(manifest), manifest["service"])
        for service in ("com.czlin.homebutler.mini-telemetry.api-key.boot-v1", m.PREFIX + "../bad", m.PREFIX):
            with self.assertRaises(ValueError):
                m.validate_manifest({**manifest, "service": service})
        signatures["B"]["cdhash"] = signatures["A"]["cdhash"]
        with self.assertRaises(ValueError):
            m.validate_manifest(manifest)

    def test_job_is_single_run_unprivileged_public_read(self):
        import os
        user = SimpleNamespace(pw_name="example", pw_gid=os.getgid(), pw_dir="/Users/example")
        root = Path("/tmp/public-probe")
        job = m.job_config(root, "example.probe", user, root / "result.json")
        self.assertEqual(job["ProgramArguments"], [str(root / "sender"), "read", "system"])
        self.assertEqual(job["UserName"], "example")
        self.assertNotIn("KeepAlive", job)
        self.assertNotIn("StartInterval", job)
        self.assertNotIn("WatchPaths", job)
        self.assertEqual(set(job["EnvironmentVariables"]), {"HOME", "LANG", "PATH"})

    def test_partial_create_failure_still_cleans_public_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            package = parent / "package"
            (package / "versions").mkdir(parents=True)
            signatures = {}
            for name in m.VERSIONS:
                data = name.encode()
                (package / "versions" / name).write_bytes(data)
                signatures[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                                    "requirement": "same", "cdhash": name}
            manifest = {"public_test_only": True, "service": m.PREFIX + "b" * 32,
                        "signatures": signatures}
            (package / "manifest.json").write_text(json.dumps(manifest))
            user = SimpleNamespace(pw_uid=502, pw_gid=20, pw_name="example")

            def fake_call(args, **kw):
                if args[-2:] == ["metadata", "system"]:
                    return SimpleNamespace(returncode=1, stdout='{"error_status":-25300}')
                return SimpleNamespace(returncode=0, stdout="")

            with patch.object(m, "SYSTEM_PARENT", parent), patch.object(m.os, "getuid", return_value=0), \
                 patch.object(m.os, "geteuid", return_value=0), patch.object(m.os, "chown"), \
                 patch.object(m.pwd, "getpwnam", return_value=user), patch.object(m, "call", side_effect=fake_call), \
                 patch.object(m, "probe", side_effect=RuntimeError("metadata failed after SecItemAdd")), \
                 patch.object(m, "remove_owned_item") as cleanup:
                with self.assertRaisesRegex(RuntimeError, "metadata failed"):
                    m.run(package, "example")
                cleanup.assert_called_once()
            report = json.loads((parent / ("HomeButlerSigningProbe-" + "b" * 32) / "result.json").read_text())
            self.assertFalse(report["passed"])
            self.assertTrue(report["test_item_removed"])

    def test_bootstrap_uncertain_outcome_unloads_only_own_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            calls = []
            printed = 0
            def fake_call(args, **kw):
                nonlocal printed
                calls.append(args)
                if args[1] == "print":
                    printed += 1
                    return SimpleNamespace(returncode=0 if printed == 2 else 1, stdout="")
                if args[1] == "bootstrap":
                    raise TimeoutError("bootstrap result unknown")
                return SimpleNamespace(returncode=0, stdout="")
            with patch.object(m, "call", side_effect=fake_call), patch.object(m, "job_config", return_value={}):
                with self.assertRaises(TimeoutError):
                    m.background_read(root, "example.unique.probe", None)
            self.assertIn(["/bin/launchctl", "bootout", "system/example.unique.probe"], calls)


if __name__ == "__main__":
    unittest.main()
