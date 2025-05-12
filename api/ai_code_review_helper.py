from flask import render_template
import os
import sys # 新增导入
import logging
import atexit
import redis # 新增导入

from api.app_factory import app, executor # 导入 executor
from api.core_config import (
    SERVER_HOST, SERVER_PORT, app_configs, ADMIN_API_KEY,
    init_redis_client, load_configs_from_redis
)
import api.core_config as core_config_module
from api.services.llm_service import initialize_openai_client
import api.services.llm_service as llm_service_module
import api.routes.config_routes
import api.routes.webhook_routes_detailed # Changed
import api.routes.webhook_routes_general # Changed


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
    try:
        init_redis_client()
        # 如果 init_redis_client 成功，redis_client 应该已经设置好
        if not core_config_module.redis_client:
            # 这是一个后备检查，理论上 init_redis_client 应该在失败时抛出异常
            logger.critical("Redis 客户端未能成功初始化，即使没有引发预期错误。服务无法启动。")
            sys.exit(1)
        logger.info(f"Redis 连接: 成功连接到 {app_configs.get('REDIS_HOST')}:{app_configs.get('REDIS_PORT')}")
        load_configs_from_redis()  # 这会填充 github_repo_configs 和 gitlab_project_configs
    except (ValueError, redis.exceptions.ConnectionError) as e:
        logger.critical(f"关键错误: Redis 初始化失败 - {e}")
        logger.critical("服务无法启动。请确保 Redis 相关环境变量 (如 REDIS_HOST, REDIS_PORT) 已正确设置，并且 Redis 服务可用。")
        sys.exit(1)

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

    if not app_configs.get("CUSTOM_WEBHOOK_URL"):
        logger.info("提示: CUSTOM_WEBHOOK_URL 未设置。自定义 Webhook 通知将被禁用。")
    else:
        logger.info(f"自定义 Webhook 通知已启用，URL: {app_configs.get('CUSTOM_WEBHOOK_URL')}")

    # Check openai_client status after initial attempt
    if not llm_service_module.openai_client:  # Check via module attribute
        logger.warning(
            "警告: OpenAI 客户端无法根据当前设置初始化。在通过管理面板或环境变量提供有效的 OpenAI 配置之前，AI 审查功能将无法工作。")

    # Redis 状态的日志记录已在初始化部分处理，如果程序运行到此处，说明 Redis 已成功连接。
    # 此处不再需要重复的 Redis 状态日志。

    logger.info("--- 配置管理 API ---")
    logger.info("使用 /config/* 端点管理密钥和令牌。")
    logger.info("需要带有从环境加载的 ADMIN_API_KEY 的 'X-Admin-API-Key' 请求头。")
    logger.info(f"管理页面位于: http://localhost:{SERVER_PORT}/admin")

    logger.info("全局设置配置 (通过管理面板或 API):")
    logger.info(
        f"  查看: curl -X GET -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" http://localhost:{SERVER_PORT}/config/global_settings")
    logger.info(f"  更新: curl -X POST -H \"Content-Type: application/json\" -H \"X-Admin-API-Key: YOUR_ADMIN_KEY\" \\")
    logger.info(
        f"    -d '{{\"OPENAI_MODEL\": \"gpt-3.5-turbo\", \"WECOM_BOT_WEBHOOK_URL\": \"YOUR_WECOM_URL\", \"CUSTOM_WEBHOOK_URL\": \"YOUR_CUSTOM_URL\"}}' \\")  # Example
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

    # 注册 atexit 处理函数以关闭 ThreadPoolExecutor
    atexit.register(lambda: executor.shutdown(wait=True))
    logger.info("ThreadPoolExecutor shutdown hook registered.")

    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False)
