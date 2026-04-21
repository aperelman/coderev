#!/usr/bin/env python3
import re
import requests
import json
import yaml
import sys
from urllib.parse import quote

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"
MAX_DIFF_CHARS = 6000   # diff portion
MAX_FILE_CHARS = 6000   # full file portion (shared budget)

SERGIO_PROMPT = """You are Sergio Ramos — a tough, uncompromising code reviewer.
You do not tolerate sloppy code, poor naming, missing error handling, or unnecessary complexity.
Be direct and brutal, but accurate. No sugarcoating.

You MUST respond with valid JSON only — no preamble, no markdown fences, no extra text.

JSON schema:
{
  "summary": "Overall assessment in 2-4 sentences. End with verdict: APPROVE | APPROVE WITH COMMENTS | REJECT",
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
- You are given BOTH the full current file content AND the diff for each changed file.
- Use the full file content to understand the complete context before judging the diff.
- inline_comments must reference real files and real [N] line numbers from the diff section.
- line numbers must be new-side (lines shown with [N] prefix, not [-] removed lines).
- Do NOT comment on things that are already correctly handled in the full file.
- Keep each inline comment focused and actionable.
- If a file has no issues, omit it from inline_comments.
- inline_comments may be [] if the code is clean.
"""


def load_config():
    try:
        with open('config.yml', 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print("config.yml not found.")
        sys.exit(1)


def gl_headers(config):
    return {'Private-Token': config["gitlab"]["token"].strip()}


def get_open_mrs(config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests'
    try:
        r = requests.get(url, headers=gl_headers(config), params={'state': 'opened'})
        r.raise_for_status()
        mrs = r.json()
        return [m for m in mrs
                if not m['title'].startswith('Draft:')
                and not m['title'].startswith('[WIP]')]
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MRs: {e}")
        return []


def get_mr_details(mr_iid, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}'
    try:
        r = requests.get(url, headers=gl_headers(config))
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MR details: {e}")
        return None


def get_mr_changes(mr_iid, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/changes'
    try:
        r = requests.get(url, headers=gl_headers(config))
        r.raise_for_status()
        return r.json().get('changes', [])
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MR changes: {e}")
        return []


def get_file_content(file_path, branch, config):
    """Fetch the full current content of a file from the head branch."""
    pid = config["gitlab"]["project_id"]
    encoded_path = quote(file_path, safe='')
    url = f'https://gitlab.com/api/v4/projects/{pid}/repository/files/{encoded_path}/raw'
    try:
        r = requests.get(url, headers=gl_headers(config), params={'ref': branch})
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"  Could not fetch full content for {file_path}: {e}")
        return None


def assign_reviewer(mr_iid, config):
    reviewer_username = config["gitlab"].get("reviewer_username", "sergioram")
    pid = config["gitlab"]["project_id"]
    r = requests.get('https://gitlab.com/api/v4/users',
                     headers=gl_headers(config),
                     params={'username': reviewer_username})
    r.raise_for_status()
    users = r.json()
    if not users:
        print(f"Reviewer {reviewer_username} not found.")
        return
    reviewer_id = users[0]['id']
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}'
    r = requests.put(url, headers=gl_headers(config), json={'reviewer_ids': [reviewer_id]})
    r.raise_for_status()
    print(f"Assigned {reviewer_username} as reviewer on MR !{mr_iid}")


def post_note(mr_iid, body, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/notes'
    r = requests.post(url, headers=gl_headers(config), json={'body': body})
    r.raise_for_status()
    print(f"Summary note posted on MR !{mr_iid}")


def post_inline_discussion(mr_iid, diff_refs, file_path, new_line, body, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/discussions'
    payload = {
        "body": body,
        "position": {
            "position_type": "text",
            "base_sha":  diff_refs["base_sha"],
            "head_sha":  diff_refs["head_sha"],
            "start_sha": diff_refs["start_sha"],
            "new_path":  file_path,
            "old_path":  file_path,
            "new_line":  new_line,
        }
    }
    try:
        r = requests.post(url, headers=gl_headers(config), json=payload)
        r.raise_for_status()
        print(f"  Inline comment posted: {file_path}:{new_line}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  Inline comment failed ({file_path}:{new_line}): {e}")
        return False


def _annotate_diff(diff_text):
    lines = diff_text.splitlines()
    result = []
    new_line = 0
    for line in lines:
        if line.startswith('@@'):
            m = re.search(r'\+(\d+)', line)
            if m:
                new_line = int(m.group(1)) - 1
            result.append(line)
        elif line.startswith('-'):
            result.append(f"[-] {line}")
        else:
            new_line += 1
            result.append(f"[{new_line}] {line}")
    return "\n".join(result)


def build_prompt_context(changes, branch, config):
    """
    Build the full context for Sergio: for each changed file, include
    the full current file content + the annotated diff.
    """
    parts = []
    total = 0
    budget = MAX_DIFF_CHARS + MAX_FILE_CHARS

    for change in changes:
        path = change.get('new_path', change.get('old_path', 'unknown'))
        annotated = _annotate_diff(change.get('diff', ''))

        # Fetch full file content
        full_content = get_file_content(path, branch, config)
        if full_content and len(full_content) > MAX_FILE_CHARS:
            full_content = full_content[:MAX_FILE_CHARS] + "\n... (truncated)"

        chunk = f"### FILE: {path}\n"
        if full_content:
            chunk += f"#### Full current content:\n```\n{full_content}\n```\n\n"
        chunk += f"#### Diff (line numbers in [N] prefix):\n{annotated}\n"

        if total + len(chunk) > budget:
            remaining = budget - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n... (truncated)")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts)


def build_file_line_map(changes):
    file_lines = {}
    for change in changes:
        path = change.get('new_path', change.get('old_path', 'unknown'))
        valid_lines = set()
        new_line = 0
        for line in change.get('diff', '').splitlines():
            if line.startswith('@@'):
                m = re.search(r'\+(\d+)', line)
                if m:
                    new_line = int(m.group(1)) - 1
            elif line.startswith('-'):
                pass
            else:
                new_line += 1
                valid_lines.add(new_line)
        file_lines[path] = valid_lines
    return file_lines


def ollama_review(prompt_context, mr_title):
    prompt = (f"{SERGIO_PROMPT}\n\n"
              f"MR Title: {mr_title}\n\n"
              f"{prompt_context}")
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        print("Sending diff to Ollama for review...")
        r = requests.post(OLLAMA_URL, json=payload, timeout=180)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())
        return json.loads(raw)
    except requests.exceptions.RequestException as e:
        print(f"Ollama request failed: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse Sergio's JSON: {e}")
        return None


def save_context(mr_iid):
    with open('context.json', 'w') as f:
        json.dump({'mr_iid': mr_iid}, f)


def main():
    config = load_config()

    mrs = get_open_mrs(config)
    if not mrs:
        print("No open (non-draft) MRs found.")
        sys.exit(0)

    mr = sorted(mrs, key=lambda x: x['updated_at'], reverse=True)[0]
    mr_iid = mr['iid']
    print(f"Found MR !{mr_iid}: {mr['title']}")

    mr_details = get_mr_details(mr_iid, config)
    diff_refs = mr_details.get('diff_refs') if mr_details else None
    if not diff_refs:
        print("Warning: could not get diff_refs — inline comments will be skipped.")

    # Get the head branch name for file fetching
    branch = mr_details.get('source_branch', 'main') if mr_details else 'main'

    changes = get_mr_changes(mr_iid, config)
    if not changes:
        print("No changes found in MR.")
        sys.exit(0)

    print(f"Changed files: {[c['new_path'] for c in changes]}")

    assign_reviewer(mr_iid, config)

    prompt_context = build_prompt_context(changes, branch, config)
    file_line_map = build_file_line_map(changes)
    review = ollama_review(prompt_context, mr['title'])
    reviewer = config["gitlab"].get("reviewer_username", "sergioram")

    if not review:
        post_note(mr_iid,
                  f"⚠️ **Sergio Ramos** (@{reviewer}): review failed (Ollama unavailable or bad JSON).",
                  config)
        save_context(mr_iid)
        return

    failed_inline = []
    inline_comments = review.get("inline_comments", [])

    if diff_refs and inline_comments:
        print(f"Posting {len(inline_comments)} inline comment(s)...")
        for ic in inline_comments:
            file_path = ic.get("file", "")
            line = ic.get("line")
            comment = ic.get("comment", "")
            if line not in file_line_map.get(file_path, set()):
                print(f"  Invalid position skipped: {file_path}:{line}")
                failed_inline.append(ic)
                continue
            body = f"**Sergio Ramos** (@{reviewer}):\n\n{comment}"
            if not post_inline_discussion(mr_iid, diff_refs, file_path, line, body, config):
                failed_inline.append(ic)
    else:
        failed_inline = inline_comments

    verdict_emoji = {"APPROVE": "✅", "APPROVE WITH COMMENTS": "⚠️", "REJECT": "❌"}.get(
        review.get("verdict", ""), "🔍")

    summary = (
        f"## {verdict_emoji} Sergio Ramos (@{reviewer}) — Code Review\n\n"
        f"{review.get('summary', '_No summary provided._')}\n"
    )
    if failed_inline:
        summary += "\n---\n### Comments (could not be posted inline)\n\n"
        for ic in failed_inline:
            summary += f"**`{ic.get('file')}` line {ic.get('line')}:** {ic.get('comment')}\n\n"

    post_note(mr_iid, summary, config)
    save_context(mr_iid)
    print("Done.")


if __name__ == '__main__':
    main()
