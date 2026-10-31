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
from dataclasses import replace
from email.message import Message
from typing import Any

import pytest

from magpie_discourse import cli, client, topics
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


def topic(ids: list[int], *, initial: int = 1) -> bytes:
    return encoded(
        {
            "id": 42,
            "title": "Proposal",
            "posts_count": len(ids),
            "post_stream": {"stream": ids, "posts": posts(ids[:initial])},
        }
    )


def page(topic_id: int, more: str | None = None, *, count: int = 1) -> bytes:
    return encoded(
        {
            "topic_list": {
                "topics": [
                    {
                        "id": topic_id + offset,
                        "title": "Proposal",
                        "posts_count": 1,
                        "created_at": DATE,
                        "bumped_at": DATE,
                        "pinned": False,
                    }
                    for offset in range(count)
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


@pytest.mark.parametrize(
    ("status", "body", "allowance"),
    [(403, encoded({"errors": ["denied"]}), 5), (502, b"<html>" + b"x" * 5000 + b"</html>", 1000)],
    ids=["403-json", "502-html"],
)
def test_error_body_shares_the_remaining_allowance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    body: bytes,
    allowance: int,
) -> None:
    _, bodies = serve(monkeypatch, [b"{}", (status, {}, body)])
    config = configured(max_body_bytes=allowance)
    client.get_json(config, "/latest.json")
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert f"HTTP {status}" in output.err
    assert "error body exceeded the remaining read budget" in output.err
    assert sum(body.consumed for body in bodies) == allowance + 1
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
    batch = encoded({"post_stream": {"posts": posts(list(range(2, 22)))}})
    if dimension == "requests":
        # Two batches fit after the initial read; an extra redirect later exhausts the budget.
        first = topic(list(range(1, 23)))
        replies: list[Reply] = [first, batch, (302, {"Location": "/last-batch.json"}, b"unused")]
        config = configured(max_requests=3)
        expected_requests = 3
    else:
        first = topic(list(range(1, 43)))
        replies = [first, batch]
        config = configured(max_body_bytes=len(first) + len(batch) - 1)
        expected_requests = 2
    requests, bodies = serve(monkeypatch, replies)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "magpie-discourse error: Discourse read budget exhausted" in output.err
    assert len(requests) == expected_requests
    assert bodies[1].consumed > 0
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
        assert "--limit" in output.err and "--since" in output.err
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


def view_replies(total: int, max_posts: int, initial: int) -> list[Reply]:
    ids = list(range(1, total + 1))
    missing = ids[initial:max_posts]
    replies: list[Reply] = [topic(ids, initial=initial)]
    replies.extend(
        encoded({"post_stream": {"posts": posts(missing[start : start + 20])}})
        for start in range(0, len(missing), 20)
    )
    return replies


@pytest.mark.parametrize(("max_posts", "expected_status"), [(1280, 0), (1281, 1), (2000, 1)])
def test_topic_preflight_rejects_known_impossible_batches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], max_posts: int, expected_status: int
) -> None:
    requests, bodies = serve(monkeypatch, view_replies(3000, max_posts, 20))
    config = configured()
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert package_main(["topic", "view", "42", "--max-posts", str(max_posts)]) == expected_status
    output = capsys.readouterr()
    assert all(body.closed for body in bodies)
    if expected_status:
        assert len(requests) == 1
        assert output.out == ""
        needed = (max_posts - 20 + 19) // 20
        assert f"at least {needed} more HTTP requests" in output.err
        assert "63 remain" in output.err
        assert "lower --max-posts" in output.err
    else:
        assert len(requests) == 64
        result = json.loads(output.out)
        assert len(result["messages"]) == 1280 and result["truncated"] is True
        assert output.err == ""


@pytest.mark.parametrize(
    ("total", "max_posts", "initial", "allowance", "expected_requests"),
    [(40, 2000, 20, 64, 2), (3000, 1281, 40, 64, 64), (20, 2000, 20, 1, 1)],
)
def test_topic_preflight_uses_actual_missing_posts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    total: int,
    max_posts: int,
    initial: int,
    allowance: int,
    expected_requests: int,
) -> None:
    requests, bodies = serve(monkeypatch, view_replies(total, max_posts, initial))
    monkeypatch.setattr(cli, "load_config", lambda: configured(max_requests=allowance))
    assert package_main(["topic", "view", "42", "--max-posts", str(max_posts)]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert len(result["messages"]) == min(total, max_posts)
    assert result["truncated"] is (total > max_posts)
    assert output.err == ""
    assert len(requests) == expected_requests
    assert all(body.closed for body in bodies)


@pytest.mark.parametrize(("allowance", "expected_status"), [(3, 1), (4, 0)])
def test_topic_preflight_counts_initial_redirects_without_reserving_requests(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], allowance: int, expected_status: int
) -> None:
    replies: list[Reply] = [(302, {"Location": "/t/proposal/42.json"}, b"unused")]
    replies.extend(view_replies(41, 41, 20))
    requests, bodies = serve(monkeypatch, replies)
    monkeypatch.setattr(cli, "load_config", lambda: configured(max_requests=allowance))
    assert package_main(["topic", "view", "42"]) == expected_status
    output = capsys.readouterr()
    if expected_status:
        assert len(requests) == 2 and output.out == ""
        assert "at least 2 more HTTP requests" in output.err
        assert "1 remain" in output.err and "--max-posts" in output.err
    else:
        assert len(requests) == 4 and output.err == ""
        assert len(json.loads(output.out)["messages"]) == 41
    assert bodies[0].read_sizes == []
    assert all(body.closed for body in bodies)


