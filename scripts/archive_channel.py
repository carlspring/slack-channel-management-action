#!/usr/bin/env python3
"""
Archives the Slack channel matching a GitHub issue when the configured
archive label (default: "slack:archived") is added to that issue.

Required env vars (set by action.yml):
    SLACK_BOT_TOKEN, ARCHIVE_LABEL, CHANNEL_PREFIX
Uses GITHUB_EVENT_PATH and GITHUB_OUTPUT, both provided automatically by GitHub Actions.
"""

import os
import sys
import urllib.error

from slack_common import (
    build_channel_name,
    build_channel_url,
    find_existing_channel_id,
    get_workspace_url,
    label_triggered,
    load_event,
    slack_post,
    write_output,
)


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    archive_label = os.environ["ARCHIVE_LABEL"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()

    event = load_event()

    if not label_triggered(event, archive_label):
        print(f"Issue does not have the '{archive_label}' label (or it wasn't the label just added), skipping archive.")
        write_output("skipped", "true")
        return

    repo_name = event["repository"]["name"]
    issue_num = event["issue"]["number"]

    # Same deterministic naming as creation, so we can find the channel
    # without needing to have stored its ID anywhere.
    channel_name = build_channel_name(channel_prefix, repo_name, issue_num)

    print(f"Looking up Slack channel '{channel_name}' to archive...")
    channel_id = find_existing_channel_id(token, channel_name)

    if not channel_id:
        print(f"::warning::No Slack channel named '{channel_name}' was found; nothing to archive.")
        write_output("skipped", "true")
        return

    print(f"Archiving channel '{channel_name}' ({channel_id})...")
    archive_resp = slack_post(token, "conversations.archive", {"channel": channel_id})

    if not archive_resp.get("ok"):
        error = archive_resp.get("error")
        if error == "already_archived":
            print(f"Channel '{channel_name}' was already archived.")
        elif error == "not_in_channel":
            print(
                "::error::Bot is not a member of this channel and can't archive it. "
                "This can happen if the channel was created outside this action. "
                "Invite the bot to the channel manually, then re-run."
            )
            sys.exit(1)
        else:
            print(f"::error::Slack API error archiving channel: {error}")
            sys.exit(1)

    workspace_url = get_workspace_url(token)
    channel_url = build_channel_url(workspace_url, channel_id)

    write_output("channel-id", channel_id)
    write_output("channel-name", channel_name)
    write_output("channel-url", channel_url)
    write_output("skipped", "false")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"::error::HTTP error calling Slack API: {exc.code} {body}")
        sys.exit(1)
