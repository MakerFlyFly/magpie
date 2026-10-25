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
"""Folded and encoded subjects across the IMAP read and draft operations."""

from __future__ import annotations

import email
import email.policy

import pytest

from conftest import FakeMailbox, build_message, make_config
from imap_source.create_draft import create_draft
from imap_source.list_recent_threads import list_recent_threads
from imap_source.read_thread import read_thread
from imap_source.searching import parse_message
from imap_source.threads import summarise

ROOT = "<subject-report@example.org>"
REPLY = "<subject-reply@example.org>"


@pytest.fixture(
    params=[
        pytest.param((b"Subject: A report", "A report", "Re: A report"), id="plain"),
        pytest.param((b"Subject: A\r\n  report", "A  report", "Re: A  report"), id="crlf-spaces"),
        pytest.param((b"Subject: A\r\n\treport", "A\treport", "Re: A\treport"), id="crlf-tab"),
        pytest.param((b"Subject: A\n report", "A report", "Re: A report"), id="lf-space"),
        pytest.param((b"Subject: A\n\treport", "A\treport", "Re: A\treport"), id="lf-tab"),
        pytest.param((b"Subject: =?utf-8?q?Re=3A_=63=61=66=C3=A9?=", "Re: café", "Re: café"), id="utf8-q"),
        pytest.param((b"Subject: =?utf-8?b?UmU6IGNhZsOp?=", "Re: café", "Re: café"), id="utf8-b"),
        pytest.param(
            (b"Subject: =?utf-8?q?Re=3A_?=\r\n =?utf-8?b?Y2Fmw6k=?=", "Re: café", "Re: café"),
            id="folded-encoded-words",
        ),
        pytest.param(
            (b"Subject: Re: =?utf-8?q?=63=61=66=C3=A9?= report", "Re: café report", "Re: café report"),
            id="mixed",
        ),
        pytest.param((b"Subject: =?iso-8859-1?q?Re=3A_=63=61=66=E9?=", "Re: café", "Re: café"), id="latin1"),
        pytest.param(
            (b"Subject: =?unknown-charset?q?Re=3A_report?=", "Re: report", "Re: report"),
            id="unknown-charset",
        ),
        pytest.param(
            (b"Subject: =?utf-8?q?A=0D=0Areport?=", "A report", "Re: A report"),
            id="encoded-crlf",
        ),
        pytest.param((b"Subject: =?utf-8?q?A=0Areport?=", "A report", "Re: A report"), id="encoded-lf"),
        pytest.param((b"Subject:", "", "Re: (no subject)"), id="empty"),
        pytest.param((b"", "", "Re: (no subject)"), id="missing"),
    ]
)
def subject_case(request: pytest.FixtureRequest) -> tuple[bytes, str, str]:
    header, expected, reply_subject = request.param
    raw = (
        (
            f"Message-ID: {ROOT}\r\nFrom: reporter@example.org\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\n"
        ).encode("ascii")
        + header
        + b"\r\n\r\nexternal content\n"
    )
    return raw, expected, reply_subject


@pytest.fixture
def mailbox(subject_case: tuple[bytes, str, str]) -> FakeMailbox:
    raw, _expected, _reply_subject = subject_case
    return FakeMailbox.seed(
        INBOX=[
            raw,
            build_message(
                REPLY,
                "Re: report",
                "peer@example.org",
                references=ROOT,
                in_reply_to=ROOT,
                date="Tue, 22 Sep 2026 09:00:00 +0000",
            ),
        ],
        Drafts=[],
    )


def test_summarise_normalises_subject(subject_case: tuple[bytes, str, str]) -> None:
    raw, expected, _reply_subject = subject_case
    position = summarise(parse_message(raw))
    assert position.subject == expected
    assert "\r" not in position.subject
    assert "\n" not in position.subject


def test_read_operations_return_normalised_subject(
    mailbox: FakeMailbox, subject_case: tuple[bytes, str, str]
) -> None:
    _raw, expected, _reply_subject = subject_case
    config = make_config()
    threads = list_recent_threads(mailbox, config, since_days=90)
    assert len(threads) == 1
    assert threads[0]["subject"] == expected
    thread = read_thread(mailbox, config, ROOT, since_days=90)
    assert thread["subject"] == expected
    assert thread["messages"][0]["subject"] == expected
    assert [message["message_id"] for message in thread["messages"]] == [ROOT, REPLY]


def test_create_draft_round_trips_normalised_subject(
    mailbox: FakeMailbox, subject_case: tuple[bytes, str, str]
) -> None:
    _raw, _expected, reply_subject = subject_case
    config = make_config()
    body = "Thanks for the report.\n"
    result = create_draft(
        mailbox,
        config,
        ROOT,
        body=body,
        to=[],
        cc=["triage@example.org"],
        from_address=None,
    )
    assert len(mailbox.folders["Drafts"]) == 1
    stored = mailbox.folders["Drafts"][result["draft_uid"]]
    parsed = email.message_from_bytes(stored, policy=email.policy.default)
    assert result["subject"] == reply_subject
    assert parsed["Subject"] == reply_subject
    assert parsed["From"] == config.user
    assert parsed["To"] == "reporter@example.org"
    assert parsed["Cc"] == "triage@example.org"
    assert parsed["In-Reply-To"] == REPLY
    assert parsed["References"] == f"{ROOT} {REPLY}"
    assert parsed.get_content() == body
    assert result["sent"] is False


def test_create_draft_accepts_automatically_folded_long_subject() -> None:
    subject = "A long report about a connector failing while processing unexpected input " * 3
    subject = subject.strip()
    raw = build_message(ROOT, subject, "reporter@example.org")
    assert b"\n " in raw
    box = FakeMailbox.seed(INBOX=[raw], Drafts=[])
    result = create_draft(box, make_config(), ROOT, body="Thanks.\n", to=[], cc=[], from_address=None)
    parsed = email.message_from_bytes(box.folders["Drafts"][result["draft_uid"]], policy=email.policy.default)
    assert parsed["Subject"] == f"Re: {subject}"
    assert result["sent"] is False
