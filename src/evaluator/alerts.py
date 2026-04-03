"""
alerts.py — AIE Alert Module

Sends Discord alerts via openclaw message tool.
Severity levels:
  - critical: Discord alert + returns False (triggers circuit breaker exit)
  - warning:  Discord alert + returns True
  - info:     Logged only to evaluator/data/logs/info.log
"""

import os
import subprocess
import datetime

ALERT_CHANNEL = os.getenv("ALERT_CHANNEL", "#evaluator-alerts")


def send_alert(
    message: str,
    severity: str,
    channel: str = ALERT_CHANNEL,
) -> bool:
    """
    Send an alert via Discord.

    Args:
        message:  The alert message text.
        severity: One of "critical", "warning", "info".
        channel:  Discord channel (default from ALERT_CHANNEL env var).
                 e.g., "#evaluator-alerts" or "123456789"

    Returns:
        True  — alert sent successfully (or logged for info)
        False — critical alert sent, caller should exit (circuit breaker)
                OR alert send itself failed (logged, return True to continue)
    """
    timestamp = datetime.datetime.now().isoformat()

    if severity == "info":
        _log_info(timestamp, message)
        return True

    # Build Discord message
    emoji = "🚨" if severity == "critical" else "⚠️"
    full_message = f"{emoji} AIE Alert ({severity.upper()}): {message}"

    # Send via openclaw message tool
    # Use --channel discord --target <channel> pattern
    try:
        result = subprocess.run(
            ["openclaw", "message", "send", "--channel", "discord", "--target", channel, "--message", full_message],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        _log_failure(timestamp, message, "openclaw message timed out")
        return severity != "critical"
    except FileNotFoundError:
        _log_failure(timestamp, message, "openclaw not found in PATH")
        return severity != "critical"

    if result.returncode != 0:
        _log_failure(timestamp, message, result.stderr or "unknown error")
        return severity != "critical"

    # critical → return False so caller exits (circuit breaker)
    # warning  → return True, flow continues
    return severity != "critical"


def _log_info(timestamp: str, message: str) -> None:
    try:
        with open("evaluator/data/logs/info.log", "a") as f:
            f.write(f"[{timestamp}] INFO: {message}\n")
    except Exception:
        pass  # Never crash on logging failure


def _log_failure(timestamp: str, message: str, error: str) -> None:
    try:
        with open("evaluator/data/logs/alert_failures.log", "a") as f:
            f.write(f"[{timestamp}] FAILED: {message} — {error}\n")
    except Exception:
        pass


# ── Alert templates ──────────────────────────────────────────────────────────

def alert_critical_drift(session_id: str, statement: str, prior_statement: str, drift_score: float) -> bool:
    """Critical drift detected — session halted."""
    msg = (
        f"CRITICAL DRIFT in session `{session_id}`: "
        f"statement \"{statement}\" contradicts prior \"{prior_statement}\" "
        f"(score={drift_score:.2f})"
    )
    return send_alert(msg, severity="critical")


def alert_critical_oracle_failure(oracle_id: str, event_type: str) -> bool:
    """Critical oracle failed — session halted."""
    msg = f"ORACLE FAILURE: `{oracle_id}` failed on `{event_type}` event"
    return send_alert(msg, severity="critical")


def alert_warning(summary: str) -> bool:
    """Warning alert — logged and sent to Discord."""
    return send_alert(summary, severity="warning")


def alert_info(message: str) -> bool:
    """Info-only alert — logged to info.log only."""
    return send_alert(message, severity="info")