import requests
import json
import traceback
from api.core_config import app_configs, gitlab_project_configs
from api.utils import parse_single_file_diff


def get_github_pr_changes(owner, repo_name, pull_number, access_token):
    """从 GitHub API 获取 Pull Request 的变更，并为每个文件解析成结构化数据"""
    if not access_token:
        print(f"Error: Access token is not configured for repository {owner}/{repo_name}.")
        return None

    current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
    files_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/pulls/{pull_number}/files"
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json"
    }

    structured_changes = {}

    try:
        print(f"Fetching PR files from: {files_url}")
        response = requests.get(files_url, headers=headers, timeout=60)
        response.raise_for_status()
        files_data = response.json()

        if not files_data:
            print(f"No files found in the Pull Request {pull_number} for {owner}/{repo_name}.")
            return {}

        print(f"Received {len(files_data)} file entries from API for PR {pull_number}.")

        for file_item in files_data:
            file_patch_text = file_item.get('patch')
            new_path = file_item.get('filename')
            old_path = file_item.get('previous_filename')
            status = file_item.get('status')

            if not file_patch_text and status != 'removed':
                print(
                    f"Warning: Skipping file item due to missing patch text for non-removed file. File: {new_path}, Status: {status}")
                continue

            if status == 'removed':
                if not file_patch_text:
                    file_changes_data = {
                        "path": new_path,
                        "old_path": None,
                        "changes": [{"type": "delete", "old_line": 0, "new_line": None, "content": "File removed"}],
                        "context": {"old": "", "new": ""},
                        "lines_changed": 0
                    }
                    structured_changes[new_path] = file_changes_data
                    print(f"Synthesized 'removed' status for {new_path}.")
                    continue

            print(f"Parsing diff for file: {new_path} (Old: {old_path if old_path else 'N/A'}, Status: {status})")
            try:
                # 使用通用的 parse_single_file_diff
                file_parsed_changes = parse_single_file_diff(file_patch_text, new_path, old_path)
                if file_parsed_changes and file_parsed_changes.get("changes"):
                    structured_changes[new_path] = file_parsed_changes
                    print(f"Successfully parsed {len(file_parsed_changes['changes'])} changes for {new_path}.")
                elif status == 'added' and not file_parsed_changes.get("changes"):
                    print(
                        f"File {new_path} is new but no changes parsed by diff parser. Content might be empty or not in hunk format.")
                else:
                    print(
                        f"No changes parsed from diff for {new_path} or file was removed without specific diff lines.")
            except Exception as parse_e:
                print(f"Error parsing diff for file {new_path}: {parse_e}")
                traceback.print_exc()

        if not structured_changes:
            print(f"No parseable changes found across all files for PR {pull_number} in {owner}/{repo_name}.")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from GitHub API ({files_url}): {e}")
        if 'response' in locals() and response is not None:
            print(f"Response status: {response.status_code}, Body: {response.text[:500]}...")
    except json.JSONDecodeError as json_e:
        print(f"Error decoding JSON response from GitHub API ({files_url}): {json_e}")
        if 'response' in locals() and response is not None:
            print(f"Response text: {response.text[:500]}...")
    except Exception as e:
        print(
            f"An unexpected error occurred while fetching/parsing diffs for PR {pull_number} in {owner}/{repo_name}: {e}")
        traceback.print_exc()

    return structured_changes


