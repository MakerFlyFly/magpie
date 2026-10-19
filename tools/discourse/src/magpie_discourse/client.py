# SPDX-License-Identifier: Apache-2.0
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Shared Discourse client configuration and HTTP helpers."""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30

QueryValue = str | int | Sequence[str | int]


class DiscourseError(Exception):
    """Raised when the Discourse bridge cannot complete a request."""


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only when they stay on the forum's HTTPS origin.

    Discourse redirects some topic URLs to their canonical slug form; a
    redirect to any other origin is refused so the API key is never sent
    to a host the adopter did not configure.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            target = urllib.parse.urljoin(req.full_url, newurl)
            destination = parse_url(target)
        except (ValueError, DiscourseError) as exc:
            raise DiscourseError("Invalid Discourse redirect URL; refusing to forward credentials") from exc
        if origin(destination) != origin(parse_url(req.full_url)) or destination.username is not None:
            raise DiscourseError(f"Discourse request redirected to {target}; refusing to forward credentials")
        return urllib.request.Request(target, headers=dict(req.header_items()), method=req.get_method())


def origin(parsed: urllib.parse.ParseResult) -> tuple[str, str | None, int | None]:
    """Return the effective origin of a URL for redirect comparisons."""
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else None)
    return (parsed.scheme, parsed.hostname, port)


def parse_url(value: str) -> urllib.parse.ParseResult:
    """Parse a URL and validate its host/port before it can reach urllib."""
    try:
        parsed = urllib.parse.urlparse(value)
        origin(parsed)
    except ValueError as exc:
        raise DiscourseError("Invalid Discourse URL: check its host and port") from exc
    return parsed


@dataclass(frozen=True)
class DiscourseConfig:
    """Environment-derived Discourse bridge configuration."""

    base_url: str
    api_key: str | None
    api_username: str | None


def load_config() -> DiscourseConfig:
    """Load the bridge configuration from environment variables."""
    raw_url = os.environ.get("DISCOURSE_URL", "").strip()
    if not raw_url:
        raise DiscourseError("DISCOURSE_URL is required (the forum's https:// base URL)")
    parsed = parse_url(raw_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DiscourseError("DISCOURSE_URL must be an https:// URL")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise DiscourseError("DISCOURSE_URL must not carry credentials, a query or a fragment")

    api_key = os.environ.get("DISCOURSE_API_KEY", "").strip() or None
    api_username = os.environ.get("DISCOURSE_API_USERNAME", "").strip() or None
    if api_key and not api_username:
        raise DiscourseError("DISCOURSE_API_USERNAME is required when DISCOURSE_API_KEY is set")
    if any("\r" in value or "\n" in value for value in (api_key or "", api_username or "")):
        raise DiscourseError("Discourse API credentials must not contain newlines")

    return DiscourseConfig(base_url=raw_url.rstrip("/"), api_key=api_key, api_username=api_username)


def auth_headers(config: DiscourseConfig) -> dict[str, str]:
    """Return the API key headers, or none for anonymous reads of a public forum."""
    if not config.api_key:
        return {}
    return {"Api-Key": config.api_key, "Api-Username": config.api_username or ""}


def api_url(config: DiscourseConfig, path: str, params: Mapping[str, QueryValue] | None = None) -> str:
    """Build an API URL under the configured forum base URL."""
    url = f"{config.base_url}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    return url


def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
    """Open a request with the same-origin redirect policy (patched in tests)."""
    opener = urllib.request.build_opener(SameOriginRedirectHandler)
    return opener.open(request, timeout=timeout)


def get_json(
    config: DiscourseConfig, path: str, params: Mapping[str, QueryValue] | None = None
) -> dict[str, Any]:
    """GET a Discourse API path and parse the JSON object it returns."""
    url = api_url(config, path, params)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **auth_headers(config)},
        method="GET",
    )
    try:
        with _urlopen(request, DEFAULT_TIMEOUT_SECONDS) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except DiscourseError:
        raise
    except urllib.error.HTTPError as exc:
        raise DiscourseError(_http_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise DiscourseError(f"Failed to connect to Discourse: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DiscourseError(f"Timed out talking to Discourse after {DEFAULT_TIMEOUT_SECONDS}s") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscourseError(f"Discourse returned a response that is not JSON: {url}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise DiscourseError(f"Failed to read the Discourse response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DiscourseError(f"Expected a JSON object from {url}")
    return parsed


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Describe an HTTP error, keeping Discourse's own error text when it sends one."""
    message = f"Discourse request failed with HTTP {exc.code}"
    if exc.code == 429:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        hint = f"{retry_after} seconds" if retry_after and retry_after.isdecimal() else retry_after
        return f"{message} (rate limited; retry after {hint or 'a while'})"
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return message
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors:
        return f"{message}: {'; '.join(str(error) for error in errors)}"
    return message
