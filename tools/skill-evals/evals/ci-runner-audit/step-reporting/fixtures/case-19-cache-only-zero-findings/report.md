<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner example --refresh`.
Scope: example's default branches. All 12 enumerated workflow files completed all requested checks.
Exit status: 1. Summary: `scan_complete=false`, `workflows_scanned=12`, `scan_errors=1`.
All finding TSVs are header-only. The only scan-errors row has stage `cache`: inventory persistence failed.
Explain that workflow coverage is complete for the enumerated files while the invocation is incomplete due to cache persistence; do not claim a clean invocation.
