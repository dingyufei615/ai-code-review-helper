from flask import request, abort, jsonify
import json
import logging
from api.app_factory import app, executor, handle_async_task_exception
from api.core_config import (
    codeup_repo_configs, app_configs,
    is_commit_processed, mark_commit_as_processed, remove_processed_commit_entries_for_pr_mr
)
from api.utils import verify_codeup_signature
from api.services.vcs_service import (
    get_codeup_mr_changes, add_codeup_mr_comment,
    add_codeup_mr_general_comment  # Used for final summary
)
from api.services.llm_review_detailed_service import get_openai_code_review
from api.services.notification_service import send_notifications
from api.services.common_service import get_final_summary_comment_text
from .webhook_helpers import _save_review_results_and_log

logger = logging.getLogger(__name__)


def _get_wecom_summary_line(num_reviews, vcs_type):
    """为企业微信通知生成摘要行。"""
    entity_name = "Merge Request"
    if num_reviews == 0:
        return "AI Code Review 已完成，所有检查均已通过，无审查建议。"
    else:
        return f"AI Code Review 已完成，共生成 {num_reviews} 条审查建议。请前往 {entity_name} 查看详情。"


def _process_codeup_detailed_payload(access_token, organization_id, repository_id, local_id, head_sha_payload, 
                                   mr_data, domain, mr_title, mr_url, repo_name_from_payload):
    """实际处理 Codeup 详细审查的核心逻辑。"""
    logger.info("Codeup (详细审查): 正在获取并解析 MR 变更...")
    structured_changes, position_info = get_codeup_mr_changes(organization_id, repository_id, local_id, access_token, domain)

    if position_info is None: 
        position_info = {}
    if head_sha_payload and not position_info.get("source_commit_id"):
        position_info["source_commit_id"] = head_sha_payload
        logger.info(f"Codeup (详细审查): 使用来自 webhook 负载的 head_sha: {head_sha_payload}")

    if structured_changes is None:
        logger.warning("Codeup (详细审查): 获取或解析 diff 内容失败。中止审查。")
        return
    if not structured_changes:
        logger.info("Codeup (详细审查): 解析后未检测到变更。无需审查。")
        _save_review_results_and_log(
            vcs_type='codeup', identifier=repository_id, pr_mr_id=str(local_id),
            commit_sha=head_sha_payload, review_json_string=json.dumps([]),
            repo_name_for_codeup=repo_name_from_payload
        )
        mark_commit_as_processed('codeup', repository_id, str(local_id), head_sha_payload)
        return

    logger.info(f"Codeup (详细审查): 正在对 {len(structured_changes)} 个文件进行 LLM 审查...")
    all_reviews = get_openai_code_review(structured_changes)

    if all_reviews is None:
        logger.warning("Codeup (详细审查): LLM 审查失败。中止。")
        return

    logger.info(f"Codeup (详细审查): LLM 审查完成，共生成 {len(all_reviews)} 条审查意见。")

    # 保存审查结果到 Redis
    _save_review_results_and_log(
        vcs_type='codeup', identifier=repository_id, pr_mr_id=str(local_id),
        commit_sha=head_sha_payload, review_json_string=json.dumps(all_reviews),
        repo_name_for_codeup=repo_name_from_payload
    )

    # 添加评论到 MR
    successful_comments = 0
    for review in all_reviews:
        if add_codeup_mr_comment(organization_id, repository_id, local_id, access_token, domain, review, position_info):
            successful_comments += 1
        else:
            logger.warning(f"Codeup (详细审查): 添加评论失败，文件: {review.get('file', 'Unknown')}")

    logger.info(f"Codeup (详细审查): 成功添加了 {successful_comments}/{len(all_reviews)} 条评论。")

    # 添加最终摘要评论
    if all_reviews:
        final_summary_comment = get_final_summary_comment_text(len(all_reviews), 'codeup')
        add_codeup_mr_general_comment(organization_id, repository_id, local_id, access_token, domain, final_summary_comment)

    # 发送通知
    wecom_summary_line = _get_wecom_summary_line(len(all_reviews), 'codeup')
    send_notifications(
        summary_line=wecom_summary_line,
        pr_mr_title=mr_title,
        pr_mr_url=mr_url,
        repo_name=repo_name_from_payload or f"Codeup Repo {repository_id}",
        vcs_type='codeup'
    )

    # 标记提交为已处理
    mark_commit_as_processed('codeup', repository_id, str(local_id), head_sha_payload)
    logger.info("Codeup (详细审查): 处理完成。")


