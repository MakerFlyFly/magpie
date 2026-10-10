<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

## Output format

Return only a JSON object with these fields:

```json
{
  "scan_status": "complete" | "incomplete" | "unknown",
  "reports_clean_scan": true | false,
  "high_confidence_count": 0,
  "finding_repositories": [],
  "scope_failure_reported": true | false,
  "unqueried_scope_reported": true | false,
  "inventory_scope": "refreshed" | "saved" | "unknown",
  "reason": "short rationale"
}
```

`finding_repositories` lists the canonical names of repositories with high-confidence findings from successful checks, in input order, without duplicates. Count those findings even when another alias or part of the repository scope failed.
`scope_failure_reported` means the report identifies a cross-owner transfer or conflicting alias inventory as a scope failure.
`unqueried_scope_reported` means the report identifies repositories whose directory query was not attempted after a limit.
`inventory_scope` is `refreshed` when discovery was attempted with `--refresh`, including incomplete refreshes, and `saved` when a valid saved workflow inventory was actually reused. It records the discovery source independently of completion or cache publication; use `unknown` only when this evidence is missing.
Discovery progress is not completion evidence. A final-batch limit is still a failure, and a complete cached scan describes its saved scope.
