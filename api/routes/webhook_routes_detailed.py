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
    get_github_pr_changes, add_github_pr_comment, 
    get_gitlab_mr_changes, add_gitlab_mr_comment,
    add_github_pr_coarse_comment, # Used for final summary
    add_gitlab_mr_coarse_comment  # Used for final summary
)
from api.services.llm_service import get_openai_code_review
from api.services.notification_service import send_to_wecom_bot
from api.services.common_service import get_final_summary_comment_text
from .webhook_helpers import _save_review_results_and_log

logger = logging.getLogger(__name__)


# --- Helper Functions Specific to Detailed Review ---
def _post_no_issues_comment(vcs_type, comment_function, **comment_args_for_func):
    """当没有审查建议时，发表一个通用的“全部通过”评论到 PR/MR。"""
    logger.info(f"{vcs_type.capitalize()}: AI 无审查建议。将发表 '全部通过' 评论。")
    overall_status_file = f"Overall {'PR' if vcs_type == 'github' else 'MR'} Status"
    no_issues_review = {
        "file": overall_status_file,
        "severity": "INFO",
        "category": "General",
        "analysis": "AI Code Review 已完成，所有检查均已通过，无审查建议。",
        "suggestion": "Looks good!",
        "lines": {}  # 确保这是一个通用的 PR/MR 评论
    }
    # comment_function 需要 'review' 作为命名参数
    comment_function(review=no_issues_review, **comment_args_for_func)


def _get_wecom_summary_line(num_reviews, vcs_type):
    """为企业微信通知生成摘要行。"""
    entity_name = "Pull Request" if vcs_type == 'github' else "Merge Request"
    if num_reviews == 0:
        return "AI Code Review 已完成，所有检查均已通过，无审查建议。"
    else:
        return f"AI Code Review 已完成，共生成 {num_reviews} 条审查建议。请前往 {entity_name} 查看详情。"
# --- End Helper Functions ---


def _process_github_detailed_payload(access_token, owner, repo_name, pull_number, head_sha, repo_full_name, pr_title, pr_html_url, repo_web_url, pr_source_branch, pr_target_branch):
    """实际处理 GitHub 详细审查的核心逻辑。"""
    logger.info("GitHub (详细审查): 正在获取并解析 PR 变更...")
    structured_changes = get_github_pr_changes(owner, repo_name, pull_number, access_token)

    if structured_changes is None:
        logger.warning("GitHub (详细审查): 获取或解析 diff 内容失败。中止审查。")
        return
    if not structured_changes:
        logger.info("GitHub (详细审查): 解析后未检测到变更。无需审查。")
        # 即使没有变更，也可能需要标记为已处理并保存空结果
        _save_review_results_and_log(
            vcs_type='github', identifier=repo_full_name, pr_mr_id=str(pull_number),
            commit_sha=head_sha, review_json_string=json.dumps([])
        )
        mark_commit_as_processed('github', repo_full_name, str(pull_number), head_sha)
        return

    logger.info(f'GitHub (详细审查): 正在发送变更给 {app_configs.get("OPENAI_MODEL", "gpt-4o")} 进行审查...')
    review_result_json = get_openai_code_review(structured_changes)

    logger.info("--- GitHub (详细审查): AI 代码审查结果 (JSON) ---")
    logger.info(f"{review_result_json}")
    logger.info("--- GitHub (详细审查) 审查 JSON 结束 ---")

    _save_review_results_and_log(
        vcs_type='github',
        identifier=repo_full_name,
        pr_mr_id=str(pull_number),
        commit_sha=head_sha,
        review_json_string=review_result_json
    )

    reviews = []
    try:
        parsed_data = json.loads(review_result_json)
        if isinstance(parsed_data, list): reviews = parsed_data
        logger.info(f"GitHub (详细审查): 从 JSON 成功解析 {len(reviews)} 个审查项。")
    except json.JSONDecodeError as e:
        logger.error(f"GitHub (详细审查): 解析审查结果 JSON 时出错: {e}。原始数据: {review_result_json[:500]}")

    if reviews:
        logger.info(f"GitHub (详细审查): 尝试向 PR 添加 {len(reviews)} 条审查评论...")
        comments_added, comments_failed = 0, 0
        for review in reviews:
            if isinstance(review, dict) and "file" in review:
                file_path = review["file"]
                if file_path in structured_changes and "old_path" in structured_changes[file_path]:
                    review["old_path"] = structured_changes[file_path]["old_path"]
            if isinstance(review, dict):
                success = add_github_pr_comment(owner, repo_name, pull_number, access_token, review, head_sha)
                if success:
                    comments_added += 1
                else:
                    comments_failed += 1
            else:
                logger.warning(f"GitHub (详细审查): 跳过无效的审查项: {review}");
                comments_failed += 1
        logger.info(f"GitHub (详细审查): 添加评论完成: {comments_added} 成功, {comments_failed} 失败。")
    else:
        _post_no_issues_comment(
            vcs_type='github',
            comment_function=add_github_pr_comment,
            owner=owner,
            repo_name=repo_name,
            pull_number=pull_number,
            access_token=access_token,
            head_sha=head_sha
        )

    if app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        logger.info("GitHub (详细审查): 正在发送摘要通知到企业微信机器人...")
        review_summary_line = _get_wecom_summary_line(len(reviews), 'github')
        summary_content = f"""**AI代码审查完成 (GitHub)**

> 仓库: [{repo_full_name}]({repo_web_url})
> PR: [{pr_title}]({pr_html_url}) (#{pull_number})
> 分支: `{pr_source_branch}` → `{pr_target_branch}`

{review_summary_line}
"""
        send_to_wecom_bot(summary_content)

    if head_sha:
        mark_commit_as_processed('github', repo_full_name, str(pull_number), head_sha)
    else:
        logger.warning(f"警告: GitHub (详细审查) PR {repo_full_name}#{pull_number} 的 head_sha 为空。无法标记为已处理。")

    final_comment_text = get_final_summary_comment_text()
    add_github_pr_coarse_comment(owner, repo_name, pull_number, access_token, final_comment_text)


