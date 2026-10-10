"""Static/parse-only checks; never register tasks or start the trading scripts."""

import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_local_schedule.ps1"


class LocalScheduleInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_preview_is_default_and_registration_is_guarded(self):
        self.assertIn("DefaultParameterSetName = 'Configure'", self.source)
        self.assertIn("[switch]$Install", self.source)
        guard = self.source.index("if (-not $Install) {")
        registration = self.source.index("$null = Register-ScheduledTask")
        self.assertLess(guard, registration)
        self.assertIn("return", self.source[guard:registration])
        self.assertNotIn("Start-ScheduledTask", self.source)

    def test_fixed_weekday_jobs_and_times(self):
        for name in ("TaiwanStock-DailyScan", "TaiwanStock-PredictionPrices"):
            self.assertIn(name, self.source)
        for hour in ("08:05", "15:17", "16:17", "22:17", "09:05", "09:20", "09:35", "10:05", "10:20",
                     "11:05", "11:20", "12:05", "12:20", "13:05", "13:20", "13:35", "13:50", "14:05", "14:20"):
            self.assertIn("'" + hour + "'", self.source)
        self.assertIn("@('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')", self.source)
        self.assertIn("-Weekly -WeeksInterval 1 -DaysOfWeek $weekdays", self.source)

    def test_current_user_ownership_is_checked_before_any_registration(self):
        first_registration = self.source.index("$null = Register-ScheduledTask")
        first_inspection = self.source.index("Inspect ownership of BOTH")
        self.assertLess(first_inspection, first_registration)
        self.assertIn("$existing[0].Description -ne $ownership", self.source)
        self.assertIn("$existing[0].Principal.UserId -notin @($ownerSid, $ownerName)", self.source)
        self.assertIn("-LogonType Interactive -RunLevel Limited", self.source)
        self.assertNotIn("-RunLevel Highest", self.source)
        self.assertNotIn("-Password", self.source)

    def test_runtime_cwd_timezone_and_readonly_preflight(self):
        self.assertIn("(Get-TimeZone).Id -ne 'Taipei Standard Time'", self.source)
        self.assertNotIn("Set-TimeZone", self.source)
        self.assertIn("import sys; print(sys.executable)", self.source)
        self.assertIn("$runner --check", self.source)
        self.assertIn("-WorkingDirectory $repo", self.source)
        self.assertIn("IsPathRooted($PythonPath)", self.source)
        self.assertNotIn("git pull", self.source)

    def test_default_repository_is_resolved_after_parameter_binding(self):
        parameter_block = self.source[:self.source.index("# No registration occurs")]
        self.assertIn("[string]$RepositoryPath = ''", parameter_block)
        self.assertNotIn("Split-Path", parameter_block)
        self.assertNotIn("$PSScriptRoot", parameter_block)
        self.assertIn("$scriptDirectory = Split-Path -Parent $PSCommandPath", self.source)
        self.assertIn("$RepositoryPath = Split-Path -Parent $scriptDirectory", self.source)

    def test_hidden_child_and_bounded_runtime_without_catchup_or_wake(self):
        self.assertIn("-WindowStyle Hidden -PassThru", self.source)
        self.assertIn("-NoProfile -NonInteractive -WindowStyle Hidden", self.source)
        self.assertIn("{ 38 } else { 8 }", self.source)
        self.assertIn("/PID ([string]$child.Id) /T /F", self.source)
        self.assertIn("-MultipleInstances IgnoreNew", self.source)
        self.assertIn("-ExecutionTimeLimit", self.source)
        self.assertIn("$settings.StartWhenAvailable = $false", self.source)
        self.assertIn("$settings.WakeToRun = $false", self.source)
        self.assertNotIn("Bypass", self.source)
        self.assertNotIn("Set-ExecutionPolicy", self.source)

    def test_no_secret_parameters_or_raw_logs_in_installer(self):
        for token in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FINMIND_TOKEN", "secrets.toml",
                      "RedirectStandardOutput", "RedirectStandardError", "--resend-telegram", "FORCE_SCAN"):
            self.assertNotIn(token, self.source)
        self.assertIn("' --job ' + $RunJob + ' --run'", self.source)

    @unittest.skipUnless(os.name == "nt", "PowerShell parser exists only on Windows test host")
    def test_powershell_ast_has_no_parse_errors_without_executing_script(self):
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if not powershell.is_file():
            self.skipTest("Windows PowerShell unavailable")
        literal = str(SCRIPT).replace("'", "''")
        command = ("$parseErrors=$null; $parseTokens=$null; "
                   f"$null=[System.Management.Automation.Language.Parser]::ParseFile('{literal}',"
                   "[ref]$parseTokens,[ref]$parseErrors); "
                   "if($parseErrors.Count){$parseErrors | ForEach-Object {$_.Message}; exit 1}; exit 0")
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
