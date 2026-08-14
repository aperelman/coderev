#!/usr/bin/env python3
import re
import subprocess
import time
import requests
import json
import yaml
import sys
from datetime import datetime
from urllib.parse import quote

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_CONTAINER = "ollama"
MAX_DIFF_CHARS = 6000  # diff portion
MAX_FILE_CHARS = 6000  # full file portion (shared budget)
GITHUB_API = "https://api.github.com"

SERGIO_PROMPT = """You are Sergio Ramos — a tough, uncompromising code reviewer.  # noqa: E501
You do not tolerate sloppy code, poor naming,
missing error handling, or unnecessary complexity.
Be direct and brutal, but accurate. No sugarcoating.

You MUST respond with valid JSON only —
no preamble, no markdown fences, no extra text.

JSON schema:
{
  "summary": "Overall assessment in 2-4 sentences.
End with verdict: APPROVE | APPROVE WITH COMMENTS | REJECT",
  "verdict": "APPROVE" | "APPROVE WITH COMMENTS" | "REJECT",
  "inline_comments": [
    {
      "file": "path/to/file.py",
      "line": <integer, new-side line number from the [N] prefix in the diff>,
      "comment": "Your specific comment about this line."
    }
  ]
}

Rules:
- You are given BOTH the full current file content
AND the diff for each changed file.
- Use the full file content to understand the
complete context before judging the diff.
- inline_comments must reference real files and
real [N] line numbers from the diff section.
- line numbers must be new-side (lines shown with
[N] prefix, not [-] removed lines).
- Do NOT comment on things that are already
correctly handled in the full file.
- Keep each inline comment focused and actionable.
- If a file has no issues, omit it from inline_comments.
- inline_comments may be [] if the code is clean.
"""


def load_config():
    try:
        with open("config.yml", "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print("config.yml not found.")
        sys.exit(1)


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ─── OLLAMA ─────────────────────────────────────────────────────────────────


def check_ollama_api_ready():
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=2)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


def ensure_ollama_running():
    if check_ollama_api_ready():
        print("Ollama API is ready.")
        return True

    print("Ollama not responding, attempting to start container...")
    try:
        result = subprocess.run(
            ["docker", "start", OLLAMA_CONTAINER],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Could not start container '{OLLAMA_CONTAINER}': {result.stderr.strip()}"  # noqa: E501
            )
            return False
    except FileNotFoundError:
        print("Docker not installed.")
        return False

    for i in range(60):
        time.sleep(1)
        if check_ollama_api_ready():
            print("Ollama API is ready.")
            return True
        if i % 10 == 0 and i > 0:
            print(f"  still waiting... ({i}s)")

    print("Ollama did not become ready within 60s.")
    return False


def _annotate_diff(diff_text):
    lines = diff_text.splitlines()
    result = []
    new_line = 0
    for line in lines:
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                new_line = int(m.group(1)) - 1
            result.append(line)
        elif line.startswith("-"):
            result.append(f"[-] {line}")
        else:
            new_line += 1
            result.append(f"[{new_line}] {line}")
    return "\n".join(result)


def build_prompt_context(files, fetch_content):
    """
    files: list of {'path': str, 'diff': str} where 'diff' is the hunk-only
    body (starting at the first '@@', no 'diff --git'/'index' header lines).
    fetch_content: callable(path) -> Optional[str], full current file content.
    """
    parts = []
    total = 0
    budget = MAX_DIFF_CHARS + MAX_FILE_CHARS

    for f in files:
        path = f["path"]
        annotated = _annotate_diff(f["diff"])

        full_content = fetch_content(path)
        if full_content and len(full_content) > MAX_FILE_CHARS:
            full_content = full_content[:MAX_FILE_CHARS] + "\n... (truncated)"

        chunk = f"### FILE: {path}\n"
        if full_content:
            chunk += (
                f"#### Full current content:\n```\n{full_content}\n```\n\n"
            )
        chunk += f"#### Diff (line numbers in [N] prefix):\n{annotated}\n"

        if total + len(chunk) > budget:
            remaining = budget - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n... (truncated)")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts)


