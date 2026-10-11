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
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scan_ci_runners as scan


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out, self.cache = self.root / "out", self.root / "cache"
        self.names = ["example/good", "example/clean"]
        self.branch, self.sha = "main", "a" * 40
        self.files = {}
        for name, label in zip(self.names, ["ubuntu-20.04", "ubuntu-24.04"], strict=True):
            path = self.root / (name.split("/")[1] + ".yml")
            path.write_text(json.dumps({"jobs": {"build": {"runs-on": label}}}), encoding="utf-8")
            self.files[name] = path
        self.responses: dict = {}
        self.calls: list[str] = []
        self.unknown: list[list[str]] = []
        self.addCleanup(lambda: self.assertEqual(self.unknown, [], "Unknown gh endpoint"))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(scan.shutil, "which", return_value="/mock/gh").start()
        mock.patch.object(scan, "yaml", SimpleNamespace(safe_load=json.loads)).start()
        self.gh = mock.patch.object(scan.subprocess, "run", side_effect=self.gh_run).start()

    @staticmethod
    def completed(args, code=0, stdout=b"", stderr=b""):
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    def failed(self, status, diagnostic="request failed"):
        return self.completed([], 1, stderr=f"gh: {diagnostic} (HTTP {status})".encode())

    def gh_run(self, args, **kwargs):
        endpoint = args[3] if args[:3] == ["gh", "api", "--paginate"] else args[2]
        kind = ""
        value: Any = None
        if args[:2] == ["gh", "api"]:
            if endpoint == "/orgs/example/repos?per_page=100&type=public" and "--paginate" in args:
                kind = "repositories"
                value = "\n".join(
                    json.dumps({"full_name": n, "default_branch": self.branch}) for n in self.names
                ).encode()
            elif re.fullmatch(r"repos/example/[^/]+/contents/\.github/workflows\?ref=.+", endpoint):
                kind = "directory"
                name = endpoint.removeprefix("repos/").split("/contents/")[0]
                value = [
                    {
                        "type": "file",
                        "path": ".github/workflows/ci.yml",
                        "download_url": self.files[name].as_uri(),
                    }
                ]
            elif re.fullmatch(r"repos/example/[^/]+/commits/.+", endpoint):
                kind, value = "commit", self.sha.encode()
            elif endpoint in ["repos/" + name for name in self.names]:
                kind = "metadata"
                value = {"full_name": endpoint[6:], "default_branch": self.branch}
        if not kind:
            self.unknown.append(args)
            raise AssertionError(f"Unknown gh endpoint: {args}")
        self.calls.append(endpoint)
        value = self.responses.get(endpoint, self.responses.get(kind, value))
        if isinstance(value, Exception):
            raise value
        if isinstance(value, subprocess.CompletedProcess):
            return value
        output = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        return self.completed(args, stdout=output)

    def invoke(self, *args, workers=2):
        options = ["--out-dir", str(self.out), "--cache-dir", str(self.cache), "--workers", str(workers)]
        with (
            mock.patch.object(sys, "argv", ["scan", *args, *options]),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = scan.main()
        return code, stderr.getvalue()

    def rows(self, suffix="scan-errors", prefix="example-good"):
        with (self.out / f"{prefix}-{suffix}.tsv").open(newline="", encoding="utf-8") as source:
            return list(csv.DictReader(source, delimiter="\t"))

    def test_success_and_complete_zero_findings(self) -> None:
        for name, count in [("example/good", 1), ("example/clean", 0)]:
            with self.subTest(name=name):
                code, stderr = self.invoke("all", "--repo", name)
                self.assertEqual(code, 0)
                self.assertIn("scan_complete=true", stderr)
                self.assertIn("workflows_scanned=1", stderr)
                prefix = name.replace("/", "-")
                self.assertEqual(self.rows(prefix=prefix), [])
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", prefix)), count)
                self.assertEqual(sum("/contents/" in call for call in self.calls), 1)
                self.calls.clear()

    def test_explicit_archived_repositories_and_aliases_are_scanned_once(self) -> None:
        self.names = ["example/good", "Example/Good"]
        self.responses["metadata"] = {"full_name": "example/good", "default_branch": "main", "archived": True}
        for failure in (None, self.failed(403)):
            with self.subTest(failure=failure):
                self.calls.clear()
                if failure is not None:
                    self.responses["repos/Example/Good"] = failure
                code, stderr = self.invoke("all", "--repo", *self.names[:1], "--repo", self.names[1])
                self.assertEqual(code, int(failure is not None))
                self.assertIn("workflows_scanned=1", stderr)
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "repo-set")), 1)
                self.assertEqual(sum("/contents/" in call for call in self.calls), 1)

    def test_listing_errors_preserve_successes_and_original_diagnostic(self) -> None:
        endpoint = "repos/example/clean/contents/.github/workflows?ref=main"
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.responses[endpoint] = self.failed(status, "original\nmultiline failure")
                code, stderr = self.invoke("all", "--repo", *self.names[:1], "--repo", self.names[1])
                self.assertEqual(code, 1)
                self.assertIn("workflows_scanned=1", stderr)
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "repo-set")), 1)
                error = self.rows(prefix="repo-set")[0]
                self.assertEqual(error["stage"], "workflow-list")
                self.assertIn("original\nmultiline failure", error["error"])

    def test_metadata_and_global_listing_failures_are_visible(self) -> None:
        for route in ("metadata", "repositories"):
            with self.subTest(route=route):
                self.responses.clear()
                args = ["--repo", "example/good"] if route == "metadata" else ["--owner", "example"]
                self.assertEqual(self.invoke("all", *args, "--refresh")[0], 0)
                self.responses[route] = self.failed(403)
                code, stderr = self.invoke("retired", *args, "--refresh")
                self.assertEqual(code, 1)
                self.assertIn("scan_complete=false", stderr)
                prefix = "example-good" if route == "metadata" else "example"
                self.assertEqual(self.rows("retired-gh-runners-confirmed", prefix), [])

    def test_dependency_failures_stop_before_api_and_do_not_reuse_old_findings(self) -> None:
        for target, value, message in [
            ("yaml", None, "PyYAML"),
            ("version_info", (3, 9), "Python 3.11"),
            ("version_info", (3, 10), "Python 3.11"),
            ("which", None, "GitHub CLI"),
        ]:
            with self.subTest(target=target):
                self.assertEqual(self.invoke("all", "--repo", "example/good")[0], 0)
                owner = scan.sys if target == "version_info" else scan.shutil if target == "which" else scan
                self.calls.clear()
                with (
                    mock.patch.object(owner, target, return_value=value)
                    if target == "which"
                    else mock.patch.object(owner, target, value)
                ):
                    code, stderr = self.invoke("all", "--repo", "example/good")
                self.assertEqual(code, 2)
                self.assertIn(message, stderr)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.rows()[0]["stage"], "prerequisite")
                self.assertEqual(self.rows("retired-gh-runners-confirmed"), [])

    def test_download_and_yaml_failures_are_reported(self) -> None:
        for target, stage in [("fetch_text", "download"), ("yaml_load", "parse")]:
            with (
                self.subTest(stage=stage),
                mock.patch.object(scan, target, side_effect=ValueError("broken input")),
            ):
                code, stderr = self.invoke("all", "--repo", "example/good")
                self.assertEqual(code, 1)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual(self.rows()[0]["stage"], stage)

    def test_all_mode_shares_io_and_preserves_the_other_check(self) -> None:
        for failed, suffix in [
            ("arch_hits", "retired-gh-runners-confirmed"),
            ("retired_hits", "macos-setup-action-arch-mismatches"),
        ]:
            with self.subTest(failed=failed):
                if failed == "retired_hits":
                    self.files["example/good"].write_text(
                        json.dumps(
                            {
                                "jobs": {
                                    "build": {
                                        "runs-on": "macos-latest",
                                        "steps": [
                                            {
                                                "uses": "actions/setup-python@v5",
                                                "with": {"architecture": "x64"},
                                            }
                                        ],
                                    }
                                }
                            }
                        )
                    )
                with (
                    mock.patch.object(scan, failed, side_effect=RuntimeError("check failed")),
                    mock.patch.object(scan, "fetch_text", wraps=scan.fetch_text) as fetch,
                    mock.patch.object(scan, "yaml_load", wraps=scan.yaml_load) as parse,
                ):
                    code, stderr = self.invoke("all", "--repo", "example/good")
                self.assertEqual(code, 1)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual((fetch.call_count, parse.call_count), (1, 1))
                self.assertEqual(len(self.rows(suffix)), 1)
                self.assertEqual(self.rows()[0]["stage"], "check")

    def test_verified_absence_empty_repository_and_invalid_ref(self) -> None:
        cases = [
            ("Not Found", None, 0, 3),
            ("This repository is empty.", None, 0, 1),
            ("Not Found", self.failed(409, "Git Repository is empty."), 0, 2),
            ("Not Found", self.failed(404, "Not Found"), 1, 2),
            ("Not Found", self.failed(403), 1, 2),
            ("Not Found", self.failed(409, "Conflict"), 1, 2),
        ]
        for diagnostic, response, expected, requests in cases:
            with self.subTest(response=response):
                self.calls.clear()
                self.responses["directory"] = self.failed(404, diagnostic)
                self.responses.pop("commit", None)
                if response is not None:
                    self.responses["commit"] = response
                code, stderr = self.invoke("all", "--repo", "example/good")
                self.assertEqual(code, expected)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual(bool(self.rows()), bool(expected))
                self.assertEqual(len(self.calls) - 1, requests)

    def test_sha_retry_recovers_and_ref_is_escaped(self) -> None:
        self.branch = "feature/path"
        self.responses["repos/example/good/contents/.github/workflows?ref=feature%2Fpath"] = self.failed(404)
        code, _ = self.invoke("retired", "--repo", "example/good")
        self.assertEqual(code, 0)
        self.assertIn("repos/example/good/commits/feature%2Fpath", self.calls)
        self.assertEqual(len(self.rows("retired-gh-runners-confirmed")), 1)

    def test_abnormal_json_and_sha_cannot_be_clean(self) -> None:
        for body in (b"{", b"null", b"{}", b"\xff"):
            with self.subTest(body=body):
                self.responses["directory"] = body
                self.assertEqual(self.invoke("all", "--repo", "example/good")[0], 1)
        self.responses["directory"] = self.failed(404)
        for body in (b"", b"a" * 39, b"a" * 41, b"z" * 40, b'{"sha":"a"}', b"\xff"):
            with self.subTest(sha=body):
                self.responses["commit"] = body
                code, stderr = self.invoke("all", "--repo", "example/good")
                self.assertEqual(code, 1)
                self.assertIn("cannot verify ref", stderr)

    def test_bad_entries_keep_valid_peers_and_prevent_cache(self) -> None:
        good = {
            "type": "file",
            "path": ".github/workflows/ci.yml",
            "download_url": self.files["example/good"].as_uri(),
        }
        for bad in (
            {},
            {"type": "file"},
            {"path": "bad.yml"},
            {"type": 1, "path": "bad.yml"},
            {"type": "unknown", "path": "bad.yml"},
            None,
            dict(good, download_url=None),
        ):
            with self.subTest(bad=bad):
                self.names = ["example/good"]
                self.responses["directory"] = [bad, good]
                code, stderr = self.invoke("retired", "--owner", "example", "--refresh")
                self.assertEqual(code, 1)
                self.assertIn("workflows_scanned=1", stderr)
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
                self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())

    def test_normal_empty_and_filtered_listings(self) -> None:
        for listing in (
            [],
            [{"type": "dir", "path": ".github/workflows/old"}, {"type": "file", "path": "README.md"}],
        ):
            with self.subTest(listing=listing):
                self.responses["directory"] = listing
                self.assertEqual(self.invoke("all", "--repo", "example/good")[0], 0)
                self.assertEqual(self.rows(), [])

    def test_partial_inventory_recovers_without_refresh_then_reuses_cache(self) -> None:
        endpoint = "repos/example/clean/contents/.github/workflows?ref=main"
        self.responses[endpoint] = self.failed(403)
        code, stderr = self.invoke("all", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("workflows_scanned=1", stderr)
        self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())
        self.responses.clear()
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        self.calls.clear()
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        self.assertEqual(self.calls, [])

    def test_legacy_and_damaged_caches_are_rebuilt(self) -> None:
        self.cache.mkdir()
        for name in ("example-repos.jsonl", "example-workflow-files.tsv", "example-workflow-files-v2.tsv"):
            (self.cache / name).write_text("old incomplete inventory")
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        (self.cache / "example-workflow-files-v3.tsv").write_text("repo\tpath\nexample/good\tbad\n")
        self.calls.clear()
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        self.assertTrue(self.calls)

    def test_cache_replace_failures_keep_old_files_and_scanned_findings(self) -> None:
        self.assertEqual(self.invoke("all", "--owner", "example")[0], 0)
        original = scan.os.replace
        for name in ("example-repos-v2.jsonl", "example-workflow-files-v3.tsv"):
            with self.subTest(cache=name):
                previous = (self.cache / name).read_bytes()

                def denied(source, target, *, name=name):
                    if Path(target).name == name:
                        raise OSError("cache denied")
                    return original(source, target)

                with mock.patch.object(scan.os, "replace", side_effect=denied):
                    code, stderr = self.invoke("all", "--owner", "example", "--refresh")
                self.assertEqual(code, 1)
                self.assertIn("workflows_scanned=2", stderr)
                self.assertEqual(self.rows(prefix="example")[0]["stage"], "cache")
                self.assertEqual(len(self.rows("retired-gh-runners-confirmed", "example")), 1)
                self.assertEqual((self.cache / name).read_bytes(), previous)
                self.assertEqual(len(list(self.cache.iterdir())), 2)

    def test_interrupted_cache_writer_cannot_publish_a_prefix(self) -> None:
        path = self.cache / "inventory"
        path.parent.mkdir()
        path.write_text("complete")

        def interrupted(temporary):
            temporary.write_text("valid prefix")
            raise OSError("interrupted")

        with self.assertRaises(OSError):
            scan.atomic_write(path, interrupted)
        self.assertEqual(path.read_text(), "complete")
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_pagination_never_publishes_partial_repositories(self) -> None:
        self.responses["repositories"] = self.completed(
            [], 1, b'{"full_name":"example/good","default_branch":"main"}\n', b"pagination failed"
        )
        code, stderr = self.invoke("all", "--owner", "example")
        self.assertEqual(code, 1)
        self.assertIn("pagination failed", stderr)
        self.assertIn("/orgs/example/repos?per_page=100&type=public", stderr)
        self.assertFalse((self.cache / "example-repos-v2.jsonl").exists())
        self.assertFalse((self.cache / "example-workflow-files-v3.tsv").exists())
        self.responses["repositories"] = b'{"full_name":"example/good","default_branch":null}\n'
        self.assertEqual(self.invoke("all", "--owner", "example", "--refresh")[0], 1)
        self.assertEqual(self.rows(prefix="example")[0]["repo"], "example/good")

    def test_utf8_responses_and_failure_diagnostics(self) -> None:
        self.responses["directory"] = [
            {
                "type": "file",
                "path": ".github/workflows/构建.yml",
                "download_url": self.files["example/good"].as_uri(),
            }
        ]
        self.assertEqual(self.invoke("retired", "--repo", "example/good")[0], 0)
        self.assertEqual(self.rows("retired-gh-runners-confirmed")[0]["path"], ".github/workflows/构建.yml")
        self.responses["directory"] = self.failed(403, "✓ 请求失败")
        self.assertEqual(self.invoke("all", "--repo", "example/good")[0], 1)
        self.assertIn("✓ 请求失败", self.rows()[0]["error"])

    def test_unreadable_repo_file_has_correct_failure_prefix(self) -> None:
        path = self.root / "repos"
        path.write_bytes(b"\xff")
        for args, prefix in [
            ([], "repo-set"),
            (["--repo", "example/good"], "example-good"),
            (["--repo", "example/good", "--scope-name", "explicit"], "explicit"),
        ]:
            with self.subTest(prefix=prefix):
                self.calls.clear()
                code, stderr = self.invoke("all", "--repo-file", str(path), *args)
                self.assertEqual(code, 2)
                self.assertIn("workflows_scanned=0", stderr)
                self.assertEqual(self.rows(prefix=prefix)[0]["stage"], "prerequisite")
                self.assertEqual(self.calls, [])
        path.write_text("example/clean\n", encoding="utf-8")
        self.assertEqual(self.invoke("all", "--repo-file", str(path), "--repo", "example/good")[0], 0)
        self.assertEqual(self.rows(prefix="repo-set"), [])

    def test_invalid_workers_are_input_errors(self) -> None:
        for workers in (0, -1):
            with self.subTest(workers=workers):
                with self.assertRaises(SystemExit) as failure:
                    self.invoke("all", "--repo", "example/good", workers=workers)
                self.assertEqual(failure.exception.code, 2)
                self.assertEqual(self.calls, [])

    def test_output_failure_is_not_success(self) -> None:
        for target, prerequisite in [("parallel_scan", False), ("write_tsv", False), ("write_tsv", True)]:
            with self.subTest(target=target, prerequisite=prerequisite):
                self.assertEqual(self.invoke("all", "--repo", "example/good")[0], 0)
                unselected = self.out / "example-good-macos-arch-mismatch-candidates.tsv"
                unselected.write_text("previous unselected output")
                with (
                    mock.patch.object(scan, target, side_effect=OSError("injected failure")),
                    mock.patch.object(scan, "yaml", None) if prerequisite else contextlib.nullcontext(),
                ):
                    code, stderr = self.invoke("retired", "--repo", "example/good")
                self.assertEqual(code, 2 if prerequisite else 1)
                self.assertIn("scan_complete=false", stderr)
                if target == "parallel_scan":
                    self.assertEqual(self.rows("retired-gh-runners-confirmed"), [])
                self.assertEqual(unselected.read_text(), "previous unselected output")


if __name__ == "__main__":
    unittest.main()
