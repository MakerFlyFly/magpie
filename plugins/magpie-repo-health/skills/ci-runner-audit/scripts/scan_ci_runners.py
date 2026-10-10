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
import shutil
import subprocess
import sys
import tempfile
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
    """A fetch or check failed; the scan is incomplete."""


class GhError(ScanError):
    def __init__(self, message: str, status: int | None, diagnostic: str) -> None:
        super().__init__(message)
        self.status, self.diagnostic = status, diagnostic


WORKFLOW_FIELDS = ["repo", "branch", "path", "url", "html_url"]
ERROR_FIELDS = ["repo", "path", "stage", "error", "html_url"]


def run(args: list[str]) -> str:
    completed = subprocess.run(args, capture_output=True, check=False)
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
    if completed.returncode:
        diagnostic = stderr.strip() or (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        status = re.search(r"\(HTTP (\d{3})\)", stderr)
        raise GhError(
            f"{' '.join(args[:3])}: {diagnostic or 'no captured diagnostic'}",
            int(status[1]) if status else None,
            diagnostic,
        )
    if completed.stdout is None:
        raise ScanError("gh stdout was not captured")
    return completed.stdout.decode("utf-8")


def gh_json(path: str) -> object:
    return json.loads(run(["gh", "api", path]))


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


def scan_error(context: dict, stage: str, exc: Exception) -> dict:
    return {
        "repo": str(context.get("repo") or context.get("full_name") or ""),
        "path": str(context.get("path") or ""),
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "html_url": str(context.get("html_url") or ""),
    }


def atomic_write(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        scratch = Path(temporary.name)
    try:
        writer(scratch)
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def valid_repo(repo: object) -> bool:
    return isinstance(repo, dict) and all(
        isinstance(repo.get(key), str) and repo[key] for key in ("full_name", "default_branch")
    )


def load_repos(cache_dir: Path, owner: str, refresh: bool) -> tuple[list[dict], list[dict]]:
    cache = cache_dir / f"{owner}-repos-v2.jsonl"
    if not refresh:
        try:
            repos = [
                json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            if all(valid_repo(repo) for repo in repos):
                return repos, []
        except (OSError, UnicodeError, ValueError):
            pass  # Rebuild an absent or damaged cache; never accept a partial write.
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
    repos, errors = [], []
    for repo in response:
        if valid_repo(repo):
            repos.append(repo)
        else:
            errors.append(scan_error({}, "metadata", ScanError(f"Invalid repository metadata: {repo!r}")))
    if not errors:
        try:
            atomic_write(cache, lambda path: path.write_text(output, encoding="utf-8"))
        except OSError as exc:
            errors.append(scan_error({"path": cache.name}, "cache", exc))
    return repos, errors


def load_repo(full_name: str) -> dict:
    repo = gh_json(f"repos/{full_name}")
    if isinstance(repo, dict) and repo.get("archived"):
        return {}
    if not isinstance(repo, dict) or not valid_repo(repo):
        raise ScanError(f"Invalid repository metadata for {full_name}")
    return {key: repo[key] for key in ("full_name", "default_branch")}


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
    except GhError as original:
        if original.status != 404:
            raise
        # A 404 can also mean an invalid ref; verify it before accepting absence.
        try:
            sha = run(
                [
                    "gh",
                    "api",
                    f"repos/{full_name}/commits/{quote(branch, safe='')}",
                    "-H",
                    "Accept: application/vnd.github.sha",
                ]
            ).strip()
            if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                raise ScanError("Ref verification must return a 40-character hexadecimal SHA")
        except Exception as exc:
            if (
                isinstance(exc, GhError)
                and exc.status == 409
                and re.search(r"(?m)(?:^|: )Git Repository is empty\.(?: \(HTTP 409\))?$", exc.diagnostic)
            ):
                return []
            raise ScanError(f"{original}; cannot verify ref {branch}: {exc}") from exc
        try:
            return gh_json(directory + sha)
        except GhError as exc:
            if exc.status == 404:
                return []
            raise ScanError(f"{original}; directory retry failed: {exc}") from exc


def list_workflows_for_repo(repo: dict) -> tuple[list[dict], list[dict]]:
    if not valid_repo(repo):
        raise ScanError(f"Invalid repository metadata: {repo!r}")
    full_name, branch = repo["full_name"], repo["default_branch"]
    contents = workflow_contents(full_name, branch)
    if not isinstance(contents, list):
        raise ScanError(f"Unexpected workflow listing for {full_name}: expected a list")
    workflows, errors = [], []
    for item in contents:
        if (
            not isinstance(item, dict)
            or any(not isinstance(item.get(key), str) or not item[key] for key in ("type", "path"))
            or item["type"] not in {"file", "dir", "symlink", "submodule"}
        ):
            errors.append(scan_error(repo, "workflow-list", ScanError(f"Invalid workflow entry: {item!r}")))
        elif item["type"] == "file" and re.search(r"\.ya?ml$", item["path"]):
            workflow = {
                "repo": full_name,
                "branch": branch,
                "path": item["path"],
                "url": item.get("download_url"),
                "html_url": f"https://github.com/{full_name}/blob/{branch}/{item['path']}",
            }
            if not isinstance(workflow["url"], str) or not workflow["url"]:
                errors.append(scan_error(workflow, "download", ScanError("Missing workflow download URL")))
            else:
                workflows.append(workflow)
    return workflows, errors


def collect_workflows(repos: list[dict], workers: int) -> tuple[list[dict], list[dict]]:
    workflows, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(list_workflows_for_repo, repo): repo for repo in repos}
        for future in as_completed(futures):
            try:
                rows, failures = future.result()
                workflows.extend(rows)
                errors.extend(failures)
            except Exception as exc:
                errors.append(scan_error(futures[future], "workflow-list", exc))
    return sorted(workflows, key=lambda row: (row["repo"], row["path"])), errors


def load_workflows(cache_dir: Path, owner: str, refresh: bool, workers: int) -> tuple[list[dict], list[dict]]:
    # Earlier inventories could contain silent omissions. Ignore them and rebuild.
    cache = cache_dir / f"{owner}-workflow-files-v3.tsv"
    if not refresh:
        try:
            with cache.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source, delimiter="\t", strict=True)
                rows = list(reader)
                if reader.fieldnames == WORKFLOW_FIELDS and all(
                    set(row) == set(WORKFLOW_FIELDS) and all(row.values()) for row in rows
                ):
                    return rows, []
        except (OSError, UnicodeError, csv.Error):
            pass  # Rebuild an absent or damaged workflow inventory.
    repos, errors = load_repos(cache_dir, owner, refresh)
    workflows, failures = collect_workflows(repos, workers)
    errors.extend(failures)
    if not errors:
        try:
            atomic_write(cache, lambda path: write_tsv(path, workflows, WORKFLOW_FIELDS))
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(scan_error({"path": cache.name}, "cache", exc))
    return workflows, errors


def load_workflows_for_repos(repo_names: list[str], workers: int) -> tuple[list[dict], list[dict]]:
    repos, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(load_repo, name): name for name in repo_names}
        for future in as_completed(futures):
            try:
                repo = future.result()
                if repo:
                    repos.append(repo)
            except Exception as exc:
                errors.append(scan_error({"repo": futures[future]}, "metadata", exc))
    workflows, failures = collect_workflows(repos, workers)
    return workflows, errors + failures


