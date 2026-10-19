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

from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any

import pytest

from magpie_discourse import client, topics
from magpie_discourse import main as package_main
from magpie_discourse.client import DiscourseConfig, DiscourseError, SameOriginRedirectHandler, load_config

BASE = "https://forum.example.test"


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeForum:
    """Serve canned JSON by URL path and record every request."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        path = urllib.parse.urlparse(request.full_url).path
        reply = self.routes[path]
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(json.dumps(reply).encode("utf-8"))


def _post(post_id: int, number: int, username: str = "alice", reply_to: int | None = None) -> dict[str, Any]:
    return {
        "id": post_id,
        "post_number": number,
        "username": username,
        "created_at": f"2026-10-0{min(number, 9)}T10:00:00.000Z",
        "updated_at": f"2026-10-0{min(number, 9)}T11:00:00.000Z",
        "cooked": f"<p>post {number}</p>",
        "reply_to_post_number": reply_to,
        "post_type": 1,
    }


def _topic(posts: list[dict[str, Any]], stream: list[int], **extra: Any) -> dict[str, Any]:
    return {
        "id": 42,
        "title": "Proposal: drop Python 3.10",
        "slug": "proposal-drop-python-3-10",
        "category_id": 7,
        "tags": [{"id": 1, "name": "governance", "slug": "governance"}],
        "created_at": "2026-10-01T10:00:00.000Z",
        "last_posted_at": "2026-10-05T10:00:00.000Z",
        "posts_count": len(stream),
        "closed": False,
        "archived": False,
        "post_stream": {"posts": posts, "stream": stream},
        **extra,
    }


@pytest.fixture
def config() -> DiscourseConfig:
    return DiscourseConfig(base_url=BASE, api_key=None, api_username=None)


@pytest.fixture
def forum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCOURSE_URL", f"{BASE}/")
    monkeypatch.delenv("DISCOURSE_API_KEY", raising=False)
    monkeypatch.delenv("DISCOURSE_API_USERNAME", raising=False)


def _serve(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> FakeForum:
    forum = FakeForum(routes)
    monkeypatch.setattr(client, "_urlopen", forum)
    return forum


# --- configuration ---------------------------------------------------------


def test_load_config_strips_the_trailing_slash(forum_env: None) -> None:
    assert load_config() == DiscourseConfig(base_url=BASE, api_key=None, api_username=None)


@pytest.mark.parametrize(
    "url",
    ["", "http://forum.example.test", "forum.example.test", "https://u:p@forum.example.test", f"{BASE}/?x=1"],
)
def test_load_config_rejects_unusable_urls(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DISCOURSE_URL", url)
    with pytest.raises(DiscourseError):
        load_config()


def test_api_key_needs_a_username(forum_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCOURSE_API_KEY", "secret")
    with pytest.raises(DiscourseError, match="DISCOURSE_API_USERNAME"):
        load_config()


def test_auth_headers_are_sent_only_with_a_key(config: DiscourseConfig) -> None:
    assert client.auth_headers(config) == {}
    keyed = DiscourseConfig(base_url=BASE, api_key="secret", api_username="magpie-bot")
    assert client.auth_headers(keyed) == {"Api-Key": "secret", "Api-Username": "magpie-bot"}


# --- topic references -------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "42",
        f"{BASE}/t/proposal-drop-python-3-10/42",
        f"{BASE}/t/proposal-drop-python-3-10/42/7",
        f"{BASE}/t/42",
        f"{BASE}/t/42.json",
        "https://FORUM.example.test/t/some-slug/42/",
    ],
)
def test_parse_topic_ref_accepts_ids_and_topic_urls(config: DiscourseConfig, ref: str) -> None:
    assert topics.parse_topic_ref(ref, config) == 42


@pytest.mark.parametrize(
    "ref",
    [
        "https://evil.example.test/t/slug/42",
        "http://forum.example.test/t/slug/42",
        f"{BASE}/c/governance/7",
        f"{BASE}/u/alice",
        "not a topic",
    ],
)
def test_parse_topic_ref_rejects_other_forums_and_paths(config: DiscourseConfig, ref: str) -> None:
    with pytest.raises(DiscourseError):
        topics.parse_topic_ref(ref, config)


def test_parse_topic_ref_honours_a_subfolder_install() -> None:
    sub = DiscourseConfig(base_url=f"{BASE}/forum", api_key=None, api_username=None)
    assert topics.parse_topic_ref(f"{BASE}/forum/t/slug/42", sub) == 42
    with pytest.raises(DiscourseError):
        topics.parse_topic_ref(f"{BASE}/t/slug/42", sub)


# --- topic fetch ------------------------------------------------------------


def test_fetch_topic_maps_onto_thread_data(monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig) -> None:
    posts = [_post(100, 1), _post(101, 2, "bob", reply_to=1), _post(102, 3, "alice", reply_to=2)]
    _serve(monkeypatch, {"/t/42.json": _topic(posts, [100, 101, 102])})

    thread = topics.fetch_topic(config, 42)

    assert thread["thread_id"] == "42"
    assert thread["list"] == BASE
    assert thread["subject"] == "Proposal: drop Python 3.10"
    assert thread["url"] == f"{BASE}/t/proposal-drop-python-3-10/42"
    assert thread["tags"] == ["governance"]
    assert thread["participant_handles"] == ["@alice", "@bob"]
    assert thread["truncated"] is False
    second = thread["messages"][1]
    assert second == {
        "message_id": "101",
        "post_number": 2,
        "from": "@bob",
        "date": "2026-10-02T10:00:00.000Z",
        "updated_at": "2026-10-02T11:00:00.000Z",
        "body": "<p>post 2</p>",
        "body_format": "html",
        "in_reply_to": "100",
        "url": f"{BASE}/t/proposal-drop-python-3-10/42/2",
        "post_type": 1,
        "hidden": False,
        "deleted": False,
    }


def test_fetch_topic_reads_the_rest_of_the_stream_in_chunks(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig
) -> None:
    stream = list(range(1000, 1045))
    first = [_post(post_id, number) for number, post_id in enumerate(stream[:20], start=1)]

    def more(request: urllib.request.Request) -> dict[str, Any]:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        ids = [int(value) for value in query["post_ids[]"]]
        assert len(ids) <= topics.POSTS_CHUNK_SIZE
        return {"post_stream": {"posts": [_post(post_id, stream.index(post_id) + 1) for post_id in ids]}}

    forum = _serve(monkeypatch, {"/t/42.json": _topic(first, stream), "/t/42/posts.json": more})

    thread = topics.fetch_topic(config, 42)

    assert [message["message_id"] for message in thread["messages"]] == [str(post_id) for post_id in stream]
    assert thread["truncated"] is False
    assert len(forum.requests) == 3  # the topic, then 25 remaining posts in chunks of 20 and 5


def test_fetch_topic_marks_a_longer_topic_as_truncated(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig
) -> None:
    stream = list(range(1000, 1030))
    first = [_post(post_id, number) for number, post_id in enumerate(stream[:20], start=1)]
    forum = _serve(monkeypatch, {"/t/42.json": _topic(first, stream)})

    thread = topics.fetch_topic(config, 42, max_posts=5)

    assert len(thread["messages"]) == 5
    assert thread["truncated"] is True
    assert len(forum.requests) == 1


def test_fetch_topic_accepts_legacy_string_tags(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig
) -> None:
    _serve(monkeypatch, {"/t/42.json": _topic([_post(100, 1)], [100], tags=["governance", "pep"])})
    assert topics.fetch_topic(config, 42)["tags"] == ["governance", "pep"]


def test_fetch_topic_flags_hidden_and_deleted_posts(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig
) -> None:
    hidden = {**_post(100, 1), "hidden": True}
    deleted = {**_post(101, 2), "deleted_at": "2026-10-03T00:00:00.000Z"}
    _serve(monkeypatch, {"/t/42.json": _topic([hidden, deleted], [100, 101])})

    messages = topics.fetch_topic(config, 42)["messages"]

    assert (messages[0]["hidden"], messages[0]["deleted"]) == (True, False)
    assert (messages[1]["hidden"], messages[1]["deleted"]) == (False, True)


def test_fetch_topic_rejects_a_zero_post_limit(config: DiscourseConfig) -> None:
    with pytest.raises(DiscourseError):
        topics.fetch_topic(config, 42, max_posts=0)


# --- HTTP behaviour ---------------------------------------------------------


def _http_error(
    code: int, body: bytes = b"", headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(f"{BASE}/t/42.json", code, "error", message, io.BytesIO(body))


def test_get_json_sends_the_api_key_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    forum = _serve(monkeypatch, {"/t/42.json": {"id": 42}})
    keyed = DiscourseConfig(base_url=BASE, api_key="secret", api_username="magpie-bot")

    client.get_json(keyed, "/t/42.json")

    request = forum.requests[0]
    assert request.get_header("Api-key") == "secret"
    assert request.get_header("Api-username") == "magpie-bot"


def test_get_json_reports_discourse_errors(monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig) -> None:
    body = json.dumps({"errors": ["The requested URL or resource could not be found."]}).encode()
    _serve(monkeypatch, {"/t/42.json": _http_error(404, body)})
    with pytest.raises(DiscourseError, match=r"HTTP 404: The requested URL"):
        client.get_json(config, "/t/42.json")


def test_get_json_reports_rate_limiting(monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig) -> None:
    _serve(monkeypatch, {"/t/42.json": _http_error(429, headers={"Retry-After": "12"})})
    with pytest.raises(DiscourseError, match="retry after 12 seconds"):
        client.get_json(config, "/t/42.json")


def test_get_json_rejects_non_json(monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig) -> None:
    monkeypatch.setattr(client, "_urlopen", lambda request, timeout: FakeResponse(b"<html>login</html>"))
    with pytest.raises(DiscourseError, match="not JSON"):
        client.get_json(config, "/t/42.json")


def _redirect(handler: SameOriginRedirectHandler, url: str, target: str) -> urllib.request.Request | None:
    request = urllib.request.Request(url, headers={"Api-Key": "secret"})
    return handler.redirect_request(request, None, 301, "Moved", Message(), target)


def test_same_origin_redirect_keeps_the_headers() -> None:
    followed = _redirect(SameOriginRedirectHandler(), f"{BASE}/t/42.json", "/t/slug/42.json")
    assert followed is not None
    assert followed.full_url == f"{BASE}/t/slug/42.json"
    assert followed.get_header("Api-key") == "secret"


@pytest.mark.parametrize(
    "target", ["https://evil.example.test/t/42.json", "http://forum.example.test/t/42.json"]
)
def test_cross_origin_redirect_is_refused(target: str) -> None:
    with pytest.raises(DiscourseError, match="refusing to forward credentials"):
        _redirect(SameOriginRedirectHandler(), f"{BASE}/t/42.json", target)


# --- CLI --------------------------------------------------------------------


def test_cli_topic_view_prints_thread_data(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(monkeypatch, {"/t/42.json": _topic([_post(100, 1)], [100])})

    assert package_main(["topic", "view", f"{BASE}/t/proposal-drop-python-3-10/42"]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["thread_id"] == "42"
    assert printed["messages"][0]["from"] == "@alice"


def test_cli_reports_bridge_errors_on_stderr(forum_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert package_main(["topic", "view", "https://evil.example.test/t/x/1"]) == 1
    assert "magpie-discourse error:" in capsys.readouterr().err


def test_topic_url_accepts_the_effective_https_port(config: DiscourseConfig) -> None:
    assert topics.parse_topic_ref(f"{BASE}:443/t/proposal/42", config) == 42
    explicit = DiscourseConfig(base_url=f"{BASE}:443", api_key=None, api_username=None)
    assert topics.parse_topic_ref(f"{BASE}/t/proposal/42", explicit) == 42


@pytest.mark.parametrize("path", ["/t/42/7", "/t/42/7.json"])
def test_short_post_urls_keep_the_topic_id(config: DiscourseConfig, path: str) -> None:
    assert topics.parse_topic_ref(f"{BASE}{path}", config) == 42


@pytest.mark.parametrize("url", [f"{BASE}:bad", f"{BASE}:65536", "https://[broken"])
def test_invalid_config_urls_fail_before_a_request(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCOURSE_URL", url)
    forum = _serve(monkeypatch, {})
    assert package_main(["topic", "view", "42"]) == 1
    assert forum.requests == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "ref",
    ["0", "-1", "²", f"{BASE}/t/proposal/0", f"{BASE}:bad/t/proposal/42", "https://[broken"],
)
def test_invalid_topic_references_fail_before_a_request(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, ref: str, capsys: pytest.CaptureFixture[str]
) -> None:
    forum = _serve(monkeypatch, {})
    assert package_main(["topic", "view", ref]) == 1
    assert forum.requests == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_invalid_post_limits_fail_before_a_request(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, limit: str, capsys: pytest.CaptureFixture[str]
) -> None:
    forum = _serve(monkeypatch, {})
    assert package_main(["topic", "view", "42", "--max-posts", limit]) == 1
    assert forum.requests == []
    assert capsys.readouterr().out == ""


def test_missing_username_fails_before_a_request(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCOURSE_API_KEY", "test-key")
    monkeypatch.setenv("DISCOURSE_API_USERNAME", " ")
    forum = _serve(monkeypatch, {})
    assert package_main(["topic", "view", "42"]) == 1
    assert forum.requests == []
    assert capsys.readouterr().out == ""


def test_missing_posts_are_an_error_not_a_complete_thread(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    forum = _serve(
        monkeypatch,
        {
            "/t/42.json": _topic([_post(100, 1)], [100, 101]),
            "/t/42/posts.json": {"post_stream": {"posts": []}},
        },
    )
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "requested post" in output.err
    assert len(forum.requests) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"id": None},
        {"id": 43},
        {"title": None},
        {"post_stream": None},
        {"post_stream": ["invalid"]},
        {"post_stream": {"posts": [_post(100, 1)]}},
        {"post_stream": {"stream": "100", "posts": []}},
        {"post_stream": {"stream": [True], "posts": []}},
        {"post_stream": {"stream": [100], "posts": ["invalid"]}},
        {"post_stream": {"stream": [100], "posts": [{"id": 100}]}},
    ],
)
def test_malformed_topics_use_the_cli_error_channel(
    monkeypatch: pytest.MonkeyPatch,
    forum_env: None,
    capsys: pytest.CaptureFixture[str],
    change: dict[str, Any],
) -> None:
    _serve(monkeypatch, {"/t/42.json": _topic([_post(100, 1)], [100], **change)})
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "magpie-discourse error:" in output.err
    assert "Traceback" not in output.err


def test_an_explicitly_empty_topic_has_no_messages(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig
) -> None:
    forum = _serve(monkeypatch, {"/t/42.json": _topic([], [])})
    thread = topics.fetch_topic(config, 42)
    assert thread["messages"] == []
    assert thread["participant_handles"] == []
    assert thread["truncated"] is False
    assert len(forum.requests) == 1


def test_batch_order_does_not_change_topic_order(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig
) -> None:
    _serve(
        monkeypatch,
        {
            "/t/42.json": _topic([_post(100, 1)], [100, 101, 102]),
            "/t/42/posts.json": {"post_stream": {"posts": [_post(102, 3), _post(101, 2)]}},
        },
    )
    thread = topics.fetch_topic(config, 42)
    assert [post["message_id"] for post in thread["messages"]] == ["100", "101", "102"]


@pytest.mark.parametrize("code", [401, 403, 404, 429])
def test_http_errors_do_not_print_success_or_retry(
    monkeypatch: pytest.MonkeyPatch,
    forum_env: None,
    capsys: pytest.CaptureFixture[str],
    code: int,
) -> None:
    forum = _serve(monkeypatch, {"/t/42.json": _http_error(code)})
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert f"HTTP {code}" in output.err
    assert len(forum.requests) == 1


@pytest.mark.parametrize("target", [f"{BASE}:bad/t/42.json", "https://[broken"])
def test_invalid_redirect_urls_use_bridge_errors(target: str) -> None:
    with pytest.raises(DiscourseError):
        _redirect(SameOriginRedirectHandler(), f"{BASE}/t/42.json", target)


def test_usage_errors_keep_argparse_exit_code(forum_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        package_main(["topic", "view"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("payload", [None, {"posts": []}, {"post_stream": []}])
def test_malformed_batches_do_not_become_complete_topics(
    monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig, payload: Any
) -> None:
    _serve(
        monkeypatch,
        {"/t/42.json": _topic([_post(100, 1)], [100, 101]), "/t/42/posts.json": payload},
    )
    with pytest.raises(DiscourseError):
        topics.fetch_topic(config, 42)


@pytest.mark.parametrize(
    "problem",
    [
        urllib.error.URLError("offline"),
        TimeoutError(),
        ConnectionResetError("connection reset"),
        http.client.IncompleteRead(b"partial", 10),
    ],
)
def test_network_failures_use_the_cli_error_channel(
    monkeypatch: pytest.MonkeyPatch,
    forum_env: None,
    capsys: pytest.CaptureFixture[str],
    problem: Exception,
) -> None:
    forum = _serve(monkeypatch, {"/t/42.json": problem})
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "magpie-discourse error:" in output.err
    assert len(forum.requests) == 1


def test_retry_after_can_be_an_http_date(monkeypatch: pytest.MonkeyPatch, config: DiscourseConfig) -> None:
    date = "Mon, 19 Oct 2026 10:00:00 GMT"
    _serve(monkeypatch, {"/t/42.json": _http_error(429, headers={"Retry-After": date})})
    with pytest.raises(DiscourseError, match=date):
        client.get_json(config, "/t/42.json")


def test_interrupted_http_error_body_keeps_the_http_status(
    monkeypatch: pytest.MonkeyPatch, forum_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    error = _http_error(503)

    def interrupted() -> bytes:
        raise http.client.IncompleteRead(b"partial", 10)

    monkeypatch.setattr(error, "read", interrupted)
    forum = _serve(monkeypatch, {"/t/42.json": error})
    assert package_main(["topic", "view", "42"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "HTTP 503" in output.err
    assert len(forum.requests) == 1


@pytest.mark.parametrize("headers", [{}, {"Api-Key": "test-key", "Api-Username": "reader"}])
def test_equivalent_origins_preserve_read_headers(headers: dict[str, str]) -> None:
    request = urllib.request.Request(f"{BASE}/t/42.json", headers=headers)
    followed = SameOriginRedirectHandler().redirect_request(
        request, None, 302, "Moved", Message(), f"{BASE}:443/t/slug/42.json"
    )
    assert followed is not None
    assert followed.get_method() == "GET"
    assert dict(followed.header_items()) == dict(request.header_items())
