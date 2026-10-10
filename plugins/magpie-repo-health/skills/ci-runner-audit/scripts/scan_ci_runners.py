#!/usr/bin/env python3
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

"""Audit Apache GitHub Actions workflows for obsolete runners and macOS arch mismatches."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

try:
    import yaml
except Exception:  # pragma: no cover - reported at runtime for YAML-dependent commands
    yaml = None

RETIRED_LABELS = {
    "ubuntu-20.04",
    "ubuntu-18.04",
    "ubuntu-16.04",
    "windows-2019",
    "windows-2016",
    "macos-13",
    "macos-12",
    "macos-11",
    "macos-10.15",
    "macos-13-large",
    "macos-13-xlarge",
}

MACOS_ARM = {"macos-latest", "macos-14", "macos-15", "macos-26", "macos-13-xlarge"}
MACOS_X64 = {
    "macos-15-intel",
    "macos-26-intel",
    "macos-13",
    "macos-12",
    "macos-11",
    "macos-10.15",
    "macos-13-large",
}
MACOS_ANY = MACOS_ARM | MACOS_X64

X64_TERMS = re.compile(
    r"(?i)(?:\bx64\b|\bx86_64\b|\bamd64\b|architecture:\s*['\"]?x64['\"]?|arch:\s*['\"]?(?:x64|x86_64|amd64)['\"]?)"
)
ARM_TERMS = re.compile(
    r"(?i)(?:\barm64\b|\baarch64\b|architecture:\s*['\"]?arm64['\"]?|arch:\s*['\"]?(?:arm64|aarch64)['\"]?)"
)
ARCH_KEYS = {
    "architecture",
    "arch",
    "target",
    "targets",
    "platform",
    "platforms",
    "os",
    "goarch",
    "node-arch",
}


class ScanError(RuntimeError):
    """A workflow could not be fetched, parsed, or checked."""

    def __init__(self, message: str, stage: str = "check") -> None:
        super().__init__(message)
        self.stage = stage


class GhError(ScanError):
    """A gh command failed, with its original diagnostic and HTTP status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


WORKFLOW_FIELDS = ["repo", "branch", "path", "url", "html_url"]
ERROR_FIELDS = ["repo", "path", "stage", "error", "html_url"]


def run(args: list[str]) -> str:
    completed = subprocess.run(args, capture_output=True, check=False)
    if completed.returncode:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
        diagnostic = stderr.strip() or stdout.strip() or "no captured diagnostic"
        status = re.search(r"\(HTTP (\d{3})\)", stderr)
        raise GhError(f"{' '.join(args[:3])}: {diagnostic}", int(status[1]) if status else None)
    if completed.stdout is None:
        raise ScanError(f"{' '.join(args[:3])}: stdout was not captured")
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScanError(f"{' '.join(args[:3])}: invalid UTF-8 response: {exc}") from exc


def gh_json(path: str) -> object:
    return json.loads(run(["gh", "api", path]))


def scan_error(context: dict, stage: str, exc: Exception) -> dict:
    return {
        "repo": str(context.get("repo") or context.get("full_name") or ""),
        "path": str(context.get("path") or ""),
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "html_url": str(context.get("html_url") or ""),
    }


def fetch_text(url: str) -> str:
    with urlopen(url, timeout=20) as response:  # nosec: auditing public GitHub URLs
        return response.read().decode("utf-8", errors="replace")


