from threading import Event, Thread
import unittest
from job_runner import JobRunner


class JobTests(unittest.TestCase):
    def test_slow_job_does_not_block_schedule_or_overlap_itself(self):
        runner = JobRunner()
        started, release, schedule = Event(), Event(), Event()
        def slow(): started.set(); release.wait(2)
        runner.add("notion", 300, slow)
        runner.add("schedules", 60, schedule.set)
        worker = Thread(target=runner.run_once, args=("notion",)); worker.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertFalse(runner.run_once("notion"))
            runner.run_once("schedules")
            self.assertTrue(schedule.is_set())
            self.assertTrue(runner.snapshot()["notion"]["running"])
        finally:
            release.set(); worker.join(2)
        self.assertIsNotNone(runner.snapshot()["notion"]["last_success_at"])

    def test_error_is_observable_and_recovery_clears_it(self):
        runner = JobRunner()
        runner.add("test", 60, lambda: (_ for _ in ()).throw(ValueError("sensitive details")))
        runner.run_once("test")
        self.assertEqual(runner.snapshot()["test"]["last_error"], "ValueError")
        self.assertIsNone(runner.snapshot()["test"]["last_success_at"])
        runner.jobs["test"]["callback"] = lambda: None
        runner.run_once("test")
        self.assertIsNone(runner.snapshot()["test"]["last_error"])
        self.assertNotIn("callback", runner.snapshot()["test"])


if __name__ == "__main__": unittest.main()
