<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# KEYS backend — `git` (`github.com`) and other non-ASF URLs

For a non-ASF adopter whose `keys_file_url` points to a GitHub repository,
`svn_command_sequence` holds the equivalent `git` commands: clone the repository, branch from the file's branch,
leave the append to the RM, commit, push, and open a PR in the browser for review (the RM runs the last line too; the skill never opens it).
The RM pushes to their fork instead when they lack write access to the repository.

The GitHub web forms are `https://github.com/<owner>/<repo>/blob/<branch>/<path>` and `https://github.com/<owner>/<repo>/raw/<branch>/<path>`.
The direct Raw form is `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>`.
Both Raw forms accept an optional `refs/heads/` immediately before the branch.
The Blob form keeps `refs/heads/` in the parsed path, so with `keys_file_branch` unset, `keys-sync` reads `blob/refs/heads/main/KEYS` as branch `refs` and file `heads/main/KEYS`.
For a `blob/refs/heads/<branch>/<path>` link, use the corresponding `raw/refs/heads/<branch>/<path>` URL for `keys_file_url`.
The first segment of the parsed path is the branch, and the rest identifies the file, for example `dist/KEYS`.
The file must be named `KEYS`.
If the branch name contains `/`, set `keys_file_branch` in `release-management-config.md` to the actual branch name, such as `release/1.x`.
The parsed path must begin with that branch. The render refuses a mismatch and names both inputs.
Without `keys_file_branch`, a path in a sub-directory comes with a warning saying which segment was taken as the branch: surface it.
The derived branch and path are what the checkout, `git add` and PR base use.

For any other URL scheme the render prints generic instructions (fetch the KEYS file with the project's own workflow, append, publish with the given message);
tailor them to the URL in `keys_file_url` when presenting them, without running anything.
