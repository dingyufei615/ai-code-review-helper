import requests
import json
import traceback
import logging
import base64
from api.core_config import app_configs, gitlab_project_configs
from api.utils import parse_single_file_diff

logger = logging.getLogger(__name__)


def get_github_pr_changes(owner, repo_name, pull_number, access_token):
    """从 GitHub API 获取 Pull Request 的变更，并为每个文件解析成结构化数据"""
    if not access_token:
        logger.error(f"错误: 仓库 {owner}/{repo_name} 未配置访问令牌。")
        return None

    current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
    files_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/pulls/{pull_number}/files"
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json"
    }

    structured_changes = {}

    try:
        logger.info(f"从以下地址获取 PR 文件: {files_url}")
        response = requests.get(files_url, headers=headers, timeout=60)
        response.raise_for_status()
        files_data = response.json()

        if not files_data:
            logger.info(f"在 {owner}/{repo_name} 的 Pull Request {pull_number} 中未找到文件。")
            return {}

        logger.info(f"从 API 收到 PR {pull_number} 的 {len(files_data)} 个文件条目。")

        for file_item in files_data:
            file_patch_text = file_item.get('patch')
            new_path = file_item.get('filename')
            old_path = file_item.get('previous_filename')
            status = file_item.get('status')

            if not file_patch_text and status != 'removed':
                logger.warning(
                    f"警告: 因非删除文件缺少补丁文本而跳过文件项。文件: {new_path}, 状态: {status}")
                continue

            if status == 'removed':
                if not file_patch_text:  # Usually removed files might not have a patch, or it's empty
                    file_changes_data = {
                        "path": new_path,  # new_path is the path of the removed file
                        "old_path": None,
                        # No old_path if it's just a removal, unless it was renamed then removed (complex case)
                        "changes": [{"type": "delete", "old_line": 0, "new_line": None, "content": "文件已删除"}],
                        "context": {"old": "", "new": ""},  # No context for a fully removed file via this path
                        "lines_changed": 0  # Or count lines if available from another source
                    }
                    structured_changes[new_path] = file_changes_data
                    logger.info(f"为 {new_path} 合成了 'removed' 状态。")
                    continue

            logger.info(f"解析文件 diff: {new_path} (旧路径: {old_path if old_path else 'N/A'}, 状态: {status})")
            try:
                # 使用通用的 parse_single_file_diff
                file_parsed_changes = parse_single_file_diff(file_patch_text, new_path, old_path)
                if file_parsed_changes and file_parsed_changes.get("changes"):
                    structured_changes[new_path] = file_parsed_changes
                    logger.info(f"成功解析 {new_path} 的 {len(file_parsed_changes['changes'])} 处变更。")
                elif status == 'added' and not file_parsed_changes.get("changes"):  # Empty new file
                    logger.info(
                        f"文件 {new_path} 是新文件但无变更内容被解析 (可能为空文件)。")
                elif status == 'removed' and not file_parsed_changes.get(
                        "changes"):  # File removed, patch might be empty
                    logger.info(f"文件 {new_path} 已删除，无具体 diff 行。")
                else:  # Other statuses or unexpected empty changes
                    logger.info(
                        f"未从 {new_path} 的 diff 中解析出变更。状态: {status}")
            except Exception as parse_e:
                logger.exception(f"解析文件 {new_path} 的 diff 时出错:")

        if not structured_changes:
            logger.info(f"在 {owner}/{repo_name} 的 PR {pull_number} 的所有文件中均未找到可解析的变更。")

    except requests.exceptions.RequestException as e:
        logger.error(f"从 GitHub API ({files_url}) 获取数据时出错: {e}")
        if 'response' in locals() and response is not None:
            logger.error(f"响应状态: {response.status_code}, 响应体: {response.text[:500]}...")
    except json.JSONDecodeError as json_e:
        logger.error(f"解码来自 GitHub API ({files_url}) 的 JSON 响应时出错: {json_e}")
        if 'response' in locals() and response is not None:
            logger.error(f"响应文本: {response.text[:500]}...")
    except Exception as e:
        logger.exception(
            f"获取/解析 {owner}/{repo_name} 中 PR {pull_number} 的 diff 时发生意外错误:")

    return structured_changes


