import os
import re
import json
import requests
import sys
from datetime import datetime, timezone
from collections import defaultdict

USER_NAME = os.getenv('USER_NAME', 'ArturFGaldino')
TOKEN = os.getenv('ACCESS_TOKEN') or os.getenv('GITHUB_TOKEN')

HEADERS = {'Authorization': f'token {TOKEN}'} if TOKEN else {'User-Agent': 'Python-Stats-Script'}
GRAPHQL_URL = 'https://api.github.com/graphql'

def log(msg):
    print(msg, flush=True)

def graphql_query(query, variables=None):
    if not TOKEN:
        return None
    try:
        res = requests.post(GRAPHQL_URL, json={'query': query, 'variables': variables or {}}, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            log(f"GraphQL returned status {res.status_code}")
            return None
        data = res.json()
        if 'errors' in data:
            log(f"GraphQL errors: {data['errors']}")
            return None
        return data.get('data')
    except Exception as e:
        log(f"GraphQL request exception: {e}")
        return None

def calculate_age(created_at_str):
    try:
        created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        diff_days = (now - created_at).days
        years = diff_days // 365
        months = (diff_days % 365) // 30
        
        parts = []
        if years > 0:
            parts.append(f"{years} yr{'s' if years > 1 else ''}")
        if months > 0 or years == 0:
            parts.append(f"{months} mo{'s' if months > 1 else ''}")
        return ", ".join(parts)
    except Exception:
        return "N/A"

def format_languages(lang_bytes_dict):
    total_bytes = sum(lang_bytes_dict.values())
    if total_bytes == 0:
        return "N/A"
    
    sorted_langs = sorted(lang_bytes_dict.items(), key=lambda x: x[1], reverse=True)
    top_langs = sorted_langs[:5]
    
    formatted = []
    for lang, bytes_count in top_langs:
        pct = (bytes_count / total_bytes) * 100
        if pct >= 1.0:
            formatted.append(f"{lang} {pct:.1f}%")
            
    return " | ".join(formatted) if formatted else "N/A"

def get_stats_via_rest(username):
    user_res = requests.get(f'https://api.github.com/users/{username}', headers=HEADERS, timeout=15)
    if user_res.status_code != 200:
        raise Exception(f"Failed to fetch user data for {username}: {user_res.status_code}")
    user_data = user_res.json()
    
    followers = user_data.get('followers', 0)
    public_repos = user_data.get('public_repos', 0)
    created_at = user_data.get('created_at', '')
    member_since = calculate_age(created_at) if created_at else "N/A"
    
    repos_res = requests.get(f'https://api.github.com/users/{username}/repos?per_page=100', headers=HEADERS, timeout=15)
    stars = 0
    lang_bytes = defaultdict(int)
    if repos_res.status_code == 200:
        repos_data = repos_res.json()
        for r in repos_data:
            if isinstance(r, dict):
                stars += r.get('stargazers_count', 0)
                lang = r.get('language')
                if lang:
                    lang_bytes[lang] += r.get('size', 1) * 1024
        
    commits_res = requests.get(
        f'https://api.github.com/search/commits?q=author:{username}',
        headers={**HEADERS, 'Accept': 'application/vnd.github.cloak-preview+json'},
        timeout=15
    )
    commits = commits_res.json().get('total_count', 0) if commits_res.status_code == 200 else 0
        
    prs_res = requests.get(
        f'https://api.github.com/search/issues?q=author:{username}+type:pr+is:merged',
        headers=HEADERS,
        timeout=15
    )
    merged_prs = prs_res.json().get('total_count', 0) if prs_res.status_code == 200 else 0

    open_issues_res = requests.get(
        f'https://api.github.com/search/issues?q=author:{username}+type:issue+is:open',
        headers=HEADERS,
        timeout=15
    )
    open_issues = open_issues_res.json().get('total_count', 0) if open_issues_res.status_code == 200 else 0

    closed_issues_res = requests.get(
        f'https://api.github.com/search/issues?q=author:{username}+type:issue+is:closed',
        headers=HEADERS,
        timeout=15
    )
    closed_issues = closed_issues_res.json().get('total_count', 0) if closed_issues_res.status_code == 200 else 0

    top_langs_str = format_languages(lang_bytes)

    return public_repos, public_repos, stars, commits, followers, merged_prs, 0, open_issues, closed_issues, top_langs_str, member_since

def get_user_info(username):
    query = '''
    query($login: String!) {
        user(login: $login) {
            id
            createdAt
            followers {
                totalCount
            }
            pullRequests(states: MERGED) {
                totalCount
            }
            openIssues: issues(states: OPEN) {
                totalCount
            }
            closedIssues: issues(states: CLOSED) {
                totalCount
            }
            contributionsCollection {
                totalCommitContributions
                restrictedContributionsCount
                totalPullRequestReviewContributions
            }
        }
    }
    '''
    data = graphql_query(query, {'login': username})
    if not data or not data.get('user'):
        return None, 0, 0, 0, 0, 0, 0, "N/A"
    user = data['user']
    followers = user['followers']['totalCount']
    contribs = user['contributionsCollection']
    commits = contribs['totalCommitContributions'] + contribs.get('restrictedContributionsCount', 0)
    user_id = user['id']
    merged_prs = user.get('pullRequests', {}).get('totalCount', 0)
    pr_reviews = contribs.get('totalPullRequestReviewContributions', 0)
    open_issues = user.get('openIssues', {}).get('totalCount', 0)
    closed_issues = user.get('closedIssues', {}).get('totalCount', 0)
    created_at = user.get('createdAt', '')
    member_since = calculate_age(created_at) if created_at else "N/A"
    
    return user_id, followers, commits, merged_prs, pr_reviews, open_issues, closed_issues, member_since

def get_repos_and_stars(username):
    owned_query = '''
    query($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
                totalCount
                edges {
                    node {
                        nameWithOwner
                        stargazers {
                            totalCount
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }
    '''
    all_repos_query = '''
    query($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
                totalCount
                edges {
                    node {
                        nameWithOwner
                        stargazers {
                            totalCount
                        }
                        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
                            edges {
                                size
                                node {
                                    name
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }
    '''
    
    owned_count = 0
    stars_count = 0
    cursor = None
    while True:
        data = graphql_query(owned_query, {'login': username, 'cursor': cursor})
        if not data or not data.get('user'):
            return 0, 0, 0, [], "N/A"
        repos = data['user']['repositories']
        owned_count = repos['totalCount']
        for edge in repos['edges']:
            node = edge['node']
            stars_count += node['stargazers']['totalCount']
        if not repos['pageInfo']['hasNextPage']:
            break
        cursor = repos['pageInfo']['endCursor']
        
    all_count = 0
    all_repos = []
    lang_bytes = defaultdict(int)
    cursor = None
    while True:
        data = graphql_query(all_repos_query, {'login': username, 'cursor': cursor})
        if not data or not data.get('user'):
            break
        repos = data['user']['repositories']
        all_count = repos['totalCount']
        for edge in repos['edges']:
            node = edge['node']
            all_repos.append(node['nameWithOwner'])
            for lang_edge in node.get('languages', {}).get('edges', []):
                lang_name = lang_edge['node']['name']
                size = lang_edge['size']
                lang_bytes[lang_name] += size
        if not repos['pageInfo']['hasNextPage']:
            break
        cursor = repos['pageInfo']['endCursor']
    
    top_langs_str = format_languages(lang_bytes)
    return owned_count, all_count, stars_count, all_repos, top_langs_str

def get_total_loc(username, user_id, repos_list):
    os.makedirs('cache', exist_ok=True)
    cache_file = 'cache/loc_cache.json'
    loc_cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                loc_cache = json.load(f)
        except Exception:
            loc_cache = {}

    loc_query = '''
    query($owner: String!, $name: String!, $cursor: String) {
        repository(owner: $owner, name: $name) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    additions
                                    deletions
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }
    '''
    
    total_additions = 0
    total_deletions = 0
    total_commits = 0
    
    new_cache = {}
    total_repos = len(repos_list)
    
    for idx, repo_name_with_owner in enumerate(repos_list, start=1):
        log(f"[{idx}/{total_repos}] Checking repo: {repo_name_with_owner}...")
        owner, name = repo_name_with_owner.split('/')
        try:
            cursor = None
            repo_adds = 0
            repo_dels = 0
            repo_commits = 0
            total_history_count = 0
            
            while True:
                data = graphql_query(loc_query, {'owner': owner, 'name': name, 'cursor': cursor})
                if not data:
                    break
                repo_data = data.get('repository')
                if not repo_data:
                    break
                ref = repo_data.get('defaultBranchRef')
                if not ref or not ref.get('target'):
                    break
                history = ref['target']['history']
                total_history_count = history['totalCount']
                
                # Use cache if commit total hasn't changed
                if repo_name_with_owner in loc_cache and loc_cache[repo_name_with_owner].get('total_count') == total_history_count:
                    cached_data = loc_cache[repo_name_with_owner]
                    repo_adds = cached_data.get('adds', 0)
                    repo_dels = cached_data.get('dels', 0)
                    repo_commits = cached_data.get('commits', 0)
                    log(f"   -> Used cache for {repo_name_with_owner} ({total_history_count} commits)")
                    break
                
                for edge in history.get('edges', []):
                    node = edge['node']
                    author_user = node.get('author', {}).get('user')
                    if author_user and author_user.get('id') == user_id:
                        repo_adds += node.get('additions', 0)
                        repo_dels += node.get('deletions', 0)
                        repo_commits += 1
                
                if not history['pageInfo']['hasNextPage']:
                    break
                cursor = history['pageInfo']['endCursor']
                
            new_cache[repo_name_with_owner] = {
                'total_count': total_history_count,
                'adds': repo_adds,
                'dels': repo_dels,
                'commits': repo_commits
            }
            
            total_additions += repo_adds
            total_deletions += repo_dels
            total_commits += repo_commits
        except Exception as e:
            log(f"Skipping repo {repo_name_with_owner}: {e}")
            if repo_name_with_owner in loc_cache:
                cached_data = loc_cache[repo_name_with_owner]
                total_additions += cached_data.get('adds', 0)
                total_deletions += cached_data.get('dels', 0)
                total_commits += cached_data.get('commits', 0)
                new_cache[repo_name_with_owner] = cached_data

    if new_cache:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(new_cache, f, indent=2)
            
    # Count only repos where user has at least 1 commit
    actual_contributed_repos = sum(1 for item in new_cache.values() if item.get('commits', 0) > 0)
        
    net_loc = total_additions - total_deletions
    return total_commits, total_additions, total_deletions, net_loc, actual_contributed_repos

def update_readme(owned_count, contrib_count, stars_count, commits_count, followers_count, merged_prs, pr_reviews, open_issues, closed_issues, top_langs, member_since, net_loc, additions, deletions):
    readme_path = 'README.md'
    with open(readme_path, 'r', encoding='utf-8') as f:
        content = f.read()

    stats_block = (
        f"<!-- START_SECTION:github_stats -->\n"
        f"- **Repos:** {owned_count:,} {{Contributed: {contrib_count:,}}} | **Stars:** {stars_count:,} | **Followers:** {followers_count:,}\n"
        f"- **Commits:** {commits_count:,} | **Merged PRs:** {merged_prs:,} | **Code Reviews:** {pr_reviews:,}\n"
        f"- **Issues:** {open_issues:,} Open | {closed_issues:,} Closed\n"
        f"- **Top Languages:** {top_langs}\n"
        f"- **Lines of Code on GitHub:** {net_loc:,} ({additions:,}++, {deletions:,}--)\n"
        f"- **Member since:** {member_since}\n"
        f"<!-- END_SECTION:github_stats -->"
    )

    pattern = r"<!-- START_SECTION:github_stats -->.*?<!-- END_SECTION:github_stats -->"
    if re.search(pattern, content, flags=re.DOTALL):
        new_content = re.sub(pattern, stats_block, content, flags=re.DOTALL)
    else:
        stats_header_pattern = r"## 💻 GitHub Stats[^\n]*\n.*?(?=\n<br/>|\n## |\Z)"
        if re.search(stats_header_pattern, content, flags=re.DOTALL):
            new_content = re.sub(stats_header_pattern, f"## 💻 GitHub Stats\n\n{stats_block}", content, flags=re.DOTALL)
        else:
            new_content = content.rstrip() + f"\n\n## 💻 GitHub Stats\n\n{stats_block}\n"

    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

if __name__ == '__main__':
    log(f"Updating GitHub Stats for {USER_NAME}...")
    
    user_id, followers, commit_contribs, merged_prs, pr_reviews, open_issues, closed_issues, member_since = get_user_info(USER_NAME)
    
    if TOKEN and user_id:
        log("Authenticated mode (GraphQL): Fetching complete stats across all repos...")
        owned, all_count, stars, all_repos, top_langs = get_repos_and_stars(USER_NAME)
        loc_commits, additions, deletions, net_loc, actual_contrib = get_total_loc(USER_NAME, user_id, all_repos)
        total_commits = max(commit_contribs, loc_commits)
        contrib = actual_contrib
    else:
        log("Unauthenticated / Local mode (REST API): Fetching public stats...")
        owned, contrib, stars, total_commits, followers, merged_prs, pr_reviews, open_issues, closed_issues, top_langs, member_since = get_stats_via_rest(USER_NAME)
        cache_file = 'cache/loc_cache.json'
        additions, deletions, net_loc = 0, 0, 0
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    for item in cache_data.values():
                        additions += item.get('adds', 0)
                        deletions += item.get('dels', 0)
                    net_loc = additions - deletions
            except Exception:
                pass

    log(f"Repos: {owned} (Contrib: {contrib}), Stars: {stars}, Followers: {followers}")
    log(f"Commits: {total_commits}, Merged PRs: {merged_prs}, Code Reviews: {pr_reviews}")
    log(f"Issues: {open_issues} Open | {closed_issues} Closed")
    log(f"Top Languages: {top_langs}")
    log(f"LOC Net: {net_loc} (+{additions}, -{deletions}), Member since: {member_since}")
    
    update_readme(owned, contrib, stars, total_commits, followers, merged_prs, pr_reviews, open_issues, closed_issues, top_langs, member_since, net_loc, additions, deletions)
    log("README.md updated successfully!")