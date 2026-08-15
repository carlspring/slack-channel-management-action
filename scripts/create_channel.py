#!/usr/bin/env python3
"""
Creates a Slack channel for a labeled GitHub issue and invites configured users.

Required env vars (set by action.yml):
    SLACK_BOT_TOKEN, ISSUE_LABEL, CHANNEL_PREFIX, INVITE_USER_IDS, CHANNEL_PRIVATE
Uses GITHUB_EVENT_PATH and GITHUB_OUTPUT, both provided automatically by GitHub Actions.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SLACK_API = "https://slack.com/api"


def slack_post(token: str, method: str, payload: dict) -> dict:
    """POST JSON to a Slack API method and return the parsed response."""
    req = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def slack_get(token: str, method: str, params: dict) -> dict:
    """GET (form-encoded) a Slack API method and return the parsed response."""
    url = f"{SLACK_API}/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="POST",  # Slack accepts POST with query-string args for these methods
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")


def is_user_id(value: str) -> bool:
    """Slack user (and some workspace-admin) IDs look like U0123ABCD or W0123ABCD."""
    return bool(USER_ID_RE.match(value.strip()))


def normalize_name(value: str) -> str:
    """Strip an optional leading '@' and surrounding whitespace, lowercase for matching."""
    return value.strip().lstrip("@").strip().lower()


def fetch_all_users(token: str) -> list[dict]:
    """Fetch the full workspace member list (paginated) for name-based lookups."""
    members: list[dict] = []
    cursor = ""
    while True:
        params = {"limit": "200"}
        if cursor:
            params["cursor"] = cursor
        result = slack_get(token, "users.list", params)
        if not result.get("ok"):
            print(f"::error::Slack API error listing users: {result.get('error')}")
            sys.exit(1)

        members.extend(result.get("members", []))
        cursor = result.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            return members


def resolve_invitees(token: str, raw_entries: list[str]) -> list[str]:
    """
    Resolve a mixed list of Slack user IDs and human names (e.g. "@Martin Todorov",
    "Martin Todorov", or a bare username) into Slack user IDs.
    """
    entries = [e.strip() for e in raw_entries if e.strip()]
    if not entries:
        return []

    ids = [e for e in entries if is_user_id(e)]
    name_entries = [e for e in entries if not is_user_id(e)]

    resolved = list(ids)

    if name_entries:
        directory = fetch_all_users(token)

        # Build lookup maps: normalized real name / display name / username -> id
        by_name: dict[str, str] = {}
        for member in directory:
            if member.get("deleted"):
                continue
            profile = member.get("profile", {})
            candidates = {
                member.get("name", ""),
                member.get("real_name", ""),
                profile.get("display_name", ""),
                profile.get("real_name", ""),
                profile.get("display_name_normalized", ""),
                profile.get("real_name_normalized", ""),
            }
            for candidate in candidates:
                if candidate:
                    by_name[candidate.strip().lower()] = member["id"]

        for entry in name_entries:
            key = normalize_name(entry)
            user_id = by_name.get(key)
            if user_id:
                print(f"Resolved '{entry}' -> {user_id}")
                resolved.append(user_id)
            else:
                print(f"::warning::Could not resolve Slack user for '{entry}', skipping invite for this entry.")

    return resolved


def sanitize(value: str) -> str:
    """Lowercase and replace anything outside [a-z0-9-] with a hyphen, per Slack's channel naming rules."""
    value = value.lower()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    return value


def write_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def find_existing_channel_id(token: str, name: str) -> str | None:
    cursor = ""
    while True:
        params = {"types": "public_channel,private_channel", "limit": "200"}
        if cursor:
            params["cursor"] = cursor
        result = slack_get(token, "conversations.list", params)
        if not result.get("ok"):
            print(f"::error::Slack API error listing channels: {result.get('error')}")
            sys.exit(1)

        for channel in result.get("channels", []):
            if channel.get("name") == name:
                return channel.get("id")

        cursor = result.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            return None


def main() -> None:
    token = os.environ["SLACK_BOT_TOKEN"]
    issue_label = os.environ["ISSUE_LABEL"]
    channel_prefix = os.environ.get("CHANNEL_PREFIX", "").strip()
    invite_user_ids = os.environ.get("INVITE_USER_IDS", "").strip()
    is_private = os.environ.get("CHANNEL_PRIVATE", "true").lower() != "false"

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.isfile(event_path):
        print("::error::GITHUB_EVENT_PATH not found. This action must run on an 'issues' event.")
        sys.exit(1)

    with open(event_path, "r", encoding="utf-8") as fh:
        event = json.load(fh)

    labels = [label.get("name") for label in event.get("issue", {}).get("labels", [])]
    if issue_label not in labels:
        print(f"Issue does not have the '{issue_label}' label, skipping.")
        write_output("skipped", "true")
        return

    repo_name = event["repository"]["name"]
    issue_num = event["issue"]["number"]
    issue_title = event["issue"]["title"]
    issue_url = event["issue"]["html_url"]

    safe_repo = sanitize(repo_name)
    if channel_prefix:
        safe_prefix = sanitize(channel_prefix)
        channel_name = f"{safe_prefix}-{safe_repo}-{issue_num}"
    else:
        channel_name = f"{safe_repo}-{issue_num}"
    channel_name = channel_name[:80]

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

    slack_post(
        token,
        "chat.postMessage",
        {"channel": channel_id, "text": f"Created for <{issue_url}|#{issue_num}: {issue_title}>"},
    )

    write_output("channel-id", channel_id)
    write_output("channel-name", channel_name)
    write_output("skipped", "false")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"::error::HTTP error calling Slack API: {exc.code} {body}")
        sys.exit(1)
