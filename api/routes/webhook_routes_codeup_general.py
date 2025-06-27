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
    get_codeup_mr_data_for_general_review, add_codeup_mr_general_comment
)
from api.services.llm_service import get_openai_code_review_general
from api.services.notification_service import send_notifications
from api.services.common_service import get_final_summary_comment_text
from .webhook_helpers import _save_review_results_and_log

logger = logging.getLogger(__name__)


def _process_codeup_general_payload(access_token, organization_id, repository_id, local_id, mr_data, head_sha_payload, 
                                  current_commit_sha_for_ops, repo_name_from_payload, domain, mr_title, mr_url):
    """实际处理 Codeup 通用审查的核心逻辑。"""
    logger.info("Codeup (通用审查): 正在获取 MR 数据 (diffs 和文件内容)...")
    file_data_list = get_codeup_mr_data_for_general_review(organization_id, repository_id, local_id, access_token, domain, mr_data)

    if file_data_list is None:
        logger.warning("Codeup (通用审查): 获取 MR 数据失败。中止审查。")
        return
    if not file_data_list:
        logger.info("Codeup (通用审查): 未检测到文件变更。无需审查。")
        _save_review_results_and_log(
            vcs_type='codeup_general', identifier=repository_id, pr_mr_id=str(local_id),
            commit_sha=current_commit_sha_for_ops, review_json_string=json.dumps([]),
            repo_name_for_codeup=repo_name_from_payload
        )
        mark_commit_as_processed('codeup_general', repository_id, str(local_id), current_commit_sha_for_ops)
        return

    logger.info(f"Codeup (通用审查): 正在对 {len(file_data_list)} 个文件进行 LLM 审查...")

    files_with_issues_details = []
    all_review_texts = []

    for file_item in file_data_list:
        current_file_path = file_item.get("file_path", "Unknown File")
        logger.info(f"Codeup (通用审查): 正在对文件 {current_file_path} 进行 LLM 审查...")
        review_text_for_file = get_openai_code_review_general(file_item)

        logger.info(f"Codeup (通用审查): 文件 {current_file_path} 的 LLM 原始输出:\n{review_text_for_file}")

        if review_text_for_file and review_text_for_file.strip() and \
           "未发现严重问题" not in review_text_for_file and \
           "没有修改建议" not in review_text_for_file and \
           "OpenAI client is not available" not in review_text_for_file and \
           "Error serializing input data" not in review_text_for_file:
            
            logger.info(f"Codeup (通用审查): 文件 {current_file_path} 发现问题。正在添加评论...")
            comment_text_for_mr = f"**AI 审查意见 (文件: `{current_file_path}`)**\n\n{review_text_for_file}"
            add_codeup_mr_general_comment(organization_id, repository_id, local_id, access_token, domain, comment_text_for_mr)
            
            files_with_issues_details.append({"file": current_file_path, "issues": review_text_for_file})
            all_review_texts.append(review_text_for_file)
        else:
            logger.info(f"Codeup (通用审查): 文件 {current_file_path} 未发现问题或审查失败。")

    # 保存审查结果到 Redis
    _save_review_results_and_log(
        vcs_type='codeup_general', identifier=repository_id, pr_mr_id=str(local_id),
        commit_sha=current_commit_sha_for_ops, review_json_string=json.dumps(files_with_issues_details),
        repo_name_for_codeup=repo_name_from_payload
    )

    # 添加最终摘要评论
    if files_with_issues_details:
        final_summary_comment = get_final_summary_comment_text(len(files_with_issues_details), 'codeup_general')
        add_codeup_mr_general_comment(organization_id, repository_id, local_id, access_token, domain, final_summary_comment)

    # 发送通知
    entity_name = "Merge Request"
    if len(files_with_issues_details) == 0:
        wecom_summary_line = "AI Code Review 已完成，所有检查均已通过，无审查建议。"
    else:
        wecom_summary_line = f"AI Code Review 已完成，共生成 {len(files_with_issues_details)} 条审查建议。请前往 {entity_name} 查看详情。"

    send_notifications(
        summary_line=wecom_summary_line,
        pr_mr_title=mr_title,
        pr_mr_url=mr_url,
        repo_name=repo_name_from_payload or f"Codeup Repo {repository_id}",
        vcs_type='codeup_general'
    )

    # 标记提交为已处理
    mark_commit_as_processed('codeup_general', repository_id, str(local_id), current_commit_sha_for_ops)
    logger.info("Codeup (通用审查): 处理完成。")


