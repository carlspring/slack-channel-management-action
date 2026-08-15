# slack-channel-management-action

A composite GitHub Action that creates a (private, by default) Slack channel
whenever an issue is labeled (default label: `slack`), invites a configured
list of Slack users, and optionally comments back on the issue with the
channel name.

Logic is implemented in Python (`scripts/create_channel.py`,
`scripts/post_comment.py`), using only the standard library (`urllib`,
`json`) — no `pip install` step needed, since `python3` is preinstalled on
GitHub-hosted runners.

Channel names follow the pattern:

```
<channel-prefix>-<repo-name>-<issue-number>
```

e.g. `ol-my-repo-42`, or just `my-repo-42` if `channel-prefix` is left blank
(the default).

## Setup

### 1. Create a Slack app / bot token

1. Create an app at https://api.slack.com/apps and install it to your workspace.
2. Add these Bot Token scopes:
   - `channels:manage` — create public channels
   - `groups:write` — create private channels (needed since `private` defaults to `true`)
   - `chat:write` — post the confirmation message
   - `users:read` — look up user IDs from full names passed to `invite-user-ids`
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
`.github/workflows/` directory. It references this action as
`carlspring/slack-channel-management-action@v1` — tag/publish this repo
accordingly, or point at a commit SHA while developing.

## Inputs

| Input              | Required | Default  | Description                                                                 |
|---------------------|----------|----------|-------------------------------------------------------------------------------|
| `slack-bot-token`   | yes      | —        | Slack bot OAuth token (`xoxb-...`).                                          |
| `label`             | no       | `slack`  | Issue label that triggers channel creation.                                  |
| `channel-prefix`    | no       | `""`     | Optional prefix for the channel name (channel is `<repo>-<issue-number>` if left blank). |
| `invite-user-ids`   | no       | `""`     | Comma-separated list of Slack user IDs and/or full names (e.g. `@Martin Todorov`) to invite. Names are resolved to IDs automatically. |
| `private`           | no       | `true`   | Create a private channel (restricts access to invited users only).          |
| `post-comment`      | no       | `true`   | Comment back on the issue with the channel name.                             |
| `github-token`      | no       | `""`     | Token used to post the issue comment (required if `post-comment` is `true`). |

## Outputs

| Output         | Description                                                |
|----------------|--------------------------------------------------------------|
| `channel-id`   | Slack ID of the created (or pre-existing) channel.          |
| `channel-name` | Name of the created (or pre-existing) channel.               |
| `skipped`      | `"true"` if the run skipped because the label wasn't present.|

## Requirements

- `python3` on the runner (preinstalled on `ubuntu-latest`; no extra setup
  step or `pip install` required, since the scripts only use the standard
  library).

## Notes

- The action is idempotent: if the channel already exists (`name_taken`), it
  looks up the existing channel and still runs invites/comment against it,
  so re-triggers (e.g. `opened` + `labeled` firing close together, or an
  issue being reopened) won't fail the workflow.
- `private: true` is what restricts the channel to invited users — Slack
  private channels aren't visible or joinable by anyone else in the
  workspace.
- Name resolution fetches the full workspace member list once per run (via
  paginated `users.list` calls) and matches case-insensitively against
  display name, real name, and username. If your workspace has two people
  with identical display names, disambiguate by using their Slack user ID
  instead.
