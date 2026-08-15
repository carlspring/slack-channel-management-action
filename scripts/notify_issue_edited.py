#!/usr/bin/env python3
"""
Posts a Slack message summarizing an issue edit (title and/or description
changes) in that issue's channel.

Required env vars: SLACK_BOT_TOKEN, CHANNEL_PREFIX
Uses GITHUB_EVENT_PATH, provided automatically by GitHub Actions.

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
)


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()

    event = load_event()

    if event.get("action") != "edited":
        return

    issue = event.get("issue") or {}
    if "pull_request" in issue:
        return  # this is a PR being edited, not an issue

    repo_name = event["repository"]["name"]
    issue_num = issue.get("number")

    channel_name = build_channel_name(channel_prefix, repo_name, issue_num)
    channel_id = find_existing_channel_id(token, channel_name)

    if not channel_id:
        print(f"No Slack channel '{channel_name}' found for issue #{issue_num}, skipping.")
        return

    issue_url = issue.get("html_url", "")
    changes = event.get("changes") or {}

    lines = [f"<{issue_url}|Issue #{issue_num}> was updated:"]

    if "title" in changes:
        old_title = escape_slack_text(changes["title"].get("from", ""))
        new_title = escape_slack_text(issue.get("title") or "")
        lines.append(f"\u2022 Title: ~{old_title}~ \u2192 *{new_title}*")

    if "body" in changes:
        lines.append("\u2022 Description was edited.")

    if len(lines) == 1:
        lines.append("\u2022 Details were updated.")

    text = "\n".join(lines)

    resp = slack_post(token, "chat.postMessage", {"channel": channel_id, "text": text})
    if not resp.get("ok"):
        print(f"::warning::Slack API error posting edit notification: {resp.get('error')}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"::warning::HTTP error posting edit notification: {exc.code} {body}")
        sys.exit(0)
