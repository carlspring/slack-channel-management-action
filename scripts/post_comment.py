#!/usr/bin/env python3
"""
Posts a comment on the GitHub issue linking to the created Slack channel.

Required env vars: GITHUB_TOKEN, CHANNEL_NAME
Uses GITHUB_EVENT_PATH, GITHUB_API_URL and GITHUB_REPOSITORY, provided by GitHub Actions.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    channel_name = os.environ["CHANNEL_NAME"]

    if not token:
        print("::warning::post-comment is enabled but github-token was not provided; skipping comment.")
        return

    event_path = os.environ["GITHUB_EVENT_PATH"]
    with open(event_path, "r", encoding="utf-8") as fh:
        event = json.load(fh)

    issue_num = event["issue"]["number"]
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo = os.environ["GITHUB_REPOSITORY"]

    url = f"{api_url}/repos/{repo}/issues/{issue_num}/comments"
    payload = json.dumps({"body": f"Slack channel created: #{channel_name}"}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    with urllib.request.urlopen(req):
        pass

    print(f"Posted comment linking to #{channel_name}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"::error::HTTP error posting comment: {exc.code} {body}")
        sys.exit(1)