def get_gitlab_mr_changes(project_id, mr_iid, access_token):
    """从 GitLab API 获取 Merge Request 的变更，并为每个文件解析成结构化数据"""
    if not access_token:
        logger.error(f"错误: 项目 {project_id} 未配置访问令牌。")
        return None, None

    project_config = gitlab_project_configs.get(str(project_id), {})
    project_specific_instance_url = project_config.get("instance_url")

    current_gitlab_instance_url = project_specific_instance_url or app_configs.get("GITLAB_INSTANCE_URL",
                                                                                   "https://gitlab.com")
    if project_specific_instance_url:
        logger.info(f"项目 {project_id} 使用项目特定的 GitLab 实例 URL: {project_specific_instance_url}")
    else:
        logger.info(f"项目 {project_id} 使用全局 GitLab 实例 URL: {current_gitlab_instance_url}")

    versions_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions"
    headers = {"PRIVATE-TOKEN": access_token}
    structured_changes = {}
    position_info = None

    try:
        logger.info(f"从以下地址获取 MR 版本: {versions_url}")
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
            logger.info(f"从最新版本 (ID: {latest_version_id}) 提取的位置信息: {position_info}")

            # current_gitlab_instance_url is already defined above using project-specific or global config
            version_detail_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions/{latest_version_id}"
            logger.info(f"从以下地址获取版本 ID {latest_version_id} 的详细信息: {version_detail_url}")
            version_detail_response = requests.get(version_detail_url, headers=headers, timeout=60)
            version_detail_response.raise_for_status()
            version_detail_data = version_detail_response.json()

            api_diffs = version_detail_data.get('diffs', [])
            logger.info(f"从 API 收到版本 ID {latest_version_id} 的 {len(api_diffs)} 个文件 diff。")

            for diff_item in api_diffs:
                file_diff_text = diff_item.get('diff')
                new_path = diff_item.get('new_path')
                old_path = diff_item.get('old_path')
                is_renamed = diff_item.get('renamed_file', False)

                if not file_diff_text or not new_path:
                    logger.warning(
                        f"警告: 因缺少 diff 文本或 new_path 而跳过 diff 项。项: {diff_item.get('new_path', 'N/A')}")
                    continue

                logger.info(f"解析文件 diff: {new_path} (旧路径: {old_path if is_renamed else 'N/A'})")
                try:
                    # 使用通用的 parse_single_file_diff
                    file_parsed_changes = parse_single_file_diff(file_diff_text, new_path,
                                                                 old_path if is_renamed else None)
                    if file_parsed_changes and file_parsed_changes.get("changes"):
                        structured_changes[new_path] = file_parsed_changes
                        logger.info(f"成功解析 {new_path} 的 {len(file_parsed_changes['changes'])} 处变更。")
                    else:
                        logger.info(f"未从 {new_path} 的 diff 中解析出变更。")
                except Exception as parse_e:
                    logger.exception(f"解析文件 {new_path} 的 diff 时出错:")

            if not structured_changes:
                logger.info(f"在项目 {project_id} 的 MR {mr_iid} 的所有文件中均未找到可解析的变更。")
        else:
            logger.info(f"GitLab 对项目 {project_id} 的 MR {mr_iid} 的初始响应中未找到版本。")

    except requests.exceptions.RequestException as e:
        request_url = locals().get('version_detail_url') or locals().get('versions_url', 'GitLab API')
        error_response = locals().get('version_detail_response') or locals().get('response')
        logger.error(f"从 {request_url} 获取数据时出错: {e}")
        if error_response is not None:
            logger.error(f"响应状态: {error_response.status_code}, 响应体: {error_response.text[:500]}...")
    except json.JSONDecodeError as json_e:
        request_url = locals().get('version_detail_url') or locals().get('versions_url', 'GitLab API')
        error_response = locals().get('version_detail_response') or locals().get('response')
        logger.error(f"解码来自 {request_url} 的 JSON 响应时出错: {json_e}")
        if error_response is not None:
            logger.error(f"响应文本: {error_response.text[:500]}...")
    except Exception as e:
        logger.exception(f"获取/解析项目 {project_id} 中 MR {mr_iid} 的 diff 时发生意外错误:")

    return structured_changes, position_info


