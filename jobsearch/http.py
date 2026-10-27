"""Minimal JSON-over-HTTPS client built on :mod:`urllib` (stdlib only).

Why not ``requests``: this repository must install and run with nothing but a
Python 3.9 interpreter. ``urllib.request`` is enough for two GET-only JSON
APIs, and keeping the dependency count at zero is the point.

Errors are converted into :class:`jobsearch.errors.NetworkError` with an
actionable hint. Credentials are never echoed in error text: query strings and
``Authorization`` headers are redacted before they reach a message.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .errors import NetworkError

USER_AGENT = "uk-job-search/0.1 (stdlib urllib; personal job search tool)"
DEFAULT_TIMEOUT = 20.0

_SECRET_PARAMS = ("app_key", "app_id", "api_key", "apikey", "key", "access_token")


def redact_url(url: str) -> str:
    """Return ``url`` with credential-looking query values replaced.

    Used in error messages only. The path is kept because it tells the user
    which endpoint failed.
    """
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not query:
        return urllib.parse.urlunsplit(parts)
    safe = [
        (key, "REDACTED" if key.lower() in _SECRET_PARAMS else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(safe))
    )


def build_url(url: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Append query parameters, skipping ``None`` values.

    Provider APIs reject empty parameters (Reed treats ``keywords=`` as a
    filter for nothing), so ``None`` and empty strings are dropped here rather
    than at every call site.
    """
    if not params:
        return url
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    if not clean:
        return url
    separator = "&" if urllib.parse.urlsplit(url).query else "?"
    return url + separator + urllib.parse.urlencode(clean)


def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """GET ``url`` and decode the JSON body.

    Raises :class:`NetworkError` for DNS failures, timeouts, non-2xx responses
    and invalid JSON. The error message names the host and status code, never
    the credentials.
    """
    full_url = build_url(url, params)
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(full_url, headers=request_headers, method="GET")
    host = urllib.parse.urlsplit(full_url).netloc

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", "replace").strip()
            detail = " ".join(body.split())[:300]
        except Exception:  # pragma: no cover - body already consumed/absent
            detail = ""
        raise NetworkError(
            "{} returned HTTP {}{}".format(
                host, exc.code, (": " + detail) if detail else ""
            ),
            hint=_http_hint(exc.code, host),
            context={"status": exc.code, "host": host},
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise NetworkError(
                "Timed out after {:.0f}s waiting for {}".format(timeout, host),
                hint="Check your connection, or raise the timeout and retry.",
                context={"host": host},
            )
        raise NetworkError(
            "Could not reach {}: {}".format(host, reason),
            hint=(
                "Check your internet connection and any proxy/VPN. "
                "Corporate networks sometimes block job-board APIs."
            ),
            context={"host": host},
        )
    except socket.timeout:
        raise NetworkError(
            "Timed out after {:.0f}s waiting for {}".format(timeout, host),
            hint="Check your connection, or raise the timeout and retry.",
            context={"host": host},
        )

    if status != 200:
        raise NetworkError(
            "{} returned unexpected status {}".format(host, status),
            hint="Retry; if it persists the provider API may have changed.",
            context={"status": status, "host": host},
        )

    body_text = raw.decode("utf-8", "replace")
    if not body_text.strip():
        raise NetworkError(
            "{} returned an empty body".format(host),
            hint="Retry in a moment. Empty bodies are usually a provider hiccup.",
            context={"host": host},
        )
    try:
        return json.loads(body_text)
    except ValueError:
        raise NetworkError(
            "{} returned a response that is not JSON".format(host),
            hint=(
                "The provider may be serving an HTML error page or a login wall. "
                "Retry, and check the endpoint in a browser."
            ),
            context={"host": host, "body_start": body_text[:200]},
        )


def _http_hint(status: int, host: str) -> str:
    if status in (401, 403):
        return (
            "The provider rejected the credentials. Check the API key "
            "environment variables are set, current and copied without spaces."
        )
    if status == 404:
        return (
            "Endpoint not found. Check the job id, or that the provider has not "
            "changed its API version."
        )
    if status == 429:
        return (
            "Rate limited by {}. Wait a few minutes before retrying, and reduce "
            "the number of requests (fewer pages, larger results_per_page)."
        ).format(host)
    if 500 <= status < 600:
        return "{} had a server error. Retry later; this is not your request.".format(
            host
        )
    if status == 400:
        return (
            "The provider rejected a query parameter. Check --location, "
            "--salary-min and --limit values."
        )
    return "Retry; if it persists, check the provider's API documentation."
