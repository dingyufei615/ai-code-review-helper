from flask import request, abort, jsonify
import json
import logging
from api.app_factory import app, executor # 导入 executor
from api.core_config import (
    github_repo_configs, gitlab_project_configs, app_configs,
    is_commit_processed, mark_commit_as_processed, remove_processed_commit_entries_for_pr_mr
)
from api.utils import verify_github_signature, verify_gitlab_signature
from api.services.vcs_service import (
    get_github_pr_data_for_coarse_review, add_github_pr_coarse_comment,
    get_gitlab_mr_data_for_coarse_review, add_gitlab_mr_coarse_comment,
    get_gitlab_mr_changes # 新增导入
)
from api.services.llm_service import get_openai_code_review_coarse
from api.services.notification_service import send_to_wecom_bot
from api.services.common_service import get_final_summary_comment_text
from .webhook_helpers import _save_review_results_and_log

logger = logging.getLogger(__name__)


def _process_github_general_payload(access_token, owner, repo_name, pull_number, pr_data, head_sha, repo_full_name, pr_title, pr_html_url, repo_web_url, pr_source_branch, pr_target_branch):
    """实际处理 GitHub 通用审查的核心逻辑。"""
    logger.info("GitHub (通用审查): 正在获取 PR 数据 (diffs 和文件内容)...")
    file_data_list = get_github_pr_data_for_coarse_review(owner, repo_name, pull_number, access_token, pr_data)

    if file_data_list is None:
        logger.warning("GitHub (通用审查): 获取 PR 数据失败。中止审查。")
        # 在异步任务中，通常会记录错误，可能不会直接返回 HTTP 响应
        return
    if not file_data_list:
        logger.info("GitHub (通用审查): 未检测到文件变更或数据。无需审查。")
        _save_review_results_and_log( # 保存空列表表示已处理且无内容
            vcs_type='github_general', identifier=repo_full_name, pr_mr_id=str(pull_number),
            commit_sha=head_sha, review_json_string=json.dumps([])
        )
        return

    aggregated_coarse_reviews_for_storage = []
    files_with_issues_details = [] # {file_path: str, issues_text: str}

    logger.info(f'GitHub (通用审查): 将对 {len(file_data_list)} 个文件逐一发送给 {app_configs.get("OPENAI_MODEL", "gpt-4o")} 进行审查...')

    for file_item in file_data_list:
        current_file_path = file_item.get("file_path", "Unknown File")
        logger.info(f"GitHub (通用审查): 正在对文件 {current_file_path} 进行 LLM 审查...")
        review_text_for_file = get_openai_code_review_coarse(file_item) # Pass single file_item

        logger.info(f"GitHub (通用审查): 文件 {current_file_path} 的 LLM 原始输出:\n{review_text_for_file}")

        if review_text_for_file and review_text_for_file.strip() and \
           "未发现严重问题" not in review_text_for_file and \
           "没有修改建议" not in review_text_for_file and \
           "OpenAI client is not available" not in review_text_for_file and \
           "Error serializing input data" not in review_text_for_file:
            
            logger.info(f"GitHub (通用审查): 文件 {current_file_path} 发现问题。正在添加评论...")
            comment_text_for_pr = f"**AI 审查意见 (文件: `{current_file_path}`)**\n\n{review_text_for_file}"
            add_github_pr_coarse_comment(owner, repo_name, pull_number, access_token, comment_text_for_pr)
            
            files_with_issues_details.append({"file": current_file_path, "issues": review_text_for_file})

            review_wrapper_for_file = {
                "file": current_file_path,
                "lines": {"old": None, "new": None},
                "category": "Coarse Review",
                "severity": "INFO",
                "analysis": review_text_for_file,
                "suggestion": "请参考上述分析。"
            }
            aggregated_coarse_reviews_for_storage.append(review_wrapper_for_file)
        else:
            logger.info(f"GitHub (通用审查): 文件 {current_file_path} 未发现问题、审查意见为空或指示无问题。")

    # After processing all files
    if aggregated_coarse_reviews_for_storage:
        review_json_string_for_storage = json.dumps(aggregated_coarse_reviews_for_storage)
        _save_review_results_and_log(
            vcs_type='github_general',
            identifier=repo_full_name,
            pr_mr_id=str(pull_number),
            commit_sha=head_sha,
            review_json_string=review_json_string_for_storage
        )
    else:
        logger.info("GitHub (通用审查): 所有被检查的文件均未发现问题。")
        no_issues_text = f"AI General Code Review 已完成，对 {len(file_data_list)} 个文件的检查均未发现主要问题或无审查建议。"
        add_github_pr_coarse_comment(owner, repo_name, pull_number, access_token, no_issues_text)
        _save_review_results_and_log(
            vcs_type='github_general',
            identifier=repo_full_name,
            pr_mr_id=str(pull_number),
            commit_sha=head_sha,
            review_json_string=json.dumps([]) # Save empty list
        )

    if app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        logger.info("GitHub (通用审查): 正在发送摘要通知到企业微信机器人...")
        num_files_with_issues = len(files_with_issues_details)
        total_files_checked = len(file_data_list)
        
        summary_line = f"AI General Code Review 已完成。在 {total_files_checked} 个已检查文件中，发现 {num_files_with_issues} 个文件可能存在问题。"
        if num_files_with_issues == 0:
            summary_line = f"AI General Code Review 已完成。所有 {total_files_checked} 个已检查文件均未发现主要问题。"
            
        summary_content = f"""**AI通用代码审查完成 (GitHub)**

> 仓库: [{repo_full_name}]({repo_web_url})
> PR: [{pr_title}]({pr_html_url}) (#{pull_number})
> 分支: `{pr_source_branch}` → `{pr_target_branch}`

{summary_line}
"""
        send_to_wecom_bot(summary_content)

    if head_sha:
        mark_commit_as_processed('github_general', repo_full_name, str(pull_number), head_sha)

    # 添加最终总结评论
    final_comment_text = get_final_summary_comment_text()
    add_github_pr_coarse_comment(owner, repo_name, pull_number, access_token, final_comment_text)