def _fetch_file_content_from_url(url: str, headers: dict, is_github: bool = False, max_size_bytes: int = None):
    """
    通用辅助函数，用于从给定 URL 获取文件内容。
    GitHub raw URL 直接返回文本。GitHub Contents API 和 GitLab Files API 返回 JSON，其中内容为 base64 编码。
    增加了 max_size_bytes 参数用于限制通过 API 获取的文件大小。
    """
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        if is_github and "application/vnd.github.v3.raw" in headers.get("Accept", ""): # GitHub raw URL
            # Try to decode as UTF-8, fallback to ISO-8859-1 then skip if fails
            try:
                return response.content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    return response.content.decode('iso-8859-1') # Common fallback
                except UnicodeDecodeError:
                    logger.warning(f"无法将 {url} 的内容解码为 UTF-8 或 ISO-8859-1。可能为二进制文件。")
                    return None
        else: # GitHub Contents API or GitLab Files API
            data = response.json()
            
            # 文件大小检查 (适用于返回 JSON 并包含 size 字段的 API)
            file_size = data.get('size')
            if file_size is not None and max_size_bytes is not None and file_size > max_size_bytes:
                logger.warning(f"文件 {url} 过大 ({file_size} 字节，限制 {max_size_bytes} 字节)。跳过获取内容。")
                return f"[Content not fetched: File size ({file_size} bytes) exceeds limit {max_size_bytes} bytes]"

            if data.get("encoding") == "base64" and data.get("content"):
                content_bytes = base64.b64decode(data["content"])
                # Try to decode as UTF-8, fallback to ISO-8859-1 then skip if fails
                try:
                    return content_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        return content_bytes.decode('iso-8859-1')
                    except UnicodeDecodeError:
                        logger.warning(f"无法将 {url} 的 base64 内容解码为 UTF-8 或 ISO-8859-1。可能为二进制文件。")
                        return None
            elif data.get("content") == "": # Empty file
                return ""
            else:
                logger.warning(f"从 {url} 获取文件内容时未找到 base64 内容或编码。响应: {data}")
                return None
    except requests.exceptions.RequestException as e:
        logger.error(f"从 {url} 获取文件内容时出错: {e}")
        return None
    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
        logger.error(f"解码/解析从 {url} 获取的文件内容时出错: {e}")
        return None


