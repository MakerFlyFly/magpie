<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 plugins/magpie-repo-health/skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo example/demo --out-dir /tmp/audit`.
Scope: example/demo's default branch.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
All findings TSVs are header-only.
The directory API succeeded, but one workflow-list entry lacked its required path/type fields.
The scan-errors TSV reports stage `workflow-list` and an unexpected-entry diagnostic.
Explain whether the repository was fully audited and can be reported as clean.
