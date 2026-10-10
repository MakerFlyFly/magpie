<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo-file /tmp/repos.txt --out-dir /tmp/audit`.
The UTF-8 repository file resolves to one repository, example/demo.
Exit status: 2.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
Authentication failed; no repository API calls or workflow checks were made.
The current `example-demo-scan-errors.tsv` records stage `prerequisite` and the authentication error.
The `example-demo-retired-gh-runners-confirmed.tsv` still contains one hit from a previous invocation; the old architecture TSVs are header-only.
Both successful and failed commands use the example-demo prefix, but the old findings are not results of the failed invocation.
Report whether these files establish current findings or a clean scope.
