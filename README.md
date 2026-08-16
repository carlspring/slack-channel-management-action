# slack-channel-management-action

A composite GitHub Action that creates a (private, by default) Slack channel
whenever an issue is labeled (default label: `slack`), invites a configured
list of Slack users, archives that channel again when a second label is
applied (default: `slack:archived`), relays PR-opened / new-comment /
issue-edited activity on the issue into that channel, and optionally
comments back on the GitHub issue when the channel is created or archived.

Logic is implemented in Python (`scripts/create_channel.py`,
`scripts/archive_channel.py`, `scripts/notify_pr_opened.py`,
`scripts/notify_issue_comment.py`, `scripts/notify_issue_edited.py`,
`scripts/slack_common.py`, `scripts/post_comment.py`,
`scripts/post_archive_comment.py`), using only the standard library
(`urllib`, `json`) — no `pip install` step needed, since `python3` is
preinstalled on GitHub-hosted runners.

Channel names follow the pattern:

```
<channel-prefix>-<repo-name>-<issue-number>
```

e.g. `ol-my-repo-42`, or just `my-repo-42` if `channel-prefix` is left blank
(the default).

### Archiving

Applying the `archive-label` (default `slack:archived`) to the same issue
archives its Slack channel. There's no stored mapping between issue and
channel — the action just rebuilds the same deterministic name
(`channel-prefix`-`repo`-`issue-number`) used at creation time and looks it
up via `conversations.list`, so `channel-prefix` must stay the same between
creating and archiving a given channel. If no matching channel is found
(e.g. it was already renamed or deleted, or the create label was never
applied), archiving is skipped with a `::warning::` rather than failing the
run.

### Activity notifications

Three more triggers relay GitHub activity on an issue into its Slack
channel, once that channel exists. All three locate the channel the same
way archiving does (rebuild the deterministic name, look it up) and quietly
no-op — no warning — if no matching channel is found, since most issues in
a repo won't have one.

- **Pull request opened** (`pull_request: opened`) — if the PR's title or
  body references an issue (a closing keyword like `fixes #42`/`closes
  #42`/`resolves #42`, or failing that, a bare `#42`), a message is posted
  naming the PR author and linking to the PR:
  > 🔀 @githubUserXYZ opened a pull request for this issue: #55 : Fix login crash
- **Comment posted** (`issue_comment: created`, on issues only — not PR
  review comments) — the comment is quoted in full (truncated past 600
  characters) with a link to it and the commenter's GitHub profile:
  > 💬 @janedoe commented:
  > > I think this is a race condition and needs a mutex.

  Comments from bots (including this action's own "channel created/archived"
  comments) are skipped by default — set `ignore-bot-comments: false` to
  include them.
- **Issue edited** (`issues: edited`) — if the title and/or description
  changed, a summary is posted with a link back to the issue:
  > Issue #42 was updated:
  > • Title: ~Fix login crash~ → *Fix login crash on retry*
  > • Description was edited.

None of these need any Slack scope beyond `chat:write`, which you already
have for the creation message. They do need the corresponding event types
added to your workflow's `on:` block — see `examples/.github/workflows/slack-channel.yml`.

## Setup

### 1. Create a Slack app / bot token

1. Create an app at https://api.slack.com/apps and install it to your workspace.
2. Add these Bot Token scopes:
   - `channels:manage` — create public channels, and archive them
   - `groups:write` — create private channels (needed since `private` defaults to `true`), and archive them
   - `chat:write` — post the confirmation message
   - `users:read` — look up user IDs from full names passed to `invite-user-ids`, and build the clickable channel link (`auth.test`)
3. Copy the Bot User OAuth Token (`xoxb-...`).

If you add scopes to an app that's already installed, you must click
**Reinstall to Workspace** on the OAuth & Permissions page afterwards —
adding a scope alone doesn't update the existing token, and you'll get a
`missing_scope` API error until you reinstall and copy the new token.

### 2. Configure the consuming repo

In the repo where issues are filed (Settings → Secrets and variables → Actions):

**Secrets:**
- `SLACK_BOT_TOKEN` — the bot token from step 1.

**Variables** (not sensitive, so plain repo Variables rather than Secrets):
- `SLACK_CHANNEL_PREFIX` — e.g. `ol`
- `SLACK_ARCHIVE_LABEL` — optional; defaults to `slack:archived` if unset.
- `SLACK_INVITE_USER_IDS` — comma-separated list of people to invite. Each
  entry can be either a Slack user ID (e.g. `U0123ABCD`) or a full name
  (e.g. `@Martin Todorov` or `Martin Todorov`) — you don't need to know
  anyone's user ID. Names are resolved behind the scenes via Slack's
  directory (`users.list`), matching against display name, real name, or
  username, case-insensitively and ignoring a leading `@`.
  Example: `U0123ABCD,@Martin Todorov,jane.doe`.
  If a name can't be matched to exactly one workspace member, that entry is
  skipped with a `::warning::` in the job log — the run still succeeds and
  invites everyone who did resolve.

### 3. Add the workflow

Copy `examples/.github/workflows/slack-channel.yml` into the consuming repo's
`.github/workflows/` directory. Its `on:` block covers every trigger this
action uses:

```yaml
on:
  issues:
    types: [opened, labeled, edited]
  pull_request:
    types: [opened]
  issue_comment:
    types: [created]
```

