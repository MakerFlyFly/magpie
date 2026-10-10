<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `python3 plugins/magpie-repo-health/skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo example/demo --out-dir /tmp/audit`.
Scope: example/demo's default branch; one workflow was downloaded and parsed once.
Exit status: 1.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
The retired-runner TSV contains one ubuntu-20.04 hit with an html_url.
Both architecture TSVs are header-only.
The scan-errors TSV names example/demo, .github/workflows/ci.yml, stage `check`, and `macos-arch: RuntimeError: check failed`.
The retired check succeeded; the architecture check failed on the same file.
Explain the retained finding and why zero fully checked workflows does not make the finding stale or the scan clean.
