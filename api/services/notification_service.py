import requests
from api.core_config import app_configs
import logging

logger = logging.getLogger(__name__)


def _send_notification(url: str, payload: dict, service_name: str):
    """通用函数，用于发送 POST 请求到指定的 URL"""
    if not url:
        logger.info(f"{service_name} URL 未配置。跳过发送消息。")
        return

    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        # 检查企业微信特定的错误码
        if service_name == "企业微信机器人" and response.json().get("errcode") != 0:
            logger.error(f"发送摘要到 {service_name} 时出错: {response.text}")
        # 对于自定义 webhook，我们假设 2xx 状态码表示成功
        elif service_name == "自定义 Webhook":
            logger.info(f"成功发送摘要到 {service_name}。状态码: {response.status_code}")
        # 其他情况或企业微信成功
        else:
            logger.info(f"成功发送摘要到 {service_name}。")

    except requests.exceptions.RequestException as e:
        logger.error(f"发送摘要消息到 {service_name} 时出错: {e}")
    except Exception as e:
        logger.error(f"发送摘要到 {service_name} 时发生意外错误: {e}")


def send_notifications(summary_content):
    """将 Code Review 摘要发送到所有已配置的通知渠道。"""

    # 发送到企业微信机器人
    wecom_url = app_configs.get("WECOM_BOT_WEBHOOK_URL")
    if wecom_url:
        wecom_payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": summary_content
            }
        }
        _send_notification(wecom_url, wecom_payload, "企业微信机器人")
    else:
        logger.info("WECOM_BOT_WEBHOOK_URL 未配置。跳过发送到企业微信。")

    # 发送到自定义 Webhook
    custom_webhook_url = app_configs.get("CUSTOM_WEBHOOK_URL")
    if custom_webhook_url:
        custom_payload = {
            "content": summary_content  # 将通知内容放在 'content' 参数中
        }
        _send_notification(custom_webhook_url, custom_payload, "自定义 Webhook")
    else:
        logger.info("CUSTOM_WEBHOOK_URL 未配置。跳过发送到自定义 Webhook。")


# --- 旧函数保留，但内部调用新的通用发送逻辑 ---
# 或者可以直接替换所有调用点为 send_notifications
def send_to_wecom_bot(summary_content):
    """将 Code Review 摘要发送到企业微信机器人 (现在调用通用函数)"""
    # 为了保持向后兼容性或逐步迁移，可以保留此函数，但其逻辑已移至 send_notifications
    # 这里我们假设所有调用点都将更新为 send_notifications，所以这个函数可以被移除或标记为废弃
    # 为了简单起见，我们假设调用点会被更新，所以这里不再需要旧的实现
    # 如果需要保留旧函数签名，它可以简单地调用 send_notifications
    logger.warning("send_to_wecom_bot 已废弃，请使用 send_notifications。")
    send_notifications(summary_content)
