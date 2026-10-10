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

import contextlib
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scan_ci_runners as scan


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / "out"
        self.cache = self.root / "cache"
        self.repos = ["example/retired", "example/clean"]
        self.files = {}
        for repo, runner in zip(self.repos, ["ubuntu-20.04", "ubuntu-24.04"], strict=True):
            path = self.root / f"{repo.split('/')[1]}.yml"
            # JSON is a YAML subset; the stdlib double keeps this suite runnable without PyYAML.
            path.write_text(json.dumps({"jobs": {"build": {"runs-on": runner}}}), encoding="utf-8")
            self.files[repo] = path
        self.list_failures: dict[str, int] = {}
        self.metadata_failures: dict[str, int] = {}
        self.missing_url = False
        self.unexpected_contents: object = None
        self.auth_failure = False
        self.org_failure = False
        self.invalid_json = False
        self.branch = "main"
        self.sha = "a" * 40
        self.commit_response: str | bytes | None = self.sha
        self.ref_failures: dict[str, int] = {}
        self.retry_recovers = False
        self.retry_failure: int | None = None
        self.org_output: str | None = None
        self.process_run = subprocess.run
        self.gh = mock.patch.object(scan.subprocess, "run", side_effect=self.gh_run).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(scan, "yaml", SimpleNamespace(safe_load=json.loads)).start()

    @staticmethod
    def completed(args, code, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args,
            code,
            stdout.encode("utf-8") if isinstance(stdout, str) else stdout,
            stderr.encode("utf-8") if isinstance(stderr, str) else stderr,
        )

    def gh_run(self, args, **kwargs):
        if args[1:3] == ["auth", "status"]:
            return self.completed(args, int(self.auth_failure), "", "authentication unavailable")
        endpoint = args[2]
        if endpoint == "--paginate":
            output = (
                self.org_output
                if self.org_output is not None
                else "\n".join(
                    json.dumps({"full_name": r, "default_branch": self.branch}, ensure_ascii=False)
                    for r in self.repos
                )
            )
            return self.completed(args, int(self.org_failure), output, "org list failed")
        if "/commits/" in endpoint:
            repo = endpoint.removeprefix("repos/").split("/commits/")[0]
            if repo in self.ref_failures:
                return self.completed(args, 1, "", f"gh: ref inaccessible (HTTP {self.ref_failures[repo]})")
            commit_output = (
                self.commit_response
                if "Accept: application/vnd.github.sha" in args
                else json.dumps({"sha": self.sha})
            )
            return self.completed(args, 0, commit_output, "")
        if "/contents/" in endpoint:
            repo = endpoint.removeprefix("repos/").split("/contents/")[0]
            if repo in self.list_failures:
                status = self.list_failures[repo]
                retry = endpoint.endswith(f"ref={self.sha}")
                if retry and self.retry_failure is not None:
                    return self.completed(args, 1, "", f"gh: retry failed (HTTP {self.retry_failure})")
                if not (retry and self.retry_recovers):
                    return self.completed(args, 1, "", f"gh: listing failed (HTTP {status})")
            contents = [
                {
                    "type": "file",
                    "path": ".github/workflows/ci.yml",
                    "download_url": None if self.missing_url else self.files[repo].as_uri(),
                }
            ]
            output = json.dumps(
                self.unexpected_contents if self.unexpected_contents is not None else contents,
                ensure_ascii=False,
            )
            return self.completed(args, 0, "invalid JSON" if self.invalid_json else output, "")
        repo = endpoint.removeprefix("repos/")
        if repo in self.metadata_failures:
            status = self.metadata_failures[repo]
            return self.completed(args, 1, "", f"gh: metadata failed (HTTP {status})")
        return self.completed(
            args, 0, json.dumps({"full_name": repo, "default_branch": self.branch}, ensure_ascii=False), ""
        )

    def invoke(self, *args: str) -> tuple[int, str]:
        stderr = io.StringIO()
        argv = ["scan", *args, "--out-dir", str(self.out), "--cache-dir", str(self.cache), "--workers", "2"]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
            code = scan.main()
        return code, stderr.getvalue()

    def rows(self, suffix: str, prefix: str = "example-retired") -> list[dict]:
        with (self.out / f"{prefix}-{suffix}.tsv").open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source, delimiter="\t"))

    def test_successful_retired_scan(self) -> None:
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertIn("scan_complete=true", stderr)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertIn("scan_errors=0", stderr)
        self.assertEqual(self.rows("retired-gh-runners-confirmed")[0]["runner"], "ubuntu-20.04")
        self.assertEqual(self.rows("scan-errors"), [])

    def test_complete_clean_scan(self) -> None:
        code, stderr = self.invoke("retired", "--repo", "example/clean")
        self.assertEqual(code, 0)
        self.assertIn("scan_complete=true", stderr)
        self.assertEqual(self.rows("retired-gh-runners-confirmed", "example-clean"), [])

    def test_listing_failure_preserves_successful_findings(self) -> None:
        for status in [403, 429, 500]:
            with self.subTest(status=status):
                self.list_failures = {"example/clean": status}
                code, stderr = self.invoke("retired", "--repo", "example/retired", "--repo", "example/clean")
                self.assertEqual(code, 1)
                self.assertIn("scan_complete=false", stderr)
                self.assertIn("workflows_scanned=1", stderr)
                self.assertIn(f"HTTP {status}", stderr)
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "repo-set")), 1)
                error = self.rows("scan-errors", "repo-set")[0]
                self.assertEqual(error["repo"], "example/clean")
                self.assertEqual(error["stage"], "workflow-list")

    def test_workflow_directory_404_is_empty(self) -> None:
        self.list_failures = {"example/retired": 404}
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=0", stderr)
        self.assertEqual(self.rows("scan-errors"), [])

    def test_metadata_404_is_an_error(self) -> None:
        self.metadata_failures = {"example/retired": 404}
        code, _ = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertEqual(self.rows("scan-errors")[0]["stage"], "metadata")

    def test_unexpected_listing_response_is_an_error(self) -> None:
        for response in [{"message": "unexpected"}, ["bad entry"]]:
            with self.subTest(response=response):
                self.unexpected_contents = response
                code, _ = self.invoke("retired", "--repo", "example/retired")
                self.assertEqual(code, 1)
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "workflow-list")

    def test_invalid_json_is_an_error(self) -> None:
        self.invalid_json = True
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("JSONDecodeError", stderr)

    def test_missing_download_url_is_an_error(self) -> None:
        self.missing_url = True
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=0", stderr)
        self.assertEqual(self.rows("scan-errors")[0]["path"], ".github/workflows/ci.yml")
        self.assertEqual(self.rows("scan-errors")[0]["stage"], "download")

    def test_download_failure_in_every_command(self) -> None:
        self.files["example/retired"].unlink()
        for command in ["retired", "macos-arch", "all"]:
            with self.subTest(command=command):
                code, stderr = self.invoke(command, "--repo", "example/retired")
                self.assertEqual(code, 1)
                self.assertIn("scan_errors=1", stderr)
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "download")

    def test_parse_failure_is_visible(self) -> None:
        self.files["example/retired"].write_text("jobs: [", encoding="utf-8")
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("scan_errors=1", stderr)
        self.assertEqual(self.rows("scan-errors")[0]["stage"], "parse")

    def test_non_mapping_yaml_is_an_error(self) -> None:
        for document in ['["not a workflow"]', "[]", "false", "0"]:
            with self.subTest(document=document):
                self.files["example/retired"].write_text(document, encoding="utf-8")
                code, _ = self.invoke("retired", "--repo", "example/retired")
                self.assertEqual(code, 1)
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "parse")

    def test_all_downloads_and_parses_once(self) -> None:
        document = {
            "jobs": {
                "build": {
                    "runs-on": "macos-13",
                    "steps": [{"uses": "actions/setup-python@v6", "with": {"architecture": "arm64"}}],
                }
            }
        }
        self.files["example/retired"].write_text(json.dumps(document), encoding="utf-8")
        with (
            mock.patch.object(scan, "fetch_text", wraps=scan.fetch_text) as fetch,
            mock.patch.object(scan, "yaml_load", wraps=scan.yaml_load) as parse,
        ):
            code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed")), 1)
        self.assertEqual(len(self.rows("macos-setup-action-arch-mismatches")), 1)

    def test_old_system_python_fails_before_gh(self) -> None:
        with mock.patch.object(scan.sys, "version_info", (3, 10)):
            code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 2)
        self.assertIn("Python 3.11", stderr)
        self.gh.assert_not_called()

    def test_missing_pyyaml_fails_before_gh(self) -> None:
        with mock.patch.object(scan, "yaml", None):
            code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 2)
        self.assertIn("PyYAML is required", stderr)
        self.gh.assert_not_called()
        self.assertFalse((self.out / "example-retired-retired-gh-runners-confirmed.tsv").exists())

    def test_authentication_failure_stops_before_listing(self) -> None:
        self.auth_failure = True
        code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 2)
        self.assertIn("scan_complete=false", stderr)
        self.assertEqual(self.gh.call_count, 1)
        self.assertFalse(self.cache.exists())

    def test_modern_auth_checks_only_the_active_account(self) -> None:
        response = self.gh_run

        def inactive_account_failure(args, **kwargs):
            if args[1:3] == ["auth", "status"] and "--active" not in args:
                return self.completed(args, 1, "", "inactive account has invalid credentials")
            return response(args, **kwargs)

        self.gh.side_effect = inactive_account_failure
        code, _ = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 0)
        auth = [call.args[0] for call in self.gh.call_args_list if call.args[0][1:3] == ["auth", "status"]]
        self.assertEqual(len(auth), 1)
        self.assertIn("--active", auth[0])

    def test_legacy_auth_retries_only_the_unsupported_active_flag(self) -> None:
        response = self.gh_run

        def legacy_auth(args, **kwargs):
            if args[1:3] == ["auth", "status"] and "--active" in args:
                return self.completed(args, 1, "", "unknown flag: --active\n\nUsage: gh auth status")
            return response(args, **kwargs)

        self.gh.side_effect = legacy_auth
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertIn("scan_complete=true", stderr)
        auth = [call.args[0] for call in self.gh.call_args_list if call.args[0][1:3] == ["auth", "status"]]
        self.assertEqual(len(auth), 2)
        self.assertEqual(auth[1], ["gh", "auth", "status", "--hostname", "github.com"])

    def test_legacy_auth_failure_remains_a_prerequisite_error(self) -> None:
        def failed_legacy_auth(args, **kwargs):
            message = "unknown flag: --active" if "--active" in args else "legacy credentials invalid"
            return self.completed(args, 1, "", message)

        self.gh.side_effect = failed_legacy_auth
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 2)
        self.assertIn("legacy credentials invalid", stderr)
        self.assertEqual(self.gh.call_count, 2)
        self.assertTrue(all(call.args[0][1:3] == ["auth", "status"] for call in self.gh.call_args_list))
        self.assertEqual(self.rows("scan-errors")[0]["stage"], "prerequisite")

    def test_unrelated_auth_failures_never_retry(self) -> None:
        for diagnostic in [
            "authentication unavailable",
            "unknown flag: --active-account",
            "authentication error mentions unknown flag: --active",
            "unknown flag: --active (HTTP 403)",
        ]:
            with self.subTest(diagnostic=diagnostic):
                self.gh.reset_mock()
                self.gh.side_effect = lambda args, diagnostic=diagnostic, **kwargs: self.completed(
                    args, 1, "", diagnostic
                )
                code, _ = self.invoke("all", "--repo", "example/retired")
                self.assertEqual(code, 2)
                self.assertEqual(self.gh.call_count, 1)

    def test_repository_file_scope_is_stable_when_authentication_fails(self) -> None:
        path = self.root / "repos.txt"
        cases = [
            ("example/retired\n", (), "example-retired"),
            ("example/retired\nexample/clean\n", (), "repo-set"),
            ("example/clean\n", ("--repo", "example/retired"), "repo-set"),
            ("example/retired\n", ("--repo", "example/retired"), "example-retired"),
            ("example/retired\n", ("--scope-name", "stable-audit"), "stable-audit"),
        ]
        for contents, extra, prefix in cases:
            with self.subTest(prefix=prefix, extra=extra, contents=contents):
                path.write_text(contents, encoding="utf-8")
                self.auth_failure = False
                self.assertEqual(self.invoke("all", "--repo-file", str(path), *extra)[0], 0)
                self.assertEqual(self.rows("scan-errors", prefix), [])
                findings = self.out / f"{prefix}-retired-gh-runners-confirmed.tsv"
                previous = findings.read_bytes()
                self.auth_failure = True
                code, stderr = self.invoke("all", "--repo-file", str(path), *extra)
                self.assertEqual(code, 2)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual(self.rows("scan-errors", prefix)[0]["stage"], "prerequisite")
                self.assertEqual(findings.read_bytes(), previous)

    def test_repository_file_scope_is_resolved_before_dependency_checks(self) -> None:
        path = self.root / "repos.txt"
        path.write_text("example/retired\n", encoding="utf-8")
        for missing in ["yaml", "python"]:
            with self.subTest(missing=missing):
                self.gh.reset_mock()
                dependency = (
                    mock.patch.object(scan, "yaml", None)
                    if missing == "yaml"
                    else mock.patch.object(scan.sys, "version_info", (3, 10))
                )
                with dependency:
                    code, _ = self.invoke("all", "--repo-file", str(path))
                self.assertEqual(code, 2)
                self.gh.assert_not_called()
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "prerequisite")

    def test_unreadable_and_empty_repo_files_stop_before_authentication(self) -> None:
        path = self.root / "repos.txt"
        code, _ = self.invoke("all", "--repo-file", str(path), "--scope-name", "stable-audit")
        self.assertEqual(code, 2)
        self.gh.assert_not_called()
        self.assertEqual(self.rows("scan-errors", "stable-audit")[0]["stage"], "prerequisite")
        path.write_text("# no repositories\n\n", encoding="utf-8")
        code, stderr = self.invoke("all", "--repo-file", str(path))
        self.assertEqual(code, 2)
        self.assertIn("repository list is empty", stderr)
        self.gh.assert_not_called()
        self.assertEqual(self.rows("scan-errors", "repo-set")[0]["stage"], "prerequisite")

    def test_utf8_bom_repo_file_accepts_comments_or_first_repository(self) -> None:
        path = self.root / "repos.txt"
        for contents in ["# 仓库\nexample/retired\n", "example/retired\n"]:
            with self.subTest(contents=contents):
                path.write_text(contents, encoding="utf-8-sig")
                code, stderr = self.invoke("all", "--repo-file", str(path))
                self.assertEqual(code, 0)
                self.assertIn("workflows_scanned=1", stderr)
                self.assertEqual(self.rows("scan-errors"), [])

    def test_empty_repository_is_complete_and_cacheable(self) -> None:
        response = self.gh_run

        def empty_repository(args, **kwargs):
            endpoint = args[2]
            if endpoint.startswith("repos/example/clean/contents/"):
                return self.completed(args, 1, "", "gh: This repository is empty. (HTTP 404)")
            if endpoint.startswith("repos/example/clean/commits/"):
                return self.completed(args, 1, "", "gh: Git Repository is empty. (HTTP 409)")
            return response(args, **kwargs)

        self.gh.side_effect = empty_repository
        code, stderr = self.invoke("all", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(self.rows("scan-errors", "example"), [])
        self.assertTrue((self.cache / "example-workflow-files-v3.tsv").exists())
        self.assertFalse(any(f"ref={self.sha}" in call.args[0][2] for call in self.gh.call_args_list))
        self.gh.reset_mock()
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        self.assertEqual(self.gh.call_count, 1)
        self.repos = ["example/clean"]
        code, stderr = self.invoke("all", "--owner", "example", "--refresh")
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=0", stderr)
        self.assertEqual(self.rows("scan-errors", "example"), [])
        self.assertEqual(self.rows("retired-gh-runners-confirmed", "example"), [])

    def test_conflicts_or_permission_errors_are_not_empty_repositories(self) -> None:
        self.list_failures = {"example/retired": 404}
        response = self.gh_run
        cases = [
            (409, message)
            for message in ["Conflict", "not Git Repository is empty.", "Git Repository is empty. maybe"]
        ]
        cases += [(status, "Git Repository is empty.") for status in [401, 403, 404, 429, 500]]
        for status, message in cases:
            with self.subTest(status=status, message=message):

                def invalid_verification(args, *, message=message, status=status, **kwargs):
                    if "/commits/" in args[2]:
                        return self.completed(args, 1, "", f"gh: {message} (HTTP {status})")
                    return response(args, **kwargs)

                self.gh.side_effect = invalid_verification
                code, stderr = self.invoke("all", "--owner", "example", "--refresh")
                self.assertEqual(code, 1)
                self.assertIn("cannot verify ref", stderr)
                self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())

    def test_stale_cached_404_has_refresh_hint_and_manual_refresh_recovers(self) -> None:
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        fetch = scan.fetch_text

        def removed_workflow(url):
            if url == self.files["example/retired"].as_uri():
                raise HTTPError(url, 404, "Not Found", Message(), None)
            return fetch(url)

        response = self.gh_run

        def current_listing(args, **kwargs):
            if args[2].startswith("repos/example/retired/contents/"):
                return self.completed(args, 0, "[]", "")
            return response(args, **kwargs)

        self.gh.side_effect = current_listing
        with mock.patch.object(scan, "fetch_text", side_effect=removed_workflow):
            self.gh.reset_mock()
            code, stderr = self.invoke("all", "--owner", "example")
            self.assertEqual(code, 1)
            self.assertIn("workflows_scanned=1", stderr)
            self.assertEqual(self.gh.call_count, 1)  # No automatic re-enumeration.
            error = self.rows("scan-errors", "example")[0]
            self.assertEqual(error["stage"], "download")
            self.assertIn("inventory may be stale", error["error"])
            self.assertIn("--refresh", error["error"])
            self.assertEqual(self.invoke("all", "--owner", "example", "--refresh")[0], 0)
            self.assertEqual(self.rows("scan-errors", "example"), [])
            self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)

    def test_refresh_hints_are_not_added_for_other_failures_or_scopes(self) -> None:
        cases = [
            (403, ("--owner", "example"), "example"),
            (429, ("--owner", "example"), "example"),
            (500, ("--owner", "example"), "example"),
            (404, ("--repo", "example/retired"), "example-retired"),
            (404, ("--owner", "example", "--refresh"), "example"),
        ]
        for status, scope, prefix in cases:
            with self.subTest(status=status, scope=scope):
                error = HTTPError("https://example.invalid/ci.yml", status, "failed", Message(), None)
                with mock.patch.object(scan, "fetch_text", side_effect=error):
                    code, _ = self.invoke("all", *scope)
                self.assertEqual(code, 1)
                self.assertTrue(
                    all("--refresh" not in row["error"] for row in self.rows("scan-errors", prefix))
                )

    def test_paginated_errors_include_endpoint_without_request_headers(self) -> None:
        self.gh.side_effect = None
        endpoint = "/orgs/example/repos?per_page=100&type=public"
        args = ["gh", "api", "--paginate", endpoint, "--jq", ".[]", "-H", "Authorization: private-test-value"]
        diagnostic = "first line\nsecond\tline (HTTP 403)"
        self.gh.return_value = self.completed(args, 1, "", diagnostic)
        with self.assertRaises(scan.GhError) as failure:
            scan.run(args)
        self.assertIn(endpoint, str(failure.exception))
        self.assertIn(diagnostic, str(failure.exception))
        self.assertNotIn("private-test-value", str(failure.exception))
        for stdout in [None, b"\xff"]:
            with self.subTest(stdout=stdout):
                self.gh.return_value = self.completed(args, 0, stdout, "")
                with self.assertRaises(scan.ScanError) as invalid_response:
                    scan.run(args)
                self.assertIn(endpoint, str(invalid_response.exception))
                self.assertNotIn("private-test-value", str(invalid_response.exception))

    def test_multiline_errors_roundtrip_through_tsv(self) -> None:
        diagnostic = 'first\nsecond\tcolumn "quoted"\r\nlast (HTTP 403)'
        self.gh.side_effect = lambda args, **kwargs: self.completed(args, 1, "", diagnostic)
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 2)
        rows = self.rows("scan-errors")
        self.assertEqual(len(rows), 1)
        self.assertIn(diagnostic, rows[0]["error"])
        self.assertIn(diagnostic, stderr)

    def test_utf8_repo_file_accepts_comments_and_blank_lines(self) -> None:
        path = self.root / "repos.txt"
        path.write_text("# 仓库\n\n example/retired \nexample/clean\n", encoding="utf-8")
        code, stderr = self.invoke("all", "--repo-file", str(path))
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "repo-set")), 1)
        self.assertEqual(self.rows("scan-errors", "repo-set"), [])

    def test_non_utf8_repo_files_are_prerequisite_errors(self) -> None:
        path = self.root / "repos.txt"
        contents = {
            "invalid-byte": b"example/retired\n\xff\n",
            "utf16": "example/retired\n".encode("utf-16"),
            "gbk-comment": "# 仓库\nexample/retired\n".encode("gbk"),
        }
        for name, content in contents.items():
            with self.subTest(encoding=name):
                path.write_bytes(content)
                self.gh.reset_mock()
                code, stderr = self.invoke("all", "--repo-file", str(path))
                self.assertEqual(code, 2)
                self.assertIn("scan_complete=false", stderr)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertIn("scan_errors=1", stderr)
                self.gh.assert_not_called()
                self.assertFalse(self.cache.exists())
                self.assertFalse((self.out / "repo-set-retired-gh-runners-confirmed.tsv").exists())
                errors = self.rows("scan-errors", "repo-set")
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["stage"], "prerequisite")
                self.assertIn("must use UTF-8", errors[0]["error"])
                self.assertIn(str(path), errors[0]["error"])

    def test_invalid_repo_file_does_not_reuse_old_findings(self) -> None:
        path = self.root / "repos.txt"
        path.write_text("example/retired\n", encoding="utf-8")
        self.assertEqual(self.invoke("all", "--repo-file", str(path), "--scope-name", "repo-set")[0], 0)
        findings = self.out / "repo-set-retired-gh-runners-confirmed.tsv"
        previous = findings.read_bytes()
        path.write_bytes(b"\xff")
        self.gh.reset_mock()
        with mock.patch.object(scan, "fetch_text", wraps=scan.fetch_text) as fetch:
            code, stderr = self.invoke("all", "--repo-file", str(path), "--scope-name", "repo-set")
        self.assertEqual(code, 2)
        self.assertIn("workflows_scanned=0", stderr)
        self.gh.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(findings.read_bytes(), previous)
        self.assertEqual(self.rows("scan-errors", "repo-set")[0]["stage"], "prerequisite")

    def test_missing_gh_fails_before_listing(self) -> None:
        with mock.patch.object(scan.subprocess, "run", side_effect=FileNotFoundError("gh unavailable")):
            code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 2)
        self.assertIn("gh unavailable", stderr)

    def test_org_discovery_failure_does_not_cache_partial_stdout(self) -> None:
        self.org_failure = True
        code, stderr = self.invoke("all", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("org list failed", stderr)
        self.assertFalse((self.cache / "example-repos-v2.jsonl").exists())
        self.assertEqual(self.gh.call_count, 2)

    def test_incomplete_inventory_is_not_cached_and_recovers_without_refresh(self) -> None:
        self.list_failures = {"example/retired": 403}
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())
        self.list_failures = {}
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertTrue((self.cache / "example-workflow-files-v3.tsv").exists())

    def test_old_inventory_is_rebuilt(self) -> None:
        self.cache.mkdir()
        old = self.cache / "example-workflow-files.tsv"
        old.write_text("repo\tpath\n", encoding="utf-8")
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(old.read_text(encoding="utf-8"), "repo\tpath\n")

    def test_refresh_failure_preserves_complete_inventory(self) -> None:
        self.invoke("retired", "--owner", "example")
        cache = self.cache / "example-workflow-files-v3.tsv"
        complete = cache.read_bytes()
        self.list_failures = {"example/retired": 403}
        code, stderr = self.invoke("retired", "--owner", "example", "--refresh")
        self.assertEqual(code, 1)
        self.assertIn("scan_errors=1", stderr)
        self.assertEqual(cache.read_bytes(), complete)
        self.assertEqual(self.rows("retired-gh-runners-confirmed", "example"), [])

    def test_complete_cache_is_reused(self) -> None:
        self.invoke("retired", "--owner", "example")
        self.gh.reset_mock()
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(self.gh.call_count, 1)  # Only the authentication preflight.

    def test_atomic_cache_write_failure_preserves_previous_inventory(self) -> None:
        self.invoke("retired", "--owner", "example")
        cache = self.cache / "example-workflow-files-v3.tsv"
        complete = cache.read_bytes()
        before = set(self.cache.iterdir())
        replace = scan.os.replace

        def fail_workflow_replace(source, target):
            if target == cache:
                raise OSError("replace failed")
            return replace(source, target)

        with mock.patch.object(scan.os, "replace", side_effect=fail_workflow_replace):
            code, stderr = self.invoke("retired", "--owner", "example", "--refresh")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(self.rows("scan-errors", "example")[0]["stage"], "cache")
        self.assertEqual(cache.read_bytes(), complete)
        self.assertEqual(set(self.cache.iterdir()), before)

    def test_invalid_cached_header_is_rebuilt(self) -> None:
        self.cache.mkdir()
        (self.cache / "example-workflow-files-v3.tsv").write_text("bad-header\n", encoding="utf-8")
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)

    def test_invalid_repo_metadata_is_an_error(self) -> None:
        with self.assertRaises(scan.ScanError):
            scan.list_workflows_for_repo({"full_name": "example/retired"})

    def test_malformed_metadata_is_not_an_empty_repository(self) -> None:
        for metadata in [None, {}, {"full_name": None, "default_branch": "main"}]:
            with (
                self.subTest(metadata=metadata),
                mock.patch.object(scan, "gh_json", return_value=metadata),
                self.assertRaises(scan.ScanError),
            ):
                scan.load_repo("example/retired")

    def test_output_failure_is_not_a_success(self) -> None:
        self.out.write_text("not a directory", encoding="utf-8")
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("scan_complete=false", stderr)
        self.assertIn("[output]", stderr)

    def test_invalid_or_inaccessible_ref_404_is_not_an_empty_directory(self) -> None:
        self.list_failures = {"example/retired": 404}
        for status in [404, 403, 429, 500]:
            with self.subTest(status=status):
                self.ref_failures = {"example/retired": status}
                code, stderr = self.invoke("retired", "--owner", "example")
                self.assertEqual(code, 1)
                self.assertIn("workflows_scanned=1", stderr)
                self.assertIn("listing failed (HTTP 404)", stderr)
                self.assertIn(f"ref inaccessible (HTTP {status})", stderr)
                self.assertEqual(self.rows("scan-errors", "example")[0]["stage"], "workflow-list")
                self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())
        self.list_failures = {}
        self.ref_failures = {}
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)

    def test_stale_cached_default_branch_requires_metadata_refresh(self) -> None:
        self.cache.mkdir()
        (self.cache / "example-repos-v2.jsonl").write_text(
            json.dumps({"full_name": "example/retired", "default_branch": "deleted-old-branch"}),
            encoding="utf-8",
        )
        response = self.gh_run

        def stale_ref(args, **kwargs):
            if "deleted-old-branch" in args[2]:
                return self.completed(args, 1, "", "gh: No commit found for the ref (HTTP 404)")
            return response(args, **kwargs)

        self.gh.side_effect = stale_ref
        code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("scan_complete=false", stderr)
        self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())
        code, _ = self.invoke("retired", "--owner", "example", "--refresh")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)

    def test_404_ref_validation_rejects_abnormal_responses(self) -> None:
        self.list_failures = {"example/retired": 404}
        responses = ["", " \n\t", "a" * 39, "a" * 41, "g" * 40, json.dumps({"sha": self.sha})]
        for response in responses:
            with self.subTest(response=response):
                self.commit_response = response
                code, stderr = self.invoke("all", "--repo", "example/retired")
                self.assertEqual(code, 1)
                self.assertIn("cannot verify ref", stderr)
                self.assertIn("listing failed (HTTP 404)", stderr)
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "workflow-list")
                self.assertFalse(any(f"ref={self.sha}" in call.args[0][2] for call in self.gh.call_args_list))

    def test_404_ref_validation_rejects_invalid_utf8_and_missing_stdout(self) -> None:
        self.list_failures = {"example/retired": 404}
        for response in [b"\xff", None]:
            with self.subTest(response=response):
                self.commit_response = response
                code, stderr = self.invoke("retired", "--repo", "example/retired")
                self.assertEqual(code, 1)
                self.assertIn("cannot verify ref", stderr)
                self.assertIn("listing failed (HTTP 404)", stderr)
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "workflow-list")

    def test_404_ref_validation_accepts_sha_text_with_surrounding_whitespace(self) -> None:
        self.sha = "Ab" * 20
        self.commit_response = f" \n{self.sha}\t\n"
        self.list_failures = {"example/retired": 404}
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertIn("scan_complete=true", stderr)
        self.assertEqual(self.rows("scan-errors"), [])
        api_calls = [call.args[0] for call in self.gh.call_args_list if call.args[0][1] == "api"]
        self.assertEqual(len(api_calls), 4)  # Metadata, directory, ref, SHA directory.
        self.assertEqual(api_calls[-1][2], f"repos/example/retired/contents/.github/workflows?ref={self.sha}")

    def test_404_retry_at_verified_sha_can_recover(self) -> None:
        self.list_failures = {"example/retired": 404}
        self.retry_recovers = True
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed")), 1)
        endpoints = [call.args[0][2] for call in self.gh.call_args_list]
        self.assertIn(f"repos/example/retired/contents/.github/workflows?ref={self.sha}", endpoints)

    def test_verified_sha_retry_failures_are_not_empty_results(self) -> None:
        self.list_failures = {"example/retired": 404}
        for status in [403, 429, 500]:
            with self.subTest(status=status):
                self.retry_failure = status
                code, stderr = self.invoke("all", "--repo", "example/retired")
                self.assertEqual(code, 1)
                self.assertIn("listing failed (HTTP 404)", stderr)
                self.assertIn(f"retry failed (HTTP {status})", stderr)
        self.retry_failure = None
        self.retry_recovers = True
        self.invalid_json = True
        code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("directory retry", stderr)

    def test_ref_is_encoded_in_both_directory_and_commit_requests(self) -> None:
        self.branch = "feature/ci+#"
        self.list_failures = {"example/retired": 404}
        self.retry_recovers = True
        code, _ = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 0)
        endpoints = [call.args[0][2] for call in self.gh.call_args_list]
        encoded = quote(self.branch, safe="")
        self.assertIn(f"repos/example/retired/contents/.github/workflows?ref={encoded}", endpoints)
        self.assertIn(f"repos/example/retired/commits/{encoded}", endpoints)
        self.assertIn(encoded, self.rows("retired-gh-runners-confirmed")[0]["html_url"])

    def test_successful_listing_does_not_probe_ref(self) -> None:
        code, _ = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertFalse(any("/commits/" in call.args[0][2] for call in self.gh.call_args_list))

    def test_malformed_entries_preserve_valid_workflows(self) -> None:
        self.unexpected_contents = [
            {},
            {"type": "file"},
            {"path": ".github/workflows/ci.yml"},
            {
                "type": "file",
                "path": ".github/workflows/ci.yml",
                "download_url": self.files[self.repos[0]].as_uri(),
            },
        ]
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertIn("scan_errors=3", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed")), 1)
        self.assertTrue(all(row["stage"] == "workflow-list" for row in self.rows("scan-errors")))

    def test_malformed_entries_do_not_publish_inventory_and_recover(self) -> None:
        for item in [
            {},
            {"type": "file"},
            {"path": ".github/workflows/ci.yml"},
            {"type": "file", "path": None},
            {"type": [], "path": "ci.yml"},
            {"type": "unknown", "path": "ci.yml"},
            {"type": "file", "path": ""},
        ]:
            with self.subTest(item=item):
                self.unexpected_contents = [item]
                code, stderr = self.invoke("retired", "--owner", "example")
                self.assertEqual(code, 1)
                self.assertIn("scan_complete=false", stderr)
                self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())
        self.unexpected_contents = None
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)

    def test_valid_non_workflow_entries_and_empty_list_are_skipped(self) -> None:
        for contents in [
            [],
            [
                {"type": "file", "path": ".github/workflows/README.md"},
                {"type": "dir", "path": ".github/workflows/nested"},
                {"type": "symlink", "path": ".github/workflows/link.yml"},
                {"type": "submodule", "path": ".github/workflows/module"},
            ],
        ]:
            with self.subTest(contents=contents):
                self.unexpected_contents = contents
                code, stderr = self.invoke("all", "--repo", "example/retired")
                self.assertEqual(code, 0)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual(self.rows("scan-errors"), [])

    def test_non_string_download_url_is_not_cached(self) -> None:
        self.unexpected_contents = [{"type": "file", "path": ".github/workflows/ci.yml", "download_url": 123}]
        code, _ = self.invoke("all", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertTrue(all(row["stage"] == "download" for row in self.rows("scan-errors", "example")))
        self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())

    def test_legacy_partial_repos_and_v2_workflows_are_rebuilt(self) -> None:
        self.cache.mkdir()
        old_repos = self.cache / "example-repos.jsonl"
        old_workflows = self.cache / "example-workflow-files-v2.tsv"
        old_repos.write_text(
            json.dumps({"full_name": "example/clean", "default_branch": "main"}), encoding="utf-8"
        )
        old_workflows.write_text("\t".join(scan.WORKFLOW_FIELDS) + "\n", encoding="utf-8")
        originals = [path.read_bytes() for path in [old_repos, old_workflows]]
        code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertTrue((self.cache / "example-repos-v2.jsonl").exists())
        self.assertTrue((self.cache / "example-workflow-files-v3.tsv").exists())
        self.assertEqual([path.read_bytes() for path in [old_repos, old_workflows]], originals)

    def test_interrupted_repo_write_never_publishes_valid_prefix(self) -> None:
        self.repos.reverse()
        write_text = Path.write_text

        def interrupt(path, text, *args, **kwargs):
            if path.parent == self.cache:
                write_text(path, text.splitlines()[0] + "\n", *args, **kwargs)
                raise OSError("interrupted repository write")
            return write_text(path, text, *args, **kwargs)

        with mock.patch.object(Path, "write_text", new=interrupt):
            code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(list(self.cache.iterdir()), [])
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(len((self.cache / "example-repos-v2.jsonl").read_text().splitlines()), 2)

    def test_failed_repo_replace_preserves_previous_complete_cache(self) -> None:
        self.invoke("retired", "--owner", "example")
        repo_cache = self.cache / "example-repos-v2.jsonl"
        workflow_cache = self.cache / "example-workflow-files-v3.tsv"
        originals = [repo_cache.read_bytes(), workflow_cache.read_bytes()]
        files = set(self.cache.iterdir())
        self.repos.reverse()
        replace = scan.os.replace

        def fail_repo_replace(source, target):
            if target == repo_cache:
                raise OSError("repository replace failed")
            return replace(source, target)

        with mock.patch.object(scan.os, "replace", side_effect=fail_repo_replace):
            code, stderr = self.invoke("retired", "--owner", "example", "--refresh")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(self.rows("scan-errors", "example")[0]["path"], repo_cache.name)
        self.assertEqual([repo_cache.read_bytes(), workflow_cache.read_bytes()], originals)
        self.assertEqual(set(self.cache.iterdir()), files)

    def test_corrupt_new_repo_cache_is_rebuilt(self) -> None:
        self.cache.mkdir()
        cache = self.cache / "example-repos-v2.jsonl"
        for contents in [b"{", b"\xff", b"null\n", b'{"full_name":"example/clean"}\n']:
            with self.subTest(contents=contents):
                cache.write_bytes(contents)
                repos, errors = scan.load_repos(self.cache, "example", False)
                self.assertEqual(len(repos), 2)
                self.assertEqual(errors, [])
                self.assertEqual(len(cache.read_text().splitlines()), 2)

    def test_bad_org_metadata_preserves_other_repositories(self) -> None:
        self.org_output = json.dumps({"full_name": "example/retired", "default_branch": "main"}) + "\n{}\n"
        code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(self.rows("scan-errors", "example")[0]["stage"], "metadata")
        self.assertFalse((self.cache / "example-repos-v2.jsonl").exists())
        self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())

    def test_unusable_cache_directory_does_not_discard_enumerated_results(self) -> None:
        self.cache.write_text("not a directory", encoding="utf-8")
        code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual(self.rows("scan-errors", "example")[0]["stage"], "cache")

    def test_interrupted_workflow_cache_write_preserves_results_and_recovers(self) -> None:
        write_tsv = scan.write_tsv

        def interrupt(path, rows, fields):
            if path.parent == self.cache:
                write_tsv(path, [], fields)
                raise OSError("interrupted workflow write")
            return write_tsv(path, rows, fields)

        with mock.patch.object(scan, "write_tsv", side_effect=interrupt):
            code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertEqual([path.name for path in self.cache.iterdir()], ["example-repos-v2.jsonl"])
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertTrue((self.cache / "example-workflow-files-v3.tsv").exists())

    def test_cache_failure_uses_current_results_not_previous_inventory(self) -> None:
        self.invoke("retired", "--owner", "example")
        cache = self.cache / "example-workflow-files-v3.tsv"
        original = cache.read_bytes()
        current = self.root / "current.yml"
        current.write_text(json.dumps({"jobs": {"build": {"runs-on": "ubuntu-24.04"}}}), encoding="utf-8")
        self.files["example/retired"] = current
        replace = scan.os.replace

        def fail_workflow_replace(source, target):
            if target == cache:
                raise OSError("workflow replace failed")
            return replace(source, target)

        with mock.patch.object(scan.os, "replace", side_effect=fail_workflow_replace):
            code, stderr = self.invoke("retired", "--owner", "example", "--refresh")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=2", stderr)
        self.assertEqual(self.rows("retired-gh-runners-confirmed", "example"), [])
        self.assertEqual(cache.read_bytes(), original)

    def test_corrupt_new_workflow_cache_is_rebuilt(self) -> None:
        self.cache.mkdir()
        cache = self.cache / "example-workflow-files-v3.tsv"
        header = "\t".join(scan.WORKFLOW_FIELDS) + "\n"
        documents: list[str | bytes] = [
            header + "example/clean\tmain\tci.yml\t\n",
            header + "a\tb\tc\td\te\tf\n",
            header + '"unterminated',
            header.encode() + b"\xff",
        ]
        for contents in documents:
            with self.subTest(contents=contents):
                cache.write_bytes(contents.encode() if isinstance(contents, str) else contents)
                code, stderr = self.invoke("retired", "--owner", "example")
                self.assertEqual(code, 0)
                self.assertIn("workflows_scanned=2", stderr)
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)

    def test_utf8_response_preserves_non_ascii_workflow_path(self) -> None:
        self.unexpected_contents = [
            {
                "type": "file",
                "path": ".github/workflows/构建.yml",
                "download_url": self.files["example/retired"].as_uri(),
            }
        ]
        code, _ = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertEqual(self.rows("retired-gh-runners-confirmed")[0]["path"], ".github/workflows/构建.yml")

    def test_utf8_failure_diagnostic_preserves_http_status(self) -> None:
        response = self.gh_run

        def non_ascii_failure(args, **kwargs):
            if "/contents/" in args[2]:
                return self.completed(args, 1, "", "gh: ✓ 请求失败 (HTTP 403)")
            return response(args, **kwargs)

        self.gh.side_effect = non_ascii_failure
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("✓ 请求失败 (HTTP 403)", stderr)
        self.assertEqual(self.rows("scan-errors")[0]["stage"], "workflow-list")

    def test_invalid_utf8_org_response_is_not_cached(self) -> None:
        response = self.gh_run

        def invalid_response(args, **kwargs):
            if args[2] == "--paginate":
                return self.completed(args, 0, b"\xff", b"")
            return response(args, **kwargs)

        self.gh.side_effect = invalid_response
        code, stderr = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("invalid UTF-8", stderr)
        self.assertFalse((self.cache / "example-repos-v2.jsonl").exists())

    def test_invalid_utf8_and_missing_streams_are_structured_prerequisite_errors(self) -> None:
        self.gh.side_effect = None
        for code, stdout, stderr in [
            (0, b"\xff", b""),
            (0, None, None),
            (1, None, None),
            (1, b"", b"\xff"),
            (1, b"failure", None),
        ]:
            with self.subTest(code=code, stdout=stdout, stderr=stderr):
                self.gh.return_value = self.completed([], code, stdout, stderr)
                result, summary = self.invoke("retired", "--repo", "example/retired")
                self.assertEqual(result, 2)
                self.assertIn("scan_complete=false", summary)
                self.assertEqual(self.rows("scan-errors")[0]["stage"], "prerequisite")

    def test_malformed_diagnostic_bytes_keep_failure_and_http_status(self) -> None:
        self.gh.side_effect = lambda args, **kwargs: self.completed(args, 1, b"\xff", b"\xff (HTTP 403)")
        with self.assertRaises(scan.GhError) as error:
            scan.run(["gh", "api", "repos/example/retired"])
        self.assertEqual(error.exception.status, 403)
        self.assertIn("(HTTP 403)", str(error.exception))

    def test_real_utf8_subprocesses_are_independent_of_parent_locale(self) -> None:
        self.branch = "feature/中文"
        response = self.gh_run

        def real_child(args, **kwargs):
            completed = response(args, **kwargs)
            stderr = "✓ authentication status\n".encode() if args[1] == "auth" else completed.stderr
            producer = (
                f"import sys; sys.stdout.buffer.write({completed.stdout!r}); "
                f"sys.stderr.buffer.write({stderr!r}); sys.exit({completed.returncode})"
            )
            return self.process_run([sys.executable, "-c", producer], **kwargs)

        self.gh.side_effect = real_child
        code, _ = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed")), 1)
        self.auth_failure = True
        code, stderr = self.invoke("retired", "--repo", "example/retired")
        self.assertEqual(code, 2)
        self.assertIn("✓ authentication status", stderr)
        self.assertEqual(self.rows("scan-errors")[0]["stage"], "prerequisite")

    def test_all_preserves_retired_findings_when_arch_check_fails(self) -> None:
        with mock.patch.object(scan, "arch_hits", side_effect=RuntimeError("arch failure")):
            code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=0", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed")), 1)
        error = self.rows("scan-errors")[0]
        self.assertEqual(error["stage"], "check")
        self.assertIn("macos-arch: RuntimeError: arch failure", error["error"])

    def test_all_runs_arch_check_when_retired_check_fails(self) -> None:
        self.files["example/retired"].write_text(
            json.dumps(
                {
                    "jobs": {
                        "build": {
                            "runs-on": "macos-13",
                            "steps": [{"uses": "actions/setup-python@v6", "with": {"architecture": "arm64"}}],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(scan, "retired_hits", side_effect=RuntimeError("retired failure")),
            mock.patch.object(scan, "fetch_text", wraps=scan.fetch_text) as fetch,
            mock.patch.object(scan, "yaml_load", wraps=scan.yaml_load) as parse,
        ):
            code, stderr = self.invoke("all", "--repo", "example/retired")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=0", stderr)
        self.assertEqual(len(self.rows("macos-setup-action-arch-mismatches")), 1)
        self.assertIn("retired: RuntimeError: retired failure", self.rows("scan-errors")[0]["error"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(parse.call_count, 1)

    def test_single_check_does_not_execute_unselected_check(self) -> None:
        for command, other in [("retired", "arch_hits"), ("macos-arch", "retired_hits")]:
            with self.subTest(command=command), mock.patch.object(scan, other) as unselected:
                code, stderr = self.invoke(command, "--repo", "example/retired")
                self.assertEqual(code, 0)
                self.assertIn("workflows_scanned=1", stderr)
                unselected.assert_not_called()

    def test_check_failure_preserves_other_workflows_and_completion_count(self) -> None:
        arch = scan.arch_hits

        def fail_one(workflow, data):
            if workflow["repo"] == "example/retired":
                raise RuntimeError("one check failed")
            return arch(workflow, data)

        with mock.patch.object(scan, "arch_hits", side_effect=fail_one):
            code, stderr = self.invoke("all", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertIn("scan_errors=1", stderr)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)


if __name__ == "__main__":
    unittest.main()