@app.route('/github_webhook', methods=['POST'])
def github_webhook():
    """处理 GitHub Webhook 请求"""
    try:
        payload_data = request.get_json()
        if payload_data is None: raise ValueError("请求体为空或非有效 JSON")
    except Exception as e:
        logger.error(f"解析 GitHub JSON 负载时出错: {e}")
        abort(400, "无效的 JSON 负载")

    repo_info = payload_data.get('repository', {})
    repo_full_name = repo_info.get('full_name')

    if not repo_full_name:
        logger.error("错误: GitHub 负载中缺少 repository.full_name。")
        abort(400, "GitHub 负载中缺少 repository.full_name")

    config = github_repo_configs.get(repo_full_name)
    if not config:
        logger.error(f"错误: 未找到 GitHub 仓库 {repo_full_name} 的配置。")
        abort(404,
              f"未找到 GitHub 仓库 {repo_full_name} 的配置。请通过 /config/github/repo 端点进行配置。")

    webhook_secret = config.get('secret')
    access_token = config.get('token')

    if not verify_github_signature(request, webhook_secret):
        abort(401, "GitHub signature verification failed.")

    event_type = request.headers.get('X-GitHub-Event')
    if event_type != "pull_request":
        logger.info(f"GitHub: 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    action = payload_data.get('action')
    pr_data = payload_data.get('pull_request', {})
    pr_state = pr_data.get('state') # 'open', 'closed'
    pr_merged = pr_data.get('merged', False) # True if merged

    if action == 'closed':
        pull_number_str = str(pr_data.get('number'))
        logger.info(f"GitHub: PR {repo_full_name}#{pull_number_str} 已关闭 (合并状态: {pr_merged})。正在清理 Redis 记录...")
        remove_processed_commit_entries_for_pr_mr('github', repo_full_name, pull_number_str)
        return f"PR {pull_number_str} 已关闭，记录已清理。", 200

    if pr_state != 'open' or action not in ['opened', 'reopened', 'synchronize']:
        logger.info(f"GitHub: 忽略 PR 操作 '{action}' 或状态 '{pr_state}'。")
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
        logger.error("错误: GitHub 负载中缺少必要的 PR 信息。")
        abort(400, "GitHub 负载中缺少必要的 PR 信息")

    logger.info(f"--- 收到 GitHub Pull Request Hook (详细审查) ---")
    logger.info(f"仓库: {repo_full_name}, PR 编号: {pull_number}, Head SHA: {head_sha}")

    if head_sha and is_commit_processed('github', repo_full_name, str(pull_number), head_sha):
        logger.info(f"GitHub (详细审查): PR {repo_full_name}#{pull_number} 的提交 {head_sha} 已处理。跳过。")
        return "提交已处理", 200

    # 调用提取出来的核心处理逻辑函数 (异步执行)
    executor.submit(
        _process_github_detailed_payload,
        access_token=access_token,
        owner=owner,
        repo_name=repo_name,
        pull_number=pull_number,
        head_sha=head_sha,
        repo_full_name=repo_full_name,
        pr_title=pr_title,
        pr_html_url=pr_html_url,
        repo_web_url=repo_web_url,
        pr_source_branch=pr_source_branch,
        pr_target_branch=pr_target_branch
    )
    
    logger.info(f"GitHub (详细审查): PR {repo_full_name}#{pull_number} 的处理任务已提交到后台执行。")
    return jsonify({"message": "GitHub Detailed Webhook processing task accepted."}), 202


@app.route('/gitlab_webhook', methods=['POST'])
def _process_gitlab_detailed_payload(access_token, project_id_str, mr_iid, head_sha_payload, project_data, mr_attrs, project_web_url, mr_title, mr_url, project_name_from_payload):
    """实际处理 GitLab 详细审查的核心逻辑。"""
    logger.info("GitLab (详细审查): 正在获取并解析 MR 变更...")
    structured_changes, position_info = get_gitlab_mr_changes(project_id_str, mr_iid, access_token)

    if position_info is None: position_info = {}
    if head_sha_payload and not position_info.get("head_sha"):
        position_info["head_sha"] = head_sha_payload
        logger.info(f"GitLab (详细审查): 使用来自 webhook 负载的 head_sha: {head_sha_payload}")
    if not all(k in position_info for k in ["base_sha", "start_sha", "head_sha"]):
        logger.warning("GitLab (详细审查): 警告: 缺少用于精确定位评论的关键提交 SHA 信息。")

    if structured_changes is None:
        logger.warning("GitLab (详细审查): 获取或解析 diff 内容失败。中止审查。")
        return
    if not structured_changes:
        logger.info("GitLab (详细审查): 解析后未检测到变更。无需审查。")
        _save_review_results_and_log(
            vcs_type='gitlab', identifier=project_id_str, pr_mr_id=str(mr_iid),
            commit_sha=head_sha_payload, review_json_string=json.dumps([]),
            project_name_for_gitlab=project_name_from_payload
        )
        mark_commit_as_processed('gitlab', project_id_str, str(mr_iid), head_sha_payload)
        return

    logger.info(f'GitLab (详细审查): 正在发送变更给 {app_configs.get("OPENAI_MODEL", "gpt-4o")} 进行审查...')
    review_result_json = get_openai_code_review(structured_changes)

    logger.info("--- GitLab (详细审查): AI 代码审查结果 (JSON) ---")
    logger.info(f"{review_result_json}")
    logger.info("--- GitLab (详细审查) 审查 JSON 结束 ---")

    current_commit_sha_for_saving = head_sha_payload
    if not current_commit_sha_for_saving and position_info and position_info.get("head_sha"):
        current_commit_sha_for_saving = position_info.get("head_sha")
        logger.info(f"GitLab (详细审查): 使用来自 position_info 的 head_sha ({current_commit_sha_for_saving}) 进行后续操作。")
    
    _save_review_results_and_log(
        vcs_type='gitlab',
        identifier=project_id_str,
        pr_mr_id=str(mr_iid),
        commit_sha=current_commit_sha_for_saving,
        review_json_string=review_result_json,
        project_name_for_gitlab=project_name_from_payload
    )

    reviews = []
    try:
        parsed_data = json.loads(review_result_json)
        if isinstance(parsed_data, list): reviews = parsed_data
        logger.info(f"GitLab (详细审查): 从 JSON 成功解析 {len(reviews)} 个审查项。")
    except json.JSONDecodeError as e:
        logger.error(f"GitLab (详细审查): 解析审查结果 JSON 时出错: {e}。原始数据: {review_result_json[:500]}")

    if reviews:
        logger.info(f"GitLab (详细审查): 尝试向 MR 添加 {len(reviews)} 条审查评论...")
        comments_added, comments_failed = 0, 0
        for review in reviews:
            if isinstance(review, dict) and "file" in review:
                file_path = review["file"]
                if file_path in structured_changes:
                    review["old_path"] = structured_changes[file_path].get("old_path")
            if isinstance(review, dict):
                success = add_gitlab_mr_comment(project_id_str, mr_iid, access_token, review, position_info)
                if success:
                    comments_added += 1
                else:
                    comments_failed += 1
            else:
                logger.warning(f"GitLab (详细审查): 跳过无效的审查项: {review}");
                comments_failed += 1
        logger.info(f"GitLab (详细审查): 添加评论完成: {comments_added} 成功, {comments_failed} 失败。")
    else:
        _post_no_issues_comment(
            vcs_type='gitlab',
            comment_function=add_gitlab_mr_comment,
            project_id=project_id_str,
            mr_iid=mr_iid,
            access_token=access_token,
            position_info=position_info
        )

    if app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        logger.info("GitLab (详细审查): 正在发送摘要通知到企业微信机器人...")
        project_name = project_data.get('name', project_id_str)
        mr_source_branch = mr_attrs.get('source_branch')
        mr_target_branch = mr_attrs.get('target_branch')

        review_summary_line = _get_wecom_summary_line(len(reviews), 'gitlab')
        summary_content = f"""**AI代码审查完成 (GitLab)**

> 项目: [{project_name}]({project_web_url})
> MR: [{mr_title}]({mr_url}) (#{mr_iid})
> 分支: `{mr_source_branch}` → `{mr_target_branch}`

{review_summary_line}
"""
        send_to_wecom_bot(summary_content)

    if head_sha_payload:
        mark_commit_as_processed('gitlab', project_id_str, str(mr_iid), head_sha_payload)
    elif position_info and position_info.get("head_sha"):
        logger.warning(
            f"警告: GitLab (详细审查) head_sha_payload 为空，使用来自 position_info 的 head_sha 进行标记处理: {position_info.get('head_sha')}")
        mark_commit_as_processed('gitlab', project_id_str, str(mr_iid), position_info.get("head_sha"))

    final_comment_text = get_final_summary_comment_text()
    add_gitlab_mr_coarse_comment(project_id_str, mr_iid, access_token, final_comment_text)


@app.route('/gitlab_webhook', methods=['POST'])
def gitlab_webhook():
    """处理 GitLab Webhook 请求"""
    try:
        data = request.get_json()
        if data is None: raise ValueError("请求体为空或非有效 JSON")
    except Exception as e:
        logger.error(f"解析 GitLab JSON 负载时出错: {e}")
        abort(400, "无效的 JSON 负载")

    project_data = data.get('project', {})
    project_id = project_data.get('id')
    project_web_url = project_data.get('web_url')
    project_name_from_payload = project_data.get('name')
    mr_attrs = data.get('object_attributes', {})
    mr_iid = mr_attrs.get('iid')
    mr_title = mr_attrs.get('title')
    mr_url = mr_attrs.get('url')
    last_commit = mr_attrs.get('last_commit', {})
    head_sha_payload = last_commit.get('id')

    if not project_id or not mr_iid:
        logger.error("错误: GitLab 负载中缺少 project_id 或 mr_iid。")
        abort(400, "GitLab 负载中缺少 project_id 或 mr_iid")

    project_id_str = str(project_id)
    config = gitlab_project_configs.get(project_id_str)
    if not config:
        logger.error(f"错误: 未找到 GitLab 项目 ID {project_id_str} 的配置。")
        abort(404,
              f"未找到 GitLab 项目 {project_id_str} 的配置。请通过 /config/gitlab/project 端点进行配置。")

    webhook_secret = config.get('secret')
    access_token = config.get('token')

    if not verify_gitlab_signature(request, webhook_secret):
        abort(401, "GitLab signature verification failed.")

    event_type = request.headers.get('X-Gitlab-Event')
    if event_type != "Merge Request Hook":
        logger.info(f"GitLab: 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    mr_action = mr_attrs.get('action')
    mr_state = mr_attrs.get('state')

    if mr_action in ['close', 'merge'] or mr_state in ['closed', 'merged']:
        mr_iid_str = str(mr_iid)
        logger.info(f"GitLab: MR {project_id_str}#{mr_iid_str} 已 {mr_action if mr_action in ['close', 'merge'] else mr_state}。正在清理 Redis 记录...")
        remove_processed_commit_entries_for_pr_mr('gitlab', project_id_str, mr_iid_str)
        return f"MR {mr_iid_str} 已 {mr_action if mr_action in ['close', 'merge'] else mr_state}，记录已清理。", 200

    if mr_state not in ['opened', 'reopened'] and mr_action != 'update':
        logger.info(f"GitLab: 忽略 MR 操作 '{mr_action}' 或状态 '{mr_state}' (非审查触发条件)。")
        return "MR 操作/状态已忽略 (非审查触发条件)", 200

    logger.info(f"--- 收到 GitLab Merge Request Hook (详细审查) ---")
    logger.info(f"项目 ID: {project_id_str}, MR IID: {mr_iid}, Head SHA (来自负载): {head_sha_payload}")

    if head_sha_payload and is_commit_processed('gitlab', project_id_str, str(mr_iid), head_sha_payload):
        logger.info(f"GitLab (详细审查): MR {project_id_str}#{mr_iid} 的提交 {head_sha_payload} 已处理。跳过。")
        return "提交已处理", 200

    # 调用提取出来的核心处理逻辑函数 (异步执行)
    executor.submit(
        _process_gitlab_detailed_payload,
        access_token=access_token,
        project_id_str=project_id_str,
        mr_iid=mr_iid,
        head_sha_payload=head_sha_payload,
        project_data=project_data,
        mr_attrs=mr_attrs,
        project_web_url=project_web_url,
        mr_title=mr_title,
        mr_url=mr_url,
        project_name_from_payload=project_name_from_payload
    )

    logger.info(f"GitLab (详细审查): MR {project_id_str}#{mr_iid} 的处理任务已提交到后台执行。")
    return jsonify({"message": "GitLab Detailed Webhook processing task accepted."}), 202
