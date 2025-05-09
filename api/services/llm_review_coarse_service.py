import json
import logging
from api.core_config import app_configs
from .llm_client_manager import get_openai_client, execute_llm_chat_completion

logger = logging.getLogger(__name__)

COARSE_REVIEW_SYSTEM_PROMPT = """
# 角色
你是一名代码审查专家。你的任务是基于提供的单个代码文件变更（包括文件路径、变更状态、diff/patch 以及可选的旧版和新版完整文件内容），对此文件进行审查，并使用中文和用户交流。

# 指令
1.  **输出格式**: 你的审查结果必须是一个**单一的 Markdown 文本块**，针对当前这一个文件。**绝对不要输出 JSON 或任何结构化数据。**
2.  **审查重点**:
    *   **只关注严重的代码问题**: 例如潜在的逻辑错误、安全漏洞、严重性能问题、导致程序崩溃的缺陷。
    *   **忽略**: 代码风格、命名规范、注释缺失、不影响功能的细微优化等非严重问题。
3.  **审查范围与内容 (针对当前单个文件)**:
    *   **问题定位**: 如果发现严重问题，请明确指出问题所在（例如行号，如果适用）。
    *   **简要分析**: 对每个严重问题，用一两句话简要描述问题。
    *   **修改建议**: 针对每个严重问题，给出一两句核心的修改建议。
    *   如果文件的旧内容或新内容未提供（例如因为文件过大或为二进制文件），请基于可用的 diff 信息进行审查，并可以注明这一点。
    *   **无严重问题**: 如果当前审查的文件没有发现严重问题，请返回一个**空字符串**或明确指出无问题，例如：“此文件未发现问题。”。
4.  **风格要求**:
    *   **极其简洁**: 避免任何不必要的寒暄、解释或背景信息。直接输出你的审查结果。
    *   **Markdown 格式**: 使用 Markdown 列表、代码块等元素清晰展示。例如，如果发现问题：
        ```markdown
        - **问题**: 在第 25 行，直接使用了用户输入构建 SQL 查询，可能导致 SQL 注入。
        - **建议**: 使用参数化查询或 ORM 来处理数据库操作。
        ```
        如果未发现问题，返回空字符串或：“此文件未发现问题。”

# 输入数据格式
你将收到一个 JSON 字符串，它代表**一个文件**的变更对象，结构如下：
{
  "file_path": "string, 文件的完整路径",
  "status": "string, 变更状态 ('added', 'modified', 'deleted', 'renamed')",
  "diff_text": "string, 该文件的 diff/patch 内容",
  "old_content": "string or null, 变更前的文件完整内容 (如果是新增文件则为 null)"
}

请现在根据这些指令，对我接下来提供的单个文件变更（将以 JSON 字符串形式出现）进行审查，并返回 Markdown 格式的中文审查意见。
"""


def get_openai_code_review_coarse(file_data: dict):
    """
    使用 OpenAI API 对单个文件的代码变更进行粗粒度的审查。
    接收一个文件数据字典，包含路径、diff、旧内容和新内容。
    返回一个针对该文件的 Markdown 格式审查意见文本字符串。
    如果文件无问题，则返回空字符串或特定无问题指示。
    """
    client = get_openai_client()
    if not client:
        logger.warning("OpenAI 客户端不可用 (未初始化或初始化失败)。跳过单个文件的粗粒度审查。")
        return "OpenAI client is not available. Skipping coarse review for single file."
    if not file_data:
        logger.info("未提供文件数据以供单个文件的粗粒度审查。")
        return ""

    try:
        user_prompt_content_for_llm = json.dumps(file_data, ensure_ascii=False, indent=2)
    except TypeError as te:
        logger.error(f"序列化文件 {file_data.get('file_path', 'N/A')} 的粗粒度审查输入数据时出错: {te}")
        return f"Error serializing input data for coarse review of file {file_data.get('file_path', 'N/A')}."

    current_model = app_configs.get("OPENAI_MODEL", "gpt-4o")
    logger.info(f"正在发送文件 {file_data.get('file_path', 'N/A')} 的粗粒度审查请求给 {current_model}...")

    try:
        # Ensure client is fresh
        client = get_openai_client()
        if not client:
            logger.warning(f"在审查 {file_data.get('file_path', 'N/A')} 前 OpenAI 客户端变得不可用。")
            return "OpenAI client is not available. Skipping coarse review for single file."

        review_text = execute_llm_chat_completion(
            client,
            current_model,
            COARSE_REVIEW_SYSTEM_PROMPT,
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
        return f"Error during coarse code review with OpenAI: {e}"
