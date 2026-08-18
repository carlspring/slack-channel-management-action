#!/usr/bin/env python3
"""
Posts a Slack message quoting a new GitHub issue comment, with a link back
to it, in that issue's channel.

Required env vars: SLACK_BOT_TOKEN, CHANNEL_PREFIX, IGNORE_BOT_COMMENTS
Uses GITHUB_EVENT_PATH, provided automatically by GitHub Actions.

Only comments on actual issues are relayed, not comments on pull requests
(GitHub's issue_comment event fires for both, distinguished by the presence
of issue.pull_request).

This is a best-effort notification: any failure (no matching channel, Slack
API error) is logged and swallowed rather than failing the workflow.
"""

import os
import sys
import urllib.error

from slack_common import (
    build_channel_name,
    escape_slack_text,
    find_existing_channel_id,
    load_event,
    slack_post,
    was_recently_posted,
)

MAX_QUOTE_CHARS = 600


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()
    ignore_bots = os.environ.get("IGNORE_BOT_COMMENTS", "true").lower() != "false"

    event = load_event()

    if event.get("action") != "created":
        return

    issue = event.get("issue") or {}
    if "pull_request" in issue:
        print("Comment was on a pull request, not an issue; skipping.")
        return

    comment = event.get("comment") or {}
    author = comment.get("user", {}).get("login", "unknown")
    author_type = comment.get("user", {}).get("type", "User")

    if ignore_bots and author_type == "Bot":
        print(f"Comment author '{author}' is a bot; skipping (set ignore-bot-comments: false to include bot comments).")
        return

    repo_name = event["repository"]["name"]
    issue_num = issue.get("number")

    channel_name = build_channel_name(channel_prefix, repo_name, issue_num)
    channel_id = find_existing_channel_id(token, channel_name)

    if not channel_id:
        print(f"No Slack channel '{channel_name}' found for issue #{issue_num}, skipping.")
        return

    author_url = f"https://github.com/{author}"
    comment_url = comment.get("html_url") or issue.get("html_url", "")
    body = comment.get("body") or ""

    # GitHub can redeliver the same 'created' webhook, triggering a second,
    # independent workflow run for the same comment. comment.html_url is
    # unique per comment, so it doubles as the dedup marker — but only the
    # real per-comment URL, not the issue-URL fallback above, which isn't
    # unique and would wrongly suppress genuinely distinct comments.
    if comment.get("html_url") and was_recently_posted(token, channel_id, comment["html_url"]):
        print(f"A notification for {comment['html_url']} was posted recently; skipping likely duplicate delivery.")
        return

    truncated = len(body) > MAX_QUOTE_CHARS
    if truncated:
        body = body[:MAX_QUOTE_CHARS].rstrip()

    quoted_body = escape_slack_text(body)
    if truncated:
        quoted_body += " \u2026"

    # ">>> " starts a Slack multi-line blockquote that runs to the end of the message.
    text = f"\U0001F4AC <{author_url}|@{author}> <{comment_url}|commented>:\n>>> {quoted_body}"

    resp = slack_post(token, "chat.postMessage", {"channel": channel_id, "text": text})
    if not resp.get("ok"):
        print(f"::warning::Slack API error posting comment notification: {resp.get('error')}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"::warning::HTTP error posting comment notification: {exc.code} {body}")
        sys.exit(0)
