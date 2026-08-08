#!/usr/bin/env python3
import subprocess
import json
import os
import yaml
import sys
import re
import requests
import time

def load_config():
    with open('config.yml', 'r') as f:
        return yaml.safe_load(f)

def setup_ssh_for_service(service, config):
    """Setup SSH for a specific service"""
    if service == 'github':
        ssh_key = config.get('ssh_key') or os.path.expanduser("~/.ssh/id_ed25519_sergio_")
    elif service == 'gitlab':
        ssh_key = config.get('ssh_key') or os.path.expanduser("~/.ssh/id_ed25519_gl")
    else:
        return False
    
    if os.path.exists(ssh_key):
        os.environ['GIT_SSH_COMMAND'] = f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
        print(f"🔑 Using {service} SSH key: {ssh_key}")
        return True
    else:
        print(f"❌ SSH key not found for {service}: {ssh_key}")
        return False

# ============================================
# OLLAMA FUNCTIONS
# ============================================

def check_ollama_running():
    """Check if Ollama is running"""
    try:
        response = requests.get('http://127.0.0.1:11434/api/tags', timeout=2)
        if response.status_code == 200:
            return True
    except:
        pass
    return False

def start_ollama():
    """Start Ollama if not running"""
    print("🔄 Ollama not running. Attempting to start...")
    
    # Check if ollama is installed
    try:
        subprocess.run(['ollama', '--version'], capture_output=True, check=True)
    except:
        print("❌ Ollama not installed!")
        print("   Install with: curl -fsSL https://ollama.com/install.sh | sh")
        return False
    
    # Start Ollama in background
    try:
        subprocess.Popen(['ollama', 'serve'], 
                        stdout=subprocess.DEVNULL, 
                        stderr=subprocess.DEVNULL,
                        start_new_session=True)
        
        # Wait for it to start
        print("⏳ Waiting for Ollama to start...")
        for i in range(30):
            time.sleep(1)
            if check_ollama_running():
                print("✅ Ollama started successfully!")
                return True
            if i % 5 == 0:
                print(f"   Still waiting... ({i+1}s)")
        
        print("❌ Ollama failed to start within 30 seconds")
        return False
    except Exception as e:
        print(f"❌ Failed to start Ollama: {e}")
        return False

def ensure_ollama_running():
    """Ensure Ollama is running, start if needed"""
    if check_ollama_running():
        print("✅ Ollama is already running")
        return True
    
    return start_ollama()

def get_ollama_model():
    """Get the Ollama model to use"""
    config = load_config()
    ollama_config = config.get('ollama', {})
    model = ollama_config.get('model', 'qwen2.5-coder:7b')
    
    # Check if model exists
    try:
        response = requests.get('http://127.0.0.1:11434/api/tags')
        if response.status_code == 200:
            models = response.json().get('models', [])
            model_names = [m.get('name') for m in models]
            if model not in model_names:
                print(f"⚠️  Model '{model}' not found. Available: {', '.join(model_names[:5])}")
                if model_names:
                    model = model_names[0]
                    print(f"   Using '{model}' instead")
    except:
        pass
    
    return model