def yaml_load(text: str) -> object:
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install python3-yaml or pyyaml.")
    return yaml.safe_load(text) or {}


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
        text = fetch_text(workflow["url"])
    except Exception as exc:
        return [], [], [scan_error(workflow, "download", exc)]
    try:
        data = yaml_load(text)
    except Exception as exc:
        return [], [], [scan_error(workflow, "parse", exc)]
    retired: list[dict] = []
    arch: list[dict] = []
    errors = []
    for name, scanner, results in [("retired", retired_hits, retired), ("macos-arch", arch_hits, arch)]:
        if command not in (name, "all"):
            continue
        try:
            results.extend(scanner(workflow, data))
        except Exception as exc:
            errors.append(scan_error(workflow, "check", ScanError(f"{name}: {exc}")))
    return retired, arch, errors


def parallel_scan(
    workflows: list[dict], command: str, workers: int
) -> tuple[list[dict], list[dict], int, list[dict]]:
    retired, arch, errors = [], [], []
    scanned = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(scan_workflow, workflow, command): workflow for workflow in workflows}
        for future in as_completed(futures):
            try:
                rows, candidates, failures = future.result()
                retired.extend(rows)
                arch.extend(candidates)
                errors.extend(failures)
                scanned += not failures
            except Exception as exc:
                errors.append(scan_error(futures[future], "check", exc))

    def key(row):
        return tuple(row.get(field, "") for field in ("repo", "path", "job", "runner", "evidence"))

    return sorted(retired, key=key), sorted(arch, key=key), scanned, errors


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
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(scan_error({}, "output", exc))
    for error in errors:
        print(f"error: {error['repo']} {error['path']} [{error['stage']}]: {error['error']}", file=sys.stderr)
    print(
        f"scan_complete={str(not errors).lower()}\nworkflows_scanned={scanned}\nscan_errors={len(errors)}",
        file=sys.stderr,
    )
    return 1 if errors else 0


def output_prefix(args: argparse.Namespace, repos: list[str]) -> str:
    return scope_key(
        args.scope_name
        or (repos[0] if len(repos) == 1 else "repo-set" if repos or args.repo_file else args.owner)
    )


def main() -> int:
    args = parse_args()
    repos = sorted(set(args.repo))
    try:
        if args.repo_file:
            repos = sorted(set(repos + load_repo_file(args.repo_file)))
        if sys.version_info < (3, 11):  # noqa: UP036 - the standalone system Python may be older
            raise ScanError("Python 3.11 or newer is required")
        if yaml is None:
            raise ScanError("PyYAML is required. Install python3-yaml or pyyaml.")
        if shutil.which("gh") is None:
            raise ScanError("GitHub CLI (gh) is required")
        if args.repo_file and not repos:
            raise ScanError("The explicit repository list is empty")
    except (ScanError, OSError, UnicodeError) as exc:
        finish_scan(args, output_prefix(args, repos), 0, [scan_error({}, "prerequisite", exc)])
        return 2
    prefix = output_prefix(args, repos)
    errors: list[dict] = []
    scanned, stage = 0, "repository-list"
    try:
        workflows, errors = (
            load_workflows_for_repos(repos, args.workers)
            if repos
            else load_workflows(args.cache_dir, args.owner, args.refresh, args.workers)
        )
        stage = "scan"
        retired, arch, scanned, failures = parallel_scan(workflows, args.command, args.workers)
        errors.extend(failures)
        stage = "output"
        write_findings(args, prefix, retired, arch)
    except Exception as exc:
        errors.append(scan_error({}, stage, exc))
    return finish_scan(args, prefix, scanned, errors)


if __name__ == "__main__":
    raise SystemExit(main())