def build_file_line_map(files):
    """
    Returns {path: {new_line: old_line_or_None}}.
    old_line is set for unchanged context lines (needed by GitLab's discussion
    position API) and None for pure additions (new_line only).
    """
    file_lines = {}
    for f in files:
        path = f["path"]
        lines = {}
        old_line = 0
        new_line = 0
        for line in f["diff"].splitlines():
            if line.startswith("@@"):
                m = re.search(r"-(\d+)(?:,\d+)? \+(\d+)", line)
                if m:
                    old_line = int(m.group(1)) - 1
                    new_line = int(m.group(2)) - 1
            elif line.startswith("-"):
                old_line += 1
            elif line.startswith("+"):
                new_line += 1
                lines[new_line] = None
            else:
                old_line += 1
                new_line += 1
                lines[new_line] = old_line
        file_lines[path] = lines
    return file_lines


def ollama_review(prompt_context, mr_title):
    prompt = (
        f"{SERGIO_PROMPT}\n\n" f"MR Title: {mr_title}\n\n" f"{prompt_context}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        print("Sending diff to Ollama for review...")
        r = requests.post(OLLAMA_URL, json=payload, timeout=300)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw.strip())
        return json.loads(raw)
    except requests.exceptions.RequestException as e:
        print(f"Ollama request failed: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse Sergio's JSON: {e}")
        return None


def save_context(platform, item_id):
    with open("context.json", "w") as f:
        json.dump({"platform": platform, "id": item_id}, f)


def render_summary(reviewer, review):
    verdict_emoji = {
        "APPROVE": "✅",
        "APPROVE WITH COMMENTS": "⚠️",
        "REJECT": "❌",
    }.get(review.get("verdict", ""), "🔍")
    return (
        f"## {verdict_emoji} Sergio Ramos (@{reviewer}) — Code Review\n\n"
        f"{review.get('summary', '_No summary provided._')}\n"
    )


def append_failed_inline(summary, failed_inline):
    if failed_inline:
        summary += "\n---\n### Comments (could not be posted inline)\n\n"
        for ic in failed_inline:
            summary += f"**`{ic.get('file')}` line {ic.get('line')}:** {ic.get('comment')}\n\n"  # noqa: E501
    return summary


# ─── GITLAB ─────────────────────────────────────────────────────────────────


def gl_headers(config):
    return {"Private-Token": config["gitlab"]["token"].strip()}


def get_open_mrs(config):
    pid = config["gitlab"]["project_id"]
    url = f"https://gitlab.com/api/v4/projects/{pid}/merge_requests"
    try:
        r = requests.get(
            url, headers=gl_headers(config), params={"state": "opened"}
        )
        r.raise_for_status()
        mrs = r.json()
        return [
            m
            for m in mrs
            if not m.get("draft")
            and not m["title"].startswith("Draft:")
            and not m["title"].startswith("[WIP]")
        ]
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MRs: {e}")
        return []


def get_mr_details(mr_iid, config):
    pid = config["gitlab"]["project_id"]
    url = f"https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}"
    try:
        r = requests.get(url, headers=gl_headers(config))
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MR details: {e}")
        return None


def get_mr_changes(mr_iid, config):
    pid = config["gitlab"]["project_id"]
    url = f"https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/changes"  # noqa: E501
    try:
        r = requests.get(url, headers=gl_headers(config))
        r.raise_for_status()
        return r.json().get("changes", [])
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MR changes: {e}")
        return []


def get_file_content_gitlab(file_path, branch, config):
    pid = config["gitlab"]["project_id"]
    encoded_path = quote(file_path, safe="")
    url = f"https://gitlab.com/api/v4/projects/{pid}/repository/files/{encoded_path}/raw"  # noqa: E501
    try:
        r = requests.get(
            url, headers=gl_headers(config), params={"ref": branch}
        )
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"  Could not fetch full content for {file_path}: {e}")
        return None