def review_with_ollama(diff, repo, pr_number, title, platform='github'):
    """Send diff to Ollama for review"""
    if not diff:
        print("   ⚠️  No diff to review")
        return None
    
    # Ensure Ollama is running
    if not ensure_ollama_running():
        print("   ❌ Ollama not available, skipping review")
        return None
    
    model = get_ollama_model()
    
    # Prepare prompt
    prompt = f"""
You are an expert code reviewer. Please review this pull request diff and provide constructive feedback.

Repository: {repo}
Platform: {platform}
PR Number: #{pr_number}
Title: {title}

Diff:
{diff[:10000]}  # Limit diff size

Please provide your review in the following format:

## Summary
[Brief summary of the changes]

## Issues Found
[List any issues, bugs, or potential problems]

## Suggestions
[Suggestions for improvement]

## Code Quality
[Comments on code quality, style, and best practices]

## Security Concerns
[Any security issues to address]

## Performance Impact
[Any performance implications]
"""
    
    print(f"   🤖 Sending to Ollama ({model})...")
    
    try:
        response = requests.post('http://127.0.0.1:11434/api/generate', 
                                 json={
                                     'model': model,
                                     'prompt': prompt,
                                     'stream': False,
                                     'temperature': 0.3
                                 },
                                 timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            review = result.get('response', '')
            print(f"   ✅ Review received ({len(review)} characters)")
            return review
        else:
            print(f"   ❌ Ollama API error: {response.status_code}")
            return None
    except requests.exceptions.Timeout:
        print("   ❌ Ollama request timed out (2 minutes)")
        return None
    except Exception as e:
        print(f"   ❌ Ollama error: {e}")
        return None

# ============================================
# GITHUB FUNCTIONS
# ============================================

def get_github_repos_for_user(username):
    """Get all repos for a GitHub user where the bot has access"""
    repos = []
    
    try:
        result = subprocess.run(
            ['gh', 'repo', 'list', username, '--limit', '100', '--json', 'name,owner,url,isPrivate'],
            capture_output=True,
            text=True,
            env={**os.environ}
        )
        if result.returncode == 0:
            repo_list = json.loads(result.stdout)
            repos = [f"{repo['owner']['login']}/{repo['name']}" for repo in repo_list]
            print(f"📂 Found {len(repos)} GitHub repos under {username}")
            return repos
    except Exception as e:
        print(f"⚠️  Error listing GitHub repos: {e}")
    
    return repos

def get_github_prs_where_reviewer(repo, reviewer_username):
    """Get open GitHub PRs where the bot is a reviewer"""
    prs = []
    
    try:
        result = subprocess.run(
            ['gh', 'pr', 'list', '--repo', repo, '--state', 'open', 
             '--json', 'number,title,headRefName,author,reviewRequests,reviews,url,additions,deletions,body,createdAt'],
            capture_output=True,
            text=True,
            env={**os.environ}
        )
        if result.returncode == 0:
            all_prs = json.loads(result.stdout)
            for pr in all_prs:
                review_requests = pr.get('reviewRequests', [])
                for req in review_requests:
                    if req.get('login') == reviewer_username:
                        prs.append(pr)
                        break
        else:
            if "no open pull requests" not in result.stderr.lower():
                print(f"   ⚠️  Error: {result.stderr[:100]}")
    except Exception as e:
        print(f"   ⚠️  Error: {str(e)[:100]}")
    
    return prs

def get_github_pr_diff(repo, pr_number):
    """Get the diff for a GitHub PR"""
    try:
        result = subprocess.run(
            ['gh', 'pr', 'diff', '--repo', repo, str(pr_number)],
            capture_output=True,
            text=True,
            env={**os.environ}
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except Exception:
        return None

def post_github_review_comment(repo, pr_number, review_text):
    """Post review comment to GitHub PR"""
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.md') as f:
            f.write(review_text)
            temp_file = f.name
        
        result = subprocess.run(
            ['gh', 'pr', 'comment', '--repo', repo, str(pr_number), '--body-file', temp_file],
            capture_output=True,
            text=True,
            env={**os.environ}
        )
        
        os.unlink(temp_file)
        
        if result.returncode == 0:
            print(f"   ✅ Review comment posted to #{pr_number}")
            return True
        else:
            print(f"   ⚠️  Failed to post comment: {result.stderr[:100]}")
            return False
    except Exception as e:
        print(f"   ⚠️  Error posting comment: {e}")
        return False

def process_github_prs():
    """Main function to process GitHub PRs"""
    print("\n" + "=" * 70)
    print("🐙 GITHUB PR REVIEW")
    print("=" * 70)
    
    config = load_config()
    github_config = config.get('github', {})
    
    owner = github_config.get('owner') or github_config.get('organization')
    reviewer = github_config.get('reviewer_username', 'sergiorev')
    
    if not owner:
        print("⚠️  No GitHub owner/organization configured")
        return []
    
    # Setup SSH for GitHub
    if not setup_ssh_for_service('github', github_config):
        print("❌ GitHub SSH setup failed")
        return []
    
    print(f"👤 Bot user: {reviewer}")
    print(f"📂 Owner: {owner}")
    print("")
    
    # Get all repos
    repos = get_github_repos_for_user(owner)
    if not repos:
        print("❌ No GitHub repos found")
        return []
    
    # Check each repo for PRs
    all_prs = []
    for i, repo in enumerate(repos, 1):
        print(f"[{i}/{len(repos)}] 🔍 Checking {repo}...")
        prs = get_github_prs_where_reviewer(repo, reviewer)
        if prs:
            print(f"   ✅ Found {len(prs)} PR(s)")
            for pr in prs:
                print(f"      • #{pr['number']}: {pr['title']} (by @{pr.get('author', {}).get('login', 'unknown')})")
                print(f"        URL: {pr['url']}")
                print(f"        +{pr.get('additions', 0)} -{pr.get('deletions', 0)} lines")
            all_prs.extend([{'repo': repo, 'pr': pr, 'platform': 'github'} for pr in prs])
        else:
            print(f"   ❌ No PRs found")
    
    # Process each PR with Ollama
    if all_prs:
        print("\n" + "=" * 70)
        print("🤖 REVIEWING PRS WITH OLLAMA")
        print("=" * 70)
        
        for item in all_prs:
            repo = item['repo']
            pr = item['pr']
            pr_number = pr['number']
            title = pr['title']
            
            print(f"\n📝 Reviewing PR #{pr_number}: {title}")
            print(f"   Repository: {repo}")
            
            # Get the diff
            print(f"   📄 Fetching diff...")
            diff = get_github_pr_diff(repo, pr_number)
            
            if diff:
                print(f"   ✅ Diff size: {len(diff)} characters")
                
                # Review with Ollama
                review = review_with_ollama(diff, repo, pr_number, title, 'github')
                
                if review:
                    print(f"\n   📋 Review summary:")
                    # Print first few lines of review
                    summary_lines = review.split('\n')[:5]
                    for line in summary_lines:
                        if line.strip():
                            print(f"      {line}")
                    print(f"      ... (full review in PR comment)")
                    
                    # Post comment to PR
                    post_github_review_comment(repo, pr_number, review)
                else:
                    print(f"   ⚠️  No review generated")
            else:
                print(f"   ❌ Failed to fetch diff")
    
    print(f"\n📊 GitHub total: {len(all_prs)} PR(s) found")
    return all_prs

# ============================================
# GITLAB FUNCTIONS
# ============================================

def get_gitlab_mrs_where_reviewer(project_id, reviewer_username, gitlab_token):
    """Get open GitLab MRs where the bot is a reviewer"""
    mrs = []
    
    url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests"
    params = {
        'state': 'opened',
        'per_page': 100,
        'view': 'simple'
    }
    headers = {'PRIVATE-TOKEN': gitlab_token}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            all_mrs = response.json()
            for mr in all_mrs:
                reviewers = mr.get('reviewers', [])
                for reviewer in reviewers:
                    if reviewer.get('username') == reviewer_username:
                        mrs.append(mr)
                        break
                
                if not mrs or mrs[-1] != mr:
                    assignees = mr.get('assignees', [])
                    for assignee in assignees:
                        if assignee.get('username') == reviewer_username:
                            mrs.append(mr)
                            break
        else:
            print(f"   ⚠️  GitLab API error: {response.status_code}")
    except Exception as e:
        print(f"   ⚠️  GitLab error: {e}")
    
    return mrs

def get_gitlab_mr_diff(project_id, mr_iid, gitlab_token):
    """Get the diff for a GitLab MR"""
    url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}/diffs"
    headers = {'PRIVATE-TOKEN': gitlab_token}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            diffs = response.json()
            diff_text = ""
            for diff in diffs:
                diff_text += f"diff --git a/{diff.get('old_path')} b/{diff.get('new_path')}\n"
                diff_text += diff.get('diff', '')
                diff_text += "\n"
            return diff_text
        return None
    except Exception:
        return None

def post_gitlab_review_comment(project_id, mr_iid, review_text, gitlab_token):
    """Post review comment to GitLab MR"""
    url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
    headers = {'PRIVATE-TOKEN': gitlab_token}
    data = {'body': review_text}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            print(f"   ✅ Review comment posted to !{mr_iid}")
            return True
        else:
            print(f"   ⚠️  Failed to post comment: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ⚠️  Error posting comment: {e}")
        return False

def process_gitlab_mrs():
    """Main function to process GitLab MRs"""
    print("\n" + "=" * 70)
    print("🦊 GITLAB MR REVIEW")
    print("=" * 70)
    
    config = load_config()
    gitlab_config = config.get('gitlab', {})
    
    project_id = gitlab_config.get('project_id')
    reviewer = gitlab_config.get('reviewer_username', 'sergioram')
    token = gitlab_config.get('token')
    
    if not project_id:
        print("⚠️  No GitLab project_id configured")
        return []
    
    if not token:
        print("⚠️  No GitLab token configured")
        return []
    
    print(f"👤 Bot user: {reviewer}")
    print(f"📂 Project ID: {project_id}")
    print("")
    
    # Get MRs where bot is a reviewer
    print(f"🔍 Checking GitLab project {project_id} for MRs assigned to {reviewer}...")
    mrs = get_gitlab_mrs_where_reviewer(project_id, reviewer, token)
    
    if mrs:
        print(f"   ✅ Found {len(mrs)} MR(s)")
        for mr in mrs:
            print(f"      • !{mr['iid']}: {mr['title']} (by @{mr.get('author', {}).get('username', 'unknown')})")
            print(f"        URL: {mr['web_url']}")
            print(f"        +{mr.get('additions', 0)} -{mr.get('deletions', 0)} lines")
    else:
        print(f"   ❌ No MRs found")
    
    # Process each MR with Ollama
    if mrs:
        print("\n" + "=" * 70)
        print("🤖 REVIEWING MRS WITH OLLAMA")
        print("=" * 70)
        
        for mr in mrs:
            mr_iid = mr['iid']
            title = mr['title']
            
            print(f"\n📝 Reviewing MR !{mr_iid}: {title}")
            print(f"   Project: {project_id}")
            
            # Get the diff
            print(f"   📄 Fetching diff...")
            diff = get_gitlab_mr_diff(project_id, mr_iid, token)
            
            if diff:
                print(f"   ✅ Diff size: {len(diff)} characters")
                
                # Review with Ollama
                review = review_with_ollama(diff, f"project_{project_id}", mr_iid, title, 'gitlab')
                
                if review:
                    print(f"\n   📋 Review summary:")
                    summary_lines = review.split('\n')[:5]
                    for line in summary_lines:
                        if line.strip():
                            print(f"      {line}")
                    print(f"      ... (full review in MR comment)")
                    
                    # Post comment to MR
                    post_gitlab_review_comment(project_id, mr_iid, review, token)
                else:
                    print(f"   ⚠️  No review generated")
            else:
                print(f"   ❌ Failed to fetch diff")
    
    print(f"\n📊 GitLab total: {len(mrs)} MR(s) found")
    return mrs

# ============================================
# MAIN FUNCTION
# ============================================

def main():
    print("=" * 70)
    print("🤖 Sergio Bot - Multi-Platform Code Review Automation")
    print("=" * 70)
    
    # Process GitHub PRs
    github_prs = process_github_prs()
    
    # Process GitLab MRs
    gitlab_mrs = process_gitlab_mrs()
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"🐙 GitHub: {len(github_prs)} PR(s) found")
    print(f"🦊 GitLab: {len(gitlab_mrs)} MR(s) found")
    print(f"📦 Total: {len(github_prs) + len(gitlab_mrs)} items to review")
    print("=" * 70)

if __name__ == "__main__":
    main()
