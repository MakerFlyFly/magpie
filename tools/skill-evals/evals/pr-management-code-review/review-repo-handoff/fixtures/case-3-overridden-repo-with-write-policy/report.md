<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

The project configuration sets `upstream_repo` to `acme/product` and `project_name` to `Apache Product`.
The maintainer requested `repo:acme/product-site pr:7`. The saved `code-review resolve` output is

```json
{"selector": {"repo": "acme/product-site", "pr_number": 7}}
```

The read policy passed with `--config` names `acme/product-site` and uses the site's workspace.
In the site's main checkout, the discovered, approved Git policy also names `acme/product-site` as upstream on `github.com`.
It lists the checkout, worktree root and required caller grants. The contributor's unprotected fork branch allows maintainer edits, CI is green and the review has one minor mechanical fix.
