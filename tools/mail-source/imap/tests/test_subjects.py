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
import json

import pytest

from conftest import FakeMailbox, build_message, make_config
from imap_source.cli import print_json, run_operation
from imap_source.create_draft import create_draft
from imap_source.list_drafts import list_drafts
from imap_source.list_recent_threads import list_recent_threads
from imap_source.list_sent_since import list_sent_since
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
        pytest.param((b"Subject: =?utf-8\x00?q?Re=3A_x?=", "Re: x", "Re: x"), id="nul-charset-q"),
        pytest.param((b"Subject: =?utf-8\x00?b?UmU6IHg=?=", "Re: x", "Re: x"), id="nul-charset-b"),
        pytest.param(
            (b"Subject: =?utf-8?q?A=0D=0Areport?=", "A report", "Re: A report"),
            id="encoded-crlf",
        ),
        pytest.param((b"Subject: =?utf-8?q?A=0Areport?=", "A report", "Re: A report"), id="encoded-lf"),
        pytest.param((b"Subject: =?utf-8?q?A=0Breport?=", "A report", "Re: A report"), id="encoded-vt"),
        pytest.param((b"Subject: =?utf-8?q?A=0Creport?=", "A report", "Re: A report"), id="encoded-ff"),
        pytest.param((b"Subject: =?utf-8?q?A=1Creport?=", "A report", "Re: A report"), id="encoded-fs"),
        pytest.param((b"Subject: =?utf-8?q?A=1Dreport?=", "A report", "Re: A report"), id="encoded-gs"),
        pytest.param((b"Subject: =?utf-8?q?A=1Ereport?=", "A report", "Re: A report"), id="encoded-rs"),
        pytest.param((b"Subject: =?utf-8?q?A=C2=85report?=", "A report", "Re: A report"), id="encoded-nel"),
        pytest.param((b"Subject: =?iso-8859-1?q?A=85report?=", "A report", "Re: A report"), id="latin1-nel"),
        pytest.param((b"Subject: =?utf-8?q?A=E2=80=A8report?=", "A report", "Re: A report"), id="encoded-ls"),
        pytest.param((b"Subject: =?utf-8?q?A=E2=80=A9report?=", "A report", "Re: A report"), id="encoded-ps"),
        pytest.param(
            (
                b"Subject: =?utf-8?q?A=0D=0A=0B=0C=1C=1D=1E=C2=85=E2=80=A8=E2=80=A9report?=",
                "A report",
                "Re: A report",
            ),
            id="consecutive-separators",
        ),
        pytest.param(
            (b"Subject: =?utf-8?q?=E2=80=A8A_report?=", " A report", "Re: A report"), id="leading-separator"
        ),
        pytest.param(
            (b"Subject: =?utf-8?q?A_report=E2=80=A9?=", "A report ", "Re: A report"), id="trailing-separator"
        ),
        pytest.param((b"Subject: =?utf-8?b?!!!?=", "", "Re: (no subject)"), id="invalid-base64"),
        pytest.param(
            (b"Subject: price =?50?= off", "price =?50?= off", "Re: price =?50?= off"), id="literal-marker"
        ),
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
    raw, expected, _reply_subject = subject_case
    config = make_config()
    threads = list_recent_threads(mailbox, config, since_days=90)
    assert len(threads) == 1
    assert threads[0]["subject"] == expected
    thread = read_thread(mailbox, config, ROOT, since_days=90)
    assert thread["subject"] == expected
    assert thread["messages"][0]["subject"] == expected
    assert [message["message_id"] for message in thread["messages"]] == [ROOT, REPLY]
    mailbox.append("Drafts", raw)
    mailbox.append("Sent", raw)
    drafts = list_drafts(mailbox, config, ROOT, since_days=90)
    sent = list_sent_since(mailbox, config, ROOT, since_days=90)
    assert [draft["subject"] for draft in drafts] == [expected]
    assert [message["subject"] for message in sent] == [expected]


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


def test_summarise_keeps_the_entire_large_plain_subject() -> None:
    subject = "word " * 20_000 + "the end"
    raw = b"Subject: " + subject.encode("ascii") + b"\r\n\r\n"
    assert summarise(parse_message(raw)).subject == subject


def test_summarise_decodes_only_once() -> None:
    raw = b"Subject: =?utf-8?q?=3D=3Futf-8=3Fq=3FRe=3A_x=3F=3D?=\r\n\r\n"
    assert summarise(parse_message(raw)).subject == "=?utf-8?q?Re: x?="


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        (b"Re: report \xc3\xa9", "Re: report \ufffd\ufffd"),
        (b"Re: report \xe9", "Re: report \ufffd"),
        (b"report \xc3\xa9 =?utf-8?q?Re=3A_x?=", "report \ufffd\ufffd Re: x"),
        (b"raw \\ufffd =?utf-8?q?ok?=", r"raw \ufffd ok"),
    ],
)
def test_compat32_subject_text_remains_compatible(subject: bytes, expected: str) -> None:
    assert summarise(parse_message(b"Subject: " + subject + b"\r\n\r\n")).subject == expected


