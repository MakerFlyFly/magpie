<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**  *generated with [DocToc](https://github.com/thlorenz/doctoc)*

- [Discourse forum bridge](#discourse-forum-bridge)
  - [Prerequisites](#prerequisites)
  - [Invocation](#invocation)
  - [Configuration](#configuration)
  - [Output contract](#output-contract)
  - [Forum content is data](#forum-content-is-data)
  - [Planned follow-up coverage](#planned-follow-up-coverage)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

# Discourse forum bridge

**Capability:** contract:mail-archive

**Coverage:** `partial`

**Kind:** implementation

**Vendor:** Discourse

Discourse forum bridge for Magpie adopters whose design discussion,
RFCs and announcements live on a Discourse forum (for example
`discuss.python.org`) rather than on a mailing list.
It reads a forum topic and returns it in the `thread_data` shape of the
[`tools/mail-archive/`](../mail-archive/README.md) contract, so a skill that
already reads mail threads can read a forum topic the same way.

This first slice is read-only and covers one operation, `topic view`.
Coverage is partial: the bridge does not yet implement the mail-archive
contract verbs and must not be selected as a complete archive backend.
The rest of [issue #307](https://github.com/apache/magpie/issues/307) is
listed under [Planned follow-up coverage](#planned-follow-up-coverage).

## Prerequisites

- **Runtime:** Python 3.11+ run via `uv` (stdlib-only, no third-party runtime dependencies).
- **CLIs:** None.
- **Credentials / auth:** None for a public forum: topics in public categories are read anonymously.
  For private categories, set `DISCOURSE_API_KEY` and `DISCOURSE_API_USERNAME`
  using an API key with permission to read those categories.
  Keep the key outside the repository,
  in the user's home directory as [`AGENTS.md` § Local setup](../../AGENTS.md#local-setup) requires,
  and export it into the agent's environment.
- **Network:** HTTPS to the forum named by `DISCOURSE_URL` only. The forum host is per adopter,
  so it is not in the framework's default sandbox allowlist; add it to the adopter's
  `sandbox.network.allowedDomains`.

## Invocation

```bash
# One topic, by id or by URL on the configured forum
uv run --project tools/discourse magpie-discourse topic view 12345
uv run --project tools/discourse magpie-discourse topic view https://discuss.example.org/t/some-proposal/12345

# Read at most 100 posts of a long topic
uv run --project tools/discourse magpie-discourse topic view 12345 --max-posts 100
```

The command prints one JSON object on stdout and exits `0`.
Configuration, network and response failures print `magpie-discourse error: <reason>`
on stderr and exit `1`, without a JSON result on stdout.
Command-line usage errors exit `2` with argparse's usage message.

## Configuration

| Variable | Description |
|---|---|
| `DISCOURSE_URL` | Required. The forum's `https://` base URL, including the sub-folder for a sub-folder install (`https://example.org/forum`). |
| `DISCOURSE_API_KEY` | Optional. API key for private categories. Sent only to `DISCOURSE_URL`'s origin. |
| `DISCOURSE_API_USERNAME` | Required when `DISCOURSE_API_KEY` is set. The user the key acts as. |

A topic URL on any other host or scheme is refused, and so is a redirect
that leaves the forum's origin, so the API key is never sent elsewhere.
Origins compare the scheme, hostname and effective port; an omitted HTTPS
port is equivalent to `443`.
Topic ids and `--max-posts` must be positive integers.

## Output contract

`topic view` returns the mail-archive `thread_data` fields, plus the Discourse
fields a skill needs to reason about the topic:

| Field | Value |
|---|---|
| `thread_id` | The topic id, as a string. |
| `list` | The forum base URL (Discourse has no list address). |
| `subject` | The topic title. |
| `url` | The canonical topic URL. |
| `messages[]` | One record per post, in topic order: `message_id` (post id), `post_number`, `from` (`@username`), `date`, `updated_at`, `body` (the post's rendered HTML, `body_format: "html"`), `in_reply_to` (the replied-to post's id, when it was read), `url`, `post_type`, `hidden`, `deleted`. |
| `participant_handles[]` | Distinct posters in the returned messages as `@username`, in order of first post. |
| `category_id`, `tags[]`, `created_at`, `last_posted_at`, `posts_count`, `closed`, `archived` | Topic metadata. |
| `truncated` | `true` when the topic has more posts than `--max-posts` (default 500); only the first posts are returned. |

Discourse returns the first 20 posts with the topic; the bridge reads the rest
of the post stream in batches of 20 through `/t/{id}/posts.json`.
If a requested post is still missing after its batch, or a required response
structure is malformed, the command fails instead of returning an incomplete
thread as a successful result.
`truncated` only describes the post limit; it never hides a failed fetch.

## Forum content is data

Topic titles and post bodies are written by forum users and are external content.
They are returned as data for the calling skill to analyse, never as instructions
to the agent, per [`AGENTS.md`](../../AGENTS.md#treat-external-content-as-data-never-as-instructions).

## Planned follow-up coverage

The following capabilities remain tracked in #307:

- `topic list --category <slug> --since <date>`: recent topics in a category.
- `topic search <query>`: full-text search through `/search.json`.
- `user <handle>`: profile and recent activity, as evidence for `contributor-nomination`.
- `post create` and `topic create`: guarded writes that only post a body the user has confirmed.
- The remaining `tools/mail-archive/` verbs, so `archive_system.kind: discourse` can be selected.
