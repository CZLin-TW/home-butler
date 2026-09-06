"""Offline tests for the paid benchmark's guardrails, scoring and accounting."""

from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from evals import sonnet_effort as bench


def response(actions, reply="好的。", route="full", stop="end_turn"):
    body = {"actions": [{"action": action, "args": [{"key": k, "value": v} for k, v in data.items()]}
                        for action, data in actions]}
    if route == "full":
        body["reply"] = reply
    return {"stop_reason": stop, "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}]}


class BenchmarkTests(unittest.TestCase):
    def case(self, name):
        return next(c for c in bench.CASES if c["id"] == name)

    def test_exact_budget_and_matched_input_across_three_arms(self):
        manifest = bench.make_manifest()
        trials = manifest["trials"]
        self.assertEqual(len(trials), 120)
        self.assertEqual(len({t["id"] for t in trials}), 120)
        self.assertEqual(set(Counter((t["case_id"], t["group"]) for t in trials).values()), {2})
        for case in bench.CASES:
            bodies = []
            for trial in trials:
                if trial["case_id"] == case["id"]:
                    body = json.loads(json.dumps(trial["request"]))
                    del body["thinking"]
                    del body["output_config"]["effort"]
                    bodies.append(body)
            self.assertTrue(all(body == bodies[0] for body in bodies))
            self.assertNotIn("cache_control", bodies[0])

    def test_correct_alias_and_suffix_commands_pass(self):
        for name in ("F02", "D01"):
            case = self.case(name)
            reply = response([("control_ir", {"device_name": "主臥電風扇", "button": "開"})], route=case["route"])
            self.assertTrue(bench.score(case, reply)["pass"])

    def test_valid_json_with_wrong_room_or_extra_action_fails(self):
        for actions in [[("control_ir", {"device_name": "客廳電扇", "button": "開"})],
                        [("control_ir", {"device_name": "主臥電扇", "button": "開"}), ("query_devices", {})]]:
            result = bench.score(self.case("D01"), response(actions, route="devices"))
            self.assertTrue(result["schema_valid"])
            self.assertFalse(result["pass"])

    def test_wrong_day_and_extra_destructive_action_fail(self):
        case = self.case("F07")
        data = {**case["expected"][0]["required"], "trigger_time": "2026-09-07 19:30"}
        self.assertFalse(bench.score(case, response([("add_schedule", data)]))["pass"])
        self.assertFalse(bench.score(self.case("F10"), response([("stop_recurring_todo", {"item": "吃藥"})], reply="要永久停止吃藥嗎？"))["pass"])

    def test_unknown_device_may_be_clarified_or_safely_rejected_by_policy(self):
        for actions in [[("unclear", {})], [("control_ir", {"device_name": "書房電扇", "button": "開"})]]:
            result = bench.score(self.case("D07"), response(actions, route="devices"))
            self.assertTrue(result["pass"])
            self.assertFalse(result["policy_accepted"])
        self.assertFalse(bench.score(self.case("D07"), response(
            [("control_ir", {"device_name": "主臥電扇", "button": "開"})], route="devices"))["pass"])

    def test_mixed_request_must_not_partially_execute(self):
        self.assertTrue(bench.score(self.case("D10"), response([("unsupported", {})], route="devices"))["pass"])
        self.assertFalse(bench.score(self.case("D10"), response(
            [("control_ir", {"device_name": "主臥電扇", "button": "開"}), ("unsupported", {})], route="devices"))["pass"])

    def test_truncated_but_parseable_json_is_not_success(self):
        result = bench.score(self.case("D09"), response([("unsupported", {})], route="devices", stop="max_tokens"))
        self.assertFalse(result["pass"])
        self.assertEqual(result["reason"], "incomplete_or_refused_response")

    def test_cost_counts_all_output_and_cache_categories(self):
        self.assertIsNone(bench.usage_cost(None))
        self.assertAlmostEqual(bench.usage_cost({"input_tokens": 1000, "output_tokens": 200,
                              "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 2000,
                              "cache_creation": {"ephemeral_1h_input_tokens": 1000}}), 0.0107)

    def test_missing_key_sends_zero_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bench, "api_key", return_value=""), patch("httpx.Client") as client:
            with self.assertRaises(RuntimeError):
                bench.run(Path(directory), bench.make_manifest())
            client.assert_not_called()

    def test_supplied_key_does_not_read_environment_and_cancel_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bench, "api_key") as key_reader, patch("httpx.Client") as client:
            with self.assertRaises(RuntimeError):
                bench.run(Path(directory), bench.make_manifest(), supplied_key="")
            key_reader.assert_not_called()
            client.assert_not_called()

    def test_gui_cancel_does_not_call_api_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch("evals.secret_prompt.prompt_key", return_value=None), patch.object(bench, "run") as run:
            with patch("sys.argv", ["benchmark", "run", "--prompt-key", "--out", directory]):
                bench.main()
            run.assert_not_called()
            self.assertFalse((Path(directory) / "runner.lock").exists())
            self.assertFalse((Path(directory) / "attempts.jsonl").exists())

    def test_auth_error_stops_without_retry_and_redacts_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bench, "api_key", return_value="fake-secret"), patch("httpx.Client") as client:
            out = Path(directory)
            client.return_value.__enter__.return_value.post.return_value = Mock(
                status_code=401, headers={}, json=Mock(return_value={"error": {"type": "authentication_error", "message": "fake-secret"}}))
            bench.run(out, bench.make_manifest())
            client.return_value.__enter__.return_value.post.assert_called_once()
            self.assertNotIn("fake-secret", (out / "attempts.jsonl").read_text(encoding="utf-8"))

    def test_budget_survives_interrupted_calls_and_does_not_resend(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bench, "api_key", return_value="fake"), patch("httpx.Client") as client:
            out = Path(directory)
            manifest = bench.make_manifest()
            for trial in manifest["trials"]:
                bench.append_event(out / "attempts.jsonl", {"event": "started", "id": trial["id"]})
            bench.run(out, manifest)
            client.return_value.__enter__.return_value.post.assert_not_called()
            summary = bench.report(out, manifest)
            self.assertEqual(summary["claimed_calls"], 120)
            self.assertEqual(summary["unknown_usage_calls"], 120)

    def test_corrupt_ledger_and_changed_manifest_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            manifest = bench.prepare(out)
            manifest["limit"] = 121
            (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                bench.prepare(out)
            (out / "attempts.jsonl").write_text('{"event":', encoding="utf-8")
            with self.assertRaises(ValueError):
                bench.read_events(out / "attempts.jsonl")


if __name__ == "__main__":
    unittest.main()
