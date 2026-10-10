<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# ci-runner-audit evals

Behavioral evals for the `ci-runner-audit` skill.

## Suites (16 cases total)

| Suite | Step | Cases | What it covers |
|---|---|---|---|
| step-scope-selection | Scope selection and command choice | 4 | explicit repo, ambiguous Apache project, full-org scan, prompt injection ignored |
| step-reporting | Reporting discipline | 12 | high-confidence vs broad candidates, CI-risk language, incomplete scans, prerequisite and input-encoding failures, complete clean scans, unknown completeness, invalid refs, malformed entries, cache failures, partial checks |

## Run

```bash
# All cases
PYTHONPATH=tools/skill-evals/src python3 -m skill_evals.runner \
    tools/skill-evals/evals/ci-runner-audit/

# Single suite
PYTHONPATH=tools/skill-evals/src python3 -m skill_evals.runner \
    tools/skill-evals/evals/ci-runner-audit/step-scope-selection/fixtures/

# Single case
PYTHONPATH=tools/skill-evals/src python3 -m skill_evals.runner \
    tools/skill-evals/evals/ci-runner-audit/step-scope-selection/fixtures/case-4-injection-ignored
```

## What the suites cover

### step-scope-selection

Given a maintainer request, the model determines whether the scan scope
is explicit enough to run immediately or whether it must ask a scope
question first. The suite also checks that a prompt-injection attempt in
user-supplied text is flagged and ignored.

### step-reporting

Given mock TSV output, the model determines how to report findings. The
suite asserts that setup-action mismatches are high-confidence, broad
macOS candidates are marked false-positive-prone, and runner findings
are described as CI breakage / portability risks rather than security
vulnerabilities.
It also asserts that empty findings from an incomplete scan are never reported as clean, that successful findings retain their partial-scan qualification, and that stale files after a prerequisite failure are not treated as current results.
The discovery regressions distinguish invalid-ref 404s and malformed workflow-list entries from absent workflows.
Cache-publication and individual-check failures keep their successful findings reportable as partial results, even when all files were enumerated or no file completed every check.
Invalid repo-file encoding stops before repository discovery; previous findings files are not results of that failed invocation.
