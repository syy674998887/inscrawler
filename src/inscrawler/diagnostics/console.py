"""Thread-local log phases and the download console callback."""

from __future__ import annotations

import builtins
import contextlib
import threading

_module_print = builtins.print


log_callback = None


_log_phase = "fetch"


_log_phase_state = threading.local()


@contextlib.contextmanager
def _log_as(phase: str):
    """Temporarily set the log phase used by print()'s prefix."""
    prev = getattr(_log_phase_state, "phase", _log_phase)
    _log_phase_state.phase = phase
    try:
        yield
    finally:
        _log_phase_state.phase = prev


def print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    tag = f"[{getattr(_log_phase_state, 'phase', _log_phase)}]"
    _module_print(tag, msg, **kwargs)
    if log_callback is not None:
        try:
            log_callback(f"{tag} {msg}")
        except Exception:
            pass
