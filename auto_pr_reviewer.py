import requests
import json
import yaml

def load_config():
    with open('config.yml', 'r') as file:
        config = yaml.safe_load(file)
    return config

def get_pr_files(pr_number, config):
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
    reviewers = []
    # Example criteria: suggest reviewers for files with specific extensions
    for file in pr_files:
        if file['filename'].endswith('.py'):
            reviewers.append('python_reviewer')
        elif file['filename'].endswith('.js'):
            reviewers.append('js_reviewer')
        # Add more criteria as needed
    return reviewers

def create_pr_review_request(pr_number, reviewers, config):
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

def main(pr_number):
    config = load_config()
    pr_files = get_pr_files(pr_number, config)
    if pr_files:
        reviewers = suggest_reviewers(pr_files)
        if reviewers:
            create_pr_review_request(pr_number, reviewers, config)

if __name__ == '__main__':
    pr_number = input("Enter the PR number: ")
    main(pr_number)
