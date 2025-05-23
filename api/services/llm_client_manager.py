import logging
import re
from openai import OpenAI, APIError # 导入 APIError
from api.core_config import app_configs

logger = logging.getLogger(__name__)

openai_client = None

# 定义思考模型列表
thinking_model_names = ["qwen3", "qwen3:32b", "qwen3:30b", "qwen3:235b"]  # 您可以根据需要扩展此列表


def _prepare_llm_user_prompt(base_prompt: str, model_name: str, context_description: str) -> str:
    """根据模型名称和上下文准备最终用户提示。如果模型是思考模型，则添加 '/no_think'。"""
    if model_name in thinking_model_names:
        logger.info(f"当前模型 {model_name} 是一个思考模型 ({context_description})。将在提示前添加 '/no_think'。")
        return "/no_think " + base_prompt
    else:
        logger.debug(f"当前模型 {model_name} 不是一个已知的思考模型 ({context_description})。按原样使用提示。")
        return base_prompt


def initialize_openai_client():
    """根据 app_configs 初始化或重新初始化全局 OpenAI 客户端。"""
    global openai_client
    try:
        current_base_url = app_configs.get("OPENAI_API_BASE_URL")
        current_api_key = app_configs.get("OPENAI_API_KEY")
        current_model = app_configs.get("OPENAI_MODEL")

        if not current_api_key or current_api_key == "xxxx-xxxx-xxxx-xxxx":
            logger.warning(
                "警告: OpenAI API Key 未配置或为占位符。OpenAI 客户端将不会初始化。")
            openai_client = None
            return

        if current_base_url and current_base_url != "https://api.openai.com/v1" and not current_base_url.endswith(
                '/v1'):
            if not current_base_url.endswith('/api') and not current_base_url.endswith('/'):
                corrected_base_url = current_base_url.rstrip('/') + '/v1'
                logger.info(
                    f"为 OpenAI 库兼容性，修正 OpenAI API 基础 URL 从 '{current_base_url}' 到 '{corrected_base_url}'。")
                current_base_url = corrected_base_url
            else:
                logger.info(f"使用自定义 OpenAI API 基础 URL: {current_base_url}")

        if current_base_url and current_base_url != "https://api.openai.com/v1":
            logger.info(f"使用自定义基础 URL 初始化 OpenAI 客户端: {current_base_url}")
            openai_client = OpenAI(
                base_url=current_base_url,
                api_key=current_api_key
            )
        else:
            logger.info("使用默认 OpenAI API 端点初始化 OpenAI 客户端。")
            openai_client = OpenAI(
                api_key=current_api_key
            )
        logger.info(f"OpenAI 客户端已初始化/重新初始化。将使用的模型: {current_model}")
    except Exception as e:
        logger.error(f"初始化 OpenAI 客户端时出错: {e}")
        logger.error(
            "请确保通过管理面板或环境变量设置了 OpenAI API Key，并且基础 URL (如果使用) 正确。")
        openai_client = None


def get_openai_client():
    """获取 OpenAI 客户端实例，如果未初始化则尝试初始化。"""
    global openai_client
    if openai_client is None:
        logger.info("OpenAI 客户端为 None，尝试初始化...")
        initialize_openai_client()
    return openai_client


def execute_llm_chat_completion(client, model_name: str, system_prompt: str, user_prompt: str, context_description: str,
                                response_format_type: str = None):
    """
    执行 LLM 请求。

    :param client: OpenAI 客户端实例。
    :param model_name: 要使用的模型名称。
    :param system_prompt: 系统提示。
    :param user_prompt: 用户原始提示。
    :param context_description: 用于 _prepare_llm_user_prompt 的上下文描述。
    :param response_format_type: 可选，响应格式类型 (例如 "json_object")。
    :return: LLM 的响应内容。
    """
    final_user_prompt = _prepare_llm_user_prompt(user_prompt, model_name, context_description)

    completion_params = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": final_user_prompt}
        ]
    }
    # if response_format_type:
    #     completion_params["response_format"] = {"type": response_format_type}

    try:
        response = client.chat.completions.create(**completion_params)
        if response and response.choices and len(response.choices) > 0:
            message = response.choices[0].message
            if message and message.content:
                # 首先获取原始响应内容
                raw_content = message.content
                # 移除 <think>...</think> 标签及其内容
                # re.DOTALL 使 . 匹配换行符
                content_after_think_tags = re.sub(r"<think>.*?</?think>", "", raw_content, flags=re.DOTALL)
                # 尝试提取被 ```...``` 包裹的内容，可选地处理语言标记如 json
                # re.DOTALL 确保 . 可以匹配换行符，处理多行 JSON
                # \s* 用于匹配 ``` 和实际内容之间，以及内容和末尾 ``` 之间的空白字符
                # (?:\w*\s*)? 是一个可选的非捕获组，匹配可选的语言名称后跟可选空格
                markdown_json_match = re.search(r"```(?:\w*\s*)?([\s\S]*?)\s*```", content_after_think_tags, re.DOTALL)

                if markdown_json_match:
                    # 如果匹配到，提取第一个捕获组的内容
                    final_content = markdown_json_match.group(1)
                    logger.info(f"从 Markdown 代码块中提取了 JSON 内容 ({context_description})。")
                else:
                    # 如果没有匹配到 Markdown 代码块，则假定内容已经是 JSON 或纯文本
                    final_content = content_after_think_tags
                    # 然后去除首尾空白
                return final_content.strip()
            else:
                logger.error(f"LLM 响应中缺少 'content' 字段 ({context_description})。响应: {response}")
                return f"Error: LLM response missing content for {context_description}."
        else:
            logger.error(f"LLM 响应无效或 choices 为空 ({context_description})。响应: {response}")
            return f"Error: Invalid LLM response or empty choices for {context_description}."
    except APIError as e:  # 使用导入的 APIError
        logger.error(f"LLM API 请求失败 ({context_description}): {e}")
        return f"Error: LLM API request failed for {context_description}: {str(e)}"
    except Exception as e:
        logger.error(f"处理 LLM 响应时发生意外错误 ({context_description}): {e}")
        return f"Error: Unexpected error during LLM processing for {context_description}: {str(e)}"
