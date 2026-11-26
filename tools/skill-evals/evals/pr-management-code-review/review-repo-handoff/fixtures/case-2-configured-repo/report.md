<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

The project configuration sets `upstream_repo` to `acme/product` and `project_name` to `Apache Product`.
The maintainer requested `pr:7`.
The saved `code-review resolve` output is

```json
{"selector": {"repo": null, "pr_number": 7}}
```

The discovered, approved Git policy names `acme/product` as upstream on `github.com`.
It lists the checkout, worktree root and required caller grants. The contributor's unprotected fork branch allows maintainer edits, CI is green and the review has one minor mechanical fix.
