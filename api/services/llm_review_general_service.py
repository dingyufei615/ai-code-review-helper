import json
import logging
from api.core_config import app_configs
from .llm_client_manager import get_openai_client, execute_llm_chat_completion
from api.prompt.prompt_loader import get_prompt

logger = logging.getLogger(__name__)


def get_openai_code_review_general(file_data: dict):
    """
    使用 OpenAI API 对单个文件的代码变更进行粗粒度的审查。
    接收一个文件数据字典，包含路径、diff、旧内容和新内容。
    返回一个针对该文件的 Markdown 格式审查意见文本字符串。
    如果文件无问题，则返回空字符串或特定无问题指示。
    """
    client = get_openai_client()
    if not client:
        logger.warning("OpenAI 客户端不可用 (未初始化或初始化失败)。跳过单个文件的粗粒度审查。")
        return "OpenAI client is not available. Skipping general review for single file."
    if not file_data:
        logger.info("未提供文件数据以供单个文件的粗粒度审查。")
        return ""

    try:
        user_prompt_content_for_llm = json.dumps(file_data, ensure_ascii=False, indent=2)
    except TypeError as te:
        logger.error(f"序列化文件 {file_data.get('file_path', 'N/A')} 的粗粒度审查输入数据时出错: {te}")
        return f"Error serializing input data for general review of file {file_data.get('file_path', 'N/A')}."

    current_model = app_configs.get("OPENAI_MODEL", "gpt-4o")
    logger.info(f"正在发送文件 {file_data.get('file_path', 'N/A')} 的粗粒度审查请求给 {current_model}...")

    try:
        # Ensure client is fresh
        client = get_openai_client()
        if not client:
            logger.warning(f"在审查 {file_data.get('file_path', 'N/A')} 前 OpenAI 客户端变得不可用。")
            return "OpenAI client is not available. Skipping general review for single file."

        general_review_system_prompt = get_prompt('general_review')
        if "Error: Prompt" in general_review_system_prompt: # Check if prompt loading failed
            error_msg = f"无法加载通用审查的 System Prompt。错误: {general_review_system_prompt}"
            logger.error(error_msg)
            return error_msg # Return the error message to be potentially shown to user/logged


        review_text = execute_llm_chat_completion(
            client,
            current_model,
            general_review_system_prompt,
            user_prompt_content_for_llm,  # This is already the JSON string
            "粗粒度审查"
            # No response_format_type for plain text Markdown
        )

        logger.info(f"-------------LLM 粗粒度审查输出-----------")
        logger.info(review_text)
        logger.info(f"-------------LLM 粗粒度审查输出结束-----------")
        return review_text
    except Exception as e:
        logger.exception("从 OpenAI 获取粗粒度代码审查时出错:")
        return f"Error during general code review with OpenAI: {e}"
