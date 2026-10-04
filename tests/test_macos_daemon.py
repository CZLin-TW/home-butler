import tempfile
import unittest
import plistlib
from pathlib import Path
from unittest.mock import patch
import sys
from unittest.mock import Mock

PACKAGE = Path(__file__).resolve().parents[1] / "agent/macos_daemon"
sys.path.insert(0, str(PACKAGE))
import install as m
import resume as r
import settings

sys.path.remove(str(PACKAGE))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.multiple(
            m,
            DEST=self.root,
            OLD_PLIST=self.root / "old.plist",
            BACKUP=self.root / "backup.plist",
            PLIST=self.root / "boot.plist",
            PROBE_PLIST=self.root / "probe.plist",
            PREVIOUS={"label": "example.old"},
            OLD_LABEL="example.old",
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)

    def test_config_scoped_unprivileged(self):
        d = m.configuration()
        p = m.configuration(True)
        self.assertEqual(d["UserName"], "example")
        self.assertEqual(d["GroupName"], "staff")
        self.assertEqual(d["StartInterval"], 60)
        self.assertNotIn("StartInterval", p)
        self.assertEqual(d["ProgramArguments"], [str(m.SENDER), "run"])
        self.assertEqual(p["ProgramArguments"], [str(m.SENDER), "probe"])
        self.assertNotIn("EnvironmentVariables", d)
        self.assertNotIn("KeepAlive", d)

    def test_external_symlink_rejected(self):
        (self.root / "bad").symlink_to("/usr/bin/python3")
        with self.assertRaises(m.Stop):
            m.tree(self.root)

    def test_two_ack_success_switches_once(self):
        original = b"original"
        m.OLD_PLIST.write_bytes(original)
        calls = []

        def put(path, obj):
            path.write_bytes(plistlib.dumps(obj))

        with patch.object(
            m, "launch", side_effect=lambda *a, **kw: calls.append(a)
        ), patch.object(m, "put_plist", side_effect=put), patch.object(
            m, "audit"
        ), patch.object(
            m,
            "status_lines",
            side_effect=[
                [],
                [
                    "2026-10-04T00:00:00Z heartbeat acknowledged",
                    "2026-10-04T00:01:02Z heartbeat acknowledged",
                ],
            ],
        ):
            m.activate(original)
        self.assertFalse(m.OLD_PLIST.exists())
        self.assertEqual(m.BACKUP.read_bytes(), original)
        self.assertEqual(
            calls,
            [("bootout", "gui/502/" + m.OLD_LABEL), ("bootstrap", "system", m.PLIST)],
        )

    def test_bootstrap_failure_restores(self):
        m.OLD_PLIST.write_bytes(b"original")
        calls = []

        def launch(*args, **kw):
            calls.append(args)
            if args[:2] == ("bootstrap", "system"):
                raise m.Stop("fake bootstrap failed")

        with patch.object(m, "launch", side_effect=launch), patch.object(
            m, "put_plist", side_effect=lambda p, o: p.write_bytes(plistlib.dumps(o))
        ), patch.object(m, "status_lines", return_value=[]), patch.object(
            m, "restore"
        ) as restore:
            with self.assertRaises(m.Stop):
                m.activate(b"original")
            restore.assert_called_once()

    def test_heartbeat_failure_restores_no_retry(self):
        m.OLD_PLIST.write_bytes(b"original")
        with patch.object(m, "launch"), patch.object(m, "put_plist"), patch.object(
            m, "audit"
        ), patch.object(
            m,
            "status_lines",
            side_effect=[
                [],
                [
                    "2026-10-04T00:00:00Z keychain unavailable status=-25293; no heartbeat sent"
                ],
            ],
        ), patch.object(
            m, "restore"
        ) as restore:
            with self.assertRaises(m.Stop):
                m.activate(b"original")
            restore.assert_called_once()

    def test_old_change_stops_before_bootout(self):
        m.OLD_PLIST.write_bytes(b"changed")
        with patch.object(m, "launch") as launch:
            with self.assertRaises(m.Stop):
                m.activate(b"original")
            launch.assert_not_called()

    def test_probe_creation_failure_cleans_only_owned_probe(self):
        (m.DEST / "probe-owned").write_text("created-by-this-install")
        calls = []
        # Initial orphan check correctly refuses another attempt instead of deleting it.
        with patch.object(m, "loaded", return_value=False), patch.object(
            m, "run"
        ) as run:
            with self.assertRaises(m.Stop):
                m.real_probe()
            run.assert_not_called()

    def test_probe_failure_after_creation_runs_cleanup(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            if args[-1] == "probe-create":
                (m.DEST / "probe-owned").write_text("created-by-this-install")
                raise m.Stop("fake ACL validation failure")

        with patch.object(m, "loaded", return_value=False), patch.object(
            m, "run", side_effect=run
        ):
            with self.assertRaises(m.Stop):
                m.real_probe()
        self.assertEqual([x[-1] for x in calls], ["probe-create", "probe-delete"])

    def test_no_secret_or_restart_paths(self):
        source = Path(m.__file__).read_text()
        for forbidden in [
            "SecItemUpdate",
            "reboot",
            "shutdown",
            "shell=True",
            "getpass(",
            "readpassphrase(",
        ]:
            self.assertNotIn(forbidden, source)
        self.assertEqual(set(m.ENV), {"HOME", "PATH", "LANG"})


import copy, json


class Tests(unittest.TestCase):
    def setUp(self):
        signer = patch.object(r, "expected_cdhash", return_value="a" * 40)
        signer.start()
        self.addCleanup(signer.stop)
        self.before = {
            "count": 1,
            "sender_cdhash": "a" * 40,
            "acl": ["exact fake ACL"],
            "partitions": [],
            "owner": [0, 0, 0],
            "sender_sha256": "fixed",
            "database_version": 256,
            "keychain_path": "/Library/Keychains/System.keychain",
        }
        self.after = copy.deepcopy(self.before)

    def test_legacy_format_ack_then_unchanged_metadata(self):
        read = Mock(side_effect=[self.before, self.after])
        send = Mock()
        self.assertEqual(r.metadata_ready_flow(read, send), self.after)
        send.assert_called_once_with(self.before)

    def test_legacy_format_does_not_create_partition(self):
        self.after["partitions"] = ["cdhash:" + ("a" * 40)]
        with self.assertRaises(r.Stop):
            r.validate_after(self.before, self.after)

    def test_failure_never_retries(self):
        send = Mock(side_effect=r.Stop("failure"))
        with self.assertRaises(r.Stop):
            r.metadata_ready_flow(lambda: self.before, send)
        send.assert_called_once()

    def test_unexpected_scope_before_send(self):
        for key, value in [
            ("count", 0),
            ("count", 2),
            ("sender_cdhash", "different"),
            ("partitions", ["apple:"]),
            ("acl", []),
            ("database_version", 512),
            ("keychain_path", "other"),
        ]:
            b = copy.deepcopy(self.before)
            b[key] = value
            send = Mock()
            with self.assertRaises(r.Stop):
                r.metadata_ready_flow(lambda: b, send)
            send.assert_not_called()

    def test_nonpartition_changes_stop(self):
        for key, value in [
            ("owner", [1, 0, 0]),
            ("acl", ["changed"]),
            ("sender_sha256", "changed"),
        ]:
            a = copy.deepcopy(self.after)
            a[key] = value
            with self.assertRaises(r.Stop):
                r.validate_after(self.before, a)

    def test_prior_attempt_not_blindly_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "attempt"
            plist = Path(d) / "job"
            marker.write_text(
                json.dumps({"version": 1, "started": 123, "snapshot": self.before})
            )
            m = Mock()
            m.configuration.return_value = {
                "Label": "unused",
                "StartInterval": 60,
                "UserName": "czlin",
                "ProgramArguments": ["fixed", "run"],
            }
            with patch.object(r, "MARKER", marker), patch.object(r, "PLIST", plist):
                with self.assertRaises(r.Stop):
                    r.ensure_attempt(m, self.before)
            m.launch.assert_not_called()

    def test_running_existing_attempt_reused(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "attempt"
            plist = Path(d) / "job"
            marker.write_text(
                json.dumps({"version": 1, "started": 123, "snapshot": self.before})
            )
            m = Mock()
            m.configuration.return_value = {
                "Label": "unused",
                "StartInterval": 60,
                "UserName": "czlin",
                "ProgramArguments": ["fixed", "run"],
            }
            m.loaded.return_value = True
            plist.write_bytes(plistlib.dumps(r.job_plist(m)))
            with patch.object(r, "MARKER", marker), patch.object(r, "PLIST", plist):
                self.assertEqual(r.ensure_attempt(m, self.before), 123)
            m.launch.assert_not_called()

    def test_acknowledged_attempt_reentry_never_resends(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "attempt"
            plist = Path(d) / "job"
            marker.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "started": 123,
                        "snapshot": self.before,
                        "acknowledged": True,
                    }
                )
            )
            m = Mock()
            m.configuration.return_value = {"Label": "unused", "StartInterval": 60}
            m.loaded.return_value = False
            with patch.object(r, "MARKER", marker), patch.object(r, "PLIST", plist):
                r.one_attempt(m, self.before)
            m.launch.assert_not_called()

    def test_no_secret_or_key_mutation_apis(self):
        source = Path(r.__file__).read_text()
        native = (PACKAGE / "sender.swift").read_text()
        for token in [
            "SecItemUpdate",
            "SecItemAdd",
            "SecItemDelete",
            "kSecReturnData",
            "getpass(",
            "readpassphrase(",
        ]:
            self.assertNotIn(token, source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("SecItemUpdate", native)
        self.assertEqual(native.count("SecItemDelete("), 1)
        self.assertIn("SecItemDelete(try baseQuery(probeService)", native)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = settings.load(PACKAGE / "config.example.json")

    def test_no_secret_or_surprise_fields(self):
        for name in ["api_key", "password", "token"]:
            c = copy.deepcopy(self.config)
            c[name] = "FAKE"
            with self.assertRaises(ValueError):
                settings.validate(c)

    def test_origin_and_identity_boundaries(self):
        for key, value in [
            ("origin", "http://example.invalid"),
            ("origin", "https://user:pass@example.invalid"),
            ("origin", "https://example.invalid/path"),
            ("uid", 0),
            ("uid", True),
            ("home", "/Users/example/../other"),
            ("install", "/Library"),
            ("python", "../bin/python"),
            ("label", "invalid/label"),
        ]:
            c = copy.deepcopy(self.config)
            c[key] = value
            with self.assertRaises(ValueError):
                settings.validate(c)

    def test_swift_settings_no_code_injection(self):
        c = copy.deepcopy(self.config)
        c["hostname"] = 'quote"; fatalError()'
        rendered = settings.swift_settings(c)
        self.assertIn('quote\\"; fatalError()', rendered)
        c["hostname"] = "\\(fatalError())"
        with self.assertRaises(ValueError):
            settings.swift_settings(c)

    def test_modern_partition_rewrite_rejected(self):
        base = {
            "count": 1,
            "sender_cdhash": "a" * 40,
            "acl": ["fake exact ACL"],
            "keychain_path": "/Library/Keychains/System.keychain",
            "database_version": 512,
            "partitions": [["cdhash:" + ("a" * 40)]],
        }
        with patch.object(r, "expected_cdhash", return_value="a" * 40):
            r.validate_before(base)
            for value in [
                [],
                [["cdhash:repair-helper"]],
                [["apple:"]],
                [["cdhash:" + ("a" * 40), "apple:"]],
            ]:
                changed = copy.deepcopy(base)
                changed["partitions"] = value
                with self.assertRaises(r.Stop):
                    r.validate_before(changed)

    def test_unknown_database_format_rejected(self):
        with patch.object(r, "expected_cdhash", return_value="a" * 40):
            with self.assertRaises(r.Stop):
                r.validate_before(
                    {
                        "count": 1,
                        "sender_cdhash": "a" * 40,
                        "acl": ["fake"],
                        "keychain_path": "/Library/Keychains/System.keychain",
                        "database_version": 768,
                        "partitions": [],
                    }
                )

    def test_hangup_during_switch_restores(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "old"
            old.write_bytes(b"original")
            with patch.multiple(
                m,
                PREVIOUS={"label": "fake"},
                OLD_PLIST=old,
                BACKUP=root / "backup",
                PLIST=root / "boot",
            ), patch.object(m, "status_lines", return_value=[]), patch.object(
                m, "launch", side_effect=KeyboardInterrupt
            ), patch.object(
                m, "restore"
            ) as restore:
                with self.assertRaises(KeyboardInterrupt):
                    m.activate(b"original")
                restore.assert_called_once()

    def test_unknown_job_never_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m, "PLIST", Path(folder) / "absent"), patch.object(
                m, "loaded", return_value=True
            ), patch.object(m, "launch") as launch:
                with self.assertRaises(m.Stop):
                    m.restore()
                launch.assert_not_called()

    def test_concurrent_operation_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(m, "DEST", Path(folder)), patch.object(
                m, "OPERATION_LOCKS", []
            ):
                try:
                    m.lock_operation()
                    with self.assertRaises(m.Stop):
                        m.lock_operation()
                finally:
                    for descriptor in m.OPERATION_LOCKS:
                        m.os.close(descriptor)

    def test_fresh_install_has_no_gui_mutations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.multiple(
                m,
                PREVIOUS=None,
                OLD_PLIST=root / "absent",
                BACKUP=root / "backup",
                PLIST=root / "boot",
            ), patch.object(m, "put_plist"), patch.object(m, "audit"), patch.object(
                m, "launch"
            ) as launch, patch.object(
                m,
                "status_lines",
                side_effect=[
                    [],
                    [
                        "2026-01-01T00:00:00Z heartbeat acknowledged",
                        "2026-01-01T00:01:01Z heartbeat acknowledged",
                    ],
                ],
            ):
                m.activate(None)
                launch.assert_called_once_with("bootstrap", "system", m.PLIST)


if __name__ == "__main__":
    unittest.main()