def get_github_pr_data_for_general_review(owner: str, repo_name: str, pull_number: int, access_token: str, pr_data: dict):
    """
    为 GitHub PR 获取粗粒度审查所需的数据：文件列表、每个文件的 diff、旧内容和新内容。
    pr_data 是 GitHub PR webhook 负载中的 'pull_request' 对象。
    """
    if not access_token:
        logger.error(f"错误: 仓库 {owner}/{repo_name} 未配置访问令牌。")
        return None

    current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
    files_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/pulls/{pull_number}/files"
    base_sha = pr_data.get('base', {}).get('sha')
    # head_sha = pr_data.get('head', {}).get('sha') # raw_url is already at head

    headers_files_api = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    headers_content_api = { # For fetching specific file content (potentially base_sha)
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json" # Gets JSON with base64 content
    }
    headers_raw_content_api = { # For fetching raw file content (new_content from raw_url)
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3.raw" # Gets raw content directly
    }

    general_review_data = []

    try:
        logger.info(f"从 {files_url} 获取 PR 文件列表 (用于粗粒度审查)。")
        response = requests.get(files_url, headers=headers_files_api, timeout=60)
        response.raise_for_status()
        files_api_data = response.json()

        if not files_api_data:
            logger.info(f"在 {owner}/{repo_name} 的 PR {pull_number} 中未找到文件。")
            return []

        for file_item in files_api_data:
            file_path = file_item.get('filename')
            status = file_item.get('status') # 'added', 'modified', 'removed', 'renamed'
            diff_text = file_item.get('patch', '')
            raw_url = file_item.get('raw_url') # Content at HEAD
            previous_filename = file_item.get('previous_filename')

            file_data_entry = {
                "file_path": file_path,
                "status": status,
                "diff_text": diff_text,
                "old_content": None
            }

            # 获取旧内容 (适用于 'modified', 'removed', 'renamed')
            path_for_old_content = previous_filename if status == 'renamed' and previous_filename else file_path
            if status in ['modified', 'removed', 'renamed'] and base_sha and path_for_old_content:
                # Check size if available (GitHub files API doesn't give old size directly)
                # We'll attempt to fetch and let _fetch_file_content_from_url handle large/binary via its internal JSON parsing if not raw
                old_content_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/contents/{path_for_old_content}?ref={base_sha}"
                logger.info(f"获取旧内容: {path_for_old_content} (ref: {base_sha}) 从 {old_content_url}")
                file_data_entry["old_content"] = _fetch_file_content_from_url(old_content_url, headers_content_api, is_github=False, max_size_bytes=1024*1024) # Not raw, expect JSON, add size limit

            general_review_data.append(file_data_entry)

    except requests.exceptions.RequestException as e:
        logger.error(f"从 GitHub API ({files_url}) 获取粗粒度审查数据时出错: {e}")
        return None # Indicate error
    except Exception as e:
        logger.exception(f"为 {owner}/{repo_name} PR {pull_number} 准备粗粒度审查数据时发生意外错误:")
        return None

    return general_review_data


