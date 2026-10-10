<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# CI runner scanner details

The [skill](../SKILL.md) contains the commands, prerequisites and reporting rules.
Read these details when checking compatibility, inventory behavior or failed discovery.

## Authentication compatibility

It uses `gh auth status --active` when supported; only an explicit unsupported-flag error triggers a hostname-only retry for `gh` versions before 2.57.
The older command checks all configured accounts on `github.com`, so an invalid inactive account can also fail its prerequisite check; upgrade `gh` for active-account-only checking.

## Repository input failures

Invalid input encoding is a global prerequisite failure: exit `2`, zero workflows scanned, and a scan-errors TSV with the UTF-8 requirement.
Local repository inputs are resolved before dependency and authentication checks, so subsequent failures use the same output prefix as a successful invocation.
If the file cannot be read, the prefix uses the known CLI scope; use `--scope-name` for stable names even when the file is invalid.

## Organization inventories

Org scans use `<owner>-repos-v2.jsonl` and `<owner>-workflow-files-v3.tsv`; older unverified inventories are retained but ignored and rebuilt automatically.
Both inventories are published through atomic replacements only after complete, valid enumeration.
A failed refresh preserves previous complete inventories; the current scan uses current enumeration results without falling back to stale workflows.
Cache-write failures are reported at stage `cache` while valid in-memory workflows continue to be scanned.
Only a real HTTP 404 downloading from a reused, valid workflow inventory includes a reminder that the inventory may be stale; the scanner does not refresh automatically.
Fresh enumeration, damaged-cache rebuilds and failed refreshes do not count as workflow-cache hits.

Organization scans list public, non-archived repositories with REST pagination, then enumerate workflow directories in sequential GraphQL batches of at most 50 repositories.
Each batch reads the current default branch's Commit and its workflow Tree entries; only regular blob YAML files are scanned, excluding directories, symlinks and submodules.
Download and evidence URLs use that branch name; workflow content is not pinned to the enumeration commit.
An empty repository requires `isEmpty=true`.
A non-empty repository has no workflow directory only when its valid Commit's directory field is explicitly null and a `NOT_FOUND` error identifies exactly that field.
Missing fields, unexplained nulls, repository/ref errors and unknown errors make the scan incomplete.
Structured GraphQL partial responses preserve valid repositories and entries even when `gh` exits non-zero; ordinary command failures remain errors.
Rate limits stop later batches and report the unqueried repositories without automatic retry or a REST-directory fallback.
Explicit repository scans keep REST directory enumeration and validated SHA retries.

## Explicit repository discovery

For explicit repositories, a workflow-directory HTTP 404 means no workflows only after the target ref resolves to a valid commit SHA and a retry at that SHA also returns 404.
A ref-validation HTTP 409 explicitly reporting `Git Repository is empty.` also means no workflows; other conflicts remain errors.
A missing or inaccessible ref, failed verification, or malformed response makes the scan incomplete.
A successful empty listing also means no workflows.