@pytest.mark.parametrize(
    "subject",
    [
        b"=?idna?q?" + b"a" * 80 + b"?=",
        b"=?utf-16?q?=FF=FEA?=",
        b"=?undefined?q?abc?=",
        b"=?rot13?q?abc?=",
        b"=?utf-7?q?+AGEAYgBj-?=",
        b"=?unicode_escape?q?A\\u2028B?=",
        b"=?utf-8\x00?q?Re=3A_x?=",
        b"=?utf-8\x00?b?UmU6IHg=?=",
    ],
)
def test_unusual_charsets_do_not_prevent_draft_creation(subject: bytes) -> None:
    raw = f"Message-ID: {ROOT}\r\nFrom: reporter@example.org\r\nSubject: ".encode("ascii")
    box = FakeMailbox.seed(INBOX=[raw + subject + b"\r\n\r\nbody"], Drafts=[])
    result = create_draft(box, make_config(), ROOT, body="Thanks.\n", to=[], cc=[], from_address=None)
    assert result["sent"] is False
    assert len(box.folders["Drafts"]) == 1


@pytest.mark.parametrize("encoded", [b"q?Re=3A_x", b"b?UmU6IHg="])
@pytest.mark.parametrize(
    "operation", ["list_recent_threads", "read_thread", "list_drafts", "list_sent_since", "create_draft"]
)
def test_unrelated_nul_charset_does_not_block_operations(
    encoded: bytes, operation: str, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy = build_message(ROOT, "A report", "reporter@example.org")
    unrelated = (
        b"Message-ID: <unrelated@example.org>\r\nFrom: other@example.org\r\n"
        b"Subject: =?utf-8\x00?" + encoded + b"?=\r\n\r\nbody"
    )
    box = FakeMailbox.seed(INBOX=[healthy, unrelated], Drafts=[healthy, unrelated], Sent=[healthy, unrelated])
    config = make_config()
    operations = {
        "list_recent_threads": lambda: list_recent_threads(box, config, since_days=90),
        "read_thread": lambda: read_thread(box, config, ROOT, since_days=90),
        "list_drafts": lambda: list_drafts(box, config, ROOT, since_days=90),
        "list_sent_since": lambda: list_sent_since(box, config, ROOT, since_days=90),
        "create_draft": lambda: create_draft(
            box, config, ROOT, body="Thanks.\n", to=[], cc=[], from_address=None
        ),
    }
    assert run_operation(operations[operation]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    if operation == "list_recent_threads":
        assert any(item["thread_id"] == ROOT and item["subject"] == "A report" for item in payload)
    elif operation in ("list_drafts", "list_sent_since"):
        assert [item["subject"] for item in payload] == ["A report"]
    else:
        assert payload["thread_id"] == ROOT
        assert payload["subject"] == ("Re: A report" if operation == "create_draft" else "A report")
    if operation == "create_draft":
        assert payload["sent"] is False
        assert len(box.folders["Drafts"]) == 3


@pytest.mark.parametrize("length", [8191, 8192, 8193])
@pytest.mark.parametrize("prefix", [b"", b"\xff "])
def test_subject_decode_budget_preserves_complete_text(
    length: int, prefix: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoded = b"=?utf-8?q?Re=3A_x?="
    padding = b" " + b"a" * (length - len(prefix) - len(encoded) - 1)
    subject = prefix + encoded + padding
    if length > 8192:

        def unexpected_decode(*args: object, **kwargs: object) -> None:
            pytest.fail("Oversized subjects must bypass both decoders")

        monkeypatch.setattr(email.header, "decode_header", unexpected_decode)
        monkeypatch.setattr(email.policy.EmailPolicy, "header_fetch_parse", unexpected_decode)
    raw = b"Subject: " + subject + b"\r\n\r\n"
    expected = prefix.decode("ascii", errors="replace")
    expected += encoded.decode("ascii") if length > 8192 else "Re: x"
    expected += padding.decode("ascii")
    assert summarise(parse_message(raw)).subject == expected


@pytest.mark.parametrize("prefix", [b"", b"\xff "])
def test_subject_decode_budget_counts_after_unfolding(prefix: bytes) -> None:
    encoded = b"=?utf-8?q?Re=3A_x?="
    padding = b" " + b"a" * (8192 - len(prefix) - len(encoded) - 1)
    # Folding adds raw bytes but keeps the unfolded value at the decoding limit.
    raw = b"Subject: " + prefix + encoded + b"\r\n" + padding + b"\r\n\r\n"
    expected = prefix.decode("ascii", errors="replace") + "Re: x" + padding.decode("ascii")
    assert summarise(parse_message(raw)).subject == expected


@pytest.mark.parametrize("prefix", [b"", b"\xff "])
def test_read_operations_preserve_oversized_encoded_subject(prefix: bytes) -> None:
    subject = prefix + b"=?utf-8?q?Re=3A_x?=" + b"a" * 8192 + b"\fend"
    raw = f"Message-ID: {ROOT}\r\nFrom: reporter@example.org\r\nSubject: ".encode("ascii")
    raw += subject + b"\r\n\r\nbody"
    box = FakeMailbox.seed(INBOX=[raw], Drafts=[raw], Sent=[raw])
    config = make_config()
    expected = subject.decode("ascii", errors="replace").replace("\f", " ")
    assert list_recent_threads(box, config, since_days=90)[0]["subject"] == expected
    assert read_thread(box, config, ROOT, since_days=90)["subject"] == expected
    assert list_drafts(box, config, ROOT, since_days=90)[0]["subject"] == expected
    assert list_sent_since(box, config, ROOT, since_days=90)[0]["subject"] == expected


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(b"=?utf-8?q?x=0D=0ABcc:_evil@example.org?=", id="encoded-crlf"),
        pytest.param(b"=?utf-8?q?A=E2=80=A8B?=", id="encoded-ls"),
        pytest.param(b"=?utf-8?q?A=0BB?=", id="encoded-vt"),
    ],
)
def test_oversized_subject_preserves_draft_structure(
    encoded: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    padding = b"a" * (8193 - len(encoded) - 1)
    raw = (
        f"Message-ID: {ROOT}\r\nFrom: reporter@example.org\r\n"
        "Date: Mon, 21 Sep 2026 10:00:00 +0000\r\nSubject: "
    ).encode("ascii")
    raw += encoded + b" " + padding + b"\r\n\r\nexternal content\n"
    box = FakeMailbox.seed(
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
    config = make_config()
    body = "Thanks for the report.\n"
    assert (
        run_operation(
            lambda: create_draft(
                box, config, ROOT, body=body, to=[], cc=["triage@example.org"], from_address=None
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert len(box.folders["Drafts"]) == 1
    parsed = email.message_from_bytes(box.folders["Drafts"][result["draft_uid"]], policy=email.policy.default)
    assert len(parsed.get_all("Subject", [])) == 1
    assert parsed["Bcc"] is None
    assert str(parsed["Subject"]) == result["subject"]
    assert str(parsed["Subject"]).endswith(padding.decode("ascii"))
    assert parsed["From"] == config.user
    assert parsed["To"] == "reporter@example.org"
    assert parsed["Cc"] == "triage@example.org"
    assert parsed["In-Reply-To"] == REPLY
    assert parsed["References"] == f"{ROOT} {REPLY}"
    assert parsed.get_content() == body
    assert result["sent"] is False


@pytest.mark.parametrize("separator", [b"\v", b"\f", b"\x1c", b"\x1d", b"\x1e", b"\v\f\x1c\x1d\x1e"])
def test_raw_separators_next_to_encoded_words_preserve_word_boundaries(separator: bytes) -> None:
    raw = b"Subject: Re: x" + separator + b"=?utf-8?q?y?=\r\n\r\n"
    assert summarise(parse_message(raw)).subject == "Re: x y"


def test_utf8_bytes_split_across_adjacent_encoded_words() -> None:
    raw = b"Subject: =?utf-8?q?=C3?= =?utf-8?q?=A9?=\r\n\r\n"
    assert summarise(parse_message(raw)).subject == "\u00e9"


def test_malformed_base64_fallback_preserves_text_and_word_boundaries() -> None:
    raw = b"Subject: Re: x\f=?utf-8?b?A?=\r\n\r\n"
    assert summarise(parse_message(raw)).subject == "Re: x =?utf-8?b?A?="


@pytest.mark.parametrize(("encoded", "character"), [(b"=00", "\x00"), (b"=1B", "\x1b")])
def test_json_escapes_control_characters(
    encoded: bytes, character: str, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = b"Subject: =?utf-8?q?A" + encoded + b"report?=\r\n\r\n"
    subject = summarise(parse_message(raw)).subject
    print_json({"subject": subject})
    output = capsys.readouterr().out
    assert character not in output
    assert json.loads(output)["subject"] == f"A{character}report"
