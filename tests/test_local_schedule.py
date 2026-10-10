import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import local_schedule as local


class LocalScheduleTests(unittest.TestCase):
    def test_commands_have_explicit_schedule_context_and_never_force(self):
        for job in ("scan", "prices"):
            command = local.child_command(job)
            self.assertIn("--scheduled", command)
            self.assertNotIn("--resend-telegram", command)
            self.assertNotIn("--allow-local", command)
            self.assertTrue(Path(command[4]).is_absolute())
        self.assertIn("--send", local.child_command("prices"))
        with self.assertRaises(ValueError):
            local.child_command("other")

    def test_schedule_does_not_inherit_forced_scan_or_forge_github_identity(self):
        with patch.dict(local.os.environ, {"FORCE_SCAN": "1", "GITHUB_EVENT_NAME": "schedule",
                                         "GITHUB_RUN_ID": "123", "GITHUB_REPOSITORY": "x/y",
                                         "TELEGRAM_BOT_TOKEN": "private"}, clear=True):
            env = local.scheduled_environment()
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "private")
        self.assertFalse(set(env) & {"FORCE_SCAN", "GITHUB_EVENT_NAME", "GITHUB_RUN_ID", "GITHUB_REPOSITORY"})

    def test_child_output_cannot_leak_into_summary(self):
        for returncode in (0, 1):
            run = Mock(return_value=Mock(returncode=returncode, stdout="token=never-print-me"))
            result = local.run_job("prices", runner=run)
            self.assertEqual(result["exit_code"], returncode)
            self.assertNotIn("never-print", json.dumps(result))
            self.assertLess(run.call_args.kwargs["timeout"], 8 * 60)
            self.assertEqual(run.call_args.kwargs["cwd"], local.ROOT)
            self.assertIs(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertNotIn("sent", result["status"])

    def test_timeout_and_start_failure_are_not_success_or_raw_error(self):
        for exc, status in ((subprocess.TimeoutExpired("private-token", 3), "timeout"),
                            (OSError("private-token"), "launch_failed")):
            result = local.run_job("scan", runner=Mock(side_effect=exc))
            self.assertEqual(result["status"], status)
            self.assertNotEqual(result["exit_code"], 0)
            self.assertNotIn("private-token", json.dumps(result))
        self.assertLess(local.TIMEOUTS["scan"], 38 * 60)

    def test_preview_and_check_never_spawn_or_write(self):
        with patch.object(local, "check_environment", return_value={"status": "ready"}), \
                patch.object(local, "run_job") as run, patch.object(local, "write_summary") as write, \
                patch("builtins.print"):
            self.assertEqual(local.main([]), 0)
            self.assertEqual(local.main(["--check"]), 0)
            self.assertEqual(local.main(["--job", "scan"]), 0)
        run.assert_not_called()
        write.assert_not_called()

    def test_not_ready_run_is_blocked(self):
        with patch.object(local, "check_environment", return_value={"status": "not_ready"}), \
                patch.object(local, "run_job") as run, patch.object(local, "write_summary") as write, \
                patch("builtins.print"):
            self.assertEqual(local.main(["--job", "scan", "--run"]), 1)
        run.assert_not_called()
        self.assertEqual(write.call_args.args[0]["status"], "not_ready")

    def test_no_config_check_returns_only_booleans_without_cloud_clients(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(local.os.environ, {}, clear=True):
            result = local.check_environment(Path(directory))
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["checks"]["firebase_configured"])
        self.assertFalse(result["checks"]["entrypoints_present"])
        self.assertTrue(all(isinstance(item, bool) for item in result["checks"].values()))

    def test_summary_is_bounded_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local.write_summary({"job": "prices", "status": "process_failed", "exit_code": 1,
                                 "stdout": "private-token"}, root)
            text = (root / "logs" / "local_schedule.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("private-token", text)
        self.assertEqual(json.loads(text)["exit_code"], 1)
        self.assertIn("+08:00", json.loads(text)["checked_at"])

    def test_cloud_backup_keeps_schedule_and_does_not_force_manual_retry_by_default(self):
        workflow = (local.ROOT / ".github" / "workflows" / "daily_scan.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("force_rescan:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn('if [ "$INPUT_FORCE_RESCAN" = "true" ]; then', workflow)
        self.assertNotIn('FORCE_SCAN=1 python', workflow)
        self.assertNotIn('FORCE_SCAN=1 SCAN_LIMIT', workflow)
        self.assertIn('python scanner.py --scheduled', workflow)


class BoundedChildTests(unittest.TestCase):
    def child(self):
        process = Mock(pid=43210, returncode=0, stdin=None, stdout=None, stderr=None)
        process.poll.return_value = None
        return process

    @patch.object(local.subprocess, "Popen")
    def test_success_discards_all_raw_output_and_never_uses_shell(self, popen):
        process = self.child()
        process.communicate.return_value = ("private-token", None)
        popen.return_value = process
        result = local._bounded_run(["python", "owned.py"], timeout=20)
        process.communicate.assert_called_once_with(timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(result.stdout)
        self.assertIs(popen.call_args.kwargs["shell"], False)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(popen.call_args.kwargs[stream], subprocess.DEVNULL)
        process.kill.assert_not_called()

    @patch.object(local.subprocess, "run")
    def test_windows_tree_kill_uses_only_owned_pid_with_bounded_hidden_command(self, run):
        process = self.child()
        run.return_value = Mock(returncode=0)
        with patch.dict(local.os.environ, {"SystemRoot": r"C:\Windows"}):
            local._terminate_owned_tree(process, windows=True)
        self.assertEqual(run.call_args.args[0],
                         [r"C:\Windows\System32\taskkill.exe", "/PID", "43210", "/T", "/F"])
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertEqual(run.call_args.kwargs["timeout"], local.TREE_KILL_TIMEOUT)
        self.assertIn("creationflags", run.call_args.kwargs)
        self.assertIs(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        process.kill.assert_not_called()

    @patch.object(local.subprocess, "run")
    def test_tree_kill_never_targets_invalid_or_already_exited_pid(self, run):
        for pid in (0, -1, True, "43210"):
            process = self.child()
            process.pid = pid
            with self.subTest(pid=pid), self.assertRaises(OSError):
                local._terminate_owned_tree(process, windows=True)
        process = self.child()
        process.poll.return_value = 0
        local._terminate_owned_tree(process, windows=True)
        run.assert_not_called()

    @patch.object(local, "_terminate_owned_tree")
    @patch.object(local.subprocess, "Popen")
    def test_timeout_terminates_tree_before_cleanup_and_discards_exception_output(self, popen, tree):
        process = self.child()
        events = []

        def communicate(**kwargs):
            events.append(("communicate", kwargs["timeout"]))
            if len(events) == 1:
                raise subprocess.TimeoutExpired("secret-command", 20, output="private-token", stderr="secret-error")
            return (None, None)

        process.communicate.side_effect = communicate
        tree.side_effect = lambda *_args, **_kwargs: events.append(("tree", process.pid))
        popen.return_value = process
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            local._bounded_run(["python", "owned.py"], timeout=20)
        self.assertEqual(events, [("communicate", 20), ("tree", 43210),
                                  ("communicate", local.CHILD_CLEANUP_TIMEOUT)])
        self.assertIsNone(raised.exception.output)
        self.assertIsNone(raised.exception.stderr)
        self.assertNotIn("secret", str(raised.exception))
        process.kill.assert_not_called()

    @patch.object(local, "_terminate_owned_tree", side_effect=OSError("private-token"))
    @patch.object(local.subprocess, "Popen")
    def test_tree_failure_and_unresponsive_child_have_only_bounded_cleanup(self, popen, tree):
        process = self.child()
        process.communicate.side_effect = subprocess.TimeoutExpired("private-token", 20)
        process.wait.side_effect = subprocess.TimeoutExpired("private-token", 10)
        popen.return_value = process
        with self.assertRaises(subprocess.TimeoutExpired):
            local._bounded_run(["python", "owned.py"], timeout=20)
        tree.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertGreaterEqual(process.kill.call_count, 1)
        process.wait.assert_called_once_with(timeout=local.CHILD_CLEANUP_TIMEOUT)

    @patch.object(local.subprocess, "run")
    def test_windows_tree_kill_failure_is_not_silently_successful(self, run):
        run.return_value = Mock(returncode=1)
        with self.assertRaises(OSError):
            local._terminate_owned_tree(self.child(), windows=True)

    @patch.object(local.subprocess, "Popen", side_effect=OSError("private-token"))
    def test_default_runner_launch_failure_is_sanitized_by_run_job(self, _popen):
        result = local.run_job("prices")
        self.assertEqual(result["status"], "launch_failed")
        self.assertNotIn("private-token", str(result))


if __name__ == "__main__":
    unittest.main()
