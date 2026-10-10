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
Within the selected owner, repository renames and case changes use the returned canonical name for results and URLs.
A repository transferred outside that owner is not scanned: its requested and resolved names are reported with a `--refresh` reminder.
Aliases resolving to the same repository and case-sensitive workflow path are scanned once; saved cache rows are also deduplicated locally without new identity queries.
If aliases return conflicting branches or Commit SHAs, the first validated inventory is kept and the conflict makes enumeration incomplete; a successful alias never erases another alias's errors.
These identity checks run only during directory enumeration. A valid workflow cache remains a manually refreshed snapshot; `--refresh` updates both repository and workflow inventories.
An empty repository requires `isEmpty=true`.
A non-empty repository has no workflow directory only when its valid Commit's directory field is explicitly null and a `NOT_FOUND` error identifies exactly that field.
Missing fields, unexplained nulls, repository/ref errors and unknown errors make the scan incomplete.
Structured GraphQL partial responses preserve valid repositories and entries even when `gh` exits non-zero; ordinary command failures remain errors.
Rate limits stop later batches and report the unqueried repositories without automatic retry or a REST-directory fallback.
Limit detection uses HTTP status, typed errors and explicit provider error diagnostics, including errors without `data` and secondary-limit messages returned with HTTP 200.
An unusable response remains an error; recognizing a limit does not make its partial data usable.
Even a final batch with otherwise verified absent directories retains the limit failure.
Explicit repository scans keep REST directory enumeration and validated SHA retries.

Discovery progress is written and flushed to stderr: `discovery_stage=repositories` precedes a REST repository-list request, and `discovery_batch=i/n` follows each attempted directory batch.
Progress counts attempts, not successful coverage; only the completion summary determines whether the audit is complete.
A valid workflow-cache hit performs no discovery or current-owner probe and prints no discovery progress.

## Explicit repository discovery

For explicit repositories, a workflow-directory HTTP 404 means no workflows only after the target ref resolves to a valid commit SHA and a retry at that SHA also returns 404.
This path retains the Contents API's download URLs and ref verification for explicitly selected repositories, which need not be public; organization batching follows the public repository listing.
See the [Contents API contract](https://docs.github.com/en/rest/repos/contents#get-repository-content) for download URL behavior.
A ref-validation HTTP 409 explicitly reporting `Git Repository is empty.` also means no workflows; other conflicts remain errors.
A missing or inaccessible ref, failed verification, or malformed response makes the scan incomplete.
A successful empty listing also means no workflows.
