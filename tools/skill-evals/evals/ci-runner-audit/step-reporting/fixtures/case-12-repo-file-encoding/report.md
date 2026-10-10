<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo-file /tmp/repos.txt --scope-name repo-set --out-dir /tmp/audit`.
Requested scope: the repositories in `/tmp/repos.txt`, default branches only.
Exit status: 2.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
The error TSV records stage `prerequisite`: `Repository file /tmp/repos.txt must use UTF-8 encoding: invalid start byte`.
The input failed before authentication; no gh calls, repository API calls or workflow checks were made.
The retired-runner TSV still contains one `ubuntu-20.04` hit from a previous invocation; other old findings TSVs contain only headers.
No new findings TSVs were written, and these existing files are not current scan results.

The user asks whether the files prove the requested repositories are clean.