@app.route('/codeup_webhook_general', methods=['POST'])
def codeup_webhook_general():
    """处理 Codeup Webhook 请求 (粗粒度审查)"""
    try:
        payload_data = request.get_json()
        if payload_data is None: 
            raise ValueError("请求体为空或非有效 JSON")
    except Exception as e:
        logger.error(f"解析 Codeup JSON 负载时出错 (粗粒度): {e}")
        abort(400, "无效的 JSON 负载")

    # 从 payload 中提取仓库信息
    repository_info = payload_data.get('repository', {})
    repository_id = repository_info.get('id')
    
    if not repository_id:
        logger.error("错误: Codeup 负载中缺少 repository.id (粗粒度)。")
        abort(400, "Codeup 负载中缺少 repository.id")

    repository_id_str = str(repository_id)
    config = codeup_repo_configs.get(repository_id_str)
    if not config:
        logger.error(f"错误: 未找到 Codeup 仓库 {repository_id_str} 的配置 (粗粒度)。")
        abort(404, f"未找到 Codeup 仓库 {repository_id_str} 的配置。")

    webhook_secret = config.get('secret')
    access_token = config.get('token')
    organization_id = config.get('organization_id')
    domain = config.get('domain')

    if not verify_codeup_signature(request, webhook_secret):
        abort(401, "Codeup signature verification failed (general).")

    event_type = request.headers.get('X-Codeup-Event')
    if event_type != "Merge Request Hook":
        logger.info(f"Codeup (粗粒度): 忽略事件类型: {event_type}")
        return "事件已忽略", 200

    # 提取 MR 信息
    mr_data = payload_data.get('merge_request', {})
    if not mr_data:
        logger.error("错误: Codeup 负载中缺少 merge_request 数据 (粗粒度)。")
        abort(400, "Codeup 负载中缺少 merge_request 数据")

    action = payload_data.get('action')
    mr_state = mr_data.get('state')
    local_id = mr_data.get('iid') or mr_data.get('id')
    head_sha_payload = mr_data.get('source_commit_id')

    if action == 'close':
        logger.info(f"Codeup (通用审查): MR {repository_id_str}#{local_id} 已关闭。正在清理已处理的 commit 记录...")
        remove_processed_commit_entries_for_pr_mr('codeup_general', repository_id_str, str(local_id))
        return f"MR {local_id} 已关闭，通用审查相关记录已清理。", 200

    if mr_state != 'opened' or action not in ['open', 'reopen', 'update']:
        logger.info(f"Codeup (粗粒度): 忽略 MR 操作 '{action}' 或状态 '{mr_state}'。")
        return "MR 操作/状态已忽略", 200

    mr_title = mr_data.get('title', 'Unknown Title')
    mr_url = mr_data.get('web_url', '')
    repo_name_from_payload = repository_info.get('name', '')

    logger.info(f"--- 收到 Codeup Merge Request Hook (通用审查) ---")
    logger.info(f"仓库 ID: {repository_id_str}, MR IID: {local_id}, Head SHA (来自负载): {head_sha_payload}")

    if head_sha_payload and is_commit_processed('codeup_general', repository_id_str, str(local_id), head_sha_payload):
        logger.info(f"Codeup (通用审查): MR {repository_id_str}#{local_id} 的提交 {head_sha_payload} 已处理。跳过。")
        return "提交已处理", 200

    current_commit_sha_for_ops = head_sha_payload

    # 调用核心处理逻辑函数 (异步执行)
    future = executor.submit(
        _process_codeup_general_payload,
        access_token=access_token,
        organization_id=organization_id,
        repository_id=repository_id_str,
        local_id=local_id,
        mr_data=mr_data,
        head_sha_payload=head_sha_payload,
        current_commit_sha_for_ops=current_commit_sha_for_ops,
        repo_name_from_payload=repo_name_from_payload,
        domain=domain,
        mr_title=mr_title,
        mr_url=mr_url
    )
    future.add_done_callback(handle_async_task_exception)
    
    logger.info(f"Codeup (通用审查): MR {repository_id_str}#{local_id} 的处理任务已提交到后台执行。")
    return jsonify({"message": "Codeup General Webhook processing task accepted."}), 202
