<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 plugins/magpie-repo-health/skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner example --out-dir /tmp/audit --refresh`.
Scope: the example org's default branches. Both workflow files were enumerated and checked.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=2`, `scan_errors=1`.
The retired-runner TSV contains one ubuntu-20.04 hit with an html_url.
Both architecture TSVs are header-only.
The scan-errors TSV reports stage `cache`: permission denied while publishing example-workflow-files-v3.tsv.
Explain the actionable finding and reporting posture, even though every enumerated workflow was checked.
