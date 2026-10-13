"""Repo-scoped Telegram credentials in the current user's Windows vault.

No credential is stored implicitly. Only --store-from-environment writes; the
default CLI reports booleans. Generic LOCAL_MACHINE persistence means future
logons of this same user on this same PC, not a machine-wide environment value.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import ctypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
from typing import Any, TypeGuard
from uuid import uuid4


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
MAX_BLOB_BYTES = 2560
SCHEMA = "taiwan_stock_telegram_v1"
NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
ALIASES = {NAMES[0]: (NAMES[0], "TELEGRAM_TOKEN"), NAMES[1]: (NAMES[1], "TELEGRAM_USER_ID")}


class LocalCredentialError(RuntimeError):
    """Fixed diagnostics only; never include API payloads, values or arguments."""


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FILETIME), ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", ctypes.c_uint32),
        ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p),
    ]


def _windows_api() -> Any:
    if os.name != "nt":
        return None
    try:
        api = getattr(ctypes, "WinDLL")("advapi32.dll", use_last_error=True)
        api.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
        api.CredReadW.restype = ctypes.c_int
        api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), ctypes.c_uint32]
        api.CredWriteW.restype = ctypes.c_int
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api
    except Exception:
        raise LocalCredentialError("Windows credential API is unavailable.") from None


def _targets(root: Path) -> dict[str, str]:
    resolved = ntpath.normcase(str(root.resolve()))
    scope = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    return {name: f"TaiwanStock/LocalScheduler/v1/{scope}/{name}" for name in NAMES}


def _valid_value(value: Any) -> TypeGuard[str]:
    return (isinstance(value, str) and 0 < len(value) <= 1024
            and not any(character.isspace() or ord(character) < 32 for character in value))


def _read(api: Any, target: str, name: str) -> dict[str, str] | None:
    pointer = ctypes.POINTER(_CREDENTIALW)()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return None
    if not pointer:
        return None
    try:
        record = pointer.contents
        if (record.Type != CRED_TYPE_GENERIC or record.Persist != CRED_PERSIST_LOCAL_MACHINE
                or record.TargetName != target or record.UserName != name
                or not 0 < record.CredentialBlobSize <= MAX_BLOB_BYTES or not record.CredentialBlob):
            return None
        raw = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize)
        value = json.loads(raw.decode("utf-8"))
        if (not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("name") != name
                or not isinstance(value.get("pair_id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", value["pair_id"])
                or not _valid_value(value.get("value"))):
            return None
        return {"pair_id": value["pair_id"], "value": value["value"]}
    finally:
        api.CredFree(pointer)


def load_local_credentials(root: Path) -> dict[str, str]:
    """Read this process user's pair; missing/malformed/inaccessible means {}.

    Matching pair IDs prevent a partial two-write update from combining a new
    token with a previous chat ID. No environment or filesystem writes occur.
    """
    try:
        api = _windows_api()
        if api is None:
            return {}
        targets = _targets(root)
        values = {name: _read(api, targets[name], name) for name in NAMES}
        token, chat = values[NAMES[0]], values[NAMES[1]]
        if token is None or chat is None or token["pair_id"] != chat["pair_id"]:
            return {}
        return {NAMES[0]: token["value"], NAMES[1]: chat["value"]}
    except Exception:
        # The caller sees unavailable configuration, not secret-bearing API text.
        return {}


def store_from_environment(root: Path, environment: Mapping[str, str] | None = None) -> None:
    """Explicit opt-in only: persist both existing values for the current user.

    A failed/partial update raises a sanitized error and cannot be loaded as a
    matching pair. It never falls back to plaintext files or OS environment.
    """
    environment = os.environ if environment is None else environment
    try:
        values: dict[str, str] = {}
        for name in NAMES:
            value = next((environment.get(alias) for alias in ALIASES[name] if environment.get(alias)), None)
            value = value.strip() if isinstance(value, str) else value
            if not _valid_value(value):
                raise LocalCredentialError("Both Telegram environment values are required and must be valid.")
            values[name] = value
        pair_id = uuid4().hex
        payloads = {name: json.dumps({"schema": SCHEMA, "name": name, "pair_id": pair_id, "value": values[name]},
                                    ensure_ascii=False).encode("utf-8") for name in NAMES}
        if any(len(payload) > MAX_BLOB_BYTES for payload in payloads.values()):
            raise LocalCredentialError("Credential payload exceeds the local vault limit.")
        api = _windows_api()
        if api is None:
            raise LocalCredentialError("Windows credential storage is unavailable on this platform.")
        targets = _targets(root)
        for name in NAMES:
            payload = payloads[name]
            blob = ctypes.create_string_buffer(payload)
            record = _CREDENTIALW()
            record.Type = CRED_TYPE_GENERIC
            record.TargetName = targets[name]
            record.UserName = name
            record.Comment = "Taiwan Stock local scheduled notifications"
            record.Persist = CRED_PERSIST_LOCAL_MACHINE
            record.CredentialBlobSize = len(payload)
            record.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
            try:
                if not api.CredWriteW(ctypes.byref(record), 0):
                    raise LocalCredentialError("Windows credential storage failed; no plaintext fallback was used.")
            finally:
                ctypes.memset(ctypes.addressof(blob), 0, len(blob))
    except LocalCredentialError:
        raise
    except Exception:
        raise LocalCredentialError("Local credential setup failed; values were not printed.") from None


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        # argparse's default error echoes unknown arguments, which could contain
        # an accidentally pasted token. Do not accept credentials as arguments.
        self.exit(2, "Unsupported local credential arguments.\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description="Read-only vault checks; explicit opt-in stores existing environment values.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--store-from-environment", action="store_true")
    args = parser.parse_args(argv)
    if args.store_from_environment:
        try:
            store_from_environment(args.root)
        except LocalCredentialError:
            print(json.dumps({"stored": False}))
            return 1
    credentials = load_local_credentials(args.root)
    report = {"telegram_bot_token_configured": bool(credentials.get(NAMES[0])),
              "telegram_chat_id_configured": bool(credentials.get(NAMES[1]))}
    if args.store_from_environment:
        report["stored"] = all(report.values())
    print(json.dumps(report))
    return 0 if not args.store_from_environment or report["stored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
