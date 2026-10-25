"""Single-publisher exclusion. Dead owners never trigger automatic lock stealing."""

import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path

from .control import Refused


@contextmanager
def exclusive(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".publish.lock"
    owner = {"pid": os.getpid(), "nonce": secrets.token_hex(16)}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Refused("publication_lock_requires_owner_review") from exc
    with os.fdopen(fd, "w") as stream:
        json.dump(owner, stream)
    try:
        yield
    finally:
        if path.exists() and json.loads(path.read_text()) == owner:
            path.unlink()


def alive(pid):
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def recover(root, expected):
    path = Path(root) / ".publish.lock"
    current = json.loads(path.read_text())
    if current != expected or alive(current["pid"]):
        raise Refused("publication_owner_not_verified_dead")
    if json.loads(path.read_text()) != expected:
        raise Refused("publication_lock_changed")
    path.unlink()
