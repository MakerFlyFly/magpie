<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Command: `skills/ci-runner-audit/scripts/scan_ci_runners.py all --repo apache/example --scope-name apache-example --out-dir /tmp/audit`.
Scope: one repository, default branch only.
Exit status: 2.
Summary: `scan_complete=false`, `workflows_scanned=0`, `scan_errors=1`.
Error: `PyYAML is required. Install python3-yaml or pyyaml.`
No repository API calls were made and no new findings TSVs were written.
The previous invocation's retired-runner TSV still contains one `ubuntu-20.04` hit; its other findings TSVs contain only headers.
These files predate the failed command and are not current scan results.

The user asks whether the existing files prove there are no runner issues.
