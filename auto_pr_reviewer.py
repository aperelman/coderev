#!/usr/bin/env python3
import subprocess
import json
import os
import yaml
import sys
import re
import requests
import time

# Available models for selection
AVAILABLE_MODELS = {
    '1': {
        'name': 'codellama:7b',
        'description': 'Code-specific model (best for code review)',
        'speed': 'Medium',
        'quality': 'Excellent',
        'timeout': 300
    },
    '2': {
        'name': 'mistral:7b',
        'description': 'Fast general-purpose model',
        'speed': 'Fast',
        'quality': 'Very Good',
        'timeout': 150
    },
    '3': {
        'name': 'neural-chat:7b',
        'description': 'Lightweight, optimized for conversations (recommended)',
        'speed': 'Very Fast',
        'quality': 'Good',
        'timeout': 150
    },
    '4': {
        'name': 'stable-code:3b',
        'description': 'Ultra-lightweight code model (fastest)',
        'speed': 'Ultra Fast',
        'quality': 'Good',
        'timeout': 120
    },
    '5': {
        'name': 'qwen2.5-coder:7b',
        'description': 'Default model (slowest but detailed)',
        'speed': 'Slow',
        'quality': 'Excellent',
        'timeout': 300
    }
}

def load_config():
    try:
        with open('config.yml', 'r') as f:
            return yaml.safe_load(f)
    except:
        return {}

# ─── DOCKER OLLAMA FUNCTIONS ──────────────────────────────────────────────────

