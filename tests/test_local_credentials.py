"""Mocked Windows vault only: these tests never access or change real secrets."""
from contextlib import redirect_stderr, redirect_stdout
import ctypes
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import local_credentials as credentials


ROOT = Path(__file__).resolve().parents[1]
ENV = {"TELEGRAM_BOT_TOKEN": "123456:private-test-token", "TELEGRAM_CHAT_ID": "-100123456"}


class FakeVault:
    def __init__(self):
        self.items = {}
        self.reads = []
        self.writes = []
        self.frees = []
        self.allocated = []
        self.fail_write_at = None
        self.override = {}

    def CredWriteW(self, pointer, flags):
        record = ctypes.cast(pointer, ctypes.POINTER(credentials._CREDENTIALW)).contents
        self.writes.append((record.TargetName, record.Type, record.Persist, flags))
        if len(self.writes) == self.fail_write_at:
            return 0
        self.items[record.TargetName] = {"name": record.UserName,
                                       "payload": ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize)}
        return 1

    def CredReadW(self, target, kind, flags, output):
        self.reads.append((target, kind, flags))
        item = self.items.get(target)
        if item is None:
            return 0
        payload = item["payload"]
        blob = ctypes.create_string_buffer(payload)
        record = credentials._CREDENTIALW()
        record.Type = 1
        record.Persist = 2
        record.TargetName = target
        record.UserName = item["name"]
        record.CredentialBlobSize = len(payload)
        record.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        for key, value in self.override.items():
            setattr(record, key, value)
        pointer = ctypes.pointer(record)
        self.allocated.append((record, blob, pointer))
        ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(credentials._CREDENTIALW)))[0] = pointer
        return 1

    def CredFree(self, pointer):
        self.frees.append(pointer)


class LocalCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeVault()
        patch("local_credentials._windows_api", return_value=self.api).start()
        self.addCleanup(patch.stopall)

    def store(self, env=None):
        credentials.store_from_environment(ROOT, ENV if env is None else env)

    def test_default_read_never_implicitly_writes_environment_values(self):
        with patch.dict("os.environ", ENV):
            self.assertEqual(credentials.load_local_credentials(ROOT), {})
        self.assertEqual(self.api.writes, [])

    def test_explicit_pair_round_trip_uses_current_user_local_persistence(self):
        self.store()
        self.assertEqual(credentials.load_local_credentials(ROOT), ENV)
        self.assertEqual(len(self.api.frees), 2)
        for target, kind, persistence, flags in self.api.writes:
            self.assertEqual((kind, persistence, flags), (1, 2, 0))
            self.assertTrue(target.startswith("TaiwanStock/LocalScheduler/v1/"))
            self.assertNotIn(str(ROOT), target)
            for value in ENV.values():
                self.assertNotIn(value, target)

    def test_aliases_become_canonical_names_and_canonical_values_win(self):
        self.store({"TELEGRAM_TOKEN": ENV[credentials.NAMES[0]], "TELEGRAM_USER_ID": ENV[credentials.NAMES[1]]})
        self.assertEqual(credentials.load_local_credentials(ROOT), ENV)
        self.store({**ENV, "TELEGRAM_TOKEN": "other", "TELEGRAM_USER_ID": "other"})
        self.assertEqual(credentials.load_local_credentials(ROOT), ENV)

    def test_repository_scope_and_resolved_path_are_stable(self):
        self.store()
        self.assertEqual(credentials.load_local_credentials(ROOT / "tests" / ".."), ENV)
        self.assertEqual(credentials.load_local_credentials(ROOT / "another-project"), {})

    def test_another_users_credential_set_cannot_see_values(self):
        self.store()
        # CredRead/CredWrite use the calling user's OS vault; the module never
        # takes a username/password or impersonates a different user.
        second_user_vault = FakeVault()
        with patch("local_credentials._windows_api", return_value=second_user_vault):
            self.assertEqual(credentials.load_local_credentials(ROOT), {})
        self.assertEqual(second_user_vault.writes, [])

    def test_validate_both_environment_values_before_any_write(self):
        for env in ({}, {credentials.NAMES[0]: ENV[credentials.NAMES[0]]},
                    {**ENV, credentials.NAMES[0]: "a\x00b"}, {**ENV, credentials.NAMES[1]: "two values"},
                    {**ENV, credentials.NAMES[0]: "x" * 1025},
                    {**ENV, credentials.NAMES[0]: "字" * 1024}):
            with self.subTest(env_keys=list(env)), self.assertRaises(credentials.LocalCredentialError):
                self.store(env)
        self.assertEqual(self.api.writes, [])

    def test_partial_pair_update_fails_closed_without_plaintext_fallback(self):
        self.store()
        self.api.fail_write_at = 4
        with self.assertRaises(credentials.LocalCredentialError):
            self.store({credentials.NAMES[0]: "999:new-token", credentials.NAMES[1]: "-999"})
        self.assertEqual(credentials.load_local_credentials(ROOT), {})

    def test_malformed_json_utf8_wrong_schema_value_and_pair_are_not_loaded(self):
        for payload in (b"not json", b"\xff", b"[]", b"{}", b'{"schema":"unknown"}',
                        json.dumps({"schema": credentials.SCHEMA, "name": credentials.NAMES[0],
                                    "pair_id": "a" * 32, "value": "bad\u0000value"}).encode()):
            with self.subTest(payload_type=type(payload).__name__):
                self.store()
                target = credentials._targets(ROOT)[credentials.NAMES[0]]
                self.api.items[target]["payload"] = payload
                before = len(self.api.frees)
                self.assertEqual(credentials.load_local_credentials(ROOT), {})
                self.assertGreater(len(self.api.frees), before)

    def test_invalid_structure_is_freed_and_not_read_as_a_secret(self):
        self.store()
        for override in ({"Type": 2}, {"Persist": 3}, {"TargetName": "wrong-repo"},
                         {"UserName": "wrong-name"}, {"CredentialBlobSize": 0},
                         {"CredentialBlobSize": credentials.MAX_BLOB_BYTES + 1},
                         {"CredentialBlob": ctypes.POINTER(ctypes.c_ubyte)()}):
            with self.subTest(fields=list(override)):
                self.api.override = override
                before = len(self.api.frees)
                self.assertEqual(credentials.load_local_credentials(ROOT), {})
                self.assertEqual(len(self.api.frees) - before, 2)

    def test_api_errors_are_sanitized(self):
        secret_error = RuntimeError(ENV[credentials.NAMES[0]])
        with patch("local_credentials._windows_api", side_effect=secret_error):
            self.assertEqual(credentials.load_local_credentials(ROOT), {})
            with self.assertRaises(credentials.LocalCredentialError) as error:
                self.store()
        self.assertNotIn(ENV[credentials.NAMES[0]], str(error.exception))

    def test_unsupported_platform_is_readonly_empty_and_cannot_store(self):
        with patch("local_credentials._windows_api", return_value=None):
            self.assertEqual(credentials.load_local_credentials(ROOT), {})
            with self.assertRaises(credentials.LocalCredentialError):
                self.store()
        self.assertEqual(self.api.writes, [])

    def test_default_cli_reports_only_booleans_and_never_stores(self):
        output = StringIO()
        with patch.dict("os.environ", ENV), redirect_stdout(output):
            self.assertEqual(credentials.main(["--root", str(ROOT)]), 0)
        self.assertTrue(all(isinstance(value, bool) for value in json.loads(output.getvalue()).values()))
        self.assertEqual(self.api.writes, [])

    def test_explicit_cli_stores_without_printing_values(self):
        output = StringIO()
        with patch.dict("os.environ", ENV), redirect_stdout(output):
            self.assertEqual(credentials.main(["--root", str(ROOT), "--store-from-environment"]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["stored"])
        self.assertTrue(all(isinstance(value, bool) for value in report.values()))
        for value in ENV.values():
            self.assertNotIn(value, output.getvalue())

    def test_cli_error_does_not_echo_api_exception_or_secret_arguments(self):
        output = StringIO()
        with patch("local_credentials.store_from_environment", side_effect=credentials.LocalCredentialError("secret")), \
                redirect_stdout(output):
            self.assertEqual(credentials.main(["--store-from-environment"]), 1)
        self.assertNotIn("secret", output.getvalue())
        error_output = StringIO()
        with redirect_stderr(error_output), self.assertRaises(SystemExit):
            credentials.main(["--token", ENV[credentials.NAMES[0]]])
        self.assertNotIn(ENV[credentials.NAMES[0]], error_output.getvalue())


class WindowsApiBindingTests(unittest.TestCase):
    def test_nonwindows_does_not_load_advapi_dll(self):
        with patch("local_credentials.os.name", "posix"), patch.object(ctypes, "WinDLL", create=True) as loader:
            self.assertIsNone(credentials._windows_api())
        loader.assert_not_called()

    def test_bindings_are_explicit_and_no_write_is_called(self):
        api = Mock()
        with patch("local_credentials.os.name", "nt"), patch.object(ctypes, "WinDLL", return_value=api, create=True) as loader:
            self.assertIs(credentials._windows_api(), api)
        loader.assert_called_once_with("advapi32.dll", use_last_error=True)
        self.assertEqual(api.CredReadW.restype, ctypes.c_int)
        self.assertEqual(api.CredWriteW.argtypes, [ctypes.POINTER(credentials._CREDENTIALW), ctypes.c_uint32])
        self.assertIsNone(api.CredFree.restype)
        api.CredWriteW.assert_not_called()


if __name__ == "__main__":
    unittest.main()
