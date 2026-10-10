"""Free local scheduling entry point; preview/check never publishes anything.

Windows Task Scheduler provides the clock. The existing production CLIs own
calendar, freshness, persistence, leases and delivery decisions. Raw child logs
are intentionally not persisted because provider errors can contain URLs/tokens.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import ntpath
import os
from pathlib import Path
import signal
import subprocess
import sys
import tomllib
from typing import Any

from market_calendar import is_scheduled_session
from scan_schedule import TPE

ROOT = Path(__file__).resolve().parent
TIMEOUTS = {"scan": 36 * 60, "prices": 6 * 60}
TREE_KILL_TIMEOUT = 15
CHILD_CLEANUP_TIMEOUT = 10
DEPENDENCIES = ("firebase_admin", "google.cloud.firestore", "streamlit", "yfinance",
                "pandas", "numpy", "PIL", "requests", "plotly")


def check_environment(root: Path = ROOT) -> dict[str, Any]:
    """Check local prerequisites only, without importing production/cloud clients."""
    checks: dict[str, bool] = {"python_314": sys.version_info[:2] == (3, 14)}
    for module in DEPENDENCIES:
        try:
            checks[f"dependency_{module}"] = importlib.util.find_spec(module) is not None
        except (ModuleNotFoundError, ValueError):
            checks[f"dependency_{module}"] = False
    try:
        with (root / ".streamlit" / "secrets.toml").open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, ValueError):
        config = {}
    firebase = config.get("firebase", {})
    checks["firebase_configured"] = isinstance(firebase, dict) and all(
        bool(firebase.get(key)) for key in ("project_id", "client_email", "private_key")
    )
    nested = config.get("telegram", {})
    nested = nested if isinstance(nested, dict) else {}

    def secret(name: str) -> Any:
        return config.get(name) or os.getenv(name)

    checks["telegram_configured"] = bool(
        (secret("TELEGRAM_BOT_TOKEN") or secret("TELEGRAM_TOKEN") or nested.get("bot_token") or nested.get("token"))
        and (secret("TELEGRAM_CHAT_ID") or secret("TELEGRAM_USER_ID") or nested.get("chat_id"))
    )
    checks["calendar_known"] = is_scheduled_session(datetime.now(TPE).date()) is not None
    checks["entrypoints_present"] = all((root / name).is_file() for name in
                                        ("scanner.py", "prediction_notifications.py"))
    return {"status": "ready" if all(checks.values()) else "not_ready", "checks": checks}


def child_command(job: str, root: Path = ROOT) -> list[str]:
    if job == "scan":
        return [sys.executable, "-X", "utf8", "-u", str(root / "scanner.py"), "--scheduled"]
    if job == "prices":
        return [sys.executable, "-X", "utf8", "-u", str(root / "prediction_notifications.py"), "--send", "--scheduled"]
    raise ValueError("Unknown local job")


def scheduled_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # A developer shell's manual maintenance flag must not force a scheduled
    # rescan. Use explicit --scheduled, not a fabricated GitHub event/run ID.
    for name in ("FORCE_SCAN", "GITHUB_EVENT_NAME", "GITHUB_RUN_ID", "GITHUB_REPOSITORY"):
        environment.pop(name, None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _terminate_owned_tree(process: Any, *, windows: bool) -> None:
    """Target only the PID returned by our Popen, before killing its parent.

    Windows taskkill must see the live parent to discover its descendants. Do
    not first call Popen.kill() as subprocess.run(timeout=...) would do.
    """
    pid = process.pid
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise OSError("Owned child PID unavailable")
    if process.poll() is not None:
        return
    if windows:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        if not ntpath.isabs(system_root):
            raise OSError("Windows system directory unavailable")
        taskkill = ntpath.join(system_root, "System32", "taskkill.exe")
        result = subprocess.run(
            [taskkill, "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=TREE_KILL_TIMEOUT, check=False, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 and process.poll() is None:
            raise OSError("Owned child tree termination failed")
    else:
        # _bounded_run starts a new session; this group cannot include the
        # caller, shell, or another scheduled job.
        getattr(os, "killpg")(pid, getattr(signal, "SIGKILL"))


def _bounded_run(command: list[str], *, timeout: float, check: bool = False, **kwargs: Any) -> Any:
    """Run privately and bound both the job and timeout cleanup.

    Never use Popen as a context manager here: its implicit wait() can hang on
    an unkillable process after the bounded cleanup. Discard all raw output.
    """
    windows = os.name == "nt"
    if not windows:
        kwargs["start_new_session"] = True
    kwargs["shell"] = False
    kwargs["stdin"] = subprocess.DEVNULL
    # No PIPE reader threads: a surviving descendant must not keep a pipe
    # open, retain secret-bearing output, or block stream.close() during cleanup.
    kwargs["stdout"] = subprocess.DEVNULL
    kwargs["stderr"] = subprocess.DEVNULL
    process = subprocess.Popen(command, **kwargs)
    try:
        process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            _terminate_owned_tree(process, windows=windows)
        except (OSError, subprocess.TimeoutExpired):
            # Best-effort direct-child fallback. A tree-kill error is never
            # interpreted as successful delivery.
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.communicate(timeout=CHILD_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=CHILD_CLEANUP_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                pass
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        # Do not propagate TimeoutExpired.output/stderr or raw child arguments.
        raise subprocess.TimeoutExpired("local scheduled child", timeout) from None
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, "local scheduled child")
    return subprocess.CompletedProcess(command, process.returncode)


def run_job(job: str, *, root: Path = ROOT, runner: Any = _bounded_run) -> dict[str, Any]:
    command = child_command(job, root)
    try:
        result = runner(command, cwd=root, env=scheduled_environment(), timeout=TIMEOUTS[job],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                        encoding="utf-8", errors="replace", check=False,
                        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        # Exit zero can mean a holiday, completed backup or a busy lease. It is
        # not evidence of Telegram delivery; only the cloud receipt proves that.
        return {"job": job, "status": "process_ok" if result.returncode == 0 else "process_failed",
                "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"job": job, "status": "timeout", "exit_code": 124}
    except OSError:
        return {"job": job, "status": "launch_failed", "exit_code": 1}


def write_summary(result: dict[str, Any], root: Path = ROOT) -> None:
    directory = root / "logs"
    directory.mkdir(exist_ok=True)
    # Store only fixed status/exit metadata, never raw child output or secrets.
    value: dict[str, Any] = {key: result[key] for key in ("job", "status", "exit_code") if key in result}
    value["checked_at"] = datetime.now(TPE).isoformat()
    handler = RotatingFileHandler(directory / "local_schedule.jsonl", maxBytes=100_000,
                                  backupCount=3, encoding="utf-8")
    try:
        record = logging.LogRecord("local_schedule", logging.INFO, __file__, 0,
                                   json.dumps(value, ensure_ascii=False), (), None)
        handler.emit(record)
    finally:
        handler.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", choices=tuple(TIMEOUTS))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only prerequisite check")
    mode.add_argument("--run", action="store_true", help="Execute the scheduled job with normal deduplication")
    args = parser.parse_args(argv)
    if args.run and not args.job:
        parser.error("--run requires --job")
    readiness = check_environment()
    if not args.run:
        print(json.dumps(readiness, ensure_ascii=False))
        return 0 if readiness["status"] == "ready" else 1
    if readiness["status"] != "ready":
        result: dict[str, Any] = {"job": args.job, "status": "not_ready", "exit_code": 1}
    else:
        result = run_job(args.job)
    write_summary(result)
    print(json.dumps(result, ensure_ascii=False))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
