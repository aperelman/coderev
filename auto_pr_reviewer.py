import requests
import json

# GitHub API token
GITHUB_TOKEN = 'your_github_token_here'
# GitHub repository details
OWNER = 'your_repo_owner'
REPO = 'your_repo_name'

def get_pr_files(pr_number):
    url = f'https://api.github.com/repos/{OWNER}/{REPO}/pulls/{pr_number}/files'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
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

def create_pr_review_request(pr_number, reviewers):
    url = f'https://api.github.com/repos/{OWNER}/{REPO}/pulls/{pr_number}/reviews'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
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
    pr_files = get_pr_files(pr_number)
    if pr_files:
        reviewers = suggest_reviewers(pr_files)
        if reviewers:
            create_pr_review_request(pr_number, reviewers)

if __name__ == '__main__':
    pr_number = input("Enter the PR number: ")
    main(pr_number)
