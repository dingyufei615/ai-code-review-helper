import requests
from api.core_config import app_configs
import logging

logger = logging.getLogger(__name__)

def send_to_wecom_bot(summary_content):
    """将 Code Review 摘要发送到企业微信机器人 (源自 GitHub 版本)"""
    current_wecom_url = app_configs.get("WECOM_BOT_WEBHOOK_URL")
    if not current_wecom_url:
        logger.info("WECOM_BOT_WEBHOOK_URL 未配置 (通过管理面板或环境变量)。跳过发送消息到企业微信机器人。")
        return

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": summary_content
        }
    }
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(current_wecom_url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        if response.json().get("errcode") == 0:
            logger.info("成功发送摘要到企业微信机器人。")
        else:
            logger.error(f"发送摘要到企业微信机器人时出错: {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"发送摘要消息到企业微信机器人时出错: {e}")
    except Exception as e:
        logger.error(f"发送摘要到企业微信机器人时发生意外错误: {e}")
