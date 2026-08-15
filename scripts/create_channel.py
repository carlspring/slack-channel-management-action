#!/usr/bin/env python3
"""
Creates a Slack channel for a labeled GitHub issue and invites configured users.

Required env vars (set by action.yml):
    SLACK_BOT_TOKEN, ISSUE_LABEL, CHANNEL_PREFIX, INVITE_USER_IDS, CHANNEL_PRIVATE
Uses GITHUB_EVENT_PATH and GITHUB_OUTPUT, both provided automatically by GitHub Actions.
"""

import os
import sys
import urllib.error

from slack_common import (
    build_channel_name,
    build_channel_url,
    build_issue_reference,
    find_existing_channel_id,
    get_workspace_url,
    label_triggered,
    load_event,
    resolve_invitees,
    slack_post,
    write_output,
)


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    issue_label = os.environ["ISSUE_LABEL"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()
    invite_user_ids = os.environ.get("INVITE_USER_IDS", "").strip()
    is_private = os.environ.get("CHANNEL_PRIVATE", "true").lower() != "false"

    event = load_event()

    if not label_triggered(event, issue_label):
        print(f"Issue does not have the '{issue_label}' label (or it wasn't the label just added), skipping.")
        write_output("skipped", "true")
        return

    repo_name = event["repository"]["name"]
    issue_num = event["issue"]["number"]
    issue_title = event["issue"]["title"]
    issue_url = event["issue"]["html_url"]

    channel_name = build_channel_name(channel_prefix, repo_name, issue_num)

    print(f"Creating Slack channel '{channel_name}' (private={is_private})...")

    create_resp = slack_post(
        token, "conversations.create", {"name": channel_name, "is_private": is_private}
    )

    if create_resp.get("ok"):
        channel_id = create_resp["channel"]["id"]
    else:
        error = create_resp.get("error")
        if error != "name_taken":
            print(f"::error::Slack API error creating channel: {error}")
            sys.exit(1)

        print(f"Channel '{channel_name}' already exists, looking it up...")
        channel_id = find_existing_channel_id(token, channel_name)
        if not channel_id:
            print(f"::error::Could not resolve ID for existing channel '{channel_name}'.")
            sys.exit(1)

    print(f"Channel ID: {channel_id}")

    workspace_url = get_workspace_url(token)
    channel_url = build_channel_url(workspace_url, channel_id)

    if invite_user_ids:
        raw_entries = invite_user_ids.split(",")
        resolved_ids = resolve_invitees(token, raw_entries)

        if not resolved_ids:
            print("::warning::No invitees could be resolved to Slack user IDs; skipping invite step.")
        else:
            print(f"Inviting users: {', '.join(resolved_ids)}")
            invite_resp = slack_post(
                token, "conversations.invite", {"channel": channel_id, "users": ",".join(resolved_ids)}
            )
            if not invite_resp.get("ok"):
                error = invite_resp.get("error")
                # already_in_channel isn't fatal (e.g. re-run on the same issue)
                if error != "already_in_channel":
                    print(f"::warning::Slack API error inviting users: {error}")

    issue_reference = build_issue_reference(issue_num, issue_title, issue_url)

    # Slack topics are capped at 250 chars; truncate defensively for very long titles.
    topic_resp = slack_post(token, "conversations.setTopic", {"channel": channel_id, "topic": issue_reference[:250]})
    if not topic_resp.get("ok"):
        print(f"::warning::Slack API error setting channel topic: {topic_resp.get('error')}")

    slack_post(
        token,
        "chat.postMessage",
        {"channel": channel_id, "text": issue_reference},
    )

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
