<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 plugins/magpie-repo-health/skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner example --cache-dir /tmp/audit-cache --out-dir /tmp/audit`.
Scope: the example org's cached default branches.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
All findings TSVs are header-only.
The workflow directory request for example/demo returned HTTP 404, but ref verification also returned HTTP 404: no commit found for deleted-old-branch.
The scan-errors TSV reports stage `workflow-list`. No new workflow inventory was published.
Explain whether this means that the organization has no workflows or no runner findings.