def assign_reviewer_gitlab(mr_iid, config):
    reviewer_username = config["gitlab"].get("reviewer_username", "sergioram")
    pid = config["gitlab"]["project_id"]
    r = requests.get(
        "https://gitlab.com/api/v4/users",
        headers=gl_headers(config),
        params={"username": reviewer_username},
    )
    r.raise_for_status()
    users = r.json()
    if not users:
        print(f"Reviewer {reviewer_username} not found.")
        return
    reviewer_id = users[0]["id"]
    url = f"https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}"
    r = requests.put(
        url, headers=gl_headers(config), json={"reviewer_ids": [reviewer_id]}
    )
    r.raise_for_status()
    print(f"Assigned {reviewer_username} as reviewer on MR !{mr_iid}")


def post_note_gitlab(mr_iid, body, config):
    pid = config["gitlab"]["project_id"]
    url = f"https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/notes"  # noqa: E501
    r = requests.post(url, headers=gl_headers(config), json={"body": body})
    r.raise_for_status()
    print(f"Summary note posted on MR !{mr_iid}")


def post_inline_discussion_gitlab(
    mr_iid, diff_refs, file_path, new_line, old_line, body, config
):
    pid = config["gitlab"]["project_id"]
    url = f"https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/discussions"  # noqa: E501
    position = {
        "position_type": "text",
        "base_sha": diff_refs["base_sha"],
        "head_sha": diff_refs["head_sha"],
        "start_sha": diff_refs["start_sha"],
        "new_path": file_path,
        "old_path": file_path,
        "new_line": new_line,
    }
    if old_line is not None:
        position["old_line"] = old_line
    payload = {"body": body, "position": position}
    try:
        r = requests.post(url, headers=gl_headers(config), json=payload)
        r.raise_for_status()
        print(f"  Inline comment posted: {file_path}:{new_line}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  Inline comment failed ({file_path}:{new_line}): {e}")
        return False


def find_gitlab_candidate(config):
    if not config.get("gitlab", {}).get("project_id") or not config.get(
        "gitlab", {}
    ).get("token"):
        return None
    mrs = get_open_mrs(config)
    if not mrs:
        return None
    mr = sorted(mrs, key=lambda x: x["updated_at"], reverse=True)[0]
    return {
        "platform": "gitlab",
        "id": mr["iid"],
        "title": mr["title"],
        "updated_at": mr["updated_at"],
    }


def _post_gitlab_comments(
    mr_iid, diff_refs, file_line_map, review, reviewer, config
):
    failed_inline = []
    inline_comments = review.get("inline_comments", [])

    if diff_refs and inline_comments:
        print(f"Posting {len(inline_comments)} inline comment(s)...")
        for ic in inline_comments:
            file_path = ic.get("file", "")
            line = ic.get("line")
            comment = ic.get("comment", "")
            line_map = file_line_map.get(file_path, {})
            if line not in line_map:
                print(f"  Invalid position skipped: {file_path}:{line}")
                failed_inline.append(ic)
                continue
            body = f"**Sergio Ramos** (@{reviewer}):\n\n{comment}"
            if not post_inline_discussion_gitlab(
                mr_iid,
                diff_refs,
                file_path,
                line,
                line_map[line],
                body,
                config,
            ):
                failed_inline.append(ic)
    else:
        failed_inline = inline_comments
    return failed_inline