def get_gitlab_mr_data_for_general_review(project_id: str, mr_iid: int, access_token: str, mr_attrs: dict, position_info: dict):
    """
    为 GitLab MR 获取粗粒度审查所需的数据：文件列表、每个文件的 diff、旧内容和新内容。
    mr_attrs 是 GitLab MR webhook 负载中的 'object_attributes'。
    position_info 包含 'base_commit_sha', 'start_commit_sha', 'head_commit_sha'。
    """
    if not access_token:
        logger.error(f"错误: 项目 {project_id} 未配置访问令牌。")
        return None

    project_config = gitlab_project_configs.get(str(project_id), {})
    project_specific_instance_url = project_config.get("instance_url")
    current_gitlab_instance_url = project_specific_instance_url or app_configs.get("GITLAB_INSTANCE_URL", "https://gitlab.com")

    base_sha = position_info.get("base_commit_sha")
    head_sha = position_info.get("head_commit_sha")
    if not head_sha: # Fallback to last_commit from webhook payload if not in position_info
        head_sha = mr_attrs.get('last_commit', {}).get('id')

    if not base_sha or not head_sha:
        logger.error(f"GitLab MR {project_id}#{mr_iid}: 缺少 base_sha 或 head_sha，无法获取文件内容。Base: {base_sha}, Head: {head_sha}")
        return None

    headers = {"PRIVATE-TOKEN": access_token}
    general_review_data = []

    # GitLab MR changes are typically fetched via versions API then details of latest version
    # This gives us the diffs. We then fetch content for each file.
    latest_version_id = position_info.get("latest_version_id") # Assuming this is passed in position_info
    if not latest_version_id: # Fallback: try to get versions if not pre-fetched
        versions_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions"
        try:
            logger.info(f"从 {versions_url} 获取 MR 版本 (用于粗粒度审查)。")
            versions_response = requests.get(versions_url, headers=headers, timeout=30)
            versions_response.raise_for_status()
            versions_data = versions_response.json()
            if versions_data:
                latest_version_id = versions_data[0].get("id")
            else:
                logger.warning(f"GitLab MR {project_id}#{mr_iid}: 未找到 MR 版本。")
                return []
        except requests.exceptions.RequestException as e:
            logger.error(f"从 GitLab API ({versions_url}) 获取 MR 版本时出错: {e}")
            return None
    
    version_detail_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions/{latest_version_id}"
    try:
        logger.info(f"从 {version_detail_url} 获取 MR 版本详情 (用于粗粒度审查)。")
        detail_response = requests.get(version_detail_url, headers=headers, timeout=60)
        detail_response.raise_for_status()
        version_detail_data = detail_response.json()
        api_diffs = version_detail_data.get('diffs', [])

        for diff_item in api_diffs:
            new_path = diff_item.get('new_path')
            old_path = diff_item.get('old_path')
            diff_text = diff_item.get('diff', '')
            is_renamed = diff_item.get('renamed_file', False)
            is_deleted = diff_item.get('deleted_file', False)
            is_new = diff_item.get('new_file', False)

            status = "modified"
            if is_new: status = "added"
            if is_deleted: status = "deleted"
            if is_renamed: status = "renamed"
            
            file_data_entry = {
                "file_path": new_path, # For deleted files, new_path is the path of the deleted file
                "status": status,
                "diff_text": diff_text,
                "old_content": None
            }

            # GitLab file content API: /projects/:id/repository/files/:file_path?ref=:sha
            # File path needs to be URL-encoded.

            # Get old content (if not new file)
            path_for_old_content = old_path if old_path else new_path # If renamed, old_path is correct. If modified, old_path is same as new_path.
            if not is_new and path_for_old_content:
                encoded_old_path = requests.utils.quote(path_for_old_content, safe='')
                old_content_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/repository/files/{encoded_old_path}?ref={base_sha}"
                logger.info(f"获取旧内容 (GitLab): {path_for_old_content} (ref: {base_sha})")
                file_data_entry["old_content"] = _fetch_file_content_from_url(old_content_url, headers, max_size_bytes=1024*1024)
            
            general_review_data.append(file_data_entry)

    except requests.exceptions.RequestException as e:
        logger.error(f"从 GitLab API ({version_detail_url}) 获取粗粒度审查数据时出错: {e}")
        return None
    except Exception as e:
        logger.exception(f"为 GitLab MR {project_id}#{mr_iid} 准备粗粒度审查数据时发生意外错误:")
        return None
        
    return general_review_data


