"""
Turn a failed Google API response into an error message a person can act on.

Google says what is wrong in the body of the error (API not enabled, permission not
granted, bad request). Surfacing that beats a generic "service unavailable", which
hid the real cause the first time Calendar failed.
"""
from __future__ import annotations


def check(resp, service: str) -> dict:
    if resp.status_code < 400:
        return resp.json()
    try:
        err = resp.json().get("error", {})
    except Exception:  # noqa: BLE001 - non-JSON error page
        err = {}
    if not isinstance(err, dict):
        err = {"message": str(err)}
    message = err.get("message") or "no details"
    text = (message + " " + str(err.get("status", "")) + " " +
            " ".join(str(e.get("reason", "")) for e in err.get("errors", []) if isinstance(e, dict))).lower()

    if resp.status_code == 401:
        raise PermissionError("Your Google sign-in expired. Sign out and sign in again.")
    if "accessnotconfigured" in text or "has not been used in project" in text or "it is disabled" in text:
        raise PermissionError(
            f"The {service} API is not enabled in the Google Cloud project. "
            "Enable it in the API Library, wait a minute, then try again.")
    if resp.status_code == 403 and ("insufficient" in text or "scope" in text):
        raise PermissionError(f"The {service} permission was not granted. Sign out, sign in again and allow it.")
    if resp.status_code == 403:
        raise PermissionError(f"Google refused the {service} request: {message}")
    raise ValueError(f"Google {service} rejected the request ({resp.status_code}): {message}")
