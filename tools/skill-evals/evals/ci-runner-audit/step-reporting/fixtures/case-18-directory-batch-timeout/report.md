<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner example --gh-api-timeout 5 --refresh`.
Scope: example's default branches.
Exit status: 1. Summary: `scan_complete=false`, `workflows_scanned=1`, `scan_errors=50`.
The first GraphQL batch timed out after five seconds; partial stdout was discarded. A later batch succeeded.
The retired-runner TSV contains one ubuntu-20.04 finding with an html_url. Architecture TSVs are header-only.
The timeout records have stage `workflow-list` and name the affected repositories.
Explain the partial scan, without describing five seconds as the whole-audit deadline.
