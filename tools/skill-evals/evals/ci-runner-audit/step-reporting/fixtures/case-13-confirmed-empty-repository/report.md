<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo example/empty --out-dir /tmp/audit`.
Scope: example/empty's default branch.
The directory request returned HTTP 404; ref validation returned HTTP 409 with the exact GitHub diagnostic `Git Repository is empty.`.
Exit status: 0.
Summary: `scan_complete=true`, `workflows_scanned=0`, `scan_errors=0`.
All findings TSVs and the scan-errors TSV are header-only and were written by this invocation.
Report the outcome without treating a confirmed empty repository as an invalid ref.
