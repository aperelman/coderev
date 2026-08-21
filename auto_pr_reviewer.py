#!/usr/bin/env python3
"""Auto PR/MR reviewer using Ollama - FINDS AND REVIEWS ALL WHERE SERGIO IS REVIEWER"""
import re
import requests
import json
import yaml
import sys
import time
import subprocess
import os
import argparse
from urllib.parse import quote

# Default Ollama URL
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_MODES_FILE = "model_modes.json"

# Available models
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
        'description': 'Fast and capable, good for code',
        'speed': 'Fast',
        'quality': 'Very Good',
        'timeout': 180
    },
    '3': {
        'name': 'qwen2.5-coder:7b',
        'description': 'Qwen coder model, balanced performance',
        'speed': 'Medium',
        'quality': 'Excellent',
        'timeout': 300
    },
    '4': {
        'name': 'neural-chat:7b',
        'description': 'Optimized for conversation and reasoning',
        'speed': 'Medium',
        'quality': 'Good',
        'timeout': 240
    },
    '5': {
        'name': 'stable-code:3b',
        'description': 'Lightweight, very fast model',
        'speed': 'Very Fast',
        'quality': 'Good',
        'timeout': 120
    }
}

def parse_arguments():
    """Parse command line arguments for platform selection"""
    parser = argparse.ArgumentParser(description="Review GitHub PRs and GitLab MRs where sergio is reviewer", formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""
Examples:
  %(prog)s                 # Review both GitHub and GitLab (default)
  %(prog)s --gh-only      # Review only GitHub PRs
  %(prog)s --glab-only    # Review only GitLab MRs
        """)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--gh-only', action='store_true', help='Review GitHub PRs only')
    group.add_argument('--glab-only', action='store_true', help='Review GitLab MRs only')
    args = parser.parse_args()
    review_github = not args.glab_only
    review_gitlab = not args.gh_only
    return review_github, review_gitlab

def load_config():
    """Load configuration from config.yml."""
    try:
        with open('config.yml', 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print("config.yml not found.")
        sys.exit(1)

def is_ollama_running():
    """Check if Ollama is responding"""
    try:
        requests.get("http://127.0.0.1:11434/api/tags", timeout=2)
        return True
    except:
        return False

def start_ollama():
    """Start Ollama container"""
    print("🚀 Starting Ollama container...")
    try:
        subprocess.run(["docker", "start", "ollama"], 
                       capture_output=True, timeout=10)
        return True
    except Exception as e:
        print(f"❌ Failed to start Ollama: {e}")
        return False

def wait_for_ollama(timeout=30):
    """Wait for Ollama to be ready"""
    for i in range(timeout):
        if is_ollama_running():
            print("✅ Ollama ready!")
            return True
        print(f"⏳ Waiting for Ollama... ({i+1}/{timeout}s)", end='\r')
        time.sleep(1)
    print("\n❌ Ollama timeout")
    return False

def check_ollama_health():
    """Check and start Ollama if needed"""
    print("🔍 Checking Ollama...")
    if is_ollama_running():
        print("✅ Ollama already running!")
        return True
    
    print("❌ Ollama not responding")
    if not start_ollama():
        return False
    
    return wait_for_ollama()

def list_available_models():
    """List installed models in Ollama"""
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
        r.raise_for_status()
        models = r.json().get('models', [])
        return [m['name'] for m in models]
    except:
        return []

def pull_model(model_name):
    """Pull a model from registry"""
    print(f"📥 Pulling model {model_name}...")
    try:
        r = requests.post(
            "http://127.0.0.1:11434/api/pull",
            json={"name": model_name},
            timeout=600
        )
        r.raise_for_status()
        print(f"✅ Model {model_name} pulled successfully!")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to pull model: {e}")
        return False

def load_model_modes():
    """Load cached model modes (json/text)"""
    if os.path.exists(MODEL_MODES_FILE):
        try:
            with open(MODEL_MODES_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_model_modes(modes):
    """Save model modes to cache"""
    try:
        with open(MODEL_MODES_FILE, 'w') as f:
            json.dump(modes, f, indent=2)
    except:
        pass

def test_model_json_support(model_name, timeout):
    """Test if model supports JSON output"""
    test_prompt = 'Respond ONLY with this JSON (no other text): {"test": "ok"}'
    
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": model_name,
            "prompt": test_prompt,
            "stream": False,
            "options": {"temperature": 0.2}
        }, timeout=10)
        
        response_text = r.json().get("response", "").strip()
        json.loads(response_text)  # Try to parse
        return "json"
    except:
        return "text"

def select_model():
    """Interactive model selection"""
    print("\n" + "="*60)
    print("🤖 Available Models for Code Review")
    print("="*60)
    
    for key, model in AVAILABLE_MODELS.items():
        print(f"\n{key}. {model['name']}")
        print(f"   📝 {model['description']}")
        print(f"   ⚡ Speed: {model['speed']}")
        print(f"   ⭐ Quality: {model['quality']}")
    
    print("\n" + "="*60)
    choice = input("Select model (1-5, or press Enter for default): ").strip()
    
    if not choice:
        choice = '3'  # Default: qwen2.5-coder
    
    if choice not in AVAILABLE_MODELS:
        print("❌ Invalid choice. Using default model.")
        choice = '3'
    
    model_info = AVAILABLE_MODELS[choice]
    model_name = model_info['name']
    
    # Check if model is available
    available = list_available_models()
    if model_name not in available:
        print(f"\n⚠️  Model {model_name} not found locally.")
        pull = input(f"Download {model_name}? (y/n): ").strip().lower()
        if pull == 'y':
            if not pull_model(model_name):
                print("Failed to pull model. Exiting.")
                sys.exit(1)
        else:
            print("Exiting.")
            sys.exit(1)
    else:
        print(f"\n✅ Model {model_name} already installed")
    
    return model_name, model_info['timeout']

def detect_model_mode(model_name, timeout):
    """Auto-detect if model supports JSON"""
    modes = load_model_modes()
    
    # Check cache first
    if model_name in modes:
        mode = modes[model_name]
        print(f"📋 Using cached mode for {model_name}: {mode.upper()}")
        return mode
    
    # Test model
    print(f"🧪 Testing {model_name} for JSON support...")
    mode = test_model_json_support(model_name, timeout)
    
    # Save to cache
    modes[model_name] = mode
    save_model_modes(modes)
    
    print(f"✅ {model_name} mode detected: {mode.upper()}")
    return mode

def get_gitlab_mrs_with_sergio(config):
    """Find ALL GitLab MRs where sergio is reviewer"""
    token = config.get("gitlab", {}).get("token")
    reviewer_username = config.get("gitlab", {}).get("reviewer_username", "sergioram")
    
    if not token:
        print("⚠️  GitLab token not configured")
        return []
    
    mrs = []
    
    try:
        # Query all open MRs where sergio is reviewer
        url = "https://gitlab.com/api/v4/merge_requests"
        headers = {'PRIVATE-TOKEN': token.strip()}
        
        # Get open MRs where reviewer_username is sergioram (using API filter)
        r = requests.get(url, headers=headers, params={
            'state': 'opened',
            'reviewer_username': reviewer_username,
            'per_page': 100,
            'scope': 'all',
        }, timeout=10)
        r.raise_for_status()
        
        all_mrs = r.json()
        
        # All returned MRs already have sergio as reviewer
        for mr in all_mrs:
            mrs.append({
                'platform': 'gitlab',
                'project_id': mr['project_id'],
                'iid': mr['iid'],
                'title': mr['title'],
                'web_url': mr['web_url']
            })
    except requests.exceptions.RequestException as e:
        print(f"⚠️  GitLab error: {e}")
    
    return mrs

def get_github_prs_with_sergio(config):
    """Find ALL GitHub PRs where sergio is reviewer"""
    username = config.get("github", {}).get("username", "aperelman")
    reviewer = config.get("github", {}).get("reviewer_username", "sergiorev")
    
    prs = []
    
    try:
        # Step 1: Get all repos owned by user
        result = subprocess.run([
            "gh", "repo", "list", username,
            "--json", "nameWithOwner",
            "--limit", "100"
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            print(f"⚠️  Failed to list repos: {result.stderr}")
            return prs
        
        repos = json.loads(result.stdout)
        
        # Step 2: For each repo, list open PRs and filter
        for repo in repos:
            repo_name = repo['nameWithOwner']
            
            result = subprocess.run([
                "gh", "pr", "list",
                "--repo", repo_name,
                "--state", "open",
                "--json", "number,title,url,reviewRequests"
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                pr_list = json.loads(result.stdout)
                for pr in pr_list:
                    # Check if sergio is in reviewRequests
                    review_requests = pr.get('reviewRequests', [])
                    for req in review_requests:
                        if req.get('login') == reviewer:
                            prs.append({
                                'platform': 'github',
                                'repo': repo_name,
                                'number': pr['number'],
                                'title': pr['title'],
                                'web_url': pr['url']
                            })
                            break
    except Exception as e:
        print(f"⚠️  GitHub error: {e}")
    
    return prs

def get_gitlab_mr_diff(project_id, mr_iid, config):
    """Get diff for a GitLab MR"""
    token = config.get("gitlab", {}).get("token")
    if not token:
        return None
    
    try:
        url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}/diffs"
        r = requests.get(url, headers={'PRIVATE-TOKEN': token.strip()}, timeout=10)
        r.raise_for_status()
        
        diffs = r.json()
        diff_text = ""
        for diff in diffs:
            diff_text += f"diff --git a/{diff.get('old_path')} b/{diff.get('new_path')}\n"
            diff_text += diff.get('diff', '')
            diff_text += "\n"
        return diff_text
    except:
        return None

def get_github_pr_diff(repo, pr_number):
    """Get diff for a GitHub PR"""
    try:
        result = subprocess.run([
            "gh", "pr", "diff",
            "--repo", repo,
            str(pr_number)
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            return result.stdout
    except:
        pass
    
    return None

def post_gitlab_review(project_id, mr_iid, review_text, config):
    """Post review comment to GitLab MR"""
    token = config.get("gitlab", {}).get("token")
    if not token:
        return False
    
    try:
        url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
        r = requests.post(url, headers={'PRIVATE-TOKEN': token.strip()},
                         json={'body': review_text}, timeout=10)
        r.raise_for_status()
        return True
    except:
        return False

def post_github_review(repo, pr_number, review_text):
    """Post review comment to GitHub PR"""
    try:
        subprocess.run([
            "gh", "pr", "comment",
            "--repo", repo,
            str(pr_number),
            "--body", review_text
        ], capture_output=True, timeout=10)
        return True
    except:
        return False

def review_with_ollama(diff, title, model_name, mode, timeout):
    """Send code to Ollama for review"""
    if not diff:
        return None
    
    if mode == "json":
        return review_json(diff, title, model_name, timeout)
    else:
        return review_text(diff, title, model_name, timeout)

def review_json(diff, title, model_name, timeout):
    """Review with JSON output"""
    prompt = (f"""You are a code reviewer. Review this code change and respond ONLY with valid JSON.

Title: {title}

Diff:
{diff}

IMPORTANT: Respond ONLY with this exact JSON format, no other text:
{{
  "summary": "1-2 sentence summary",
  "verdict": "APPROVED / NEEDS REVIEW / NEEDS CHANGES",
  "comments": ["comment 1", "comment 2"]
}}

JSON Response:""")
    
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2}
    }
    
    try:
        print(f"  Sending to Ollama ({model_name}, JSON mode)...")
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        raw = raw.replace("```json\n", "").replace("\n```", "").replace("```", "")
        return json.loads(raw)
    except requests.exceptions.RequestException as e:
        print(f"  Ollama request failed: {e}")
        return None
    except json.JSONDecodeError:
        print(f"  JSON parsing failed, trying TEXT mode...")
        return review_text(diff, title, model_name, timeout)

def review_text(diff, title, model_name, timeout):
    """Review with text output"""
    prompt = (f"""You are a code reviewer. Review this code change and provide feedback.

Title: {title}

Diff:
{diff}

Provide a review with:
1. Brief summary of changes
2. Verdict: APPROVED / NEEDS REVIEW / NEEDS CHANGES
3. Specific comments or suggestions (if any)

Keep it concise and actionable.""")
    
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2}
    }
    
    try:
        print(f"  Sending to Ollama ({model_name}, TEXT mode)...")
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        response_text = r.json().get("response", "").strip()
        
        # Parse text into structured format
        lines = response_text.split('\n')
        summary = ""
        verdict = "NEEDS REVIEW"
        comments = []
        
        for line in lines:
            line_lower = line.lower()
            if not summary and ('summary' in line_lower or 'change' in line_lower):
                summary = line
            elif 'approved' in line_lower or 'good' in line_lower:
                verdict = "APPROVED"
            elif 'need' in line_lower or 'issue' in line_lower:
                verdict = "NEEDS CHANGES"
            if line.strip().startswith('-') or line.strip().startswith('*'):
                comments.append(line.strip())
        
        if not summary:
            summary = lines[0] if lines else "Code review complete"
        
        return {
            'summary': summary[:200],
            'verdict': verdict,
            'comments': comments[:5]
        }
    except requests.exceptions.RequestException as e:
        print(f"  Ollama request failed: {e}")
        return None

def review_item(item, config, model_name, mode, timeout):
    """Review a single PR or MR"""
    platform = item['platform']
    
    if platform == 'gitlab':
        review_gitlab_mr(item, config, model_name, mode, timeout)
    else:
        review_github_pr(item, config, model_name, mode, timeout)

def review_gitlab_mr(mr, config, model_name, mode, timeout):
    """Review GitLab MR"""
    print(f"\n📋 GitLab MR !{mr['iid']}: {mr['title']}")
    
    diff = get_gitlab_mr_diff(mr['project_id'], mr['iid'], config)
    if not diff:
        print(f"  ❌ Failed to fetch diff")
        return
    
    print(f"  📄 Diff size: {len(diff)} characters")
    
    review = review_with_ollama(diff, mr['title'], model_name, mode, timeout)
    
    if review:
        verdict = review.get("verdict", "NEEDS REVIEW")
        summary = review.get('summary', '')
        comments = review.get('comments', [])
        
        print(f"  ✅ Verdict: {verdict}")
        
        note = f"## 🤖 Sergio Ramos Review\n\n**{verdict}**\n\n{summary}\n"
        if comments:
            note += "\n### Comments:\n"
            for comment in comments:
                note += f"- {comment}\n"
        
        if post_gitlab_review(mr['project_id'], mr['iid'], note, config):
            print(f"  ✅ Review posted")
        else:
            print(f"  ⚠️  Failed to post review")
    else:
        print(f"  ❌ Review failed")

def review_github_pr(pr, config, model_name, mode, timeout):
    """Review GitHub PR"""
    print(f"\n📋 GitHub PR #{pr['number']}: {pr['title']}")
    print(f"   Repository: {pr['repo']}")
    
    diff = get_github_pr_diff(pr['repo'], pr['number'])
    if not diff:
        print(f"  ❌ Failed to fetch diff")
        return
    
    print(f"  📄 Diff size: {len(diff)} characters")
    
    review = review_with_ollama(diff, pr['title'], model_name, mode, timeout)
    
    if review:
        verdict = review.get("verdict", "NEEDS REVIEW")
        summary = review.get('summary', '')
        comments = review.get('comments', [])
        
        print(f"  ✅ Verdict: {verdict}")
        
        note = f"## 🤖 Sergio Ramos Review\n\n**{verdict}**\n\n{summary}\n"
        if comments:
            note += "\n### Comments:\n"
            for comment in comments:
                note += f"- {comment}\n"
        
        if post_github_review(pr['repo'], pr['number'], note):
            print(f"  ✅ Review posted")
        else:
            print(f"  ⚠️  Failed to post review")
    else:
        print(f"  ❌ Review failed")

def main():
    """Main: Find and review all PRs/MRs where sergio is reviewer"""
    review_github, review_gitlab = parse_arguments()
    
    # Check Ollama
    if not check_ollama_health():
        print("Cannot start Ollama. Exiting.")
        sys.exit(1)
    
    print()  # Blank line
    
    # Select model
    model_name, timeout = select_model()
    
    # Auto-detect mode (json vs text)
    mode = detect_model_mode(model_name, timeout)
    
    # Load config
    config = load_config()
    
    # Find ALL PRs/MRs where sergio is reviewer
    print("\n" + "="*60)
    print("🔍 Finding PRs/MRs where sergio is reviewer...")
    print("="*60)
    
    github_prs = get_github_prs_with_sergio(config) if review_github else []
    gitlab_mrs = get_gitlab_mrs_with_sergio(config) if review_gitlab else []
    
    if review_github:
        print(f"🐙 GitHub: {len(github_prs)} PR(s) found")
    if review_gitlab:
        print(f"🦊 GitLab: {len(gitlab_mrs)} MR(s) found")
    
    all_items = github_prs + gitlab_mrs
    
    if not all_items:
        print("\n✅ No items to review!")
        return
    
    print(f"\n🚀 Reviewing {len(all_items)} item(s) with {model_name}...\n")
    
    # Review all
    for item in all_items:
        review_item(item, config, model_name, mode, timeout)
    
    print("\n" + "="*60)
    print("✅ All reviews complete!")
    print("="*60)

if __name__ == '__main__':
    main()
