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
    all_reviews_json = get_openai_code_review(structured_changes)

    if all_reviews_json is None:
        logger.warning("Codeup (详细审查): LLM 审查失败。中止。")
        return

    # 解析 JSON 字符串为 Python 列表
    try:
        if isinstance(all_reviews_json, str):
            all_reviews = json.loads(all_reviews_json)
        else:
            all_reviews = all_reviews_json

        if not isinstance(all_reviews, list):
            logger.warning(f"Codeup (详细审查): LLM 审查结果格式异常，期望列表但得到: {type(all_reviews)}")
            all_reviews = []
    except json.JSONDecodeError as e:
        logger.error(f"Codeup (详细审查): 解析 LLM 审查结果 JSON 失败: {e}")
        logger.error(f"原始数据: {all_reviews_json}")
        all_reviews = []

    logger.info(f"Codeup (详细审查): LLM 审查完成，共生成 {len(all_reviews)} 条审查意见。")

    # 保存审查结果到 Redis
    _save_review_results_and_log(
        vcs_type='codeup', identifier=repository_id, pr_mr_id=str(local_id),
        commit_sha=head_sha_payload, review_json_string=json.dumps(all_reviews),
        repo_name_for_codeup=repo_name_from_payload
    )

    # 添加评论到 MR
    successful_comments = 0
    for i, review in enumerate(all_reviews):
        try:
            logger.debug(f"Codeup (详细审查): 处理第 {i+1} 条审查意见: {type(review)}")
            if add_codeup_mr_comment(organization_id, repository_id, local_id, access_token, domain, review, position_info):
                successful_comments += 1
            else:
                # 处理 review 可能是字符串或字典的情况
                file_info = review.get('file', 'Unknown') if isinstance(review, dict) else 'Unknown'
                logger.warning(f"Codeup (详细审查): 添加评论失败，文件: {file_info}")
        except Exception as e:
            file_info = review.get('file', 'Unknown') if isinstance(review, dict) else 'Unknown'
            logger.error(f"Codeup (详细审查): 处理审查意见时发生异常，文件: {file_info}, 错误: {e}")
            logger.error(f"审查意见内容: {review}")

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

    # 从 Codeup Webhook 负载中获取 repository ID
    repository_info = payload_data.get('repository', {})
    object_attributes = payload_data.get('object_attributes', {})

    # Codeup 的 repository ID 在 object_attributes.project_id 字段中
    repository_id = object_attributes.get('project_id')
    logger.info(f"从 object_attributes.project_id 获取 repository ID: {repository_id}")

    if not repository_id:
        logger.error(f"错误: Codeup 负载中缺少 object_attributes.project_id。")
        logger.error(f"object_attributes 内容: {object_attributes}")
        abort(400, "Codeup 负载中缺少 object_attributes.project_id")

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
    object_kind = payload_data.get('object_kind')
    if object_kind != "merge_request":
        logger.info(f"Codeup: 忽略事件类型: {object_kind}")
        return "事件已忽略", 200

    # 提取 MR 信息 - Codeup 的 MR 数据在 object_attributes 中
    mr_data = object_attributes
    if not mr_data:
        logger.error("错误: Codeup 负载中缺少 object_attributes 数据。")
        abort(400, "Codeup 负载中缺少 object_attributes 数据")

    action = mr_data.get('action')
    mr_state = mr_data.get('state')
    local_id = mr_data.get('local_id')  # Codeup 使用 local_id
    head_sha_payload = mr_data.get('last_commit', {}).get('id')  # 从 last_commit 获取 commit ID

    if action == 'close':
        logger.info(f"Codeup (详细审查): MR {repository_id_str}#{local_id} 已关闭。正在清理已处理的 commit 记录...")
        remove_processed_commit_entries_for_pr_mr('codeup', repository_id_str, str(local_id))
        return f"MR {local_id} 已关闭，详细审查相关记录已清理。", 200

    if mr_state != 'opened' or action not in ['open', 'reopen', 'update']:
        logger.info(f"Codeup: 忽略 MR 操作 '{action}' 或状态 '{mr_state}'。")
        return "MR 操作/状态已忽略", 200

    mr_title = mr_data.get('title', 'Unknown Title')
    mr_url = mr_data.get('url', '')  # Codeup 使用 url 字段
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