def get_gitlab_mr_changes(project_id, mr_iid, access_token):
    """从 GitLab API 获取 Merge Request 的变更，并为每个文件解析成结构化数据"""
    if not access_token:
        print(f"Error: Access token is not configured for project {project_id}.")
        return None, None

    project_config = gitlab_project_configs.get(str(project_id), {})
    project_specific_instance_url = project_config.get("instance_url")
    
    current_gitlab_instance_url = project_specific_instance_url or app_configs.get("GITLAB_INSTANCE_URL", "https://gitlab.com")
    if project_specific_instance_url:
        print(f"Using project-specific GitLab instance URL for project {project_id}: {project_specific_instance_url}")
    else:
        print(f"Using global GitLab instance URL for project {project_id}: {current_gitlab_instance_url}")

    versions_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions"
    headers = {"PRIVATE-TOKEN": access_token}
    structured_changes = {}
    position_info = None

    try:
        print(f"Fetching MR versions from: {versions_url}")
        response = requests.get(versions_url, headers=headers, timeout=60)
        response.raise_for_status()
        versions_data = response.json()

        if versions_data:
            latest_version = versions_data[0]
            position_info = {
                "base_sha": latest_version.get("base_commit_sha"),
                "start_sha": latest_version.get("start_commit_sha"),
                "head_sha": latest_version.get("head_commit_sha"),
            }
            latest_version_id = latest_version.get("id")
            print(f"Extracted position info from latest version (ID: {latest_version_id}): {position_info}")

            # current_gitlab_instance_url is already defined above using project-specific or global config
            version_detail_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions/{latest_version_id}"
            print(f"Fetching details for version ID {latest_version_id} from: {version_detail_url}")
            version_detail_response = requests.get(version_detail_url, headers=headers, timeout=60)
            version_detail_response.raise_for_status()
            version_detail_data = version_detail_response.json()

            api_diffs = version_detail_data.get('diffs', [])
            print(f"Received {len(api_diffs)} file diffs from API for version ID {latest_version_id}.")

            for diff_item in api_diffs:
                file_diff_text = diff_item.get('diff')
                new_path = diff_item.get('new_path')
                old_path = diff_item.get('old_path')
                is_renamed = diff_item.get('renamed_file', False)

                if not file_diff_text or not new_path:
                    print(
                        f"Warning: Skipping diff item due to missing diff text or new_path. Item: {diff_item.get('new_path', 'N/A')}")
                    continue

                print(f"Parsing diff for file: {new_path} (Old: {old_path if is_renamed else 'N/A'})")
                try:
                    # 使用通用的 parse_single_file_diff
                    file_parsed_changes = parse_single_file_diff(file_diff_text, new_path,
                                                                 old_path if is_renamed else None)
                    if file_parsed_changes and file_parsed_changes.get("changes"):
                        structured_changes[new_path] = file_parsed_changes
                        print(f"Successfully parsed {len(file_parsed_changes['changes'])} changes for {new_path}.")
                    else:
                        print(f"No changes parsed from diff for {new_path}.")
                except Exception as parse_e:
                    print(f"Error parsing diff for file {new_path}: {parse_e}")
                    traceback.print_exc()

            if not structured_changes:
                print(f"No parseable changes found across all files for MR {mr_iid} in project {project_id}.")
        else:
            print(f"No versions found in the initial response from GitLab for MR {mr_iid} in project {project_id}.")

    except requests.exceptions.RequestException as e:
        request_url = locals().get('version_detail_url') or locals().get('versions_url', 'GitLab API')
        error_response = locals().get('version_detail_response') or locals().get('response')
        print(f"Error fetching data from {request_url}: {e}")
        if error_response is not None:
            print(f"Response status: {error_response.status_code}, Body: {error_response.text[:500]}...")
    except json.JSONDecodeError as json_e:
        request_url = locals().get('version_detail_url') or locals().get('versions_url', 'GitLab API')
        error_response = locals().get('version_detail_response') or locals().get('response')
        print(f"Error decoding JSON response from {request_url}: {json_e}")
        if error_response is not None:
            print(f"Response text: {error_response.text[:500]}...")
    except Exception as e:
        print(f"An unexpected error occurred while fetching/parsing diffs for MR {mr_iid} in project {project_id}: {e}")
        traceback.print_exc()

    return structured_changes, position_info


def add_github_pr_comment(owner, repo_name, pull_number, access_token, review, head_sha):
    """向 GitHub Pull Request 的特定行添加评论"""
    if not access_token:
        print("Error: Cannot add comment, access token is missing.")
        return False
    if not head_sha:
        print("Error: Cannot add comment, head_sha is missing.")
        return False

    current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
    comment_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/pulls/{pull_number}/comments"
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }

    body = f"""**AI Review [{review.get('severity', 'N/A').upper()}]**: {review.get('category', 'General')}

**分析**: {review.get('analysis', 'N/A')}

**建议**:
```suggestion
{review.get('suggestion', 'N/A')}
```
"""

    lines_info = review.get("lines", {})
    file_path = review.get("file")

    if not file_path:
        print("Warning: Skipping comment, review is missing 'file' path.")
        return False

    payload = {
        "body": body,
        "commit_id": head_sha,
        "path": file_path,
    }

    line_comment_possible = False
    if lines_info and lines_info.get("new") is not None:
        payload["line"] = lines_info["new"]
        line_comment_possible = True
        target_desc = f"file {file_path} line {lines_info['new']}"

    if not line_comment_possible:
        current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
        general_comment_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/issues/{pull_number}/comments"
        general_payload = {"body": f"**AI Review Comment (File: {file_path})**\n\n{body}"}
        target_desc = f"general PR comment for file {file_path}"
        current_url_to_use = general_comment_url
        current_payload_to_use = general_payload
        print(f"No specific new line for review on {file_path}. Posting as general PR comment.")
    else:
        current_url_to_use = comment_url
        current_payload_to_use = payload
        print(f"Attempting to add line comment to {target_desc}")

    try:
        response = requests.post(current_url_to_use, headers=headers, json=current_payload_to_use, timeout=30)
        response.raise_for_status()
        print(f"Successfully added comment to GitHub PR #{pull_number} ({target_desc})")
        return True
    except requests.exceptions.RequestException as e:
        error_message = f"Error adding GitHub comment ({target_desc}): {e}"
        if 'response' in locals() and response is not None:
            error_message += f" - Status: {response.status_code} - Body: {response.text[:500]}"
        print(error_message)

        if line_comment_possible and current_url_to_use == comment_url:
            print("Falling back to posting as a general PR comment due to specific line comment error.")
            current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
            general_comment_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/issues/{pull_number}/comments"
            fallback_payload = {"body": f"**(Comment originally for {target_desc})**\n\n{body}"}
            try:
                fallback_response = requests.post(general_comment_url, headers=headers, json=fallback_payload,
                                                  timeout=30)
                fallback_response.raise_for_status()
                print(f"Successfully added comment as general PR discussion after line comment failure.")
                return True
            except Exception as fallback_e:
                fb_error_message = f"Error adding fallback general GitHub comment: {fallback_e}"
                if 'fallback_response' in locals() and fallback_response is not None:
                    fb_error_message += f" - Status: {fallback_response.status_code} - Body: {fallback_response.text[:500]}"
                print(fb_error_message)
                return False
        return False
    except Exception as e:
        print(f"An unexpected error occurred while adding GitHub comment ({target_desc}): {e}")
        return False


