#!/usr/bin/env python3
import re
import sys
import json
import yaml
import argparse
import requests
from urllib.parse import quote

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"
MAX_DIFF_CHARS = 6000
MAX_FILE_CHARS = 6000

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


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def load_config(path='config.yml'):
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"{path} not found.")
        sys.exit(1)


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


def build_file_line_map(changes):
    """Return {path: set_of_valid_new_side_line_numbers}."""
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
            elif not line.startswith('-'):
                new_line += 1
                valid_lines.add(new_line)
        file_lines[path] = valid_lines
    return file_lines


def ollama_review(prompt_context, title):
    prompt = (f"{SERGIO_PROMPT}\n\n"
              f"PR/MR Title: {title}\n\n"
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


def save_context(data):
    with open('context.json', 'w') as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------

def gl_headers(config):
    return {'Private-Token': config["gitlab"]["token"].strip()}


def gl_get_open_mrs(config):
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
        print(f"GitLab: failed to fetch MRs: {e}")
        return []


def gl_get_mr_details(mr_iid, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}'
    try:
        r = requests.get(url, headers=gl_headers(config))
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"GitLab: failed to fetch MR details: {e}")
        return None


def gl_get_mr_changes(mr_iid, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/changes'
    try:
        r = requests.get(url, headers=gl_headers(config))
        r.raise_for_status()
        return r.json().get('changes', [])
    except requests.exceptions.RequestException as e:
        print(f"GitLab: failed to fetch MR changes: {e}")
        return []


def gl_get_file_content(file_path, branch, config):
    pid = config["gitlab"]["project_id"]
    encoded_path = quote(file_path, safe='')
    url = f'https://gitlab.com/api/v4/projects/{pid}/repository/files/{encoded_path}/raw'
    try:
        r = requests.get(url, headers=gl_headers(config), params={'ref': branch})
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"  GitLab: could not fetch {file_path}: {e}")
        return None


def gl_assign_reviewer(mr_iid, config):
    reviewer_username = config["gitlab"].get("reviewer_username", "sergioram")
    pid = config["gitlab"]["project_id"]
    r = requests.get('https://gitlab.com/api/v4/users',
                     headers=gl_headers(config),
                     params={'username': reviewer_username})
    r.raise_for_status()
    users = r.json()
    if not users:
        print(f"GitLab: reviewer {reviewer_username} not found.")
        return
    reviewer_id = users[0]['id']
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}'
    r = requests.put(url, headers=gl_headers(config), json={'reviewer_ids': [reviewer_id]})
    r.raise_for_status()
    print(f"GitLab: assigned {reviewer_username} as reviewer on MR !{mr_iid}")


def gl_post_note(mr_iid, body, config):
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}/notes'
    r = requests.post(url, headers=gl_headers(config), json={'body': body})
    r.raise_for_status()
    print(f"GitLab: summary note posted on MR !{mr_iid}")


