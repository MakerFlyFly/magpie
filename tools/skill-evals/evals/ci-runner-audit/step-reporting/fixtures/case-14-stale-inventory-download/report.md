<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner example --out-dir /tmp/audit`.
Scope: the example org's default branches, using an existing workflow inventory without refresh.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=1`, `scan_errors=1`.
The current retired-runner TSV contains one ubuntu-20.04 hit from the successfully checked file; both architecture TSVs are header-only.
The scan-errors TSV records another file's download HTTP 404 and the hint `Organization inventory may be stale; rerun with --refresh.`.
No automatic re-enumeration was performed.
Explain the retained finding and the incomplete coverage without asserting that refresh is guaranteed to fix the underlying HTTP error.
