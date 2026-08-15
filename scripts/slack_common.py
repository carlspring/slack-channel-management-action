"""
Shared helpers for talking to the Slack API and working with GitHub issue
events. Imported by create_channel.py and archive_channel.py — Python adds a
script's own directory to sys.path automatically, so no packaging is needed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SLACK_API = "https://slack.com/api"

USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")


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


def build_channel_name(channel_prefix: str, repo_name: str, issue_num: int) -> str:
    """Build the deterministic channel name for a given repo/issue, matching creation logic."""
    safe_repo = sanitize(repo_name)
    if channel_prefix:
        safe_prefix = sanitize(channel_prefix)
        name = f"{safe_prefix}-{safe_repo}-{issue_num}"
    else:
        name = f"{safe_repo}-{issue_num}"
    return name[:80]


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


def get_workspace_url(token: str) -> str:
    """Return the workspace's base URL (e.g. 'https://myteam.slack.com/') via auth.test."""
    result = slack_get(token, "auth.test", {})
    if not result.get("ok"):
        print(f"::warning::Slack API error calling auth.test: {result.get('error')}; channel link will be omitted.")
        return ""
    return result.get("url", "")


def build_channel_url(workspace_url: str, channel_id: str) -> str:
    if not workspace_url:
        return ""
    return f"{workspace_url.rstrip('/')}/archives/{channel_id}"


def write_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def load_event() -> dict:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.isfile(event_path):
        print("::error::GITHUB_EVENT_PATH not found. This action must run on an 'issues' event.")
        sys.exit(1)
    with open(event_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def label_triggered(event: dict, target_label: str) -> bool:
    """
    True if this event should be treated as triggered by `target_label`.

    - action == 'labeled': only the specific label that was just added counts
      (avoids double-firing the create step when the archive label is added
      to an issue that already carries the create label, and vice versa).
    - action == 'opened': falls back to checking the full label set, since
      an issue can be opened pre-labeled and there's no single "added" label.
    """
    action = event.get("action")
    if action == "labeled":
        return event.get("label", {}).get("name") == target_label
    if action == "opened":
        labels = [label.get("name") for label in event.get("issue", {}).get("labels", [])]
        return target_label in labels
    return False
