#!/usr/bin/env python3
"""
Posts a Slack message in the issue's channel when a pull request that
references that issue (via a closing keyword like "fixes #123", or a bare
"#123") is opened.

Required env vars: SLACK_BOT_TOKEN, CHANNEL_PREFIX
Uses GITHUB_EVENT_PATH, provided automatically by GitHub Actions.

This is a best-effort notification: any failure (no issue reference found,
no matching channel, Slack API error) is logged and swallowed rather than
failing the workflow, since it's a secondary feature layered on top of
channel creation/archiving.
"""

import os
import sys
import urllib.error

from slack_common import (
    build_channel_name,
    escape_slack_text,
    extract_referenced_issue_number,
    find_existing_channel_id,
    load_event,
    slack_post,
)


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()

    event = load_event()

    if event.get("action") != "opened":
        return

    pr = event.get("pull_request")
    if not pr:
        return

    repo_name = event["repository"]["name"]
    issue_num = extract_referenced_issue_number(pr.get("title"), pr.get("body"))

    if issue_num is None:
        print("No issue reference (e.g. 'Fixes #123') found in the PR title/body, skipping Slack notification.")
        return

    channel_name = build_channel_name(channel_prefix, repo_name, issue_num)
    channel_id = find_existing_channel_id(token, channel_name)

    if not channel_id:
        print(f"No Slack channel '{channel_name}' found for issue #{issue_num}, skipping.")
        return

    pr_author = pr["user"]["login"]
    pr_author_url = f"https://github.com/{pr_author}"
    pr_url = pr["html_url"]
    pr_number = pr["number"]
    pr_title = escape_slack_text(pr.get("title"))

    text = f"\U0001F500 <{pr_author_url}|@{pr_author}> opened a pull request for this issue: <{pr_url}|#{pr_number} : {pr_title}>"

    resp = slack_post(token, "chat.postMessage", {"channel": channel_id, "text": text})
    if not resp.get("ok"):
        print(f"::warning::Slack API error posting PR notification: {resp.get('error')}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"::warning::HTTP error posting PR notification: {exc.code} {body}")
        sys.exit(0)
