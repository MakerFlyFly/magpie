<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Determine the repository argument for each renderer if its branch of the review flow is reached.
Return only a JSON object with `render_repo` and `slop_comment_repo`.
Each field holds the single `owner/name` value following `--repo`, for example `"example/widgets"`.
Use JSON `null` when that renderer omits the option.