def add_github_pr_comment(owner, repo_name, pull_number, access_token, review, head_sha):
    """向 GitHub Pull Request 的特定行添加评论"""
    if not access_token:
        logger.error("错误: 无法添加评论，缺少访问令牌。")
        return False
    if not head_sha:
        logger.error("错误: 无法添加评论，缺少 head_sha。")
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
        logger.warning("警告: 跳过评论，审查缺少 'file' 路径。")
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
        target_desc = f"针对文件 {file_path} 的通用 PR 评论"
        current_url_to_use = general_comment_url
        current_payload_to_use = general_payload
        logger.info(f"{file_path} 上没有特定新行的审查。将作为通用 PR 评论发布。")
    else:
        current_url_to_use = comment_url
        current_payload_to_use = payload
        logger.info(f"尝试向 {target_desc} 添加行评论")

    try:
        response = requests.post(current_url_to_use, headers=headers, json=current_payload_to_use, timeout=30)
        response.raise_for_status()
        logger.info(f"成功向 GitHub PR #{pull_number} ({target_desc}) 添加评论")
        return True
    except requests.exceptions.RequestException as e:
        error_message = f"添加 GitHub 评论 ({target_desc}) 时出错: {e}"
        if 'response' in locals() and response is not None:
            error_message += f" - 状态: {response.status_code} - 响应体: {response.text[:500]}"
        logger.error(error_message)

        if line_comment_possible and current_url_to_use == comment_url:
            logger.warning("由于特定行评论错误，回退到作为通用 PR 评论发布。")
            current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
            general_comment_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/issues/{pull_number}/comments"
            fallback_payload = {"body": f"**(评论原针对 {target_desc})**\n\n{body}"}
            try:
                fallback_response = requests.post(general_comment_url, headers=headers, json=fallback_payload,
                                                  timeout=30)
                fallback_response.raise_for_status()
                logger.info(f"行评论失败后，成功作为通用 PR 讨论添加评论。")
                return True
            except Exception as fallback_e:
                fb_error_message = f"添加回退的通用 GitHub 评论时出错: {fallback_e}"
                if 'fallback_response' in locals() and fallback_response is not None:
                    fb_error_message += f" - 状态: {fallback_response.status_code} - 响应体: {fallback_response.text[:500]}"
                logger.error(fb_error_message)
                return False
        return False
    except Exception as e:
        logger.exception(f"添加 GitHub 评论 ({target_desc}) 时发生意外错误:")
        return False


def add_gitlab_mr_comment(project_id, mr_iid, access_token, review, position_info):
    """向 GitLab Merge Request 的特定行添加评论"""
    if not access_token:
        logger.error("错误: 无法添加评论，缺少访问令牌。")
        return False
    if not position_info or not position_info.get("head_sha") or not position_info.get(
            "base_sha") or not position_info.get("start_sha"):
        logger.error(
            f"错误: 无法添加评论，缺少必要的位置信息 (head_sha/base_sha/start_sha)。得到: {position_info}")
        return False

    project_config = gitlab_project_configs.get(str(project_id), {})
    project_specific_instance_url = project_config.get("instance_url")

    current_gitlab_instance_url = project_specific_instance_url or app_configs.get("GITLAB_INSTANCE_URL",
                                                                                   "https://gitlab.com")
    if project_specific_instance_url:
        logger.info(f"项目 {project_id} 的评论使用项目特定的 GitLab 实例 URL: {project_specific_instance_url}")
    else:
        logger.info(f"项目 {project_id} 的评论使用全局 GitLab 实例 URL: {current_gitlab_instance_url}")
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
        logger.warning("警告: 跳过评论，审查缺少 'file' 路径。")
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
        target_desc = f"文件 {position_data['old_path']} 旧行号 {lines_info['old']}"
    else:
        target_desc = f"针对文件 {file_path} 的通用讨论"
        line_comment_possible = False

    if line_comment_possible:
        payload = {"body": body, "position": position_data}
        logger.info(f"尝试向 {target_desc} 添加带位置的评论")
    else:
        payload = {"body": f"**AI Review Comment (File: {file_path})**\n\n{body}"}
        logger.info(f"{file_path} 的审查中没有特定行信息。将作为通用 MR 讨论发布。")

    response_obj = None  # Define response_obj to ensure it's available in except block
    try:
        response_obj = requests.post(comment_url, headers=headers, json=payload, timeout=30)
        response_obj.raise_for_status()
        logger.info(f"成功向 GitLab MR {mr_iid} ({target_desc}) 添加评论")
        return True
    except requests.exceptions.RequestException as e:
        error_message = f"添加 GitLab 评论 ({target_desc}) 时出错: {e}"
        if response_obj is not None:  # Check if response_obj was assigned
            error_message += f" - 状态: {response_obj.status_code} - 响应体: {response_obj.text[:500]}"
        logger.error(error_message)

        if line_comment_possible:
            logger.warning("由于位置错误，回退到作为通用评论发布。")
            fallback_payload = {"body": f"**(评论原针对 {target_desc})**\n\n{body}"}
            fallback_response_obj = None
            try:
                fallback_response_obj = requests.post(comment_url, headers=headers, json=fallback_payload, timeout=30)
                fallback_response_obj.raise_for_status()
                logger.info(f"位置评论失败后，成功作为通用讨论添加评论。")
                return True
            except Exception as fallback_e:
                fb_error_message = f"添加回退的通用 GitLab 评论时出错: {fallback_e}"
                if fallback_response_obj is not None:
                    fb_error_message += f" - 状态: {fallback_response_obj.status_code} - 响应体: {fallback_response_obj.text[:500]}"
                logger.error(fb_error_message)
                return False
        return False
    except Exception as e:
        logger.exception(f"添加 GitLab 评论 ({target_desc}) 时发生意外错误:")
        return False


