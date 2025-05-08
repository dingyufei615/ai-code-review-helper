from flask import render_template
import os
import logging

from api.app_factory import app
from api.core_config import (
    SERVER_HOST, SERVER_PORT, app_configs, ADMIN_API_KEY,
    init_redis_client, load_configs_from_redis
)
import api.core_config as core_config_module
from api.services.llm_service import initialize_openai_client
import api.services.llm_service as llm_service_module
import api.routes.config_routes
import api.routes.webhook_routes


# --- Admin Page ---
@app.route('/admin')
def admin_page():
    """提供管理界面的 HTML 页面"""
    return render_template('admin.html')


# --- 主程序入口 ---
if __name__ == '__main__':
    # 配置日志记录
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        handlers=[logging.StreamHandler()])  # 输出到控制台
    logger = logging.getLogger(__name__)

    logger.info(f"启动统一代码审查 Webhook 服务于 {SERVER_HOST}:{SERVER_PORT}")

    # Initial call to set up the client based on initial configs
    initialize_openai_client()

    # 初始化 Redis 客户端并加载配置
    logger.info("--- 持久化配置 ---")
    init_redis_client()
    load_configs_from_redis()  # 这会填充 github_repo_configs 和 gitlab_project_configs

    logger.info("--- 当前应用配置 ---")
    for key, value in app_configs.items():
        if "KEY" in key.upper() or "TOKEN" in key.upper() or "PASSWORD" in key.upper() or "SECRET" in key.upper():  # Basic redaction for logs
            if value and len(value) > 8:
                logger.info(f"  {key}: ...{value[-4:]}")
            elif value:
                logger.info(f"  {key}: <已设置>")
            else:
                logger.info(f"  {key}: <未设置>")
        else:
            logger.info(f"  {key}: {value}")

    if ADMIN_API_KEY == "change_this_unified_secret_key":
        logger.critical(
            "严重警告: ADMIN_API_KEY 正在使用默认的不安全值。请通过环境变量设置一个强密钥。")
    else:
        logger.info("Admin API 密钥已配置 (从环境加载)。")

    if not app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        logger.info("提示: WECOM_BOT_WEBHOOK_URL 未设置。企业微信机器人通知将被禁用。")
    else:
        url_parts = app_configs.get("WECOM_BOT_WEBHOOK_URL").split('?')
        key_preview = app_configs.get("WECOM_BOT_WEBHOOK_URL")[-6:] if len(
            app_configs.get("WECOM_BOT_WEBHOOK_URL")) > 6 else ''
        logger.info(f"企业微信机器人通知已启用，URL: {url_parts[0]}?key=...{key_preview}")

    # Check openai_client status after initial attempt
    if not llm_service_module.openai_client:  # Check via module attribute
        logger.warning(
            "警告: OpenAI 客户端无法根据当前设置初始化。在通过管理面板或环境变量提供有效的 OpenAI 配置之前，AI 审查功能将无法工作。")

    logger.info("--- Redis 状态 ---")
    if app_configs.get("REDIS_HOST"):
        if core_config_module.redis_client:  # Check via module attribute
            logger.info(f"Redis 连接: 已连接到 {app_configs.get('REDIS_HOST')}:{app_configs.get('REDIS_PORT')}")
        else:
            logger.warning(
                f"Redis 连接: 连接到 {app_configs.get('REDIS_HOST')}:{app_configs.get('REDIS_PORT')} 失败。将使用内存存储。")
    else:
        logger.info("Redis 连接: 未配置。将使用内存存储。")

    logger.info("--- 配置管理 API ---")
    logger.info("使用 /config/* 端点管理密钥和令牌。")
    logger.info("需要带有从环境加载的 ADMIN_API_KEY 的 'X-Admin-API-Key' 请求头。")
    logger.info(f"管理页面位于: http://localhost:{SERVER_PORT}/admin")

    logger.info("全局设置配置 (通过管理面板或 API):")
    logger.info(
        f"  查看: curl -X GET -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" http://localhost:{SERVER_PORT}/config/global_settings")
    logger.info(f"  更新: curl -X POST -H \"Content-Type: application/json\" -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" \\")
    logger.info(
        f"    -d '{{\"OPENAI_MODEL\": \"gpt-3.5-turbo\", \"GITHUB_API_URL\": \"https://api.github.com\"}}' \\")  # Example
    logger.info(f"    http://localhost:{SERVER_PORT}/config/global_settings")

    logger.info("GitHub 仓库配置示例 (通过管理面板或 API):")
    logger.info(
        f"  添加/更新: curl -X POST -H \"Content-Type: application/json\" -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" \\")
    logger.info(
        f"    -d '{{\"repo_full_name\": \"owner/repo\", \"secret\": \"YOUR_GH_WEBHOOK_SECRET\", \"token\": \"YOUR_GITHUB_TOKEN\"}}' \\")
    logger.info(f"    http://localhost:{SERVER_PORT}/config/github/repo")
    logger.info(
        f"  删除: curl -X DELETE -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" http://localhost:{SERVER_PORT}/config/github/repo/owner/repo")
    logger.info(
        f"  列表: curl -X GET -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" http://localhost:{SERVER_PORT}/config/github/repos")

    logger.info("GitLab 项目配置示例 (通过管理面板或 API):")
    logger.info(
        f"  添加/更新: curl -X POST -H \"Content-Type: application/json\" -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" \\")
    logger.info(
        f"    -d '{{\"project_id\": 123, \"secret\": \"YOUR_GL_WEBHOOK_SECRET\", \"token\": \"YOUR_GITLAB_TOKEN\"}}' \\")
    logger.info(f"    http://localhost:{SERVER_PORT}/config/gitlab/project")
    logger.info(
        f"  删除: curl -X DELETE -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" http://localhost:{SERVER_PORT}/config/gitlab/project/123")
    logger.info(
        f"  列表: curl -X GET -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" http://localhost:{SERVER_PORT}/config/gitlab/projects")

    logger.info("--- Webhook 端点 ---")
    logger.info(f"GitHub Webhook URL (详细审查): http://localhost:{SERVER_PORT}/github_webhook")
    logger.info(f"GitLab Webhook URL (详细审查): http://localhost:{SERVER_PORT}/gitlab_webhook")
    logger.info(f"GitHub Webhook URL (通用审查): http://localhost:{SERVER_PORT}/github_webhook_general")
    logger.info(f"GitLab Webhook URL (通用审查): http://localhost:{SERVER_PORT}/gitlab_webhook_general")
    logger.info("--- ---")

    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False)
