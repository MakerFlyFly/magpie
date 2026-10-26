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

"""Offline CLI regressions for category discovery and recent-topic pagination."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any

import pytest

from magpie_discourse import client
from magpie_discourse import main as package_main

BASE = "https://forum.example.test"
DATE = "2026-10-20T12:00:00.000Z"
CATEGORY = {"id": 7, "slug": "dev", "name": "Development"}


def topic(topic_id: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": topic_id,
        "title": f"Proposal {topic_id}",
        "created_at": "2026-10-01T00:00:00Z",
        "bumped_at": DATE,
        "last_posted_at": "2026-10-02T00:00:00Z",
        "posts_count": 2,
        "tags": ["python", {"id": 1, "name": "design", "slug": "design"}],
        "pinned": False,
        "closed": False,
        "archived": False,
        **fields,
    }


def page(rows: Any, more: Any = None) -> dict[str, Any]:
    return {"topic_list": {"topics": rows, "more_topics_url": more}}


def next_url(number: int, category: int = 7) -> str:
    return f"/latest?category={category}&order=activity&ascending=false&no_subcategories=false&page={number}"


@pytest.fixture(autouse=True)
def forum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCOURSE_URL", BASE)
    monkeypatch.delenv("DISCOURSE_API_KEY", raising=False)
    monkeypatch.delenv("DISCOURSE_API_USERNAME", raising=False)


def serve(
    monkeypatch: pytest.MonkeyPatch, pages: dict[int, Any], **routes: Any
) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []
    replies = {"/c/7/show.json": {"category": CATEGORY}, **routes}

    def open_request(request: urllib.request.Request, timeout: float, *, base_url: str) -> io.BytesIO:
        assert timeout == 30
        requests.append(request)
        parsed = urllib.parse.urlsplit(request.full_url)
        prefix = urllib.parse.urlsplit(base_url).path
        path = parsed.path[len(prefix) :]
        if path == "/latest.json":
            params = urllib.parse.parse_qs(parsed.query)
            reply = pages[int(params["page"][0])]
        else:
            reply = replies[path]
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(reply if isinstance(reply, bytes) else json.dumps(reply).encode("utf-8"))

    monkeypatch.setattr(client, "_urlopen", open_request)
    return requests


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = package_main(["topic", "list", "--category", "7", *args])
    output = capsys.readouterr()
    return status, output.out, output.err


def test_list_uses_activity_order_and_keeps_message_time_separate(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    requests = serve(monkeypatch, {0: page([topic(42, title="设计 🐦", pinned=True)])})
    status, out, err = run(capsys, "--since", "2026-10-20T20:00:00+08:00")
    assert (status, err) == (0, "")
    data = json.loads(out)
    assert data == {
        "category": CATEGORY,
        "since": "2026-10-20T12:00:00Z",
        "threads": [
            {
                "thread_id": "42",
                "subject": "设计 🐦",
                "first_message_date": "2026-10-01T00:00:00Z",
                "last_message_date": "2026-10-02T00:00:00Z",
                "bumped_at": DATE,
                "message_count": 2,
                "permalink": f"{BASE}/t/42",
                "tags": ["python", "design"],
                "pinned": True,
                "closed": False,
                "archived": False,
            }
        ],
        "truncated": False,
    }
    assert out.isascii()
    assert len(requests) == 2
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[-1].full_url).query)
    assert query == {
        "category": ["7"],
        "order": ["activity"],
        "ascending": ["false"],
        "no_subcategories": ["false"],
        "page": ["0"],
    }
    assert all(request.get_method() == "GET" for request in requests)


def test_cutoff_after_old_pins_matches_and_overlap_stops_pagination(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    requests = serve(
        monkeypatch,
        {
            0: page([topic(1, pinned=True, bumped_at="2020-01-01T00:00:00Z"), topic(2)], next_url(1)),
            1: page([topic(2), topic(3, bumped_at="2020-01-01T00:00:00Z")], next_url(2)),
        },
    )
    status, out, err = run(capsys, "--since", "2026-10-20", "--limit", "1")
    assert (status, err) == (0, "")
    assert [t["thread_id"] for t in json.loads(out)["threads"]] == ["2"]
    assert json.loads(out)["truncated"] is False
    assert len(requests) == 3  # category + two pages, stopping at an old non-pinned topic


@pytest.mark.parametrize("tail", [[], [topic(1)], [topic(2, bumped_at="2020-01-01T00:00:00Z")]])
def test_exact_limit_is_not_truncated_without_an_extra_match(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, tail: Any
) -> None:
    requests = serve(monkeypatch, {0: page([topic(1)], next_url(1)), 1: page(tail)})
    status, out, err = run(capsys, "--limit", "1", "--since", "2026-10-20")
    assert (status, err) == (0, "")
    assert json.loads(out)["truncated"] is False
    assert len(requests) == 3


def test_filtering_precedes_deduplication(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    serve(
        monkeypatch,
        {
            0: page([topic(1, pinned=True, bumped_at="2020-01-01T00:00:00Z")], next_url(1)),
            1: page([topic(1, title="New activity")]),
        },
    )
    status, out, err = run(capsys, "--since", "2026-10-20")
    assert (status, err) == (0, "")
    assert json.loads(out)["threads"][0]["subject"] == "New activity"


@pytest.mark.parametrize("last", [None, "missing"])
def test_missing_message_time_never_falls_back_to_activity(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, last: Any
) -> None:
    row = topic(1, last_posted_at=last)
    if last == "missing":
        del row["last_posted_at"]
    serve(monkeypatch, {0: page([row])})
    status, out, err = run(capsys, "--since", "2026-10-20")
    assert (status, err) == (0, "")
    assert json.loads(out)["threads"][0]["last_message_date"] is None


@pytest.mark.parametrize("ref", ["dev/core/proposals", "%64ev/core/proposals"])
def test_nested_category_resolution(monkeypatch: pytest.MonkeyPatch, capsys: Any, ref: str) -> None:
    leaf = {"id": 7, "slug": "proposals", "name": "Proposals"}
    catalog = {
        "category_list": {
            "categories": [
                {
                    "id": 1,
                    "slug": "dev",
                    "name": "Dev",
                    "subcategory_list": [
                        {"id": 2, "slug": "core", "name": "Core", "subcategory_list": [leaf]}
                    ],
                }
            ]
        }
    }
    requests = serve(monkeypatch, {0: page([])}, **{"/categories.json": catalog})
    status = package_main(["topic", "list", "--category", ref])
    output = capsys.readouterr()
    assert (status, output.err) == (0, "")
    assert json.loads(output.out)["category"] == leaf
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(requests[0].full_url).query) == {
        "include_subcategories": ["true"]
    }
    assert urllib.parse.urlsplit(requests[1].full_url).path == "/latest.json"


@pytest.mark.parametrize("ref", ["讨论/提案", "%E8%AE%A8%E8%AE%BA/%E6%8F%90%E6%A1%88"])
def test_unicode_category_names_do_not_enter_request_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, ref: str
) -> None:
    catalog = {
        "category_list": {
            "categories": [
                {
                    "id": 1,
                    "slug": "讨论",
                    "name": "讨论",
                    "subcategory_list": [{"id": 7, "slug": "%E6%8F%90%E6%A1%88", "name": "提案"}],
                }
            ]
        }
    }
    requests = serve(monkeypatch, {0: page([])}, **{"/categories.json": catalog})
    assert package_main(["topic", "list", "--category", ref]) == 0
    assert json.loads(capsys.readouterr().out)["category"]["id"] == 7
    assert all(
        urllib.parse.urlsplit(r.full_url).path in ("/categories.json", "/latest.json") for r in requests
    )


@pytest.mark.parametrize("ref,needle", [("core", "category"), ("unknown", "category")])
def test_unresolved_categories_fail_without_guessing_cause(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, ref: str, needle: str
) -> None:
    catalog = {
        "category_list": {
            "categories": [
                {
                    "id": 1,
                    "slug": "dev",
                    "name": "Dev",
                    "subcategory_list": [{"id": 7, "slug": "core", "name": "Core"}],
                },
            ]
        }
    }
    requests = serve(monkeypatch, {}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", ref)
    assert status == 1 and out == "" and needle in err.lower()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "args",
    [
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--category", "0"],
        ["--category", "\uff14\uff12"],
        ["--category", "-7"],
        ["--category", "+7"],
        ["--category", "²"],
        ["--category", "dev//core"],
        ["--category", "../dev"],
        ["--category", "dev%2fcore"],
        ["--category", "%2564ev"],
        ["--category", "%zz"],
        ["--since", "2026-02-30"],
        ["--since", "2026-10-20T12:00:00"],
        ["--since", "2026-10-20T12:00:00.1234567Z"],
        ["--since", "nonsense"],
    ],
)
def test_invalid_list_inputs_fail_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, args: list[str]
) -> None:
    requests = serve(monkeypatch, {})
    status, out, err = run(capsys, *args)
    assert status == 1 and out == "" and "magpie-discourse error:" in err
    assert requests == []


@pytest.mark.parametrize(
    "bumped,expected",
    [
        ("2026-10-20T00:00:00Z", True),
        ("2026-10-19T23:59:59.999999Z", False),
        ("2026-10-20T08:00:00+08:00", True),
    ],
)
def test_since_is_inclusive_utc_midnight(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, bumped: str, expected: bool
) -> None:
    serve(monkeypatch, {0: page([topic(1, bumped_at=bumped)])})
    status, out, err = run(capsys, "--since", "2026-10-20")
    assert (status, err) == (0, "")
    assert bool(json.loads(out)["threads"]) is expected


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/latest?category=7&page=1",
        "http://forum.example.test/latest?category=7&page=1",
        "/other-app/latest?category=7&page=1",
        "/latest?category=8&page=1",
        "/latest?category=7&page=0",
        "/latest?category=7&page=-1",
        "/latest?category=7&page=1&page=2",
        "/latest?category=7",
        "/latest?category=7&page=\uff11",
        "/latest?category=7&page=1#fragment",
        "/latest;params?category=7&page=1",
        "/%6catest?category=7&page=1",
        "/latest?category=7&page=2",
        "/latest?category=7&page=1&order=created",
        "/latest?category=7&page=1&ascending=true",
        "/latest?category=7&page=1&no_subcategories=true",
    ],
)
def test_unsafe_or_nonadvancing_pagination_fails_before_next_request(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, target: str
) -> None:
    requests = serve(monkeypatch, {0: page([topic(1)], target)})
    status, out, err = run(capsys, "--limit", "1")
    assert status == 1 and out == "" and "magpie-discourse error:" in err
    assert len(requests) == 2


@pytest.mark.parametrize(
    "target",
    [
        next_url(1),
        BASE + next_url(1),
        "https://forum.example.test:443" + next_url(1),
        "latest.json?category=7&page=1",
        "/latest?ascending=false&category=7&no_subcategories=false&order=activity&page=1",
    ],
)
def test_supported_pagination_links_are_rebuilt_locally(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, target: str
) -> None:
    requests = serve(monkeypatch, {0: page([topic(1)], target), 1: page([])})
    status, out, err = run(capsys, "--limit", "1")
    assert (status, err) == (0, "") and json.loads(out)["truncated"] is False
    assert requests[-1].full_url.startswith(BASE + "/latest.json?")
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(requests[-1].full_url).query) == {
        "category": ["7"],
        "order": ["activity"],
        "ascending": ["false"],
        "no_subcategories": ["false"],
        "page": ["1"],
    }


def test_subfolder_pagination_and_auth_use_the_configured_client(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("DISCOURSE_URL", BASE + "/forum")
    monkeypatch.setenv("DISCOURSE_API_KEY", "test-key")
    monkeypatch.setenv("DISCOURSE_API_USERNAME", "reader")
    requests = serve(monkeypatch, {0: page([topic(1)], "/forum" + next_url(1)), 1: page([])})
    status, out, err = run(capsys, "--limit", "1")
    assert (status, err) == (0, "")
    assert json.loads(out)["threads"][0]["permalink"] == BASE + "/forum/t/1"
    assert len(requests) == 3
    assert all(
        r.get_header("Api-key") == "test-key" and r.get_header("Api-username") == "reader" for r in requests
    )


def test_repeated_complete_page_is_an_operational_error(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    requests = serve(monkeypatch, {0: page([topic(1)], next_url(1)), 1: page([topic(1)], next_url(2))})
    status, out, err = run(capsys, "--limit", "1")
    assert status == 1 and out == "" and "paginat" in err.lower()
    assert len(requests) == 3


@pytest.mark.parametrize("code", [403, 404, 429])
def test_lookahead_http_failure_is_not_partial_success(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, code: int
) -> None:
    headers = Message()
    headers["Retry-After"] = "10"
    failure = urllib.error.HTTPError(
        BASE + "/latest.json", code, "failure", headers, io.BytesIO(b'{"errors":["unavailable"]}')
    )
    requests = serve(monkeypatch, {0: page([topic(1)], next_url(1)), 1: failure})
    status, out, err = run(capsys, "--limit", "1")
    assert status == 1 and out == "" and str(code) in err
    if code == 429:
        assert "10 seconds" in err
    assert len(requests) == 3


@pytest.mark.parametrize(
    "reply",
    [
        b"not JSON",
        [],
        {},
        {"topic_list": None},
        {"topic_list": {}},
        page(None),
        page({}),
        page([None]),
        page([], True),
        TimeoutError(),
    ],
)
def test_bad_lookahead_responses_do_not_become_complete_results(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, reply: Any
) -> None:
    serve(monkeypatch, {0: page([topic(1)], next_url(1)), 1: reply})
    status, out, err = run(capsys, "--limit", "1")
    assert status == 1 and out == "" and "magpie-discourse error:" in err


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("id", 0),
        ("title", None),
        ("posts_count", True),
        ("posts_count", -1),
        ("created_at", None),
        ("bumped_at", None),
        ("bumped_at", "bad"),
        ("last_posted_at", "bad"),
        ("pinned", "true"),
        ("closed", 1),
        ("archived", None),
        ("tags", {}),
        ("tags", [{}]),
        ("tags", [None]),
    ],
)
def test_invalid_summaries_are_rejected_even_beyond_the_output_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, field: str, value: Any
) -> None:
    serve(monkeypatch, {0: page([topic(1), topic(2), topic(3, **{field: value})])})
    status, out, err = run(capsys, "--limit", "1")
    assert status == 1 and out == "" and "magpie-discourse error:" in err


def test_default_limit_looks_for_one_extra_unique_match(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    serve(monkeypatch, {0: page([topic(i) for i in range(1, 102)])})
    status, out, err = run(capsys)
    assert (status, err) == (0, "")
    data = json.loads(out)
    assert len(data["threads"]) == 100 and data["truncated"] is True and data["since"] is None


def test_list_permalink_can_be_viewed_by_pr1(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    serve(
        monkeypatch,
        {0: page([topic(42)])},
        **{
            "/t/42.json": {
                "id": 42,
                "title": "Proposal",
                "slug": "proposal",
                "posts_count": 0,
                "post_stream": {"stream": [], "posts": []},
            }
        },
    )
    assert package_main(["topic", "list", "--category", "7"]) == 0
    permalink = json.loads(capsys.readouterr().out)["threads"][0]["permalink"]
    assert package_main(["topic", "view", permalink]) == 0
    assert json.loads(capsys.readouterr().out)["thread_id"] == "42"


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"category": None},
        {"category": {**CATEGORY, "id": 8}},
        {"category": {**CATEGORY, "id": True}},
        {"category": {**CATEGORY, "slug": None}},
    ],
)
def test_invalid_direct_category_response_stops_before_listing(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, reply: Any
) -> None:
    requests = serve(monkeypatch, {}, **{"/c/7/show.json": reply})
    status, out, err = run(capsys)
    assert status == 1 and out == "" and "magpie-discourse error:" in err
    assert len(requests) == 1


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"category_list": None},
        {"category_list": {}},
        {"category_list": {"categories": None}},
        {"category_list": {"categories": [None]}},
        {"category_list": {"categories": [{**CATEGORY, "subcategory_list": None}]}},
        {"category_list": {"categories": [{**CATEGORY, "parent_category_id": True}]}},
        {"category_list": {"categories": [{**CATEGORY, "parent_category_id": 7}]}},
    ],
)
def test_bad_category_catalog_is_not_a_successful_empty_result(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, reply: Any
) -> None:
    serve(monkeypatch, {}, **{"/categories.json": reply})
    status, out, err = run(capsys, "--category", "dev")
    assert status == 1 and out == "" and "magpie-discourse error:" in err


def test_flat_catalog_preserves_ancestry(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    catalog = {
        "category_list": {
            "categories": [
                {"id": 1, "slug": "parent", "name": "Parent"},
                {**CATEGORY, "parent_category_id": 1},
            ]
        }
    }
    serve(monkeypatch, {0: page([])}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", "parent/dev")
    assert (status, err) == (0, "")
    assert json.loads(out)["category"] == CATEGORY


def test_optional_flags_and_tags_have_documented_defaults(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    row = topic(1, tags=None)
    for field in ("pinned", "closed", "archived"):
        del row[field]
    serve(monkeypatch, {0: page([row])})
    status, out, err = run(capsys)
    assert (status, err) == (0, "")
    summary = json.loads(out)["threads"][0]
    assert summary["tags"] == []
    assert all(summary[field] is False for field in ("pinned", "closed", "archived"))


@pytest.mark.parametrize("field", ["id", "title", "created_at", "bumped_at", "posts_count"])
def test_missing_required_summary_fields_fail(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, field: str
) -> None:
    row = topic(1)
    del row[field]
    serve(monkeypatch, {0: page([row])})
    status, out, err = run(capsys)
    assert status == 1 and out == "" and "magpie-discourse error:" in err


def test_empty_category_has_no_threads(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    serve(monkeypatch, {0: page([])})
    status, out, err = run(capsys)
    assert (status, err) == (0, "")
    assert json.loads(out) == {"category": CATEGORY, "since": None, "threads": [], "truncated": False}


@pytest.mark.parametrize(
    "target",
    [
        "/latest?category=7&page=1",
        "/forum-other/latest?category=7&page=1",
        "/forum/../latest?category=7&page=1",
        "/forum/%2flatest?category=7&page=1",
        "/forum/%256catest?category=7&page=1",
    ],
)
def test_pagination_cannot_leave_a_configured_subfolder(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, target: str
) -> None:
    monkeypatch.setenv("DISCOURSE_URL", BASE + "/forum")
    requests = serve(monkeypatch, {0: page([topic(1)], target)})
    status, out, err = run(capsys, "--limit", "1")
    assert status == 1 and out == "" and "magpie-discourse error:" in err
    assert len(requests) == 2


def test_large_category_scan_ends_at_the_since_boundary(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    pages = {}
    for number in range(100):
        rows = [
            topic(index + 1, bumped_at=DATE if index < 2 else "2019-01-01T00:00:00Z")
            for index in range(number * 30, (number + 1) * 30)
        ]
        pages[number] = page(rows, next_url(number + 1) if number < 99 else None)
    requests = serve(monkeypatch, pages)
    status, out, err = run(capsys, "--since", "2026-10-20")
    assert (status, err) == (0, "")
    assert [row["thread_id"] for row in json.loads(out)["threads"]] == ["1", "2"]
    assert json.loads(out)["truncated"] is False
    assert len(requests) == 2


def test_missing_pinned_evidence_does_not_trigger_cutoff(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    unknown = topic(1, bumped_at="2019-01-01T00:00:00Z")
    del unknown["pinned"]
    requests = serve(monkeypatch, {0: page([unknown], next_url(1)), 1: page([topic(2), topic(3)])})
    status, out, err = run(capsys, "--since", "2026-10-20", "--limit", "1")
    assert (status, err) == (0, "")
    assert [row["thread_id"] for row in json.loads(out)["threads"]] == ["2"]
    assert json.loads(out)["truncated"] is True
    assert len(requests) == 3


def test_since_equality_and_old_pin_are_not_cutoffs(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    requests = serve(
        monkeypatch,
        {
            0: page(
                [
                    topic(1, pinned=True, bumped_at="2019-01-01T00:00:00Z"),
                    topic(2),
                    topic(3, bumped_at="2026-10-20T11:59:59.999999Z"),
                ],
                next_url(1),
            )
        },
    )
    status, out, err = run(capsys, "--since", DATE, "--limit", "1")
    assert (status, err) == (0, "")
    assert [row["thread_id"] for row in json.loads(out)["threads"]] == ["2"]
    assert json.loads(out)["truncated"] is False
    assert len(requests) == 2


def test_extra_match_before_cutoff_still_proves_truncation(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    requests = serve(monkeypatch, {0: page([topic(1), topic(2), topic(3, bumped_at="2019-01-01T00:00:00Z")])})
    status, out, err = run(capsys, "--since", "2026-10-20", "--limit", "1")
    assert (status, err) == (0, "")
    assert json.loads(out)["truncated"] is True
    assert len(requests) == 2


def test_cutoff_does_not_bypass_received_page_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    serve(monkeypatch, {0: page([topic(1, bumped_at="2019-01-01T00:00:00Z"), topic(2, posts_count=None)])})
    status, out, err = run(capsys, "--since", "2026-10-20")
    assert status == 1 and out == "" and "magpie-discourse error:" in err


@pytest.mark.parametrize("slug", ["", "bad%zz", "encoded%2fseparator"])
def test_numeric_id_does_not_require_a_usable_slug(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, slug: str
) -> None:
    selected = {**CATEGORY, "slug": slug}
    requests = serve(monkeypatch, {0: page([])}, **{"/c/7/show.json": {"category": selected}})
    status, out, err = run(capsys)
    assert (status, err) == (0, "")
    assert json.loads(out)["category"] == selected
    assert len(requests) == 2


@pytest.mark.parametrize("slug", ["", "bad%zz", "encoded%2fseparator"])
@pytest.mark.parametrize("ref", ["dev", "parent/dev"])
def test_unrelated_unusable_slug_does_not_poison_lookup(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, slug: str, ref: str
) -> None:
    selected = (
        CATEGORY
        if ref == "dev"
        else {"id": 1, "slug": "parent", "name": "Parent", "subcategory_list": [CATEGORY]}
    )
    catalog = {
        "category_list": {
            "categories": [
                {
                    "id": 8,
                    "slug": slug,
                    "name": "Other",
                    "subcategory_list": [{"id": 9, "slug": "child", "name": "Child"}],
                },
                selected,
            ]
        }
    }
    serve(monkeypatch, {0: page([])}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", ref)
    assert (status, err) == (0, "")
    assert json.loads(out)["category"] == CATEGORY


def test_bare_slug_prefers_confirmed_root_to_same_named_child(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    selected = {**CATEGORY, "slug": "core"}
    catalog = {
        "category_list": {
            "categories": [
                {
                    "id": 1,
                    "slug": "dev",
                    "name": "Dev",
                    "subcategory_list": [{"id": 2, "slug": "core", "name": "Child"}],
                },
                selected,
            ]
        }
    }
    serve(monkeypatch, {0: page([])}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", "core")
    assert (status, err) == (0, "")
    assert json.loads(out)["category"] == selected


@pytest.mark.parametrize("ref", ["core", "ops/core"])
def test_partial_catalog_does_not_promote_an_orphan_child_to_root(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, ref: str
) -> None:
    catalog = {"category_list": {"categories": [{**CATEGORY, "slug": "core", "parent_category_id": 99}]}}
    requests = serve(monkeypatch, {}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", ref)
    assert status == 1 and out == "" and "numeric" in err.lower()
    assert len(requests) == 1


def test_duplicate_confirmed_root_slugs_remain_ambiguous(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    catalog = {"category_list": {"categories": [CATEGORY, {**CATEGORY, "id": 8}]}}
    requests = serve(monkeypatch, {}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", "dev")
    assert status == 1 and out == "" and "ambiguous" in err.lower()
    assert len(requests) == 1


def test_empty_slug_ancestor_does_not_invent_a_path(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    catalog = {
        "category_list": {
            "categories": [{"id": 1, "slug": "", "name": "Parent", "subcategory_list": [CATEGORY]}]
        }
    }
    requests = serve(monkeypatch, {}, **{"/categories.json": catalog})
    status, out, err = run(capsys, "--category", "1-category/dev")
    assert status == 1 and out == "" and "numeric" in err.lower()
    assert len(requests) == 1


def test_oversized_ascii_id_uses_the_conversion_error_channel(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    requests = serve(monkeypatch, {})
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(4300)
        status, out, err = run(capsys, "--category", "9" * 4301)
    finally:
        sys.set_int_max_str_digits(previous)
    assert status == 1 and out == "" and "Invalid category id" in err
    assert requests == []
