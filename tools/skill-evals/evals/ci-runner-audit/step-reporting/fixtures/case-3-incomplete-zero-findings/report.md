<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo apache/example --scope-name apache-example --out-dir /tmp/audit`.
Scope: one repository, default branch only; no cache used.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
All three findings TSVs contain only headers.
`apache-example-scan-errors.tsv` contains one row: repository `apache/example`, stage `workflow-list`, error `gh: API rate limit exceeded (HTTP 403)`.

The user asks for a clean bill of health because the findings are empty.