If you only want channel creation/archiving and don't need the activity
notifications, you can drop the `pull_request` and `issue_comment` triggers
(and `edited` from `issues`) — the corresponding steps simply won't run.

It references this action as `carlspring/slack-channel-management-action@v1`
— tag/publish this repo accordingly, or point at a commit SHA while
developing.

## Inputs

| Input              | Required | Default  | Description                                                                 |
|---------------------|----------|----------|-------------------------------------------------------------------------------|
| `slack-bot-token`   | yes      | —        | Slack bot OAuth token (`xoxb-...`).                                          |
| `label`             | no       | `slack`  | Issue label that triggers channel creation.                                  |
| `archive-label`     | no       | `slack:archived` | Issue label that triggers archiving the channel.                    |
| `channel-prefix`    | no       | `""`     | Optional prefix for the channel name (channel is `<repo>-<issue-number>` if left blank). |
| `invite-user-ids`   | no       | `""`     | Comma-separated list of Slack user IDs and/or full names (e.g. `@Martin Todorov`) to invite. Names are resolved to IDs automatically. |
| `private`           | no       | `true`   | Create a private channel (restricts access to invited users only).          |
| `post-comment`      | no       | `true`   | Comment back on the issue when the channel is created or archived.          |
| `ignore-bot-comments` | no     | `true`   | Skip relaying issue comments authored by bots to Slack.                     |
| `github-token`      | no       | `""`     | Token used to post the issue comment (required if `post-comment` is `true`). |

## Outputs

| Output         | Description                                                |
|----------------|--------------------------------------------------------------|
| `channel-id`   | Slack ID of the created (or pre-existing) channel.          |
| `channel-name` | Name of the created (or pre-existing) channel.               |
| `channel-url`  | Clickable URL to the channel (used to link it in the issue comment). |
| `skipped`      | `"true"` if the create run skipped because the create label wasn't present/wasn't the one just added. |
| `archived-channel-id`   | Slack ID of the archived channel, if archiving ran.  |
| `archived-channel-name` | Name of the archived channel, if archiving ran.      |
| `archive-skipped`       | `"true"` if archiving was skipped (label not present/not just added, or channel not found). |

## Requirements

- `python3` on the runner (preinstalled on `ubuntu-latest`; no extra setup
  step or `pip install` required, since the scripts only use the standard
  library).

## Notes

- The action is idempotent: if the channel already exists (`name_taken`), it
  looks up the existing channel and still runs invites/comment against it,
  so re-triggers (e.g. the label being removed and re-added, or two
  `labeled` deliveries racing each other) won't fail the workflow.
- `private: true` is what restricts the channel to invited users — Slack
  private channels aren't visible or joinable by anyone else in the
  workspace.
- Name resolution fetches the full workspace member list once per run (via
  paginated `users.list` calls) and matches case-insensitively against
  display name, real name, and username. If your workspace has two people
  with identical display names, disambiguate by using their Slack user ID
  instead.
- The issue comment links directly to the channel (e.g. `[#ol-my-repo-42](https://myteam.slack.com/archives/C0123ABCD)`).
  The link is built from your workspace's domain via `auth.test`, so
  clicking it opens the channel straight in Slack. If `auth.test` fails for
  any reason, the comment falls back to plain text (`#channel-name`)
  instead of failing the whole run.
- On creation, the channel's **topic** is set to a clickable link back to
  the issue in the form `#1234 : Fix this and that`, and the same text is
  posted as the channel's first message. This replaces the generic
  "Created for #12: test 11" wording with something that actually tells you
  which issue the channel is for, and stays visible in the channel header
  (not just buried at the top of history). If setting the topic fails for
  any reason (e.g. a scope issue), it's a warning, not a hard failure — the
  channel and message still get created.
- Create and archive only ever fire on an actual `labeled` GitHub event
  carrying the matching label name — never on `opened`, even if the issue
  already has the label at creation time. This is deliberate: GitHub fires
  a separate `labeled` webhook for every label attached during issue
  creation, in addition to `opened`, so a rule like "trigger on `opened`
  if the label is present" double-fires — once for `opened`, once for the
  immediately-following `labeled` event. Relying solely on `labeled` means
  the channel is created (or archived) exactly once, however the label got
  there.
- If the bot isn't a member of the channel it's trying to archive (e.g. the
  channel was created by hand rather than by this action), archiving fails
  with a clear error rather than silently no-op'ing — invite the bot to the
  channel and re-run.
- `create` and `archive` both run on every trigger and decide internally
  whether to act, rather than being conditionally skipped at the step
  level — a step that GitHub Actions skips outright leaves its outputs
  undefined, and undefined isn't `'true'`, which would trick a downstream
  step's `steps.x.outputs.skipped != 'true'` check into thinking it should
  proceed. Any future step gated on one of these two should follow the
  same self-guarding pattern rather than adding a step-level `if:`.
- The three activity-notification steps (PR opened, comment posted, issue
  edited) are best-effort: unlike channel creation/archiving, a Slack API
  error there logs a `::warning::` and moves on rather than failing the
  job, since they're a secondary layer on top of the core create/archive
  behavior.
- The PR-to-issue link is inferred from the PR's title/body text, not from
  any GitHub API relationship — GitHub doesn't expose "which issue does
  this PR close" directly in the `pull_request` webhook payload. A PR that
  doesn't mention the issue number anywhere won't trigger a notification.
