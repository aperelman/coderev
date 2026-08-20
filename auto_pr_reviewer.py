#!/usr/bin/env python3
"""Auto PR/MR reviewer using Ollama + Sergio bot - REVIEWS ALL OPEN MRs WITH MODEL SELECTION"""
import re
import requests
import json
import yaml
import sys
import time
import subprocess
from urllib.parse import quote

# Default Ollama URL
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

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

def load_config():
    """Load configuration from config.yml."""
    try:
        with open('config.yml', 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print("config.yml not found.")
        sys.exit(1)

def gl_headers(config):
    """Build GitLab API headers with authentication token."""
    return {'Private-Token': config["gitlab"]["token"].strip()}

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
    """Wait for Ollama to be ready (max 30 seconds)"""
    for i in range(timeout):
        if is_ollama_running():
            print("✅ Ollama ready!")
            return True
        print(f"⏳ Waiting for Ollama... ({i+1}/{timeout}s)", end='\r')
        time.sleep(1)
    print("\n❌ Ollama timeout - giving up")
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
    """List available models in Ollama"""
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
        r.raise_for_status()
        models = r.json().get('models', [])
        return [m['name'] for m in models]
    except Exception as e:
        print(f"Failed to list models: {e}")
        return []

def pull_model(model_name):
    """Pull a model from registry if not available"""
    print(f"📥 Pulling model {model_name}...")
    try:
        r = requests.post(
            "http://127.0.0.1:11434/api/pull",
            json={"name": model_name},
            timeout=600  # 10 minutes for download
        )
        r.raise_for_status()
        print(f"✅ Model {model_name} pulled successfully!")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to pull model: {e}")
        return False

def select_model():
    """Interactive model selection menu"""
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
        choice = '3'  # Default to qwen2.5-coder
    
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
    
    print(f"\n✅ Using model: {model_name}")
    return model_name, model_info['timeout']

def get_open_mrs(config):
    """Fetch open (non-draft) merge requests from GitLab."""
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests'
    try:
        r = requests.get(
            url,
            headers=gl_headers(config),
            params={'state': 'opened'},
            timeout=10
        )
        r.raise_for_status()
        mrs = r.json()
        return [
            m for m in mrs
            if not m['title'].startswith('Draft:')
            and not m['title'].startswith('[WIP]')
        ]
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MRs: {e}")
        return []

def get_mr_details(mr_iid, config):
    """Fetch detailed information about a specific MR."""
    pid = config["gitlab"]["project_id"]
    url = f'https://gitlab.com/api/v4/projects/{pid}/merge_requests/{mr_iid}'
    try:
        r = requests.get(url, headers=gl_headers(config), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MR details: {e}")
        return None

def get_mr_changes(mr_iid, config):
    """Fetch the diff/changes for a specific MR."""
    pid = config["gitlab"]["project_id"]
    url = (f'https://gitlab.com/api/v4/projects/{pid}/'
           f'merge_requests/{mr_iid}/changes')
    try:
        r = requests.get(url, headers=gl_headers(config), timeout=10)
        r.raise_for_status()
        return r.json().get('changes', [])
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch MR changes: {e}")
        return []

def post_note(mr_iid, body, config):
    """Post a general note/comment on the MR."""
    pid = config["gitlab"]["project_id"]
    url = (f'https://gitlab.com/api/v4/projects/{pid}/'
           f'merge_requests/{mr_iid}/notes')
    try:
        r = requests.post(
            url,
            headers=gl_headers(config),
            json={'body': body},
            timeout=10
        )
        r.raise_for_status()
        print(f"✅ Review posted on MR !{mr_iid}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to post note: {e}")

def ollama_review(prompt_context, mr_title, model_name, timeout):
    """Send code review request to Ollama and parse JSON response."""
    prompt = (f"Review this code change:\n\n"
              f"MR Title: {mr_title}\n\n"
              f"{prompt_context}\n\n"
              f"Respond with JSON: {{'summary': '...', 'verdict': '...', 'comments': [...]}}")
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        print(f"  Sending to Ollama ({model_name})...")
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        raw = raw.replace("```json\n", "").replace("\n```", "").replace("```", "")
        return json.loads(raw)
    except requests.exceptions.RequestException as e:
        print(f"  Ollama request failed: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"  Failed to parse Sergio's JSON: {e}")
        return None

def review_mr(mr, config, model_name, timeout):
    """Review a single MR"""
    mr_iid = mr['iid']
    print(f"\n📋 Reviewing MR !{mr_iid}: {mr['title']}")
    
    # Get details
    mr_details = get_mr_details(mr_iid, config)
    if not mr_details:
        print(f"  ❌ Failed to fetch MR details")
        return
    
    # Get changes
    changes = get_mr_changes(mr_iid, config)
    if not changes:
        print(f"  ⚠️  No changes found")
        return
    
    print(f"  📁 Changed files: {len(changes)}")
    
    # Build context
    prompt_context = f"Files changed: {len(changes)}\n"
    for change in changes:
        path = change.get('new_path', change.get('old_path', 'unknown'))
        diff = change.get('diff', '')[:500]  # First 500 chars
        prompt_context += f"\n### {path}\n{diff}..."
    
    # Review
    review = ollama_review(prompt_context, mr['title'], model_name, timeout)
    
    if review:
        verdict = review.get("verdict", "")
        summary = review.get('summary', '')
        comments = review.get('comments', [])
        
        print(f"  Verdict: {verdict}")
        
        # Build note
        note = f"## 🤖 Sergio Ramos Review\n\n**{verdict}**\n\n{summary}\n"
        if comments:
            note += "\n### Comments:\n"
            for comment in comments:
                note += f"- {comment}\n"
        
        post_note(mr_iid, note, config)
    else:
        print(f"  ❌ Review failed")

def main():
    """Review ALL open MRs with model selection"""
    # Check/start Ollama first
    if not check_ollama_health():
        print("Cannot start Ollama. Exiting.")
        sys.exit(1)
    
    print()  # Blank line for readability
    
    # Select model
    model_name, timeout = select_model()
    
    config = load_config()
    
    mrs = get_open_mrs(config)
    if not mrs:
        print("No open (non-draft) MRs found.")
        sys.exit(0)
    
    print(f"\n🚀 Found {len(mrs)} open MR(s). Reviewing all with {model_name}...\n")
    
    # Review EACH MR
    for mr in mrs:
        review_mr(mr, config, model_name, timeout)
    
    print("\n✅ All reviews complete!")

if __name__ == '__main__':
    main()
