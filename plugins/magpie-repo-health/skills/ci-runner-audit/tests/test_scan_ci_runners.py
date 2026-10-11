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
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
        self.gh = mock.patch.object(scan.subprocess, "run", side_effect=self.gh_run).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(scan, "yaml", SimpleNamespace(safe_load=json.loads)).start()

    def gh_run(self, args, **kwargs):
        if args[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(args, int(self.auth_failure), "", "authentication unavailable")
        endpoint = args[2]
        if endpoint == "--paginate":
            output = "\n".join(json.dumps({"full_name": r, "default_branch": "main"}) for r in self.repos)
            return subprocess.CompletedProcess(args, int(self.org_failure), output, "org list failed")
        if "/contents/" in endpoint:
            repo = endpoint.removeprefix("repos/").split("/contents/")[0]
            if repo in self.list_failures:
                status = self.list_failures[repo]
                return subprocess.CompletedProcess(args, 1, "", f"gh: listing failed (HTTP {status})")
            contents = [
                {
                    "type": "file",
                    "path": ".github/workflows/ci.yml",
                    "download_url": None if self.missing_url else self.files[repo].as_uri(),
                }
            ]
            output = json.dumps(
                self.unexpected_contents if self.unexpected_contents is not None else contents
            )
            return subprocess.CompletedProcess(args, 0, "invalid JSON" if self.invalid_json else output, "")
        repo = endpoint.removeprefix("repos/")
        if repo in self.metadata_failures:
            status = self.metadata_failures[repo]
            return subprocess.CompletedProcess(args, 1, "", f"gh: metadata failed (HTTP {status})")
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"full_name": repo, "default_branch": "main"}), ""
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
        self.assertFalse((self.cache / "example-repos.jsonl").exists())
        self.assertEqual(self.gh.call_count, 2)

    def test_incomplete_inventory_is_not_cached_and_recovers_without_refresh(self) -> None:
        self.list_failures = {"example/retired": 403}
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertFalse((self.cache / "example-workflow-files-v2.tsv").exists())
        self.list_failures = {}
        code, _ = self.invoke("retired", "--owner", "example")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
        self.assertTrue((self.cache / "example-workflow-files-v2.tsv").exists())

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
        cache = self.cache / "example-workflow-files-v2.tsv"
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
        cache = self.cache / "example-workflow-files-v2.tsv"
        complete = cache.read_bytes()
        before = set(self.cache.iterdir())
        with mock.patch.object(scan.os, "replace", side_effect=OSError("replace failed")):
            code, _ = self.invoke("retired", "--owner", "example", "--refresh")
        self.assertEqual(code, 1)
        self.assertEqual(cache.read_bytes(), complete)
        self.assertEqual(set(self.cache.iterdir()), before)

    def test_invalid_cached_header_is_rebuilt(self) -> None:
        self.cache.mkdir()
        (self.cache / "example-workflow-files-v2.tsv").write_text("bad-header\n", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