@pytest.mark.parametrize(
    ("limit", "more_after_last_page", "expected_status"),
    [(1889, True, 0), (1890, True, 1), (1890, False, 0)],
)
def test_list_capacity_depends_on_end_signal_and_lookahead(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    limit: int,
    more_after_last_page: bool,
    expected_status: int,
) -> None:
    replies: list[Reply] = [encoded({"category": CATEGORY})]
    replies.extend(
        page(1 + number * 30, "/next" if number < 62 or more_after_last_page else None, count=30)
        for number in range(63)
    )
    requests, bodies = serve(monkeypatch, replies)
    monkeypatch.setattr(cli, "load_config", configured)
    assert package_main(["topic", "list", "--category", "7", "--limit", str(limit)]) == expected_status
    output = capsys.readouterr()
    assert len(requests) == 64
    assert all(body.closed for body in bodies)
    if expected_status:
        assert output.out == ""
        assert "--limit" in output.err and "--since" in output.err
    else:
        result = json.loads(output.out)
        assert len(result["threads"]) == limit
        assert result["truncated"] is more_after_last_page
        assert output.err == ""


@pytest.mark.parametrize(
    ("reply", "body_limit", "expected"),
    [
        ((403, {}, encoded({"errors": ["denied"]})), 1000, "HTTP 403"),
        (urllib.error.URLError("offline"), 1000, "offline"),
        (b"<html>not JSON</html>", 1000, "not JSON"),
        (encoded({"category": CATEGORY}), 1, "response-body bytes"),
    ],
)
def test_list_count_hint_is_not_added_to_other_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reply: Reply,
    body_limit: int,
    expected: str,
) -> None:
    requests, bodies = serve(monkeypatch, [reply])
    monkeypatch.setattr(cli, "load_config", lambda: configured(max_requests=1, max_body_bytes=body_limit))
    assert package_main(["topic", "list", "--category", "7"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and expected in output.err
    assert "--limit" not in output.err and "--since" not in output.err
    assert len(requests) == 1 and all(body.closed for body in bodies)


@pytest.mark.parametrize("allowance", [1, 2])
def test_spent_byte_budget_blocks_the_next_request(monkeypatch: pytest.MonkeyPatch, allowance: int) -> None:
    requests, bodies = serve(monkeypatch, [b"{}"])
    config = configured(max_body_bytes=allowance)
    if allowance == 1:
        with pytest.raises(client.DiscourseError, match="response-body bytes"):
            client.get_json(config, "/latest.json")
    else:
        assert client.get_json(config, "/latest.json") == {}
    with pytest.raises(client.DiscourseError, match="response-body bytes"):
        client.get_json(config, "/latest.json")
    assert len(requests) == config.budget.requests == 1
    assert bodies[0].closed


def test_reused_and_replaced_configs_share_budget_but_fresh_configs_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests, bodies = serve(monkeypatch, [topic([1])])
    config = configured(max_requests=2)
    topics.fetch_topic(config, 42)
    topics.fetch_topic(replace(config), 42)
    with pytest.raises(client.DiscourseError, match="2 HTTP requests"):
        topics.fetch_topic(config, 42)
    topics.fetch_topic(client.DiscourseConfig(BASE, None, None), 42)
    assert len(requests) == 3
    assert all(body.closed for body in bodies)


@pytest.mark.parametrize("terminated", [False, True])
def test_chunked_response_requires_terminator_even_after_valid_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], terminated: bool
) -> None:
    body = topic([])
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(body):x}\r\n".encode()
        + body
        + b"\r\n"
        + (b"0\r\n\r\n" if terminated else b"")
    )

    class FakeSocket:
        def makefile(self, *args: Any, **kwargs: Any) -> io.BytesIO:
            return io.BytesIO(raw)

    response = http.client.HTTPResponse(FakeSocket())  # type: ignore[arg-type]
    response.begin()
    monkeypatch.setattr(client, "_urlopen", lambda *args, **kwargs: response)
    monkeypatch.setattr(cli, "load_config", configured)
    assert package_main(["topic", "view", "42"]) == (0 if terminated else 1)
    output = capsys.readouterr()
    if terminated:
        assert json.loads(output.out)["thread_id"] == "42" and output.err == ""
    else:
        assert output.out == "" and "Failed to read the Discourse response" in output.err
    assert response.isclosed()
