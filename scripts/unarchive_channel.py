#!/usr/bin/env python3
"""
Unarchives the Slack channel matching a GitHub issue when that issue is
reopened (action: "reopened").

Required env vars (set by action.yml):
    SLACK_BOT_TOKEN, CHANNEL_PREFIX, UNARCHIVE_ON_REOPEN
Uses GITHUB_EVENT_PATH and GITHUB_OUTPUT, both provided automatically by GitHub Actions.

Deliberately has no step-level 'if:' in action.yml — like create_channel.py
and archive_channel.py, it always runs and always writes the 'skipped'
output itself. A step-level 'if:' would leave 'skipped' unset (not 'true')
whenever the condition was false, which the downstream unarchive-comment
step's 'steps.unarchive.outputs.skipped != 'true'' check would misread as
"ran successfully" — that's what caused a blank "Slack channel unarchived: #"
comment on issues that were merely opened, not reopened.
"""

import os
import sys
import urllib.error

from slack_common import (
    build_channel_name,
    build_channel_url,
    find_existing_channel_id,
    get_workspace_url,
    load_event,
    slack_post,
    write_output,
)


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()
    unarchive_on_reopen = os.environ.get("UNARCHIVE_ON_REOPEN", "true").lower() != "false"

    event = load_event()

    if not unarchive_on_reopen:
        write_output("skipped", "true")
        return

    if event.get("action") != "reopened":
        write_output("skipped", "true")
        return

    if "issue" not in event:
        # A pull_request can also be "reopened"; its payload has no top-level
        # "issue" key, so this isn't the event we're here for.
        write_output("skipped", "true")
        return

    issue = event["issue"]
    if "pull_request" in issue:
        write_output("skipped", "true")
        return  # this is a PR being reopened, not an issue

    repo_name = event["repository"]["name"]
    issue_num = issue["number"]

    # Same deterministic naming as creation/archiving, so we can find the
    # channel without needing to have stored its ID anywhere.
    channel_name = build_channel_name(channel_prefix, repo_name, issue_num)

    print(f"Looking up Slack channel '{channel_name}' to unarchive...")
    channel_id = find_existing_channel_id(token, channel_name)

    if not channel_id:
        print(f"No Slack channel named '{channel_name}' was found; nothing to unarchive.")
        write_output("skipped", "true")
        return

    print(f"Unarchiving channel '{channel_name}' ({channel_id})...")
    unarchive_resp = slack_post(token, "conversations.unarchive", {"channel": channel_id})

    if not unarchive_resp.get("ok"):
        error = unarchive_resp.get("error")
        if error == "not_archived":
            print(f"Channel '{channel_name}' wasn't archived, nothing to do.")
        else:
            print(f"::error::Slack API error unarchiving channel: {error}")
            sys.exit(1)

    workspace_url = get_workspace_url(token)
    channel_url = build_channel_url(workspace_url, channel_id)

    issue_url = issue.get("html_url", "")
    reopen_text = f"\U0001F513 Issue reopened: <{issue_url}|#{issue_num}>. This channel has been unarchived."
    post_resp = slack_post(token, "chat.postMessage", {"channel": channel_id, "text": reopen_text})
    if not post_resp.get("ok"):
        print(f"::warning::Slack API error posting reopen notification: {post_resp.get('error')}")

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
