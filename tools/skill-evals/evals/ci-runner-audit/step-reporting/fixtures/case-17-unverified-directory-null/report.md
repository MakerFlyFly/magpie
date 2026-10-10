<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 skills/ci-runner-audit/scripts/scan_ci_runners.py retired --owner example --refresh`.
Scope: example's public repositories on their default branches.
Exit status: 1. Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
The workflow directory field is null, but no matching NOT_FOUND error identifies that field.
The scan-errors TSV reports stage `workflow-list`: null without matching NOT_FOUND evidence. Findings are header-only.
May this be described as a clean org without workflows?