def check_ollama_container_running():
    """Check if the Ollama Docker container is running"""
    try:
        result = subprocess.run(
            ['docker', 'ps', '--filter', 'name=ollama', '--format', '{{.Status}}'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0 and 'Up' in result.stdout
    except:
        return False

def check_ollama_api_ready():
    """Check if Ollama API is responding"""
    try:
        response = requests.get('http://127.0.0.1:11434/api/tags', timeout=2)
        return response.status_code == 200
    except:
        return False

def start_ollama_via_node():
    """Start Ollama using the Node.js script"""
    print("🔄 Starting Ollama container via ollama-start.js...")
    
    # Check if the Node script exists in various locations
    script_paths = [
        os.path.join(os.path.dirname(__file__), 'ollama-start.js'),
        './ollama-start.js',
        os.path.expanduser('~/src/coderev/ollama-start.js'),
        '/home/amitp/src/coderev/ollama-start.js'
    ]
    
    script_path = None
    for path in script_paths:
        if os.path.exists(path):
            script_path = path
            break
    
    if not script_path:
        print("❌ ollama-start.js not found!")
        print("   Please ensure ollama-start.js is in the current directory")
        return False
    
    try:
        # Start the Node script in the background with output redirected
        process = subprocess.Popen(
            ['node', script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True
        )
        
        # Wait up to 60 seconds for Ollama to be ready
        print("⏳ Waiting for Ollama container to start...")
        for i in range(60):
            time.sleep(1)
            if check_ollama_api_ready():
                print("✅ Ollama container started and API is ready!")
                return True
            if i % 10 == 0 and i > 0:
                print(f"   Still waiting... ({i}s)")
        
        print("❌ Ollama container failed to start within 60 seconds")
        print("   Check: docker logs ollama")
        return False
        
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"Error: Ollama failed to start: {e}")
        return False
        print(f"❌ Failed to start Ollama via Node: {e}")
        return False

def ensure_ollama_running():
    """Ensure Ollama is running (using Docker container)"""
    
    # First check if the Docker container is already running
    if check_ollama_container_running():
        # Check if API is ready
        if check_ollama_api_ready():
            print("✅ Ollama container is running")
            return True
        else:
            print("⏳ Ollama container running but API not ready yet...")
            # Wait a bit for API to become ready
            for i in range(10):
                time.sleep(1)
                if check_ollama_api_ready():
                    print("✅ Ollama API is now ready!")
                    return True
            print("⚠️  API still not responding, attempting restart...")
    
    # If container isn't running or API isn't ready, start it
    return start_ollama_via_node()

def pull_model(model_name):
    """Pull a model from Ollama Hub using the Docker container"""
    print(f"\n📥 Pulling model: {model_name}...")
    print(f"   This may take a few minutes (model is ~4-5GB)...\n")
    
    try:
        subprocess.run(['docker', 'exec', 'ollama', 'ollama', 'pull', model_name], check=True)
        print(f"\n✅ Model {model_name} installed successfully!\n")
        return True
    except subprocess.CalledProcessError:
        print(f"\n❌ Failed to pull {model_name}")
        return False
    except FileNotFoundError:
        print(f"\n❌ Docker not installed or not running!")
        return False

def get_installed_models():
    """Get list of currently installed models from Docker container"""
    try:
        result = subprocess.run(
            ['docker', 'exec', 'ollama', 'ollama', 'list'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # Parse the output (skip header line)
            models = []
            for line in result.stdout.strip().split('\n')[1:]:
                if line.strip():
                    # Extract model name (first field)
                    parts = line.split()
                    if parts:
                        models.append(parts[0])
            return models
    except:
        pass
    return []

def select_model_interactive():
    """Prompt user to select a model - shows ALL models and auto-pulls if needed"""
    print("\n" + "=" * 70)
    print("🤖 SELECT CODE REVIEW MODEL")
    print("=" * 70)
    
    # Make sure Ollama is running before checking models
    if not ensure_ollama_running():
        print("❌ Cannot proceed without Ollama running")
        sys.exit(1)
    
    # Get installed models
    print("\n📦 Checking installed models...\n")
    installed_names = get_installed_models()
    
    # Show ALL 5 models with status
    print("Available models:\n")
    for key in sorted(AVAILABLE_MODELS.keys()):
        model = AVAILABLE_MODELS[key]
        status = "✅ INSTALLED" if model['name'] in installed_names else "⬇️  NOT INSTALLED"
        print(f"  {key}️⃣  {model['name']:<25} [{status}]")
        print(f"     {model['description']}")
        print(f"     Speed: {model['speed']} | Quality: {model['quality']}")
        print()
    
    # Get config default
    config = load_config()
    default_model = config.get('ollama', {}).get('model', 'qwen2.5-coder:7b')
    default_key = None
    for key, model in AVAILABLE_MODELS.items():
        if model['name'] == default_model:
            default_key = key
            break
    
    if default_key:
        print(f"📌 Default from config: {default_key} ({default_model})\n")
    
    # Prompt for selection - accept numbers 1-5
    while True:
        prompt = f"Enter model number (1-5) [{default_key if default_key else '1'}]: "
        choice = input(prompt).strip()
        
        # Use default if empty input
        if not choice:
            choice = default_key if default_key else '1'
        
        # Validate it's a valid number
        if choice not in AVAILABLE_MODELS:
            print(f"❌ Invalid! Enter a number between 1-5\n")
            continue
        
        selected = AVAILABLE_MODELS[choice]
        model_name = selected['name']
        
        # Refresh installed list
        installed_names = get_installed_models()
        
        # Check if model is installed
        if model_name not in installed_names:
            print(f"\n⚠️  Model {model_name} is not installed")
            response = input(f"Do you want to download it now? (y/n) [y]: ").strip().lower()
            
            if response != 'n':
                if not pull_model(model_name):
                    print("Continuing without this model...\n")
                    continue
                # After pulling, refresh and continue
                installed_names = get_installed_models()
            else:
                print("Skipping this model. Choose another...\n")
                continue
        
        print(f"✅ Selected: {model_name}")
        print(f"   {selected['description']}")
        print(f"   Timeout: {selected['timeout']}s\n")
        return model_name, selected['timeout']

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
# DIFF CHUNKING FUNCTIONS
# ============================================

def chunk_diff(diff, max_chunk_size=15000):
    """Split large diffs into smaller chunks for Ollama processing."""
    if len(diff) <= max_chunk_size:
        return [diff]
    
    chunks = []
    current_chunk = ""
    
    file_diffs = re.split(r'(diff --git [^\n]+\n)', diff)
    
    for i, part in enumerate(file_diffs):
        if not part.strip():
            continue
        
        if len(current_chunk) + len(part) > max_chunk_size and current_chunk:
            chunks.append(current_chunk)
            current_chunk = part
        else:
            current_chunk += part
    
    if current_chunk.strip():
        chunks.append(current_chunk)
    
    return chunks if chunks else [diff]

# ============================================
# OLLAMA REVIEW FUNCTIONS
# ============================================

def build_review_prompt(diff, repo, pr_number, title, platform='github', chunk_idx=None, total_chunks=None):
    """Build review prompt with cleaner, more readable format"""
    part_info = f"(Part {chunk_idx} of {total_chunks}) " if chunk_idx else ""
    
    return f"""You are an expert code reviewer. Please review this code diff and provide constructive feedback.

Repository: {repo}
Platform: {platform}
PR Number: #{pr_number}
Title: {title}
{part_info}

Diff:
{diff}

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

def review_with_ollama(diff, repo, pr_number, title, model, timeout, platform='github', fallback_model=None):
    """Send diff to Ollama for review"""
    if not diff:
        print("   ⚠️  No diff to review")
        return None
    
    # Ensure Ollama is running
    if not ensure_ollama_running():
        print("   ❌ Ollama not available, skipping review")
        return None
    
    chunks = chunk_diff(diff, max_chunk_size=15000)
    
    if len(chunks) > 1:
        print(f"   📦 Large diff split into {len(chunks)} chunks")
    
    all_reviews = []
    
    for chunk_idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            print(f"   📝 Processing chunk {chunk_idx + 1}/{len(chunks)}...")
        
        prompt = build_review_prompt(
            chunk, repo, pr_number, title, platform,
            chunk_idx + 1 if len(chunks) > 1 else None,
            len(chunks) if len(chunks) > 1 else None
        )
        
        print(f"   🤖 Sending to Ollama ({model})...")
        
        try:
            response = requests.post('http://127.0.0.1:11434/api/generate', 
                                     json={
                                         'model': model,
                                         'prompt': prompt,
                                         'stream': False,
                                         'temperature': 0.3
                                     },
                                     timeout=timeout)
            
            if response.status_code == 200:
                result = response.json()
                review = result.get('response', '')
                print(f"   ✅ Review received ({len(review)} characters)")
                all_reviews.append(review)
            else:
                print(f"   ❌ Ollama API error: {response.status_code}")
                return None
        except requests.exceptions.Timeout:
            print(f"   ⏱️  Request timed out ({timeout}s)")
            if not fallback_model:
                print(f"      💡 Retrying with faster model (neural-chat:7b)...")
                return review_with_ollama(diff, repo, pr_number, title, 'neural-chat:7b', 150, platform, fallback_model='used')
            else:
                print(f"      Tip: Try a faster model or increase timeout")
                return None
        except requests.exceptions.ConnectionError:
            print(f"   ❌ Cannot connect to Ollama (http://127.0.0.1:11434)")
            print(f"      Check: docker ps | grep ollama")
            return None
        except requests.exceptions.RequestException as e:
            print(f"   ❌ Network error: {type(e).__name__}")
            return None
        except ValueError as e:
            print(f"   ❌ Invalid response from Ollama: {e}")
            return None
        except Exception as e:
            print(f"   ❌ Unexpected error: {type(e).__name__}: {str(e)[:80]}")
            return None
    
    if all_reviews:
        if len(all_reviews) > 1:
            combined_review = f"## Code Review Summary (Analyzed {len(chunks)} parts)\n\n" + "\n\n---\n\n".join(all_reviews)
        else:
            combined_review = all_reviews[0]
        return combined_review
    
    return None

# ============================================
# GITHUB FUNCTIONS
# ============================================

def get_github_repos_for_user(username):
    """Get all repos for a GitHub user"""
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
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"Error listing GitHub repos: {e}")
    
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
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"Error: {str(e)[:100]}")
    
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
    except subprocess.CalledProcessError as e:
        print(f"Error: {str(e)[:100]}")
    
        print(f"   ⚠️  Error: {str(e)[:100]}")
    
    return None

def post_github_review_comment(repo, pr_number, review_text):
    """Post review comment to GitHub PR"""
    if not review_text:
        return
    
    max_comment_size = 65000
    if len(review_text) > max_comment_size:
        review_text = review_text[:max_comment_size] + f"\n\n... (truncated, {len(review_text) - max_comment_size} characters omitted)"
    
    try:
        result = subprocess.run(
            ['gh', 'pr', 'comment', '--repo', repo, str(pr_number), '--body', review_text],
            capture_output=True,
            text=True,
            env={**os.environ}
        )
        if result.returncode == 0:
            print(f"   ✅ Review comment posted to PR #{pr_number}")
            return True
        else:
            print(f"   ⚠️  Failed to post comment: {result.stderr[:100]}")
            return False
    except subprocess.CalledProcessError as e:
        print(f"Error posting comment: {e}")
        return False
        print(f"   ⚠️  Error posting comment: {e}")
        return False

def process_github_prs(model, timeout):
    """Main function to process GitHub PRs"""
    print("\n" + "=" * 70)
    print("🐙 GITHUB PR REVIEW")
    print("=" * 70)
    
    config = load_config()
    github_config = config.get('github', {})
    
    if not setup_ssh_for_service('github', github_config):
        print("⚠️  Could not setup GitHub SSH")
        return []
    
    username = github_config.get('owner', 'NimbusHelmAI')
    reviewer = github_config.get('reviewer', 'sergiorev')
    
    print(f"👤 Bot user: {reviewer}")
    print(f"📂 Owner: {username}")
    print("")
    
    repos = get_github_repos_for_user(username)
    if not repos:
        print("❌ No repos found")
        return []
    
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
            
            print(f"   📄 Fetching diff...")
            diff = get_github_pr_diff(repo, pr_number)
            
            if diff:
                print(f"   ✅ Diff size: {len(diff)} characters")
                
                review = review_with_ollama(diff, repo, pr_number, title, model, timeout, 'github')
                
                if review:
                    print(f"\n   📋 Review summary:")
                    summary_lines = review.split('\n')[:5]
                    for line in summary_lines:
                        if line.strip():
                            print(f"      {line}")
                    print(f"      ... (full review in PR comment)")
                    
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

def get_gitlab_mrs_where_reviewer(reviewer_username, gitlab_token):
    """Get open GitLab MRs where the bot is a reviewer"""
    mrs = []
    
    url = "https://gitlab.com/api/v4/merge_requests"
    params = {
        'state': 'opened',
        'reviewer_username': reviewer_username,
        'scope': 'all',
        'per_page': 100,
        'view': 'simple'
    }
    headers = {'PRIVATE-TOKEN': gitlab_token}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            all_mrs = response.json()
            mrs = all_mrs  # API already filters by reviewer_username
    except requests.exceptions.RequestException as e:
        print(f"GitLab error: {e}")
    
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
    except requests.exceptions.Timeout:
        print(f"   ⚠️  GitLab API timeout - response took too long")
    except requests.exceptions.ConnectionError:
        print(f"   ⚠️  GitLab connection error - check network")
    except requests.exceptions.HTTPError as e:
        print(f"   ⚠️  GitLab API error: {e.response.status_code}")
    except Exception as e:
        print(f"   ⚠️  Unexpected error fetching GitLab diff: {e}")
    
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
    except requests.exceptions.RequestException as e:
        print(f"Error posting comment: {e}")
    
        print(f"   ⚠️  Error posting comment: {e}")
    
    return False

def process_gitlab_mrs(model, timeout):
    """Main function to process GitLab MRs"""
    print("\n" + "=" * 70)
    print("🦊 GITLAB MR REVIEW")
    print("=" * 70)
    
    config = load_config()
    gitlab_config = config.get('gitlab', {})
    
    project_id = gitlab_config.get('project_id')
    reviewer = gitlab_config.get('reviewer', 'sergioram')
    token = gitlab_config.get('owner_token')
    
    if not token:
        print("⚠️  No GitLab token configured")
        return []
    
    print(f"👤 Bot user: {reviewer}")
    print("")
    
    print(f"🔍 Searching all GitLab projects for MRs assigned to {reviewer}...")
    mrs = get_gitlab_mrs_where_reviewer(reviewer, token)
    
    if mrs:
        print(f"   ✅ Found {len(mrs)} MR(s)")
        for mr in mrs:
            print(f"      • !{mr['iid']}: {mr['title']}")
    else:
        print(f"   ❌ No MRs found")
    
    if mrs:
        print("\n" + "=" * 70)
        print("🤖 REVIEWING MRS WITH OLLAMA")
        print("=" * 70)
        
        for mr in mrs:
            mr_iid = mr['iid']
            project_id = mr.get('project_id')
            title = mr['title']
            
            print(f"\n📝 Reviewing MR !{mr_iid}: {title}")
            print(f"   Project: {project_id}")
            
            print(f"   📄 Fetching diff...")
            diff = get_gitlab_mr_diff(project_id, mr_iid, token)
            
            if diff:
                print(f"   ✅ Diff size: {len(diff)} characters")
                
                review = review_with_ollama(diff, f"project_{project_id}", mr_iid, title, model, timeout, 'gitlab')
                
                if review:
                    print(f"\n   📋 Review summary:")
                    summary_lines = review.split('\n')[:5]
                    for line in summary_lines:
                        if line.strip():
                            print(f"      {line}")
                    print(f"      ... (full review in MR comment)")
                    
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
    import argparse
    
    parser = argparse.ArgumentParser(description="Automated multi-platform PR reviewer powered by Ollama.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--github-only", "--gh-only", action="store_true", help="Review GitHub pull requests only")
    group.add_argument("--gitlab-only", "--glab-only", action="store_true", help="Review GitLab merge requests only")
    parser.add_argument("--model", type=str, help="Specify Ollama model to use")
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds")
    args = parser.parse_args()
    
    if args.github_only and args.gitlab_only:
        parser.error("Cannot use both --github-only and --gitlab-only")
    
    review_github = not args.gitlab_only
    review_gitlab = not args.github_only
    
    print("=" * 70)
    print("🤖 Sergio Bot - Multi-Platform Code Review Automation")
    print("=" * 70)
    
    # Select model (with auto-pull)
    model, timeout = select_model_interactive()
    
    # Process GitHub PRs
    github_prs = process_github_prs(model, timeout) if review_github else []
    
    # Process GitLab MRs
    gitlab_mrs = process_gitlab_mrs(model, timeout) if review_gitlab else []
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"🤖 Model used: {model} (timeout: {timeout}s)")
    print(f"🐙 GitHub: {len(github_prs)} PR(s) found")
    print(f"🦊 GitLab: {len(gitlab_mrs)} MR(s) found")
    print(f"📦 Total: {len(github_prs) + len(gitlab_mrs)} items to review")
    print("=" * 70)

if __name__ == "__main__":
    main()