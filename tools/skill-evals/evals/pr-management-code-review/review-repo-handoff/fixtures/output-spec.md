<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Determine the repository argument for each renderer if its branch of the review flow is reached.
Return only a JSON object with `render_repo`, `slop_comment_repo`, `footer_project` and `pass_head_pushable`.
Each repository field holds the single `owner/name` value following `--repo`, for example `"example/widgets"`.
Use JSON `null` when that renderer omits the option.
For `footer_project`, give the project name that appears in the review footer.
For `pass_head_pushable`, give a boolean indicating whether Step 6 should pass `--head-pushable` to `disposition`.