def add_github_pr_general_comment(owner: str, repo_name: str, pull_number: int, access_token: str, review_text: str):
    """向 GitHub Pull Request 添加一个通用的粗粒度审查评论。"""
    if not access_token:
        logger.error("错误: 无法添加粗粒度评论，缺少访问令牌。")
        return False
    if not review_text.strip():
        logger.info("粗粒度审查文本为空，不添加评论。")
        return True # Technically successful as there's nothing to post

    current_github_api_url = app_configs.get("GITHUB_API_URL", "https://api.github.com")
    # General PR comments are posted as issue comments
    comment_url = f"{current_github_api_url}/repos/{owner}/{repo_name}/issues/{pull_number}/comments"
    headers = {
        "Authorization": f"token {access_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
    payload = {"body": review_text}

    try:
        response = requests.post(comment_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        logger.info(f"成功向 GitHub PR #{pull_number} 添加粗粒度审查评论。")
        return True
    except requests.exceptions.RequestException as e:
        error_message = f"添加 GitHub 粗粒度审查评论时出错: {e}"
        if 'response' in locals() and response is not None:
            error_message += f" - 状态: {response.status_code} - 响应体: {response.text[:500]}"
        logger.error(error_message)
        return False
    except Exception as e:
        logger.exception(f"添加 GitHub 粗粒度审查评论时发生意外错误:")
        return False


def add_gitlab_mr_general_comment(project_id: str, mr_iid: int, access_token: str, review_text: str):
    """向 GitLab Merge Request 添加一个通用的粗粒度审查讨论/评论。"""
    if not access_token:
        logger.error("错误: 无法添加粗粒度评论，缺少访问令牌。")
        return False
    if not review_text.strip():
        logger.info("粗粒度审查文本为空，不添加评论。")
        return True

    project_config = gitlab_project_configs.get(str(project_id), {})
    project_specific_instance_url = project_config.get("instance_url")
    current_gitlab_instance_url = project_specific_instance_url or app_configs.get("GITLAB_INSTANCE_URL", "https://gitlab.com")
    
    # Post as a new discussion (thread) without position for general comments
    comment_url = f"{current_gitlab_instance_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/discussions"
    headers = {"PRIVATE-TOKEN": access_token, "Content-Type": "application/json"}
    payload = {"body": review_text}
    
    response_obj = None
    try:
        response_obj = requests.post(comment_url, headers=headers, json=payload, timeout=30)
        response_obj.raise_for_status()
        logger.info(f"成功向 GitLab MR {mr_iid} 添加粗粒度审查评论。")
        return True
    except requests.exceptions.RequestException as e:
        error_message = f"添加 GitLab 粗粒度审查评论时出错: {e}"
        if response_obj is not None:
            error_message += f" - 状态: {response_obj.status_code} - 响应体: {response_obj.text[:500]}"
        logger.error(error_message)
        return False
    except Exception as e:
        logger.exception(f"添加 GitLab 粗粒度审查评论时发生意外错误:")
        return False
