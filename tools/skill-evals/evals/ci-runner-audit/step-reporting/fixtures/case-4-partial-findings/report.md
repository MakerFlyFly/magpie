<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --owner apache --scope-name example-project --cache-dir /tmp/audit-cache --out-dir /tmp/audit --refresh`.
Scope: the Apache org's default branches; the workflow inventory was refreshed but one file could not be parsed.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=2`, `scan_errors=1`.
The retired-runner TSV contains one `ubuntu-20.04` hit with an html_url.
The setup-action TSV and broad-candidate TSV are header-only.
The scan-errors TSV names `apache/other`, `.github/workflows/ci.yml`, stage `parse`, and a YAML syntax error.

Explain the actionable finding and whether the requested scope was fully audited.