def review_gitlab_mr(candidate, config):
    mr_iid = candidate["id"]
    print(f"Found MR !{mr_iid}: {candidate['title']}")

    mr_details = get_mr_details(mr_iid, config)
    diff_refs = mr_details.get("diff_refs") if mr_details else None
    if not diff_refs:
        print(
            "Warning: could not get diff_refs — inline comments will be skipped."  # noqa: E501
        )

    branch = mr_details.get("source_branch", "main") if mr_details else "main"

    changes = get_mr_changes(mr_iid, config)
    if not changes:
        print("No changes found in MR.")
        return

    files = [
        {
            "path": c.get("new_path", c.get("old_path", "unknown")),
            "diff": c.get("diff", ""),
        }
        for c in changes
    ]
    print(f"Changed files: {[f['path'] for f in files]}")

    assign_reviewer_gitlab(mr_iid, config)

    prompt_context = build_prompt_context(
        files, lambda p: get_file_content_gitlab(p, branch, config)
    )
    file_line_map = build_file_line_map(files)
    review = ollama_review(prompt_context, candidate["title"])
    reviewer = config["gitlab"].get("reviewer_username", "sergioram")

    if not review:
        post_note_gitlab(
            mr_iid,
            f"⚠️ **Sergio Ramos** (@{reviewer}): review failed (Ollama unavailable or bad JSON).",  # noqa: E501
            config,
        )
        save_context("gitlab", mr_iid)
        return

    failed_inline = _post_gitlab_comments(
        mr_iid, diff_refs, file_line_map, review, reviewer, config
    )

    summary = append_failed_inline(
        render_summary(reviewer, review), failed_inline
    )
    post_note_gitlab(mr_iid, summary, config)
    save_context("gitlab", mr_iid)
    print("Done.")


# ─── GITHUB ─────────────────────────────────────────────────────────────────

_gh_token_cache = None


def gh_token():
    global _gh_token_cache
    if _gh_token_cache is None:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=True,
            )
            _gh_token_cache = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Could not get GitHub token via `gh auth token`: {e}")
            _gh_token_cache = ""
    return _gh_token_cache


def gh_headers(accept="application/vnd.github+json"):
    return {"Authorization": f"Bearer {gh_token()}", "Accept": accept}


def find_github_candidate(config):
    gh_config = config.get("github", {})
    reviewer = gh_config.get("reviewer_username")
    if not reviewer or not gh_token():
        return None

    q = f"type:pr state:open review-requested:{reviewer}"
    owner = gh_config.get("owner")
    if owner:
        q += f" user:{owner}"

    try:
        r = requests.get(
            f"{GITHUB_API}/search/issues",
            headers=gh_headers(),
            params={"q": q},
        )
        r.raise_for_status()
        items = [i for i in r.json().get("items", []) if not i.get("draft")]
    except requests.exceptions.RequestException as e:
        print(f"Failed to search GitHub PRs: {e}")
        return None

    if not items:
        return None

    item = sorted(items, key=lambda x: x["updated_at"], reverse=True)[0]
    repo = item["repository_url"].removeprefix(f"{GITHUB_API}/repos/")
    return {
        "platform": "github",
        "id": (repo, item["number"]),
        "title": item["title"],
        "updated_at": item["updated_at"],
    }


def get_pr_github(repo, number):
    try:
        r = requests.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{number}", headers=gh_headers()
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch PR details: {e}")
        return None


def get_pr_files_github(repo, number):
    files = []
    url = f"{GITHUB_API}/repos/{repo}/pulls/{number}/files"
    params = {"per_page": 100}
    try:
        while url:
            r = requests.get(url, headers=gh_headers(), params=params)
            r.raise_for_status()
            for f in r.json():
                if f.get("patch"):
                    files.append({"path": f["filename"], "diff": f["patch"]})
            url = r.links.get("next", {}).get("url")
            params = None
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch PR files: {e}")
    return files


def get_file_content_github(repo, file_path, ref):
    url = f"{GITHUB_API}/repos/{repo}/contents/{quote(file_path)}"
    try:
        r = requests.get(
            url,
            headers=gh_headers(accept="application/vnd.github.raw+json"),
            params={"ref": ref},
        )
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"  Could not fetch full content for {file_path}: {e}")
        return None