def flatten(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from flatten(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from flatten(child)
    elif value is not None:
        yield str(value)


def lower_values(value) -> list[str]:
    return [item.strip().lower() for item in flatten(value)]


def atomic_write(path: Path, writer: Callable[[Path], object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        writer(temporary_path)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def valid_repo_metadata(repo: object) -> bool:
    return isinstance(repo, dict) and all(
        isinstance(repo.get(key), str) and bool(repo[key]) for key in ("full_name", "default_branch")
    )


def load_repos(cache_dir: Path, owner: str, refresh: bool) -> tuple[list[dict], list[dict]]:
    repo_file = cache_dir / f"{owner}-repos-v2.jsonl"
    if not refresh:
        try:
            cached = [
                json.loads(line)
                for line in repo_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if all(valid_repo_metadata(repo) for repo in cached):
                return cached, []
        except (OSError, UnicodeError, ValueError):
            pass  # An absent or damaged cache must be rebuilt from the API.
    output = run(
        [
            "gh",
            "api",
            "--paginate",
            f"/orgs/{owner}/repos?per_page=100&type=public",
            "--jq",
            ".[] | select(.archived == false) | {full_name, default_branch}",
        ]
    )
    response = [json.loads(line) for line in output.splitlines() if line.strip()]
    repos = []
    errors = []
    for repo in response:
        if valid_repo_metadata(repo):
            repos.append(repo)
        else:
            errors.append(
                scan_error(
                    repo if isinstance(repo, dict) else {},
                    "metadata",
                    ScanError("Repository metadata must include full_name and default_branch strings"),
                )
            )
    if not errors:
        try:
            atomic_write(repo_file, lambda path: path.write_text(output, encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(scan_error({"path": repo_file.name}, "cache", exc))
    return repos, errors


def load_repo(full_name: str) -> dict:
    repo = gh_json(f"repos/{full_name}")
    if not isinstance(repo, dict):
        raise ScanError(f"Unexpected repository metadata for {full_name}")
    if repo.get("archived"):
        return {}
    if not valid_repo_metadata(repo):
        raise ScanError(f"Repository metadata for {full_name} must include full_name and default_branch")
    return {"full_name": repo.get("full_name"), "default_branch": repo.get("default_branch")}


def load_repo_file(path: Path) -> list[str]:
    repos = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            repos.append(line)
    return repos


def scope_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "scope"


def workflow_contents(full_name: str, branch: str) -> object:
    directory = f"repos/{full_name}/contents/.github/workflows?ref="
    try:
        return gh_json(directory + quote(branch, safe=""))
    except GhError as exc:
        if exc.status != 404:
            raise
        # A directory 404 can mean an inaccessible repository or an invalid ref.
        # Resolve the ref, then retry at that immutable commit before accepting absence.
        try:
            commit = gh_json(f"repos/{full_name}/commits/{quote(branch, safe='')}")
            if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
                raise ScanError("Unexpected ref-validation response")
            sha = commit["sha"]
            if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                raise ScanError("Ref-validation response must include a commit SHA")
        except Exception as validation_error:
            raise ScanError(f"{exc}; cannot verify ref {branch}: {validation_error}") from validation_error
        try:
            return gh_json(directory + sha)
        except Exception as retry_error:
            if isinstance(retry_error, GhError) and retry_error.status == 404:
                return []
            raise ScanError(f"{exc}; directory retry at {sha} failed: {retry_error}") from retry_error


def list_workflows_for_repo(repo: dict) -> tuple[list[dict], list[dict]]:
    if not valid_repo_metadata(repo):
        raise ScanError("Repository metadata must include full_name and default_branch strings")
    full_name = repo["full_name"]
    branch = repo["default_branch"]
    contents = workflow_contents(full_name, branch)
    if not isinstance(contents, list):
        raise ScanError(f"Unexpected workflow-list response for {full_name}")
    workflows = []
    errors = []
    for index, item in enumerate(contents):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not item["path"]
            or item.get("type") not in ("file", "dir", "symlink", "submodule")
        ):
            errors.append(
                scan_error(
                    {"repo": full_name, "path": item.get("path") if isinstance(item, dict) else ""},
                    "workflow-list",
                    ScanError(f"Unexpected workflow-list entry {index}: valid path and type are required"),
                )
            )
            continue
        path = item["path"]
        if item["type"] == "file" and re.search(r"\.ya?ml$", path):
            workflows.append(
                {
                    "repo": full_name,
                    "branch": branch,
                    "path": path,
                    "url": item.get("download_url"),
                    "html_url": f"https://github.com/{full_name}/blob/{quote(branch, safe='')}/{quote(path)}",
                }
            )
    return workflows, errors


def collect_workflows(repos: list[dict], workers: int) -> tuple[list[dict], list[dict]]:
    workflows: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(list_workflows_for_repo, repo): repo for repo in repos}
        for future in as_completed(futures):
            try:
                repo_workflows, repo_errors = future.result()
                workflows.extend(repo_workflows)
                errors.extend(repo_errors)
            except Exception as exc:
                errors.append(scan_error(futures[future], "workflow-list", exc))
    return sorted(workflows, key=lambda row: (row["repo"], row["path"])), errors


def load_workflows(cache_dir: Path, owner: str, refresh: bool, workers: int) -> tuple[list[dict], list[dict]]:
    # v1/v2 inventories could have trusted truncated repo lists or ambiguous 404s.
    workflow_file = cache_dir / f"{owner}-workflow-files-v3.tsv"
    if not refresh:
        try:
            with workflow_file.open(newline="", encoding="utf-8") as input_file:
                reader = csv.DictReader(input_file, delimiter="\t", strict=True)
                if reader.fieldnames == WORKFLOW_FIELDS:
                    workflows = list(reader)
                    if all(
                        set(row) == set(WORKFLOW_FIELDS) and all(row.get(field) for field in WORKFLOW_FIELDS)
                        for row in workflows
                    ):
                        return workflows, []
        except (OSError, UnicodeError, csv.Error):
            pass
    repos, errors = load_repos(cache_dir, owner, refresh)
    workflows, listing_errors = collect_workflows(repos, workers)
    errors.extend(listing_errors)
    if not errors and all(isinstance(workflow.get("url"), str) and workflow["url"] for workflow in workflows):
        try:
            atomic_write(workflow_file, lambda path: write_tsv(path, workflows, WORKFLOW_FIELDS))
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(scan_error({"path": workflow_file.name}, "cache", exc))
    return workflows, errors


def load_workflows_for_repos(repo_names: list[str], workers: int) -> tuple[list[dict], list[dict]]:
    repos = []
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(load_repo, repo_name): repo_name for repo_name in repo_names}
        for future in as_completed(futures):
            try:
                repo = future.result()
            except Exception as exc:
                errors.append(scan_error({"repo": futures[future]}, "metadata", exc))
                continue
            if repo:
                repos.append(repo)
    workflows, listing_errors = collect_workflows(repos, workers)
    return workflows, errors + listing_errors


def yaml_load(text: str) -> object:
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install python3-yaml or pyyaml.")
    data = yaml.safe_load(text)
    return {} if data is None else data


def matrix_rows(matrix: object) -> list[dict]:
    if not isinstance(matrix, dict):
        return [{}]
    keys: list[str] = []
    values: list[list] = []
    for key, value in matrix.items():
        if key in ("include", "exclude"):
            continue
        keys.append(str(key))
        values.append(value if isinstance(value, list) else [value])
    rows: list[dict[str, Any]] = [{}]
    for key, vals in zip(keys, values, strict=True):
        rows = [{**row, key: val} for row in rows for val in vals]

    excludes = matrix.get("exclude")
    if isinstance(excludes, list):

        def is_excluded(row: dict) -> bool:
            return any(
                isinstance(item, dict)
                and all(str(row.get(k)).lower() == str(v).lower() for k, v in item.items())
                for item in excludes
            )

        rows = [row for row in rows if not is_excluded(row)]

    includes = matrix.get("include")
    if isinstance(includes, list):
        rows.extend(item for item in includes if isinstance(item, dict))
    return rows or [{}]


def runner_arch(label: str) -> str | None:
    label = label.strip().lower()
    if label in MACOS_ARM:
        return "arm64"
    if label in MACOS_X64:
        return "x64"
    return None


def candidate_runner_contexts(job: dict) -> list[tuple[str, str | None, dict]]:
    runs_on_values = lower_values(job.get("runs-on"))
    contexts: list[tuple[str, str | None, dict]] = []
    for label in runs_on_values:
        if label in MACOS_ANY:
            contexts.append((label, runner_arch(label), {}))
    if "matrix." in " ".join(runs_on_values):
        rows = matrix_rows((job.get("strategy") or {}).get("matrix") or {})
        for row in rows:
            for value in lower_values(row):
                if value in MACOS_ANY:
                    contexts.append((value, runner_arch(value), row))
    seen = set()
    unique = []
    for label, arch, row in contexts:
        key = (label, arch, tuple(sorted((str(k), str(v)) for k, v in row.items())))
        if key not in seen:
            seen.add(key)
            unique.append((label, arch, row))
    return unique


def retired_hits(workflow: dict, data: object) -> list[dict]:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return []
    hits = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        run_values = lower_values(job.get("runs-on"))
        rows = matrix_rows((job.get("strategy") or {}).get("matrix") or {})
        labels = {value for value in run_values if value in RETIRED_LABELS}
        if "matrix." in " ".join(run_values):
            for row in rows:
                labels.update(value for value in lower_values(row) if value in RETIRED_LABELS)
        if any("self-hosted" in value for value in run_values):
            labels.clear()
        for label in sorted(labels):
            hits.append({**workflow, "job": str(job_name), "runner": label})
    return hits


def arch_hits(workflow: dict, data: object) -> list[dict]:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return []
    hits = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        contexts = candidate_runner_contexts(job)
        if not contexts:
            continue
        steps = job.get("steps") or []
        observed = []
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            step_if = str(step.get("if", "")).lower()
            skip_non_macos_branch = any(
                token in step_if
                for token in [
                    "runner.os == 'windows'",
                    'runner.os == "windows"',
                    "matrix.os == 'windows",
                    'matrix.os == "windows',
                    "runner.os == 'linux'",
                    'runner.os == "linux"',
                    "matrix.os == 'ubuntu",
                    'matrix.os == "ubuntu',
                ]
            )
            if skip_non_macos_branch:
                continue
            name = str(step.get("name", ""))
            uses = str(step.get("uses", ""))
            raw_with = step.get("with")
            action_inputs = raw_with if isinstance(raw_with, dict) else {}
            for key, value in action_inputs.items():
                key_text = str(key).lower()
                value_text = " ".join(lower_values(value))
                if key_text in ARCH_KEYS or "arch" in key_text or "platform" in key_text:
                    evidence = f"with.{key}={value}"
                    if X64_TERMS.search(f"{key_text}: {value_text}"):
                        observed.append(
                            (
                                "x64",
                                name,
                                uses,
                                evidence,
                                "setup-action" if uses.startswith("actions/setup-") else "action-input",
                            )
                        )
                    if ARM_TERMS.search(f"{key_text}: {value_text}"):
                        observed.append(
                            (
                                "arm64",
                                name,
                                uses,
                                evidence,
                                "setup-action" if uses.startswith("actions/setup-") else "action-input",
                            )
                        )
            run_script = step.get("run")
            if isinstance(run_script, str):
                for line in run_script.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if X64_TERMS.search(line):
                        observed.append(("x64", name, uses, line[:180], "script"))
                    if ARM_TERMS.search(line):
                        observed.append(("arm64", name, uses, line[:180], "script"))
        for label, arch, matrix in contexts:
            for binary_arch, step_name, uses, evidence, confidence in observed:
                if arch and binary_arch != arch:
                    hits.append(
                        {
                            **workflow,
                            "job": str(job_name),
                            "runner": label,
                            "runner_arch": arch,
                            "requested_arch": binary_arch,
                            "step": step_name,
                            "uses": uses,
                            "evidence": evidence,
                            "matrix": ",".join(f"{k}={v}" for k, v in matrix.items()),
                            "confidence": confidence,
                        }
                    )
    return hits


def scan_workflow(workflow: dict, command: str) -> tuple[list[dict], list[dict], list[dict]]:
    try:
        if not isinstance(workflow.get("url"), str) or not workflow["url"]:
            raise ScanError("Workflow has no download URL")
        text = fetch_text(workflow["url"])
    except Exception as exc:
        raise ScanError(str(exc), "download") from exc
    try:
        data = yaml_load(text)
        if not isinstance(data, dict):
            raise ScanError("Workflow YAML must contain a mapping")
    except Exception as exc:
        raise ScanError(str(exc), "parse") from exc
    results: dict[str, list[dict]] = {"retired": [], "macos-arch": []}
    errors = []
    for name, check in (("retired", retired_hits), ("macos-arch", arch_hits)):
        if command not in (name, "all"):
            continue
        try:
            results[name] = check(workflow, data)
        except Exception as exc:
            errors.append(scan_error(workflow, "check", ScanError(f"{name}: {type(exc).__name__}: {exc}")))
    return results["retired"], results["macos-arch"], errors


def parallel_scan(
    workflows: list[dict], command: str, workers: int
) -> tuple[list[dict], list[dict], int, list[dict]]:
    retired: list[dict] = []
    arch: list[dict] = []
    errors: list[dict] = []
    scanned = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(scan_workflow, workflow, command): workflow for workflow in workflows}
        for future in as_completed(futures):
            try:
                retired_rows, arch_rows, check_errors = future.result()
            except Exception as exc:
                errors.append(scan_error(futures[future], getattr(exc, "stage", "check"), exc))
                continue
            retired.extend(retired_rows)
            arch.extend(arch_rows)
            errors.extend(check_errors)
            scanned += int(not check_errors)
    return sorted_hits(retired), sorted_hits(arch), scanned, errors


def sorted_hits(results: list[dict]) -> list[dict]:
    return sorted(
        results,
        key=lambda row: (
            row.get("repo", ""),
            row.get("path", ""),
            row.get("job", ""),
            row.get("runner", ""),
            row.get("evidence", ""),
        ),
    )


def write_tsv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output, delimiter="\t", fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["retired", "macos-arch", "all"])
    parser.add_argument("--owner", default="apache")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Repository full name, e.g. apache/polaris. May be repeated.",
    )
    parser.add_argument("--repo-file", type=Path, help="File containing repository full names, one per line.")
    parser.add_argument("--scope-name", help="Output filename prefix for explicit repo/repo-file scans.")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    return args


def write_findings(args: argparse.Namespace, prefix: str, retired: list[dict], arch: list[dict]) -> None:

    if args.command in ("retired", "all"):
        write_tsv(
            args.out_dir / f"{prefix}-retired-gh-runners-confirmed.tsv",
            retired,
            ["repo", "path", "job", "runner", "html_url"],
        )
        print(f"retired_runner_hits={len(retired)}", file=sys.stderr)

    if args.command in ("macos-arch", "all"):
        write_tsv(
            args.out_dir / f"{prefix}-macos-arch-mismatch-candidates.tsv",
            arch,
            [
                "repo",
                "path",
                "job",
                "runner",
                "runner_arch",
                "requested_arch",
                "confidence",
                "step",
                "uses",
                "evidence",
                "matrix",
                "html_url",
            ],
        )
        setup = []
        seen = set()
        for row in arch:
            if row.get("confidence") == "setup-action":
                key = (
                    row.get("repo"),
                    row.get("path"),
                    row.get("job"),
                    row.get("runner"),
                    row.get("uses"),
                    row.get("evidence"),
                )
                if key not in seen:
                    seen.add(key)
                    setup.append(row)
        write_tsv(
            args.out_dir / f"{prefix}-macos-setup-action-arch-mismatches.tsv",
            setup,
            [
                "repo",
                "path",
                "job",
                "runner",
                "runner_arch",
                "requested_arch",
                "step",
                "uses",
                "evidence",
                "html_url",
            ],
        )
        print(f"macos_arch_candidates={len(arch)}", file=sys.stderr)
        print(f"setup_action_mismatches={len(setup)}", file=sys.stderr)


def finish_scan(args: argparse.Namespace, prefix: str, scanned: int, errors: list[dict]) -> int:
    errors.sort(key=lambda row: (row["repo"], row["path"], row["stage"], row["error"]))
    try:
        write_tsv(args.out_dir / f"{prefix}-scan-errors.tsv", errors, ERROR_FIELDS)
    except OSError as exc:
        errors.append(scan_error({}, "output", exc))
    for error in errors:
        print(f"error: {error['repo']} {error['path']} [{error['stage']}]: {error['error']}", file=sys.stderr)
    print(f"scan_complete={str(not errors).lower()}", file=sys.stderr)
    print(f"workflows_scanned={scanned}", file=sys.stderr)
    print(f"scan_errors={len(errors)}", file=sys.stderr)
    return 1 if errors else 0


def main() -> int:
    args = parse_args()
    repo_names = sorted(set(args.repo))
    prefix = scope_key(
        args.scope_name
        or (
            repo_names[0]
            if len(repo_names) == 1
            else "repo-set"
            if repo_names or args.repo_file
            else args.owner
        )
    )
    try:
        if sys.version_info < (3, 11):  # noqa: UP036 - standalone script may use an older system Python
            raise ScanError("Python 3.11 or newer is required")
        if yaml is None:
            raise ScanError("PyYAML is required. Install python3-yaml or pyyaml.")
        run(["gh", "auth", "status", "--active", "--hostname", "github.com"])
        if args.repo_file:
            repo_names = sorted(set(repo_names + load_repo_file(args.repo_file)))
            if not repo_names:
                raise ScanError("The explicit repository list is empty")
    except (ScanError, OSError) as exc:
        finish_scan(args, prefix, 0, [scan_error({}, "prerequisite", exc)])
        return 2

    if not args.scope_name and repo_names:
        prefix = scope_key(repo_names[0]) if len(repo_names) == 1 else "repo-set"
    errors: list[dict] = []
    scanned = 0
    stage = "repository-list"
    try:
        if repo_names:
            workflows, errors = load_workflows_for_repos(repo_names, args.workers)
        else:
            workflows, errors = load_workflows(args.cache_dir, args.owner, args.refresh, args.workers)
        stage = "scan"
        retired, arch, scanned, workflow_errors = parallel_scan(workflows, args.command, args.workers)
        errors.extend(workflow_errors)
        stage = "output"
        write_findings(args, prefix, retired, arch)
    except Exception as exc:
        errors.append(scan_error({}, stage, exc))
    return finish_scan(args, prefix, scanned, errors)


if __name__ == "__main__":
    raise SystemExit(main())