def gl_post_inline(mr_iid, diff_refs, file_path, new_line, body, config):
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
        print(f"  GitLab: inline comment posted: {file_path}:{new_line}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"  GitLab: inline comment failed ({file_path}:{new_line}): {e}")
        return False


def gl_build_prompt_context(changes, branch, config):
    parts = []
    total = 0
    budget = MAX_DIFF_CHARS + MAX_FILE_CHARS
    for change in changes:
        path = change.get('new_path', change.get('old_path', 'unknown'))
        annotated = _annotate_diff(change.get('diff', ''))
        full_content = gl_get_file_content(path, branch, config)
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


def run_gitlab(config):
    mrs = gl_get_open_mrs(config)
    if not mrs:
        print("GitLab: no open (non-draft) MRs found.")
        return

    mr = sorted(mrs, key=lambda x: x['updated_at'], reverse=True)[0]
    mr_iid = mr['iid']
    print(f"GitLab: MR !{mr_iid}: {mr['title']}")

    mr_details = gl_get_mr_details(mr_iid, config)
    diff_refs = mr_details.get('diff_refs') if mr_details else None
    if not diff_refs:
        print("GitLab: warning — no diff_refs, inline comments will be skipped.")
    branch = mr_details.get('source_branch', 'main') if mr_details else 'main'

    changes = gl_get_mr_changes(mr_iid, config)
    if not changes:
        print("GitLab: no changes found.")
        return

    print(f"GitLab: changed files: {[c['new_path'] for c in changes]}")
    gl_assign_reviewer(mr_iid, config)

    prompt_context = gl_build_prompt_context(changes, branch, config)
    file_line_map = build_file_line_map(changes)
    review = ollama_review(prompt_context, mr['title'])
    reviewer = config["gitlab"].get("reviewer_username", "sergioram")

    if not review:
        gl_post_note(mr_iid,
                     f"⚠️ **Sergio Ramos** (@{reviewer}): review failed (Ollama unavailable or bad JSON).",
                     config)
        save_context({'gitlab_mr_iid': mr_iid})
        return

    failed_inline = []
    inline_comments = review.get("inline_comments", [])

    if diff_refs and inline_comments:
        print(f"GitLab: posting {len(inline_comments)} inline comment(s)...")
        for ic in inline_comments:
            file_path = ic.get("file", "")
            line = ic.get("line")
            comment = ic.get("comment", "")
            if line not in file_line_map.get(file_path, set()):
                print(f"  Invalid position skipped: {file_path}:{line}")
                failed_inline.append(ic)
                continue
            body = f"**Sergio Ramos** (@{reviewer}):\n\n{comment}"
            if not gl_post_inline(mr_iid, diff_refs, file_path, line, body, config):
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

    gl_post_note(mr_iid, summary, config)
    save_context({'gitlab_mr_iid': mr_iid})
    print("GitLab: done.")


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def gh_headers(config):
    return {
        'Authorization': f"token {config['github']['token'].strip()}",
        'Accept': 'application/vnd.github.v3+json',
    }


def gh_get_open_prs(config):
    repo = config["github"]["repo"]  # e.g. "aperelman/attn2graph"
    url = f'https://api.github.com/repos/{repo}/pulls'
    try:
        r = requests.get(url, headers=gh_headers(config), params={'state': 'open'})
        r.raise_for_status()
        return [p for p in r.json() if not p.get('draft', False)]
    except requests.exceptions.RequestException as e:
        print(f"GitHub: failed to fetch PRs: {e}")
        return []


def gh_get_pr_files(pr_number, config):
    repo = config["github"]["repo"]
    url = f'https://api.github.com/repos/{repo}/pulls/{pr_number}/files'
    try:
        r = requests.get(url, headers=gh_headers(config))
        r.raise_for_status()
        files = []
        for f in r.json():
            files.append({
                'new_path': f['filename'],
                'old_path': f.get('previous_filename', f['filename']),
                'diff': f.get('patch', ''),
            })
        return files
    except requests.exceptions.RequestException as e:
        print(f"GitHub: failed to fetch PR files: {e}")
        return []


def gh_get_file_content(file_path, branch, config):
    import base64
    repo = config["github"]["repo"]
    url = f'https://api.github.com/repos/{repo}/contents/{file_path}'
    try:
        r = requests.get(url, headers=gh_headers(config), params={'ref': branch})
        r.raise_for_status()
        content = r.json().get('content', '')
        return base64.b64decode(content).decode('utf-8', errors='replace')
    except requests.exceptions.RequestException as e:
        print(f"  GitHub: could not fetch {file_path}: {e}")
        return None


def gh_build_prompt_context(changes, branch, config):
    parts = []
    total = 0
    budget = MAX_DIFF_CHARS + MAX_FILE_CHARS
    for change in changes:
        path = change.get('new_path', 'unknown')
        annotated = _annotate_diff(change.get('diff', ''))
        full_content = gh_get_file_content(path, branch, config)
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


def gh_build_diff_position_map(changes):
    """
    GitHub inline comments use 'position': the 1-based line offset within the
    unified diff (counting @@ headers and all +/- lines).
    Returns {path: {new_line_number: diff_position}}.
    """
    pos_map = {}
    for change in changes:
        path = change.get('new_path', 'unknown')
        mapping = {}
        position = 0
        new_line = 0
        for line in change.get('diff', '').splitlines():
            position += 1
            if line.startswith('@@'):
                m = re.search(r'\+(\d+)', line)
                if m:
                    new_line = int(m.group(1)) - 1
            elif not line.startswith('-'):
                new_line += 1
                mapping[new_line] = position
        pos_map[path] = mapping
    return pos_map


def gh_request_review(pr_number, config):
    repo = config["github"]["repo"]
    reviewer = config["github"].get("reviewer_username", "sergiorev")
    url = f'https://api.github.com/repos/{repo}/pulls/{pr_number}/requested_reviewers'
    try:
        r = requests.post(url, headers=gh_headers(config),
                          json={'reviewers': [reviewer]})
        r.raise_for_status()
        print(f"GitHub: requested {reviewer} as reviewer on PR #{pr_number}")
    except requests.exceptions.RequestException as e:
        print(f"GitHub: could not request reviewer: {e}")


def gh_post_review(pr_number, head_sha, review, changes, config):
    repo = config["github"]["repo"]
    reviewer = config["github"].get("reviewer_username", "sergiorev")
    url = f'https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews'

    pos_map = gh_build_diff_position_map(changes)

    verdict = review.get("verdict", "APPROVE WITH COMMENTS")
    gh_event = {
        "APPROVE": "APPROVE",
        "APPROVE WITH COMMENTS": "COMMENT",
        "REJECT": "REQUEST_CHANGES",
    }.get(verdict, "COMMENT")

    verdict_emoji = {"APPROVE": "✅", "APPROVE WITH COMMENTS": "⚠️", "REJECT": "❌"}.get(verdict, "🔍")
    body = (f"## {verdict_emoji} Sergio Ramos (@{reviewer}) — Code Review\n\n"
            f"{review.get('summary', '_No summary provided._')}")

    comments = []
    failed_inline = []
    for ic in review.get("inline_comments", []):
        file_path = ic.get("file", "")
        line = ic.get("line")
        comment_text = ic.get("comment", "")
        position = pos_map.get(file_path, {}).get(line)
        if position is None:
            print(f"  GitHub: invalid position skipped: {file_path}:{line}")
            failed_inline.append(ic)
            continue
        comments.append({
            "path": file_path,
            "position": position,
            "body": f"**Sergio Ramos** (@{reviewer}):\n\n{comment_text}",
        })

    if failed_inline:
        body += "\n\n---\n### Comments (could not be posted inline)\n\n"
        for ic in failed_inline:
            body += f"**`{ic.get('file')}` line {ic.get('line')}:** {ic.get('comment')}\n\n"

    payload = {
        "commit_id": head_sha,
        "body": body,
        "event": gh_event,
        "comments": comments,
    }

    try:
        r = requests.post(url, headers=gh_headers(config), json=payload)
        r.raise_for_status()
        print(f"GitHub: review posted on PR #{pr_number} ({gh_event}, {len(comments)} inline comment(s))")
    except requests.exceptions.RequestException as e:
        print(f"GitHub: failed to post review: {e}")
        # Fallback: plain issue comment
        fallback_url = f'https://api.github.com/repos/{repo}/issues/{pr_number}/comments'
        try:
            requests.post(fallback_url, headers=gh_headers(config), json={'body': body})
            print(f"GitHub: fallback comment posted on PR #{pr_number}")
        except Exception:
            print("GitHub: fallback comment also failed.")


def run_github(config):
    prs = gh_get_open_prs(config)
    if not prs:
        print("GitHub: no open (non-draft) PRs found.")
        return

    pr = sorted(prs, key=lambda x: x['updated_at'], reverse=True)[0]
    pr_number = pr['number']
    head_sha = pr['head']['sha']
    branch = pr['head']['ref']
    print(f"GitHub: PR #{pr_number}: {pr['title']}")

    changes = gh_get_pr_files(pr_number, config)
    if not changes:
        print("GitHub: no changed files found.")
        return

    print(f"GitHub: changed files: {[c['new_path'] for c in changes]}")
    gh_request_review(pr_number, config)

    prompt_context = gh_build_prompt_context(changes, branch, config)
    review = ollama_review(prompt_context, pr['title'])
    reviewer = config["github"].get("reviewer_username", "sergiorev")

    if not review:
        repo = config["github"]["repo"]
        fallback_url = f'https://api.github.com/repos/{repo}/issues/{pr_number}/comments'
        requests.post(fallback_url, headers=gh_headers(config),
                      json={'body': f"⚠️ **Sergio Ramos** (@{reviewer}): review failed (Ollama unavailable or bad JSON)."})
        save_context({'github_pr_number': pr_number})
        return

    gh_post_review(pr_number, head_sha, review, changes, config)
    save_context({'github_pr_number': pr_number})
    print("GitHub: done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sergio Ramos — automated code reviewer",
        epilog="With no flags, both GitLab MRs and GitHub PRs are reviewed."
    )
    parser.add_argument('--config', default='config.yml', help='Path to config file (default: config.yml)')
    parser.add_argument('--gitlab', action='store_true', help='Review GitLab MRs only')
    parser.add_argument('--github', action='store_true', help='Review GitHub PRs only')
    args = parser.parse_args()

    config = load_config(args.config)

    # No flags = run both
    run_gl = args.gitlab or (not args.gitlab and not args.github)
    run_gh = args.github or (not args.gitlab and not args.github)

    if run_gl:
        if "gitlab" not in config:
            print("GitLab config missing — skipping.")
        else:
            run_gitlab(config)

    if run_gh:
        if "github" not in config:
            print("GitHub config missing — skipping.")
        else:
            run_github(config)


if __name__ == '__main__':
    main()
