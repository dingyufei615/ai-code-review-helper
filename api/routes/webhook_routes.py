from flask import request, abort, jsonify
import json
import logging
from api.app_factory import app
from api.core_config import (
    github_repo_configs, gitlab_project_configs, app_configs,
    is_commit_processed, mark_commit_as_processed, remove_processed_commit_entries_for_pr_mr,
    save_review_results # 新增导入
)
from api.utils import verify_github_signature, verify_gitlab_signature
from api.services.vcs_service import (
    get_github_pr_changes, add_github_pr_comment, 
    get_gitlab_mr_changes, add_gitlab_mr_comment,
    get_github_pr_data_for_coarse_review, add_github_pr_coarse_comment,
    get_gitlab_mr_data_for_coarse_review, add_gitlab_mr_coarse_comment
)
from api.services.llm_service import get_openai_code_review, get_openai_code_review_coarse
from api.services.notification_service import send_to_wecom_bot

logger = logging.getLogger(__name__)


# --- Helper Functions ---
def _save_review_results_and_log(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str, review_json_string: str, project_name_for_gitlab: str = None):
    """统一保存审查结果到 Redis 并记录日志。"""
    if not commit_sha:
        logger.warning(f"警告: {vcs_type.capitalize()} {identifier}#{pr_mr_id} 的 commit_sha 为空。无法保存审查结果。")
        return

    try:
        # 统一处理所有 github* 和 gitlab* 类型
        if vcs_type.startswith('github'): # 包括 'github' 和 'github_general'
            save_review_results(vcs_type, identifier, pr_mr_id, commit_sha, review_json_string)
        elif vcs_type.startswith('gitlab'): # 包括 'gitlab' 和 'gitlab_general'
            save_review_results(vcs_type, identifier, pr_mr_id, commit_sha, review_json_string, project_name=project_name_for_gitlab)
        else:
            logger.error(f"未知的 VCS 类型 '{vcs_type}'，无法保存审查结果。")
            return
        
        # 日志记录已在 save_review_results 内部处理，这里可以不再重复记录成功信息，
        # 但保留一个简短的调用日志可能有用。
        logger.info(f"{vcs_type.capitalize()}: 审查结果保存调用完成，针对 commit {commit_sha}。")

    except Exception as e:
        # save_review_results 内部已经有错误日志，这里可以捕获更通用的错误或决定是否需要额外日志
        logger.error(f"调用 save_review_results 时发生意外错误 ({vcs_type} {identifier}#{pr_mr_id}, Commit: {commit_sha}): {e}")

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

    # 如果 PR 被关闭（且未合并，因为合并的 PR 也是 closed 状态）或已合并，则清理 Redis 记录
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

    logger.info(f"--- 收到 GitHub Pull Request Hook ---")
    logger.info(f"仓库: {repo_full_name}, PR 编号: {pull_number}, 标题: {pr_title}")
    logger.info(f"状态: {pr_state}, 操作: {action}, Head SHA: {head_sha}, PR URL: {pr_html_url}")

    # 检查此 commit 是否已被处理
    if head_sha and is_commit_processed('github', repo_full_name, str(pull_number), head_sha):
        logger.info(f"GitHub: PR {repo_full_name}#{pull_number} 的提交 {head_sha} 已处理。跳过。")
        return "提交已处理", 200

    logger.info("GitHub: 正在获取并解析 PR 变更...")
    structured_changes = get_github_pr_changes(owner, repo_name, pull_number, access_token)

    if structured_changes is None:
        logger.warning("GitHub: 获取或解析 diff 内容失败。中止审查。")
        return "获取/解析 diff 失败", 200
    if not structured_changes:
        logger.info("GitHub: 解析后未检测到变更。无需审查。")
        return "未检测到变更", 200

    logger.info(f'GitHub: 正在发送变更给 {app_configs.get("OPENAI_MODEL", "gpt-4o")} 进行审查...')
    review_result_json = get_openai_code_review(structured_changes)

    logger.info("--- GitHub: AI 代码审查结果 (JSON) ---")
    logger.info(f"{review_result_json}")
    logger.info("--- GitHub 审查 JSON 结束 ---")

    # 使用新的辅助函数保存审查结果
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
        logger.info(f"GitHub: 从 JSON 成功解析 {len(reviews)} 个审查项。")
    except json.JSONDecodeError as e:
        logger.error(f"GitHub: 解析审查结果 JSON 时出错: {e}。原始数据: {review_result_json[:500]}")

    if reviews:
        logger.info(f"GitHub: 尝试向 PR 添加 {len(reviews)} 条审查评论...")
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
                logger.warning(f"GitHub: 跳过无效的审查项: {review}");
                comments_failed += 1
        logger.info(f"GitHub: 添加评论完成: {comments_added} 成功, {comments_failed} 失败。")
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
        logger.info("GitHub: 正在发送摘要通知到企业微信机器人...")
        review_summary_line = _get_wecom_summary_line(len(reviews), 'github')
        summary_content = f"""**AI代码审查完成 (GitHub)**

> 仓库: [{repo_full_name}]({repo_web_url})
> PR: [{pr_title}]({pr_html_url}) (#{pull_number})
> 分支: `{pr_source_branch}` → `{pr_target_branch}`

{review_summary_line}
"""
        send_to_wecom_bot(summary_content)

    # 标记此 commit 为已处理
    if head_sha:  # 确保 head_sha 存在
        mark_commit_as_processed('github', repo_full_name, str(pull_number), head_sha)
    else:
        logger.warning(f"警告: GitHub PR {repo_full_name}#{pull_number} 的 head_sha 为空。无法标记为已处理。")

    # 添加最终总结评论
    model_name = app_configs.get("OPENAI_MODEL", "gpt-4o")
    final_comment_text = f"本次AI代码审查已完成，审核模型:「{model_name}」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
    add_github_pr_coarse_comment(owner, repo_name, pull_number, access_token, final_comment_text)

    return "GitHub Webhook 处理成功", 200


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
    project_name_from_payload = project_data.get('name') # 新增：提取项目名称
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
    if event_type != "Merge Request Hook":  # GitLab uses "Merge Request Hook" or "Note Hook" etc.
        logger.info(f"GitLab: 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    mr_action = mr_attrs.get('action')
    mr_state = mr_attrs.get('state')
    # Supported actions: open, reopen, update (when new commits are pushed)
    # GitLab MR actions: open, reopen, update, close, merge, approve, unapprove
    # We are interested in 'open', 'reopened', and 'update' (if source branch changes)
    # 'update' action is triggered when new commits are pushed to the source branch of an open MR.
    # GitLab MR actions: 'open', 'reopen', 'update', 'close', 'merge', 'approve', 'unapprove'
    # 'state' can be 'opened', 'closed', 'merged', 'locked'

    # 如果 MR 被关闭或合并，则清理 Redis 记录
    if mr_action in ['close', 'merge'] or mr_state in ['closed', 'merged']:
        mr_iid_str = str(mr_iid)
        logger.info(f"GitLab: MR {project_id_str}#{mr_iid_str} 已 {mr_action if mr_action in ['close', 'merge'] else mr_state}。正在清理 Redis 记录...")
        remove_processed_commit_entries_for_pr_mr('gitlab', project_id_str, mr_iid_str)
        return f"MR {mr_iid_str} 已 {mr_action if mr_action in ['close', 'merge'] else mr_state}，记录已清理。", 200

    if mr_state not in ['opened', 'reopened'] and mr_action != 'update': # 确保只处理开放和更新的MR进行审查
        logger.info(f"GitLab: 忽略 MR 操作 '{mr_action}' 或状态 '{mr_state}' (非审查触发条件)。")
        return "MR 操作/状态已忽略 (非审查触发条件)", 200

    # For 'update' action, ensure it's not just a metadata update without new commits.
    # The 'last_commit' object changes when new code is pushed.
    # A simple check is if 'oldrev' is present and different from 'last_commit.id' for 'update' action.
    # However, GitLab's 'update' action for MR hooks usually implies a new commit.
    # If 'action' is 'update', it means commits were pushed or MR was rebased.

    logger.info(f"--- 收到 GitLab Merge Request Hook ---")
    logger.info(f"项目 ID: {project_id_str}, MR IID: {mr_iid}, 标题: {mr_title}")
    logger.info(f"状态: {mr_state}, 操作: {mr_action}, MR URL: {mr_url}, Head SHA (来自负载): {head_sha_payload}")

    # 检查此 commit 是否已被处理
    if head_sha_payload and is_commit_processed('gitlab', project_id_str, str(mr_iid), head_sha_payload):
        logger.info(f"GitLab: MR {project_id_str}#{mr_iid} 的提交 {head_sha_payload} 已处理。跳过。")
        return "提交已处理", 200

    logger.info("GitLab: 正在获取并解析 MR 变更...")
    structured_changes, position_info = get_gitlab_mr_changes(project_id_str, mr_iid, access_token)

    if position_info is None: position_info = {}
    if head_sha_payload and not position_info.get("head_sha"):
        position_info["head_sha"] = head_sha_payload
        logger.info(f"GitLab: 使用来自 webhook 负载的 head_sha: {head_sha_payload}")
    if not all(k in position_info for k in ["base_sha", "start_sha", "head_sha"]):
        logger.warning("GitLab: 警告: 缺少用于精确定位评论的关键提交 SHA 信息。")

    if structured_changes is None:
        logger.warning("GitLab: 获取或解析 diff 内容失败。中止审查。")
        return "获取/解析 diff 失败", 200
    if not structured_changes:
        logger.info("GitLab: 解析后未检测到变更。无需审查。")
        return "未检测到变更", 200

    logger.info(f'GitHub: 正在发送变更给 {app_configs.get("OPENAI_MODEL", "gpt-4o")} 进行审查...')
    review_result_json = get_openai_code_review(structured_changes)

    logger.info("--- GitLab: AI 代码审查结果 (JSON) ---")
    logger.info(f"{review_result_json}")
    logger.info("--- GitLab 审查 JSON 结束 ---")

    # 确定用于保存的 commit SHA
    current_commit_sha_for_saving = head_sha_payload
    if not current_commit_sha_for_saving and position_info and position_info.get("head_sha"):
        current_commit_sha_for_saving = position_info.get("head_sha")
        logger.info(f"GitLab: 使用来自 position_info 的 head_sha ({current_commit_sha_for_saving}) 进行后续操作。")
    
    # 使用新的辅助函数保存审查结果
    _save_review_results_and_log(
        vcs_type='gitlab',
        identifier=project_id_str,
        pr_mr_id=str(mr_iid),
        commit_sha=current_commit_sha_for_saving, # 使用已确定的 SHA
        review_json_string=review_result_json,
        project_name_for_gitlab=project_name_from_payload
    )

    reviews = []
    try:
        parsed_data = json.loads(review_result_json)
        if isinstance(parsed_data, list): reviews = parsed_data
        logger.info(f"GitLab: 从 JSON 成功解析 {len(reviews)} 个审查项。")
    except json.JSONDecodeError as e:
        logger.error(f"GitLab: 解析审查结果 JSON 时出错: {e}。原始数据: {review_result_json[:500]}")

    if reviews:
        logger.info(f"GitLab: 尝试向 MR 添加 {len(reviews)} 条审查评论...")
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
                logger.warning(f"GitLab: 跳过无效的审查项: {review}");
                comments_failed += 1
        logger.info(f"GitLab: 添加评论完成: {comments_added} 成功, {comments_failed} 失败。")
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
        logger.info("GitLab: 正在发送摘要通知到企业微信机器人...")
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

    # 标记此 commit 为已处理
    # 使用从 webhook payload 中获取的 head_sha_payload，因为它更直接对应于触发事件的 commit。
    # position_info 中的 head_sha 是从 MR versions API 获取的，理论上应该一致，但 payload 的更可靠。
    if head_sha_payload:
        mark_commit_as_processed('gitlab', project_id_str, str(mr_iid), head_sha_payload)
    elif position_info and position_info.get("head_sha"):  # Fallback if somehow payload SHA was missing
        logger.warning(
            f"警告: head_sha_payload 为空，使用来自 position_info 的 head_sha 进行标记处理: {position_info.get('head_sha')}")
        mark_commit_as_processed('gitlab', project_id_str, str(mr_iid), position_info.get("head_sha"))

    # 添加最终总结评论
    model_name = app_configs.get("OPENAI_MODEL", "gpt-4o")
    final_comment_text = f"本次AI代码审查已完成，审核模型:「{model_name}」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
    add_gitlab_mr_coarse_comment(project_id_str, mr_iid, access_token, final_comment_text)

    return "GitLab Webhook 处理成功", 200


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
        # General reviews might use a different Redis key pattern if needed, but for processed commits, it's the same.
        # For now, assume general reviews don't have separate "results" to clean beyond commit tracking.
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

    logger.info("GitHub (通用审查): 正在获取 PR 数据 (diffs 和文件内容)...")
    file_data_list = get_github_pr_data_for_coarse_review(owner, repo_name, pull_number, access_token, pr_data)

    if file_data_list is None:
        logger.warning("GitHub (通用审查): 获取 PR 数据失败。中止审查。")
        return "获取 PR 数据失败", 200
    if not file_data_list:
        logger.info("GitHub (通用审查): 未检测到文件变更或数据。无需审查。")
        _save_review_results_and_log( # 保存空列表表示已处理且无内容
            vcs_type='github_general', identifier=repo_full_name, pr_mr_id=str(pull_number),
            commit_sha=head_sha, review_json_string=json.dumps([])
        )
        return "未检测到变更", 200

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
    model_name = app_configs.get("OPENAI_MODEL", "gpt-4o")
    final_comment_text = f"本次AI代码审查已完成，审核模型:「{model_name}」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
    add_github_pr_coarse_comment(owner, repo_name, pull_number, access_token, final_comment_text)

    return "GitHub General Webhook 处理成功", 200


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

    # For GitLab, we need position_info (base_sha, head_sha) to fetch content correctly.
    # Try to get it similar to the detailed review, but it's mainly for SHAs here.
    # The get_gitlab_mr_data_for_coarse_review will need these.
    # A simplified way to get SHAs if full position_info isn't built yet:
    temp_position_info = {
        "base_commit_sha": mr_attrs.get("diff_base_sha") or mr_attrs.get("base_commit_sha"), # diff_base_sha is often available
        "head_commit_sha": head_sha_payload, # From payload
        "start_commit_sha": mr_attrs.get("start_commit_sha") # Less critical for content fetching
    }
    # If base_commit_sha is still missing, it's problematic.
    # The get_gitlab_mr_changes (original) fetches versions to get this. We might need a lightweight version getter.
    # For now, assume mr_attrs or a pre-fetched position_info (if we adapt flow) provides it.
    # The get_gitlab_mr_data_for_coarse_review function will need to be robust.

    # Let's try to get more robust position_info including latest_version_id
    # This part is similar to the original webhook, to ensure we have base/head SHAs
    _, version_derived_position_info = get_gitlab_mr_changes(project_id_str, mr_iid, access_token) # Call original to get position_info
    
    final_position_info = temp_position_info
    if version_derived_position_info:
        final_position_info["base_commit_sha"] = version_derived_position_info.get("base_sha", temp_position_info["base_commit_sha"])
        final_position_info["head_commit_sha"] = version_derived_position_info.get("head_sha", temp_position_info["head_commit_sha"])
        final_position_info["latest_version_id"] = version_derived_position_info.get("id") # if get_gitlab_mr_changes returns version id

    if not final_position_info.get("base_commit_sha") or not final_position_info.get("head_commit_sha"):
         logger.error(f"GitLab (通用审查) MR {project_id_str}#{mr_iid}: 无法确定 base_sha 或 head_sha。中止。")
         return "无法确定提交SHA", 500


    logger.info("GitLab (通用审查): 正在获取 MR 数据 (diffs 和文件内容)...")
    file_data_list = get_gitlab_mr_data_for_coarse_review(project_id_str, mr_iid, access_token, mr_attrs, final_position_info)

    if file_data_list is None:
        logger.warning("GitLab (通用审查): 获取 MR 数据失败。中止审查。")
        return "获取 MR 数据失败", 200
    if not file_data_list:
        logger.info("GitLab (通用审查): 未检测到文件变更或数据。无需审查。")
        _save_review_results_and_log( # 保存空列表表示已处理且无内容
            vcs_type='gitlab_general', identifier=project_id_str, pr_mr_id=str(mr_iid),
            commit_sha=final_position_info.get("head_commit_sha", head_sha_payload), # Use determined SHA
            review_json_string=json.dumps([]), project_name_for_gitlab=project_name_from_payload
        )
        return "未检测到变更", 200

    aggregated_coarse_reviews_for_storage = []
    files_with_issues_details = [] # {file_path: str, issues_text: str}
    current_commit_sha_for_ops = final_position_info.get("head_commit_sha", head_sha_payload)

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
    model_name = app_configs.get("OPENAI_MODEL", "gpt-4o")
    final_comment_text = f"本次AI代码审查已完成，审核模型:「{model_name}」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
    add_gitlab_mr_coarse_comment(project_id_str, mr_iid, access_token, final_comment_text)

    return "GitLab General Webhook 处理成功", 200
