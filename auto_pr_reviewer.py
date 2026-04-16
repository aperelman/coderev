import requests
import json
import yaml
import sys

def load_config():
    # Load configuration from config.yml
    with open('config.yml', 'r') as file:
        config = yaml.safe_load(file)
    return config

def get_pr_files(pr_number, config):
    # Fetch files associated with a pull request
    url = f'https://api.github.com/repos/{config["github"]["owner"]}/{config["github"]["repo"]}/pulls/{pr_number}/files'
    headers = {
        'Authorization': f'token {config["github"]["token"]}',
        'Accept': 'application/vnd.github.v3+json'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to fetch PR files: {response.status_code}")
        return []

def suggest_reviewers(pr_files):
    # Suggest reviewers based on the files changed
    reviewers = []
    for file in pr_files:
        if file['filename'].endswith('.py'):
            reviewers.append('python_reviewer')
        elif file['filename'].endswith('.js'):
            reviewers.append('js_reviewer')
        # Add more criteria as needed
    return reviewers

def create_pr_review_request(pr_number, reviewers, config):
    # Create a review request for the pull request
    url = f'https://api.github.com/repos/{config["github"]["owner"]}/{config["github"]["repo"]}/pulls/{pr_number}/reviews'
    headers = {
        'Authorization': f'token {config["github"]["token"]}',
        'Accept': 'application/vnd.github.v3+json'
    }
    payload = {
        'reviewers': reviewers
    }
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    if response.status_code == 201:
        print(f"Review request created for PR {pr_number}")
    else:
        print(f"Failed to create review request: {response.status_code}")

def save_context(pr_number, reviewers):
    # Save context to context.json
    with open('context.json', 'w') as file:
        json.dump({'pr_number': pr_number, 'reviewers': reviewers}, file)

def load_context():
    # Load context from context.json
    try:
        with open('context.json', 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return None

def main(pr_number):
    config = load_config()
    if not config:
        print("Failed to load configuration. Please check config.yml.")
        sys.exit(1)

    pr_files = get_pr_files(pr_number, config)
    if not pr_files:
        print(f"No files found for PR {pr_number}.")
        sys.exit(0)

    reviewers = suggest_reviewers(pr_files)
    if not reviewers:
        print("No suitable reviewers found.")
        sys.exit(0)

    create_pr_review_request(pr_number, reviewers, config)  # Pass config here
    save_context(pr_number, reviewers)

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python auto_pr_reviewer.py <pr_number>")
        sys.exit(1)
    pr_number = int(sys.argv[1])
    context = load_context()
    if context and context['pr_number'] == pr_number:
        reviewers = context['reviewers']
        create_pr_review_request(pr_number, reviewers, config)  # Pass config here
    else:
        main(pr_number)