@app.route('/github_webhook_general', methods=['POST'])
def github_webhook_general():
    """处理 GitHub Webhook 请求 (粗粒度审查)"""
    try:
        payload_data = request.get_json()
        if payload_data is None: raise ValueError("请求体为空或非有效 JSON")
    except Exception as e:
        logger.error(f"解析 GitHub JSON 负载时出错 (粗粒度): {e}")
        abort(400, "无效的 JSON 负载")

    repo_info = payload_data.get('repository', {})
    repo_full_name = repo_info.get('full_name')

    if not repo_full_name:
        logger.error("错误: GitHub 负载中缺少 repository.full_name (粗粒度)。")
        abort(400, "GitHub 负载中缺少 repository.full_name")

    config = github_repo_configs.get(repo_full_name)
    if not config:
        logger.error(f"错误: 未找到 GitHub 仓库 {repo_full_name} 的配置 (粗粒度)。")
        abort(404, f"未找到 GitHub 仓库 {repo_full_name} 的配置。")

    webhook_secret = config.get('secret')
    access_token = config.get('token')

    if not verify_github_signature(request, webhook_secret):
        abort(401, "GitHub signature verification failed (coarse).")

    event_type = request.headers.get('X-GitHub-Event')
    if event_type != "pull_request":
        logger.info(f"GitHub (粗粒度): 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    action = payload_data.get('action')
    pr_data = payload_data.get('pull_request', {})
    pr_state = pr_data.get('state')
    pr_merged = pr_data.get('merged', False)

    if action == 'closed':
        pull_number_str = str(pr_data.get('number'))
        logger.info(f"GitHub (通用审查): PR {repo_full_name}#{pull_number_str} 已关闭 (合并状态: {pr_merged})。正在清理已处理的 commit 记录...")
        remove_processed_commit_entries_for_pr_mr('github_general', repo_full_name, pull_number_str) # Use distinct type for safety
        return f"PR {pull_number_str} 已关闭，通用审查相关记录已清理。", 200

    if pr_state != 'open' or action not in ['opened', 'reopened', 'synchronize']:
        logger.info(f"GitHub (粗粒度): 忽略 PR 操作 '{action}' 或状态 '{pr_state}'。")
        return "PR 操作/状态已忽略", 200

    owner = repo_info.get('owner', {}).get('login')
    repo_name = repo_info.get('name')
    pull_number = pr_data.get('number')
    pr_title = pr_data.get('title')
    pr_html_url = pr_data.get('html_url')
    head_sha = pr_data.get('head', {}).get('sha')
    repo_web_url = repo_info.get('html_url')
    pr_source_branch = pr_data.get('head', {}).get('ref')
    pr_target_branch = pr_data.get('base', {}).get('ref')

    if not all([owner, repo_name, pull_number, head_sha]):
        logger.error("错误: GitHub 负载中缺少必要的 PR 信息 (粗粒度)。")
        abort(400, "GitHub 负载中缺少必要的 PR 信息")

    logger.info(f"--- 收到 GitHub Pull Request Hook (通用审查) ---")
    logger.info(f"仓库: {repo_full_name}, PR 编号: {pull_number}, Head SHA: {head_sha}")

    if head_sha and is_commit_processed('github_general', repo_full_name, str(pull_number), head_sha):
        logger.info(f"GitHub (通用审查): PR {repo_full_name}#{pull_number} 的提交 {head_sha} 已处理。跳过。")
        return "提交已处理", 200

    # 调用提取出来的核心处理逻辑函数 (异步执行)
    executor.submit(
        _process_github_general_payload,
        access_token=access_token,
        owner=owner,
        repo_name=repo_name,
        pull_number=pull_number,
        pr_data=pr_data,
        head_sha=head_sha,
        repo_full_name=repo_full_name,
        pr_title=pr_title,
        pr_html_url=pr_html_url,
        repo_web_url=repo_web_url,
        pr_source_branch=pr_source_branch,
        pr_target_branch=pr_target_branch
    )
    
    logger.info(f"GitHub (通用审查): PR {repo_full_name}#{pull_number} 的处理任务已提交到后台执行。")
    return jsonify({"message": "GitHub General Webhook processing task accepted."}), 202


def _process_gitlab_general_payload(access_token, project_id_str, mr_iid, mr_attrs, final_position_info, head_sha_payload, current_commit_sha_for_ops, project_name_from_payload, project_web_url, mr_title, mr_url):
    """实际处理 GitLab 通用审查的核心逻辑。"""
    logger.info("GitLab (通用审查): 正在获取 MR 数据 (diffs 和文件内容)...")
    file_data_list = get_gitlab_mr_data_for_coarse_review(project_id_str, mr_iid, access_token, mr_attrs, final_position_info)

    if file_data_list is None:
        logger.warning("GitLab (通用审查): 获取 MR 数据失败。中止审查。")
        return
    if not file_data_list:
        logger.info("GitLab (通用审查): 未检测到文件变更或数据。无需审查。")
        _save_review_results_and_log( # 保存空列表表示已处理且无内容
            vcs_type='gitlab_general', identifier=project_id_str, pr_mr_id=str(mr_iid),
            commit_sha=current_commit_sha_for_ops, 
            review_json_string=json.dumps([]), project_name_for_gitlab=project_name_from_payload
        )
        return

    aggregated_coarse_reviews_for_storage = []
    files_with_issues_details = [] # {file_path: str, issues_text: str}

    logger.info(f'GitLab (通用审查): 将对 {len(file_data_list)} 个文件逐一发送给 {app_configs.get("OPENAI_MODEL", "gpt-4o")} 进行审查...')

    for file_item in file_data_list:
        current_file_path = file_item.get("file_path", "Unknown File")
        logger.info(f"GitLab (通用审查): 正在对文件 {current_file_path} 进行 LLM 审查...")
        review_text_for_file = get_openai_code_review_coarse(file_item)

        logger.info(f"GitLab (通用审查): 文件 {current_file_path} 的 LLM 原始输出:\n{review_text_for_file}")

        if review_text_for_file and review_text_for_file.strip() and \
           "此文件未发现问题" not in review_text_for_file and \
           "没有修改建议" not in review_text_for_file and \
           "OpenAI client is not available" not in review_text_for_file and \
           "Error serializing input data" not in review_text_for_file:
            
            logger.info(f"GitLab (通用审查): 文件 {current_file_path} 发现问题。正在添加评论...")
            comment_text_for_mr = f"**AI 审查意见 (文件: `{current_file_path}`)**\n\n{review_text_for_file}"
            add_gitlab_mr_coarse_comment(project_id_str, mr_iid, access_token, comment_text_for_mr)
            
            files_with_issues_details.append({"file": current_file_path, "issues": review_text_for_file})

            review_wrapper_for_file = {
                "file": current_file_path,
                "lines": {"old": None, "new": None},
                "category": "Coarse Review",
                "severity": "INFO",
                "analysis": review_text_for_file,
                "suggestion": "请参考上述分析。"
            }
            aggregated_coarse_reviews_for_storage.append(review_wrapper_for_file)
        else:
            logger.info(f"GitLab (通用审查): 文件 {current_file_path} 未发现问题、审查意见为空或指示无问题。")

    # After processing all files
    if aggregated_coarse_reviews_for_storage:
        review_json_string_for_storage = json.dumps(aggregated_coarse_reviews_for_storage)
        _save_review_results_and_log(
            vcs_type='gitlab_general',
            identifier=project_id_str,
            pr_mr_id=str(mr_iid),
            commit_sha=current_commit_sha_for_ops,
            review_json_string=review_json_string_for_storage,
            project_name_for_gitlab=project_name_from_payload
        )
    else:
        logger.info("GitLab (通用审查): 所有被检查的文件均未发现问题。")
        no_issues_text = f"AI General Code Review 已完成，对 {len(file_data_list)} 个文件的检查均未发现主要问题或无审查建议。"
        add_gitlab_mr_coarse_comment(project_id_str, mr_iid, access_token, no_issues_text)
        _save_review_results_and_log(
            vcs_type='gitlab_general',
            identifier=project_id_str,
            pr_mr_id=str(mr_iid),
            commit_sha=current_commit_sha_for_ops,
            review_json_string=json.dumps([]), # Save empty list
            project_name_for_gitlab=project_name_from_payload
        )

    if app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        logger.info("GitLab (通用审查): 正在发送摘要通知到企业微信机器人...")
        num_files_with_issues = len(files_with_issues_details)
        total_files_checked = len(file_data_list)

        summary_line = f"AI General Code Review 已完成。在 {total_files_checked} 个已检查文件中，发现 {num_files_with_issues} 个文件可能存在问题。"
        if num_files_with_issues == 0:
            summary_line = f"AI General Code Review 已完成。所有 {total_files_checked} 个已检查文件均未发现主要问题。"
            
        mr_source_branch = mr_attrs.get('source_branch')
        mr_target_branch = mr_attrs.get('target_branch')
        summary_content = f"""**AI通用代码审查完成 (GitLab)**

> 项目: [{project_name_from_payload or project_id_str}]({project_web_url})
> MR: [{mr_title}]({mr_url}) (!{mr_iid}) 
> 分支: `{mr_source_branch}` → `{mr_target_branch}`

{summary_line}
"""
        send_to_wecom_bot(summary_content)

    if current_commit_sha_for_ops:
        mark_commit_as_processed('gitlab_general', project_id_str, str(mr_iid), current_commit_sha_for_ops)

    # 添加最终总结评论
    final_comment_text = get_final_summary_comment_text()
    add_gitlab_mr_coarse_comment(project_id_str, mr_iid, access_token, final_comment_text)


@app.route('/gitlab_webhook_general', methods=['POST'])
def gitlab_webhook_general():
    """处理 GitLab Webhook 请求 (粗粒度审查)"""
    try:
        data = request.get_json()
        if data is None: raise ValueError("请求体为空或非有效 JSON")
    except Exception as e:
        logger.error(f"解析 GitLab JSON 负载时出错 (粗粒度): {e}")
        abort(400, "无效的 JSON 负载")

    project_data = data.get('project', {})
    project_id = project_data.get('id')
    project_web_url = project_data.get('web_url')
    project_name_from_payload = project_data.get('name')
    mr_attrs = data.get('object_attributes', {})
    mr_iid = mr_attrs.get('iid')
    mr_title = mr_attrs.get('title')
    mr_url = mr_attrs.get('url')
    last_commit_payload = mr_attrs.get('last_commit', {})
    head_sha_payload = last_commit_payload.get('id') # SHA from webhook payload

    if not project_id or not mr_iid:
        logger.error("错误: GitLab 负载中缺少 project_id 或 mr_iid (粗粒度)。")
        abort(400, "GitLab 负载中缺少 project_id 或 mr_iid")

    project_id_str = str(project_id)
    config = gitlab_project_configs.get(project_id_str)
    if not config:
        logger.error(f"错误: 未找到 GitLab 项目 ID {project_id_str} 的配置 (粗粒度)。")
        abort(404, f"未找到 GitLab 项目 {project_id_str} 的配置。")

    webhook_secret = config.get('secret')
    access_token = config.get('token')

    if not verify_gitlab_signature(request, webhook_secret):
        abort(401, "GitLab signature verification failed (coarse).")

    event_type = request.headers.get('X-Gitlab-Event')
    if event_type != "Merge Request Hook":
        logger.info(f"GitLab (粗粒度): 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    mr_action = mr_attrs.get('action')
    mr_state = mr_attrs.get('state')

    if mr_action in ['close', 'merge'] or mr_state in ['closed', 'merged']:
        mr_iid_str = str(mr_iid)
        logger.info(f"GitLab (通用审查): MR {project_id_str}#{mr_iid_str} 已 {mr_action or mr_state}。正在清理已处理的 commit 记录...")
        remove_processed_commit_entries_for_pr_mr('gitlab_general', project_id_str, mr_iid_str) # Use distinct type
        return f"MR {mr_iid_str} 已 {mr_action or mr_state}，通用审查相关记录已清理。", 200

    if mr_state not in ['opened', 'reopened'] and mr_action != 'update':
        logger.info(f"GitLab (通用审查): 忽略 MR 操作 '{mr_action}' 或状态 '{mr_state}'。")
        return "MR 操作/状态已忽略", 200

    logger.info(f"--- 收到 GitLab Merge Request Hook (通用审查) ---")
    logger.info(f"项目 ID: {project_id_str}, MR IID: {mr_iid}, Head SHA (来自负载): {head_sha_payload}")

    if head_sha_payload and is_commit_processed('gitlab_general', project_id_str, str(mr_iid), head_sha_payload):
        logger.info(f"GitLab (通用审查): MR {project_id_str}#{mr_iid} 的提交 {head_sha_payload} 已处理。跳过。")
        return "提交已处理", 200

    temp_position_info = {
        "base_commit_sha": mr_attrs.get("diff_base_sha") or mr_attrs.get("base_commit_sha"),
        "head_commit_sha": head_sha_payload,
        "start_commit_sha": mr_attrs.get("start_commit_sha")
    }
    _, version_derived_position_info = get_gitlab_mr_changes(project_id_str, mr_iid, access_token)
    
    final_position_info = temp_position_info
    if version_derived_position_info:
        final_position_info["base_commit_sha"] = version_derived_position_info.get("base_sha", temp_position_info["base_commit_sha"])
        final_position_info["head_commit_sha"] = version_derived_position_info.get("head_sha", temp_position_info["head_commit_sha"])
        final_position_info["latest_version_id"] = version_derived_position_info.get("id")

    if not final_position_info.get("base_commit_sha") or not final_position_info.get("head_commit_sha"):
         logger.error(f"GitLab (通用审查) MR {project_id_str}#{mr_iid}: 无法确定 base_sha 或 head_sha。中止。")
         return "无法确定提交SHA", 500
    
    current_commit_sha_for_ops = final_position_info.get("head_commit_sha", head_sha_payload)

    # 调用提取出来的核心处理逻辑函数 (异步执行)
    executor.submit(
        _process_gitlab_general_payload,
        access_token=access_token,
        project_id_str=project_id_str,
        mr_iid=mr_iid,
        mr_attrs=mr_attrs,
        final_position_info=final_position_info,
        head_sha_payload=head_sha_payload,
        current_commit_sha_for_ops=current_commit_sha_for_ops,
        project_name_from_payload=project_name_from_payload,
        project_web_url=project_web_url,
        mr_title=mr_title,
        mr_url=mr_url
    )

    logger.info(f"GitLab (通用审查): MR {project_id_str}#{mr_iid} 的处理任务已提交到后台执行。")
    return jsonify({"message": "GitLab General Webhook processing task accepted."}), 202
