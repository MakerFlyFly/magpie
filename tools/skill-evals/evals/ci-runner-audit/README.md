<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# ci-runner-audit evals

Behavioral evals for the `ci-runner-audit` skill.

## Suites (29 cases total)

| Suite | Step | Cases | What it covers |
|---|---|---|---|
| step-scope-selection | Scope selection and command choice | 4 | explicit repo, ambiguous Apache project, full-org scan, prompt injection ignored |
| step-reporting | Reporting discipline | 19 | high-confidence vs broad candidates, CI-risk language, incomplete scans, prerequisite and input-encoding failures, consistent failure prefixes, confirmed empty repositories, stale-inventory hints, unknown completeness, invalid refs, malformed entries, cache failures, partial checks |
| step-reporting-scope | Inventory scope and coverage | 6 | canonical renames, cross-owner transfers, conflicting aliases, unqueried scope after limits, final-batch limits, saved snapshot scope |

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
Confirmed empty Git repositories are complete zero-workflow scans, while stale workflow URLs remain incomplete results with a manual refresh hint.
Resolving a repo-file scope before authentication gives matching error and findings prefixes; old findings remain stale when authentication fails.

GraphQL partial success and unexplained directory nulls retain incomplete reporting; a directory-batch timeout preserves later successful findings without becoming a whole-audit deadline.
Cache-only failures distinguish completed workflow coverage from an incomplete invocation, including zero-finding audits.

### step-reporting-scope

Reports use canonical names for successful same-owner renames and identify cross-owner transfers or conflicting inventories as scope failures.
Rate-limit progress does not prove complete coverage, and a limit in the final batch still makes the invocation incomplete.
A complete audit using a saved workflow inventory reports that snapshot's scope without claiming current organization membership was checked.
