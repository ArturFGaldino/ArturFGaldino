import os
import re
import json
import requests

USER_NAME = os.getenv('USER_NAME', 'ArturFGaldino')
TOKEN = os.getenv('ACCESS_TOKEN') or os.getenv('GITHUB_TOKEN')

HEADERS = {'Authorization': f'token {TOKEN}'} if TOKEN else {'User-Agent': 'Python-Stats-Script'}
GRAPHQL_URL = 'https://api.github.com/graphql'

def graphql_query(query, variables=None):
    if not TOKEN:
        return None
    try:
        res = requests.post(GRAPHQL_URL, json={'query': query, 'variables': variables or {}}, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"GraphQL returned status {res.status_code}")
            return None
        data = res.json()
        if 'errors' in data:
            print(f"GraphQL errors: {data['errors']}")
            return None
        return data.get('data')
    except Exception as e:
        print(f"GraphQL request exception: {e}")
        return None

def get_stats_via_rest(username):
    user_res = requests.get(f'https://api.github.com/users/{username}', headers=HEADERS, timeout=15)
    if user_res.status_code != 200:
        raise Exception(f"Failed to fetch user data for {username}: {user_res.status_code}")
    user_data = user_res.json()
    
    followers = user_data.get('followers', 0)
    public_repos = user_data.get('public_repos', 0)
    
    repos_res = requests.get(f'https://api.github.com/users/{username}/repos?per_page=100', headers=HEADERS, timeout=15)
    stars = 0
    if repos_res.status_code == 200:
        repos_data = repos_res.json()
        stars = sum(r.get('stargazers_count', 0) for r in repos_data if isinstance(r, dict))
        
    commits_res = requests.get(
        f'https://api.github.com/search/commits?q=author:{username}',
        headers={**HEADERS, 'Accept': 'application/vnd.github.cloak-preview+json'},
        timeout=15
    )
    commits = 0
    if commits_res.status_code == 200:
        commits = commits_res.json().get('total_count', 0)
        
    return public_repos, public_repos, stars, commits, followers

def get_user_info(username):
    query = '''
    query($login: String!) {
        user(login: $login) {
            id
            followers {
                totalCount
            }
            contributionsCollection {
                totalCommitContributions
                restrictedContributionsCount
            }
        }
    }
    '''
    data = graphql_query(query, {'login': username})
    if not data or not data.get('user'):
        return None, 0, 0
    user = data['user']
    followers = user['followers']['totalCount']
    contribs = user['contributionsCollection']
    commits = contribs['totalCommitContributions'] + contribs.get('restrictedContributionsCount', 0)
    user_id = user['id']
    return user_id, followers, commits

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
            return 0, 0, 0, []
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
        if not repos['pageInfo']['hasNextPage']:
            break
        cursor = repos['pageInfo']['endCursor']
    
    return owned_count, all_count, stars_count, all_repos

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
        print(f"[{idx}/{total_repos}] Checking repo: {repo_name_with_owner}...")
        owner, name = repo_name_with_owner.split('/')
        try:
            cursor = None
            repo_adds = 0
            repo_dels = 0
            repo_commits = 0
            total_history_count = 0
            page_count = 0
            max_pages = 10  # Cap at 1000 commits per repo for speed
            
            while page_count < max_pages:
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
                    print(f"   -> Used cache for {repo_name_with_owner} ({total_history_count} commits)")
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
                page_count += 1
                
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
            print(f"Skipping repo {repo_name_with_owner}: {e}")
            if repo_name_with_owner in loc_cache:
                cached_data = loc_cache[repo_name_with_owner]
                total_additions += cached_data.get('adds', 0)
                total_deletions += cached_data.get('dels', 0)
                total_commits += cached_data.get('commits', 0)
                new_cache[repo_name_with_owner] = cached_data

    if new_cache:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(new_cache, f, indent=2)
        
    net_loc = total_additions - total_deletions
    return total_commits, total_additions, total_deletions, net_loc

def update_readme(owned_count, contrib_count, stars_count, commits_count, followers_count, net_loc, additions, deletions):
    readme_path = 'README.md'
    with open(readme_path, 'r', encoding='utf-8') as f:
        content = f.read()

    stats_block = (
        f"<!-- START_SECTION:github_stats -->\n"
        f"- **Repos:** {owned_count:,} {{Contributed: {contrib_count:,}}} | **Stars:** {stars_count:,}\n"
        f"- **Commits:** {commits_count:,} | **Followers:** {followers_count:,}\n"
        f"- **Lines of Code on GitHub:** {net_loc:,} ({additions:,}++, {deletions:,}--)\n"
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
    print(f"Updating GitHub Stats for {USER_NAME}...")
    
    user_id, followers, commit_contribs = get_user_info(USER_NAME)
    
    if TOKEN and user_id:
        print("Authenticated mode (GraphQL): Fetching complete stats across all repos...")
        owned, contrib, stars, all_repos = get_repos_and_stars(USER_NAME)
        loc_commits, additions, deletions, net_loc = get_total_loc(USER_NAME, user_id, all_repos)
        total_commits = max(commit_contribs, loc_commits)
    else:
        print("Unauthenticated / Local mode (REST API): Fetching public stats...")
        owned, contrib, stars, total_commits, followers = get_stats_via_rest(USER_NAME)
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

    print(f"Repos: {owned} (Contrib: {contrib}), Stars: {stars}")
    print(f"Commits: {total_commits}, Followers: {followers}")
    print(f"LOC Net: {net_loc} (+{additions}, -{deletions})")
    
    update_readme(owned, contrib, stars, total_commits, followers, net_loc, additions, deletions)
    print("README.md updated successfully!")