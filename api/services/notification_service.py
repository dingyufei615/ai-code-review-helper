import requests
from api.core_config import app_configs


def send_to_wecom_bot(summary_content):
    """将 Code Review 摘要发送到企业微信机器人 (源自 GitHub 版本)"""
    current_wecom_url = app_configs.get("WECOM_BOT_WEBHOOK_URL")
    if not current_wecom_url:
        print("WECOM_BOT_WEBHOOK_URL not configured (via admin or env). Skipping sending message to WeCom bot.")
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
            print("Successfully sent summary to WeCom bot.")
        else:
            print(f"Error sending summary to WeCom bot: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending summary message to WeCom bot: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while sending summary to WeCom bot: {e}")