def add_gitlab_mr_comment(project_id, mr_iid, access_token, review, position_info):
    """向 GitLab Merge Request 的特定行添加评论"""
    if not access_token:
        print("Error: Cannot add comment, access token is missing.")
        return False
    if not position_info or not position_info.get("head_sha") or not position_info.get(
            "base_sha") or not position_info.get("start_sha"):
        print(
            f"Error: Cannot add comment, essential position info (head_sha/base_sha/start_sha) is missing. Got: {position_info}")
        return False

    project_config = gitlab_project_configs.get(str(project_id), {})
    project_specific_instance_url = project_config.get("instance_url")

    current_gitlab_instance_url = project_specific_instance_url or app_configs.get("GITLAB_INSTANCE_URL", "https://gitlab.com")
    if project_specific_instance_url:
        print(f"Using project-specific GitLab instance URL for comments on project {project_id}: {project_specific_instance_url}")
    else:
        print(f"Using global GitLab instance URL for comments on project {project_id}: {current_gitlab_instance_url}")
    comment_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/discussions"
    headers = {"PRIVATE-TOKEN": access_token, "Content-Type": "application/json"}

    body = f"""**AI Review [{review.get('severity', 'N/A').upper()}]**: {review.get('category', 'General')}

**分析**: {review.get('analysis', 'N/A')}

**建议**:
```suggestion
{review.get('suggestion', 'N/A')}
```
"""
    position_data = {
        "base_sha": position_info.get("base_sha"),
        "start_sha": position_info.get("start_sha"),
        "head_sha": position_info.get("head_sha"),
        "position_type": "text",
    }

    lines_info = review.get("lines", {})
    file_path = review.get("file")
    old_file_path = review.get("old_path")

    if not file_path:
        print("Warning: Skipping comment, review is missing 'file' path.")
        return False

    line_comment_possible = False
    if lines_info and lines_info.get("new") is not None:
        position_data["new_path"] = file_path
        position_data["new_line"] = lines_info["new"]
        position_data["old_path"] = old_file_path if old_file_path else file_path
        line_comment_possible = True
        target_desc = f"file {file_path} line {lines_info['new']}"
    elif lines_info and lines_info.get("old") is not None:
        position_data["old_path"] = old_file_path if old_file_path else file_path
        position_data["old_line"] = lines_info["old"]
        position_data["new_path"] = file_path
        line_comment_possible = True
        target_desc = f"file {position_data['old_path']} old line {lines_info['old']}"
    else:
        target_desc = f"general discussion for file {file_path}"
        line_comment_possible = False

    if line_comment_possible:
        payload = {"body": body, "position": position_data}
        print(f"Attempting to add positioned comment to {target_desc}")
    else:
        payload = {"body": f"**AI Review Comment (File: {file_path})**\n\n{body}"}
        print(f"No specific line info in review for {file_path}. Posting as general MR discussion.")

    response_obj = None  # Define response_obj to ensure it's available in except block
    try:
        response_obj = requests.post(comment_url, headers=headers, json=payload, timeout=30)
        response_obj.raise_for_status()
        print(f"Successfully added comment to GitLab MR {mr_iid} ({target_desc})")
        return True
    except requests.exceptions.RequestException as e:
        error_message = f"Error adding GitLab comment ({target_desc}): {e}"
        if response_obj is not None:  # Check if response_obj was assigned
            error_message += f" - Status: {response_obj.status_code} - Body: {response_obj.text[:500]}"
        print(error_message)

        if line_comment_possible:
            print("Falling back to posting as a general comment due to position error.")
            fallback_payload = {"body": f"**(Comment originally for {target_desc})**\n\n{body}"}
            fallback_response_obj = None
            try:
                fallback_response_obj = requests.post(comment_url, headers=headers, json=fallback_payload, timeout=30)
                fallback_response_obj.raise_for_status()
                print(f"Successfully added comment as general discussion after position failure.")
                return True
            except Exception as fallback_e:
                fb_error_message = f"Error adding fallback general GitLab comment: {fallback_e}"
                if fallback_response_obj is not None:
                    fb_error_message += f" - Status: {fallback_response_obj.status_code} - Body: {fallback_response_obj.text[:500]}"
                print(fb_error_message)
                return False
        return False
    except Exception as e:
        print(f"An unexpected error occurred while adding GitLab comment ({target_desc}): {e}")
        return False
