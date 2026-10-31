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

"""Command-wide read budgets through urllib's real processing chain."""

from __future__ import annotations

import http.client
import io
import json
import socket
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from typing import Any

import pytest

from magpie_discourse import cli, client
from magpie_discourse import main as package_main

BASE = "https://forum.example.test"
CATEGORY = {"id": 7, "slug": "dev", "name": "Development"}
DATE = "2026-10-01T00:00:00Z"
Reply = bytes | Exception | tuple[int, dict[str, str], bytes]


class ObservedBody(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.read_sizes: list[int] = []
        self.consumed = 0

    def read(self, size: int | None = -1) -> bytes:
        assert size is not None and 0 < size <= 64 * 1024, "Unbounded response read"
        self.read_sizes.append(size)
        data = super().read(size)
        self.consumed += len(data)
        return data


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def posts(ids: list[int]) -> list[dict[str, Any]]:
    return [
        {
            "id": number,
            "post_number": number,
            "username": "reader",
            "created_at": DATE,
            "cooked": "<p>Text</p>",
        }
        for number in ids
    ]


def topic(ids: list[int]) -> bytes:
    return encoded(
        {
            "id": 42,
            "title": "Proposal",
            "posts_count": len(ids),
            "post_stream": {"stream": ids, "posts": posts(ids[:1])},
        }
    )


def page(topic_id: int, more: str | None = None) -> bytes:
    return encoded(
        {
            "topic_list": {
                "topics": [
                    {
                        "id": topic_id,
                        "title": "Proposal",
                        "posts_count": 1,
                        "created_at": DATE,
                        "bumped_at": DATE,
                        "pinned": False,
                    }
                ],
                "more_topics_url": more,
            }
        }
    )


def serve(
    monkeypatch: pytest.MonkeyPatch, replies: list[Reply]
) -> tuple[list[urllib.request.Request], list[ObservedBody]]:
    """Replace HTTPS I/O, keeping urllib's request, error and redirect handlers."""
    requests: list[urllib.request.Request] = []
    bodies: list[ObservedBody] = []
    original_build_opener = urllib.request.build_opener

    class Response(urllib.response.addinfourl):
        msg: str

    class FakeHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request: urllib.request.Request) -> Any:
            assert request.timeout == 30
            requests.append(request)
            reply = replies[min(len(requests) - 1, len(replies) - 1)]
            if isinstance(reply, Exception):
                raise reply
            code, fields, data = (200, {}, reply) if isinstance(reply, bytes) else reply
            headers = Message()
            for key, value in fields.items():
                headers[key] = value
            body = ObservedBody(data)
            bodies.append(body)
            response = Response(body, headers, request.full_url, code)
            response.msg = "OK" if code == 200 else "Error"
            return response

    def build_opener(*handlers: Any) -> urllib.request.OpenerDirector:
        return original_build_opener(*handlers, FakeHTTPS())

    def unexpected_connection(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("A budget test attempted a real network connection")

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(socket, "create_connection", unexpected_connection)
    return requests, bodies


def configured(**limits: int) -> client.DiscourseConfig:
    return client.DiscourseConfig(BASE, None, None, budget=client.ReadBudget(**limits))


def test_default_request_budget_stops_before_attempt_65(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, bodies = serve(monkeypatch, [b"{}"])
    config = configured()
    for _ in range(64):
        assert client.get_json(config, "/latest.json") == {}
    with pytest.raises(client.DiscourseError, match="64 HTTP requests"):
        client.get_json(config, "/latest.json")
    assert len(requests) == 64
    assert all(body.closed for body in bodies)


def test_failed_request_consumes_its_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, _ = serve(monkeypatch, [urllib.error.URLError("offline")])
    config = configured(max_requests=1)
    with pytest.raises(client.DiscourseError, match="offline"):
        client.get_json(config, "/latest.json")
    with pytest.raises(client.DiscourseError, match="1 HTTP requests"):
        client.get_json(config, "/latest.json")
    assert len(requests) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_count_requests_without_reading_unused_bodies(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    requests, bodies = serve(monkeypatch, [(status, {"Location": "/next.json"}, b"x" * 131072), b"{}"])
    assert client.get_json(configured(max_requests=2, max_body_bytes=2), "/latest.json") == {}
    assert len(requests) == 2
    assert bodies[0].read_sizes == []
    assert bodies[1].consumed == 2
    assert all(body.closed for body in bodies)


def test_redirect_budget_exhaustion_closes_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, bodies = serve(monkeypatch, [(302, {"Location": "/next.json"}, b"unused")])
    with pytest.raises(client.DiscourseError, match="1 HTTP requests"):
        client.get_json(configured(max_requests=1), "/latest.json")
    assert len(requests) == 1
    assert bodies[0].closed and not bodies[0].read_sizes


def test_rejected_redirect_is_not_an_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, bodies = serve(
        monkeypatch, [(302, {"Location": "https://other.example.test/"}, b"unused"), b"{}"]
    )
    config = configured(max_requests=2)
    with pytest.raises(client.DiscourseError, match="refusing to forward credentials"):
        client.get_json(config, "/latest.json")
    assert client.get_json(config, "/latest.json") == {}
    assert len(requests) == 2
    assert all(body.closed for body in bodies)
    assert not bodies[0].read_sizes


def test_redirect_loop_keeps_urllib_protection_and_closes_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, bodies = serve(monkeypatch, [(302, {"Location": "/loop.json"}, b"unused")])
    with pytest.raises(client.DiscourseError, match="HTTP 302"):
        client.get_json(configured(), "/loop.json")
    assert 1 < len(requests) < 64
    assert all(body.closed and not body.read_sizes for body in bodies)


@pytest.mark.parametrize("data", [b"{}", b"{}" + b" " * 65536, encoded({"text": "设计 🐦"})])
def test_exact_body_allowance_succeeds_in_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    _, bodies = serve(monkeypatch, [data])
    assert client.get_json(configured(max_body_bytes=len(data)), "/latest.json") == json.loads(data)
    assert bodies[0].consumed == len(data)
    assert bodies[0].closed


def test_body_overflow_reads_only_one_extra_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    _, bodies = serve(monkeypatch, [b"{}" + b" " * 131072])
    with pytest.raises(client.DiscourseError, match="2 response-body bytes"):
        client.get_json(configured(max_body_bytes=2), "/latest.json")
    assert bodies[0].consumed == 3
    assert bodies[0].closed


def test_small_responses_share_the_byte_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    _, bodies = serve(monkeypatch, [b"{}"])
    config = configured(max_body_bytes=5)
    assert client.get_json(config, "/latest.json") == {}
    assert client.get_json(config, "/latest.json") == {}
    with pytest.raises(client.DiscourseError, match="5 response-body bytes"):
        client.get_json(config, "/latest.json")
    assert sum(body.consumed for body in bodies) == 6
    assert all(body.closed for body in bodies)


def test_error_body_within_allowance_preserves_status_and_message(monkeypatch: pytest.MonkeyPatch) -> None:
    data = encoded({"errors": ["denied"]})
    _, bodies = serve(monkeypatch, [(403, {}, data)])
    with pytest.raises(client.DiscourseError, match="HTTP 403: denied"):
        client.get_json(configured(max_body_bytes=len(data)), "/latest.json")
    assert bodies[0].closed


def test_redirect_status_without_location_keeps_bounded_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    data = encoded({"errors": ["missing location"]})
    _, bodies = serve(monkeypatch, [(302, {}, data)])
    with pytest.raises(client.DiscourseError, match="HTTP 302: missing location"):
        client.get_json(configured(max_body_bytes=len(data)), "/latest.json")
    assert bodies[0].consumed == len(data)
    assert bodies[0].closed


def test_error_body_shares_the_remaining_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    _, bodies = serve(monkeypatch, [b"{}", (403, {}, encoded({"errors": ["denied"]}))])
    config = configured(max_body_bytes=5)
    client.get_json(config, "/latest.json")
    with pytest.raises(client.DiscourseError, match="5 response-body bytes"):
        client.get_json(config, "/latest.json")
    assert sum(body.consumed for body in bodies) == 6
    assert all(body.closed for body in bodies)


def test_429_uses_headers_and_closes_without_reading_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _, bodies = serve(monkeypatch, [(429, {"Retry-After": "7"}, b"x" * 131072)])
    with pytest.raises(client.DiscourseError, match=r"HTTP 429.*retry after 7 seconds"):
        client.get_json(configured(max_body_bytes=1), "/latest.json")
    assert bodies[0].closed and not bodies[0].read_sizes


@pytest.mark.parametrize("dimension", ["requests", "bytes"])
def test_post_batches_share_budget_and_never_print_partial_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], dimension: str
) -> None:
    first = topic(list(range(1, 43)))
    batch = encoded({"post_stream": {"posts": posts(list(range(2, 22)))}})
    requests, bodies = serve(monkeypatch, [first, batch])
    config = (
        configured(max_requests=2)
        if dimension == "requests"
        else configured(max_body_bytes=len(first) + len(batch) - 1)
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "magpie-discourse error: Discourse read budget exhausted" in output.err
    assert len(requests) == 2
    assert all(body.closed for body in bodies)


@pytest.mark.parametrize("category", ["7", "dev"])
@pytest.mark.parametrize("allowance", [2, 3])
def test_category_pages_and_lookahead_share_request_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], category: str, allowance: int
) -> None:
    category_data = (
        {"category": CATEGORY} if category == "7" else {"category_list": {"categories": [CATEGORY]}}
    )
    requests, bodies = serve(monkeypatch, [encoded(category_data), page(42, "/next"), page(43)])
    config = configured(max_requests=allowance)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    status = package_main(["topic", "list", "--category", category, "--limit", "1"])
    output = capsys.readouterr()
    assert len(requests) == allowance
    assert all(body.closed for body in bodies)
    if allowance == 2:
        assert status == 1 and output.out == ""
        assert "read budget exhausted" in output.err
    else:
        assert status == 0 and output.err == ""
        result = json.loads(output.out)
        assert result["truncated"] is True
        assert [thread["thread_id"] for thread in result["threads"]] == ["42"]


def test_cli_commands_have_independent_budgets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCOURSE_URL", BASE)
    monkeypatch.delenv("DISCOURSE_API_KEY", raising=False)
    monkeypatch.delenv("DISCOURSE_API_USERNAME", raising=False)
    requests, bodies = serve(monkeypatch, [topic([])])
    for _ in range(65):
        assert package_main(["topic", "view", "42"]) == 0
        output = capsys.readouterr()
        assert json.loads(output.out)["thread_id"] == "42"
        assert output.err == ""
    assert len(requests) == 65
    assert all(body.closed for body in bodies)


def test_bounded_reads_still_reject_premature_eof_after_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSocket:
        def makefile(self, *args: Any, **kwargs: Any) -> io.BytesIO:
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n{}")

    response = http.client.HTTPResponse(FakeSocket())  # type: ignore[arg-type]
    response.begin()
    monkeypatch.setattr(client, "_urlopen", lambda *args, **kwargs: response)
    with pytest.raises(client.DiscourseError, match="ended before its declared Content-Length"):
        client.get_json(configured(), "/latest.json")
    assert response.isclosed()
