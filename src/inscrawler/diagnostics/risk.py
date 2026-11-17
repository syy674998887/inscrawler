"""RC strategy diagnostics and request-attempt context."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

import inscrawler.diagnostics.classification as diagnostics_classification
import inscrawler.diagnostics.console as diagnostics_console

_RISK_STRATEGY_VERSION = "RC-16"


_PROCESS_RISK_RUN_ID = uuid.uuid4().hex[:8]


_risk_diagnostic_state = threading.local()


def _begin_risk_attempt(media_type, *, run_id=None, queue_completed_before=None):
    """Start one compact, serial-only RC diagnostic attempt."""
    normalized_completed = None
    if type(queue_completed_before) is int and queue_completed_before >= 0:
        normalized_completed = queue_completed_before
    _risk_diagnostic_state.current = {
        "v": _RISK_STRATEGY_VERSION,
        "run": (
            str(run_id or _PROCESS_RISK_RUN_ID).strip()[:32] or _PROCESS_RISK_RUN_ID
        ),
        "attempt": uuid.uuid4().hex[:8],
        "at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "media": str(media_type or "unknown").strip().lower() or "unknown",
        "session": None,
        "api": {},
        "last": None,
        "incident": None,
        "queue_completed_before": normalized_completed,
    }


def _current_risk_attempt():
    current = getattr(_risk_diagnostic_state, "current", None)
    return current if isinstance(current, dict) else None


def _risk_session(action):
    current = _current_risk_attempt()
    if current is not None:
        current["session"] = str(action or "unknown").strip().lower() or "unknown"


def _risk_api(category):
    """Count a sanitized application-level request boundary without logging it."""
    current = _current_risk_attempt()
    if current is None:
        return
    name = str(category or "unknown").strip().lower() or "unknown"
    counters = current["api"]
    counters[name] = counters.get(name, 0) + 1
    current["last"] = name


def _risk_incident_kind(result, error=None):
    parts = []
    if isinstance(result, dict):
        parts.append(result.get("message", ""))
        parts.append(result.get("status", ""))
    if error is not None:
        parts.append(error)
    text = " ".join(str(part) for part in parts if part).lower()

    for marker in (
        "checkpoint_required",
        "challenge_required",
        "feedback_required",
    ):
        if marker in text:
            return marker
    if "please wait a few minutes" in text:
        return "please_wait"
    if diagnostics_classification._looks_like_instagram_api_status(text, 429):
        return "api_429"
    for code in (401, 400):
        if diagnostics_classification._looks_like_instagram_api_status(text, code):
            return f"api_{code}"
    return None


def _risk_note_incident(value):
    """Retain the strongest sanitized incident seen before result mapping."""
    current = _current_risk_attempt()
    if current is None:
        return
    priority = {
        "api_400": 1,
        "api_401": 2,
        "api_429": 3,
        "please_wait": 4,
        "feedback_required": 5,
        "challenge_required": 6,
        "checkpoint_required": 7,
    }
    try:
        normalized = str(value or "").strip().lower()
        incident = (
            normalized if normalized in priority else _risk_incident_kind(None, value)
        )
    except Exception:
        return
    if incident is None:
        return
    existing = current.get("incident")
    if priority.get(incident, 0) >= priority.get(existing, 0):
        current["incident"] = incident


def _finish_risk_attempt(result=None, error=None):
    """Emit exactly one minified summary; never include targets or request data."""
    current = _current_risk_attempt()
    if current is None:
        return
    try:
        status = result.get("status") if isinstance(result, dict) else None
        payload = {
            "v": current["v"],
            "run": current["run"],
            "attempt": current["attempt"],
            "at": current["at"],
            "media": current["media"],
            "status": str(status or ("exception" if error is not None else "unknown")),
        }
        if current["session"]:
            payload["session"] = current["session"]
        if current["api"]:
            payload["api"] = {
                key: current["api"][key] for key in sorted(current["api"])
            }
        if current["last"]:
            payload["last"] = current["last"]
        detected_incident = _risk_incident_kind(result, error)
        if detected_incident:
            _risk_note_incident(detected_incident)
        incident = current.get("incident")
        if incident:
            payload["incident"] = incident
            if current["queue_completed_before"] is not None:
                payload["completed_before"] = current["queue_completed_before"]
        with diagnostics_console._log_as("fetch"):
            diagnostics_console.print(
                "[risk] "
                + json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    except Exception:
        # Diagnostics must never change the result of a download attempt.
        pass
    finally:
        try:
            del _risk_diagnostic_state.current
        except AttributeError:
            pass
