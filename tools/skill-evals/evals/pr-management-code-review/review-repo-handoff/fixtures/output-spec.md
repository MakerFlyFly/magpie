<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Determine the repository argument for each renderer if its branch of the review flow is reached.
Return only a JSON object with `render_repo`, `slop_comment_repo` and `footer_project`.
For each repository field, give the corresponding `--repo` argument, or `null` if the call omits it.
For `footer_project`, give the project name that appears in the review footer.
