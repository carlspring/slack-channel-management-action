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


def escape_slack_text(text: str | None) -> str:
    """Escape Slack mrkdwn's reserved characters (&, <, >) in user-supplied text."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_issue_reference(issue_num: int, issue_title: str, issue_url: str) -> str:
    """Build a Slack mrkdwn link like '<url|#1234 : Fix this and that>'."""
    return f"<{issue_url}|#{issue_num} : {escape_slack_text(issue_title)}>"


CLOSING_KEYWORD_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE)
ANY_ISSUE_REF_RE = re.compile(r"#(\d+)\b")


def extract_referenced_issue_number(*texts: str | None) -> int | None:
    """
    Find the issue number a PR is for, by scanning its title/body.
    Prefers GitHub closing keywords ("fixes #123", "closes #123", ...);
    falls back to the first bare "#123" reference if no keyword match is found.
    """
    combined = "\n".join(t for t in texts if t)
    match = CLOSING_KEYWORD_RE.search(combined)
    if match:
        return int(match.group(1))
    match = ANY_ISSUE_REF_RE.search(combined)
    if match:
        return int(match.group(1))
    return None


def summarize_text(text: str | None, max_chars: int = 400) -> str:
    """
    Build a short plain-text summary from a longer body of text (e.g. a PR
    description): takes the first non-empty paragraph, truncated, so a huge
    PR template doesn't dump its entire checklist into Slack.
    """
    if not text:
        return "_No description provided._"

    for paragraph in text.strip().split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph:
            if len(paragraph) > max_chars:
                paragraph = paragraph[:max_chars].rstrip() + " \u2026"
            return escape_slack_text(paragraph)

    return "_No description provided._"


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


def was_recently_posted(token: str, channel_id: str, marker: str, lookback: int = 30) -> bool:
    """
    Check the channel's recent history for a message containing `marker`
    (e.g. a PR's or comment's html_url, which is unique per event).

    GitHub's webhook delivery is "at least once", not "exactly once" — the
    same event can be redelivered and independently trigger a full workflow
    run. There's no event ID we can persist between runs (no external
    storage), so this checks Slack itself as the source of truth: if a
    message referencing this exact URL was already posted recently, treat
    the current run as a duplicate delivery and skip posting again.

    Fails open (returns False, i.e. "not a duplicate, go ahead and post")
    if the history lookup itself errors, since a missed dedup check is far
    less disruptive than silently dropping a real notification.
    """
    result = slack_get(token, "conversations.history", {"channel": channel_id, "limit": str(lookback)})
    if not result.get("ok"):
        print(f"::warning::Slack API error checking channel history for duplicates: {result.get('error')}; proceeding without dedup check.")
        return False

    for message in result.get("messages", []):
        if marker in message.get("text", ""):
            return True

    return False


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
    True only if `target_label` is the specific label that was just added on
    this event (action == 'labeled', label.name == target_label).

    Deliberately does NOT also check the full label set on an 'opened'
    event. It's tempting to add that as a fallback for "issue opened
    pre-labeled" — but GitHub fires a separate 'labeled' webhook for every
    label attached during issue creation, in addition to 'opened'. Checking
    the label set on 'opened' too would make both events match, running
    channel creation/archiving twice for the same label attachment.
    """
    return event.get("action") == "labeled" and event.get("label", {}).get("name") == target_label