def post_review_github(repo, number, commit_id, body, comments, config):
    url = f"{GITHUB_API}/repos/{repo}/pulls/{number}/reviews"
    payload = {
        "commit_id": commit_id,
        "body": body,
        "event": "COMMENT",
        "comments": comments,
    }
    try:
        r = requests.post(url, headers=gh_headers(), json=payload)
        r.raise_for_status()
        print(
            f"Review posted on {repo}#{number} ({len(comments)} inline comment(s))."  # noqa: E501
        )
        return True
    except requests.exceptions.RequestException as e:
        print(
            f"Full review post failed ({e}); retrying as summary-only comment..."  # noqa: E501
        )
        try:
            r = requests.post(
                f"{GITHUB_API}/repos/{repo}/issues/{number}/comments",
                headers=gh_headers(),
                json={"body": body},
            )
            r.raise_for_status()
            print(f"Summary-only comment posted on {repo}#{number}.")
            return False
        except requests.exceptions.RequestException as e2:
            print(f"Summary-only comment also failed: {e2}")
            return False


def _prepare_github_comments(file_line_map, review, reviewer):
    failed_inline = []
    valid_comments = []
    for ic in review.get("inline_comments", []):
        file_path = ic.get("file", "")
        line = ic.get("line")
        comment = ic.get("comment", "")
        if line not in file_line_map.get(file_path, {}):
            print(f"  Invalid position skipped: {file_path}:{line}")
            failed_inline.append(ic)
            continue
        valid_comments.append(
            {
                "path": file_path,
                "line": line,
                "body": f"**Sergio Ramos** (@{reviewer}):\n\n{comment}",
            }
        )
    return valid_comments, failed_inline


def review_github_pr(candidate, config):
    repo, number = candidate["id"]
    print(f"Found PR {repo}#{number}: {candidate['title']}")

    pr = get_pr_github(repo, number)
    if not pr:
        return
    commit_id = pr["head"]["sha"]
    branch = pr["head"]["ref"]

    files = get_pr_files_github(repo, number)
    if not files:
        print("No reviewable file changes found in PR.")
        return

    print(f"Changed files: {[f['path'] for f in files]}")

    prompt_context = build_prompt_context(
        files, lambda p: get_file_content_github(repo, p, branch)
    )
    file_line_map = build_file_line_map(files)
    review = ollama_review(prompt_context, candidate["title"])
    reviewer = config["github"].get("reviewer_username", "sergiorev")

    if not review:
        post_review_github(
            repo,
            number,
            commit_id,
            f"⚠️ **Sergio Ramos** (@{reviewer}): review failed (Ollama unavailable or bad JSON).",  # noqa: E501
            [],
            config,
        )
        save_context("github", f"{repo}#{number}")
        return

    valid_comments, failed_inline = _prepare_github_comments(
        file_line_map, review, reviewer
    )

    summary = append_failed_inline(
        render_summary(reviewer, review), failed_inline
    )
    post_review_github(
        repo, number, commit_id, summary, valid_comments, config
    )

    save_context("github", f"{repo}#{number}")
    print("Done.")


# ─── MAIN ───────────────────────────────────────────────────────────────────


def main():
    config = load_config()

    if not ensure_ollama_running():
        print("Cannot proceed without Ollama running.")
        sys.exit(1)

    candidates = []
    gl = find_gitlab_candidate(config)
    if gl:
        candidates.append(gl)
    gh = find_github_candidate(config)
    if gh:
        candidates.append(gh)

    if not candidates:
        print("No open (non-draft) MRs/PRs found to review.")
        sys.exit(0)

    candidate = sorted(
        candidates, key=lambda c: parse_ts(c["updated_at"]), reverse=True
    )[0]

    if candidate["platform"] == "gitlab":
        review_gitlab_mr(candidate, config)
    else:
        review_github_pr(candidate, config)


if __name__ == "__main__":
    main()