@app.route('/codeup_webhook', methods=['POST'])
def codeup_webhook():
    """处理 Codeup Webhook 请求 (详细审查)"""
    try:
        payload_data = request.get_json()
        if payload_data is None:
            raise ValueError("请求体为空或非有效 JSON")
    except Exception as e:
        logger.error(f"解析 Codeup JSON 负载时出错: {e}")
        abort(400, "无效的 JSON 负载")

    # 添加调试日志来查看实际的负载结构
    logger.info(f"收到 Codeup Webhook 负载 (详细审查): {json.dumps(payload_data, indent=2, ensure_ascii=False)}")

    # 尝试多种方式获取 repository ID
    repository_id = None
    repository_info = {}

    # 方式1: payload_data.repository.id (类似 GitLab)
    if 'repository' in payload_data:
        repository_info = payload_data.get('repository', {})
        repository_id = repository_info.get('id')
        logger.info(f"尝试从 repository.id 获取: {repository_id}")

    # 方式2: payload_data.project.id (可能的字段名)
    if not repository_id and 'project' in payload_data:
        project_info = payload_data.get('project', {})
        repository_id = project_info.get('id')
        repository_info = project_info  # 使用 project 信息
        logger.info(f"尝试从 project.id 获取: {repository_id}")

    # 方式3: 直接从顶级字段获取
    if not repository_id:
        repository_id = payload_data.get('repository_id') or payload_data.get('project_id')
        logger.info(f"尝试从顶级字段获取: {repository_id}")

    if not repository_id:
        logger.error(f"错误: Codeup 负载中缺少 repository ID。负载键: {list(payload_data.keys())}")
        logger.error(f"repository 字段内容: {payload_data.get('repository', 'N/A')}")
        logger.error(f"project 字段内容: {payload_data.get('project', 'N/A')}")
        abort(400, "Codeup 负载中缺少 repository ID")

    repository_id_str = str(repository_id)
    config = codeup_repo_configs.get(repository_id_str)
    if not config:
        logger.error(f"错误: 未找到 Codeup 仓库 {repository_id_str} 的配置。")
        abort(404, f"未找到 Codeup 仓库 {repository_id_str} 的配置。请通过 /config/codeup/repo 端点进行配置。")

    webhook_secret = config.get('secret')
    access_token = config.get('token')
    organization_id = config.get('organization_id')
    domain = config.get('domain')

    if not verify_codeup_signature(request, webhook_secret):
        abort(401, "Codeup signature verification failed.")

    event_type = request.headers.get('X-Codeup-Event')
    if event_type != "Merge Request Hook":
        logger.info(f"Codeup: 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    # 提取 MR 信息
    mr_data = payload_data.get('merge_request', {})
    if not mr_data:
        logger.error("错误: Codeup 负载中缺少 merge_request 数据。")
        abort(400, "Codeup 负载中缺少 merge_request 数据")

    action = payload_data.get('action')
    mr_state = mr_data.get('state')
    local_id = mr_data.get('iid') or mr_data.get('id')
    head_sha_payload = mr_data.get('source_commit_id')

    if action == 'close':
        logger.info(f"Codeup (详细审查): MR {repository_id_str}#{local_id} 已关闭。正在清理已处理的 commit 记录...")
        remove_processed_commit_entries_for_pr_mr('codeup', repository_id_str, str(local_id))
        return f"MR {local_id} 已关闭，详细审查相关记录已清理。", 200

    if mr_state != 'opened' or action not in ['open', 'reopen', 'update']:
        logger.info(f"Codeup: 忽略 MR 操作 '{action}' 或状态 '{mr_state}'。")
        return "MR 操作/状态已忽略", 200

    mr_title = mr_data.get('title', 'Unknown Title')
    mr_url = mr_data.get('web_url', '')
    repo_name_from_payload = repository_info.get('name', '')

    logger.info(f"--- 收到 Codeup Merge Request Hook (详细审查) ---")
    logger.info(f"仓库 ID: {repository_id_str}, MR IID: {local_id}, Head SHA (来自负载): {head_sha_payload}")

    if head_sha_payload and is_commit_processed('codeup', repository_id_str, str(local_id), head_sha_payload):
        logger.info(f"Codeup (详细审查): MR {repository_id_str}#{local_id} 的提交 {head_sha_payload} 已处理。跳过。")
        return "提交已处理", 200

    # 调用核心处理逻辑函数 (异步执行)
    future = executor.submit(
        _process_codeup_detailed_payload,
        access_token=access_token,
        organization_id=organization_id,
        repository_id=repository_id_str,
        local_id=local_id,
        head_sha_payload=head_sha_payload,
        mr_data=mr_data,
        domain=domain,
        mr_title=mr_title,
        mr_url=mr_url,
        repo_name_from_payload=repo_name_from_payload
    )
    future.add_done_callback(handle_async_task_exception)

    logger.info(f"Codeup (详细审查): MR {repository_id_str}#{local_id} 的处理任务已提交到后台执行。")
    return jsonify({"message": "Codeup Detailed Webhook processing task accepted."}), 202
