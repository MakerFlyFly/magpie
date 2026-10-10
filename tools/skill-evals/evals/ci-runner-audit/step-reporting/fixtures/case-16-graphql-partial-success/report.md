<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner example --refresh`.
Scope: example's public non-archived repositories, default branches.
Exit status: 1. Summary: `scan_complete=false`, `workflows_scanned=49`, `scan_errors=1`.
A GraphQL directory batch returned valid aliases and one repository-level FORBIDDEN error.
The retired-runner TSV contains one ubuntu-20.04 hit with an html_url. Both architecture TSVs are header-only.
The failed repository was not checked; the new workflow inventory was not published.
Report the preserved finding and scope limitations.
