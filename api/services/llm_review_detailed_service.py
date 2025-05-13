import json
import logging
from openai import OpenAI # Ensure OpenAI client is available for type hinting if needed
from api.core_config import app_configs
from .llm_client_manager import get_openai_client, execute_llm_chat_completion

logger = logging.getLogger(__name__)

DETAILED_REVIEW_SYSTEM_PROMPT = """
# 角色
你现在是专业的代码审查专家，你的核心职责是深入分析提供的代码变更，发现其中潜在的错误、安全隐患、性能问题、设计缺陷或不符合最佳实践的地方。
你的审查结果必须**极度严格**地遵守后续指定的 JSON 数组输出格式要求，**不包含**任何额外的解释性文字、代码块标记（如 ```json ... ```）或其他非JSON数组内容。

# 审查维度及判断标准（按优先级排序）
1.  **正确性与健壮性**：代码是否能正确处理预期输入和边界情况？是否存在潜在的空指针、资源泄漏、并发问题？错误处理是否恰当？
2.  **安全性**：是否存在安全漏洞，如注入、XSS、不安全的依赖、敏感信息泄露？
3.  **性能**：是否存在明显的性能瓶颈？是否有不必要的计算或资源消耗？算法或数据结构是否最优？
4.  **设计与架构**：代码是否遵循良好的设计原则（如 SOLID）？模块化和封装是否合理？
5.  **最佳实践**：是否遵循了语言或框架的最佳实践？是否有更简洁或 Pythonic/Java-idiomatic 的写法？

**重要提示：** 仅反馈重要或中等严重程度以上的问题和潜在的安全隐患。细小的代码风格问题或吹毛求疵之处请忽略。

# 输入数据格式
输入是一个 JSON 对象，包含单个文件的变更信息：
{
    "file_meta": {
        "path": "当前文件路径",
        "old_path": "原文件路径（重命名时存在，否则为null）",
        "lines_changed": "变更行数统计（仅add/delete，例如 '+5,-2'）",
        "context": {
            "old": "原文件相关上下文代码片段（可能包含行号）",
            "new": "新文件相关上下文代码片段（可能包含行号）"
        }
    },
    "changes": [
        {
            "type": "变更类型（add/delete）",
            "old_line": "原文件行号（删除时为整数，新增时为null）",
            "new_line": "新文件行号（新增时为整数，删除时为null）",
            "content": "变更内容（不含+/-前缀）"
        }
        // ... more changes in this file
    ]
}
- `old_line`：该 `content` 在原文件中的行号，为 `null` 表示该行是新增的。
- `new_line`：该 `content` 在新文件中的行号，为 `null` 表示该行是被删除的。
- `context` 提供了变更区域附近的代码行，以帮助理解变更的背景。

# 示例输入与输出 (Few-shot Examples)

## 示例输入 1 (包含一个潜在问题)
```json
{
    "file_meta": {
        "path": "service/user_service.py",
        "old_path": null,
        "lines_changed": "+4",
        "context": {
            "old": "def get_user_info(user_id):\n    # Existing code\n    pass",
            "new": "def get_user_info(user_id):\n    # Existing code\n    conn = db.connect()\n    cursor = conn.cursor()\n    query = f\"SELECT * FROM users WHERE id = {user_id}\"\n    cursor.execute(query)\n    user_data = cursor.fetchone()\n    conn.close()\n    return user_data"
        }
    },
    "changes": [
        {"type": "add", "old_line": null, "new_line": 3, "content": "    conn = db.connect()"},
        {"type": "add", "old_line": null, "new_line": 4, "content": "    cursor = conn.cursor()"},
        {"type": "add", "old_line": null, "new_line": 5, "content": "    query = f\"SELECT * FROM users WHERE id = {user_id}\""},
        {"type": "add", "old_line": null, "new_line": 6, "content": "    cursor.execute(query)"},
        {"type": "add", "old_line": null, "new_line": 7, "content": "    user_data = cursor.fetchone()"},
        {"type": "add", "old_line": null, "new_line": 8, "content": "    conn.close()"}
    ]
}
```

## 示例输出 1 (对应示例输入 1 的正确 JSON数组 输出)
```json
[
  {
    "file": "service/user_service.py",
    "lines": {
      "old": null,
      "new": 5
    },
    "category": "安全性",
    "severity": "critical",
    "analysis": "直接将 user_id 拼接到 SQL 查询字符串中存在 SQL 注入风险。",
    "suggestion": "query = \"SELECT * FROM users WHERE id = %s\"\ncursor.execute(query, (user_id,))"
  }
]
```

## 示例输入 2 (没有发现重要问题)
```json
{
    "file_meta": {
        "path": "util/string_utils.py",
        "old_path": null,
        "lines_changed": "+3",
        "context": {
            "old": "def greet(name):\n    return f\"Hello, {name}!\"",
            "new": "def greet(name):\n    # Add an exclamation mark\n    greeting = f\"Hello, {name}!\"\n    return greeting + \"!!\""
        }
    },
    "changes": [
        {"type": "add", "old_line": null, "new_line": 2, "content": "    # Add an exclamation mark"},
        {"type": "add", "old_line": null, "new_line": 3, "content": "    greeting = f\"Hello, {name}!\""},
        {"type": "add", "old_line": null, "new_line": 4, "content": "    return greeting + \"!!\""}
    ]
}
```

## 示例输出 2 (对应示例输入 2 的正确 JSON数组 输出)
```json
[]
```

# 输出格式
你的输出必须严格按照以下 JSON数组 格式输出一个审查结果JSON数组。数组中的每个对象代表一个具体的审查意见。
[
  {
    "file": "string, 发生问题的文件的完整路径",
    "lines": {
      "old": "integer or null, 原文件行号。如果是针对新增代码或无法精确到原文件行，则为 null。",
      "new": "integer or null, 新文件行号。如果是针对删除代码或无法精确到新文件行，则为 null。"
    },
    "category": "string, 问题分类，从 [正确性, 安全性, 性能, 设计, 最佳实践] 中选择。",
    "severity": "string, 严重程度，从 [critical, high, medium, low] 中选择。",
    "analysis": "string, 结合代码上下文对问题进行的简短分析和审查意见。限制在 100 字以内，使用中文。",
    "suggestion": "string, 针对该问题位置的纠正或改进建议代码。如果难以提供直接代码，可以提供文字说明。"
  }
  // ... more review comments
]

**行号处理规则强化：**
- 如果审查意见针对**新增**的代码行，请将 `lines.old` 设为 `null`，`lines.new` 设为该行在**新文件**中的对应行号 (务必与输入 `changes` 中的 `new_line` 精确匹配)。
- 如果审查意见针对**删除**的代码行，请将 `lines.old` 设为该行在**原文件**中的对应行号 (务必与输入 `changes` 中的 `old_line` 精确匹配)，`lines.new` 设为 `null`。
- 如果审查意见是针对**修改**后的代码行（即涉及旧行和新行），请优先关联到**新文件**的行号：`lines.old` 设为 `null`，`lines.new` 设为修改后该行在**新文件**中的对应行号 (务必与输入 `changes` 中的 `new_line` 精确匹配)。
- 如果审查意见针对整个文件、某个函数签名或无法精确到输入 `changes` 中的某一行，可以将 `lines` 设为 `{"old": null, "new": null}`。
- **请再次确认：你输出的每个审查意见对象中的 `lines.old` 或 `lines.new` 至少有一个值必须与输入 `changes` 数组中某个元素的 `old_line` 或 `new_line` 精确匹配（除非是针对整个文件或无法精确到行的意见）。**

**输出格式绝对禁止：**
- **不允许**在 JSON 数组前后或内部添加任何解释性文字、markdown 格式（如代码块标记 ```json ```）。
- **不允许**输出任何注释。
- **不允许**在 JSON数组 之外有任何其他内容。
- **不允许**输出的 JSON 中存在其他key。

如果提供的文件变更中没有发现任何需要反馈的问题（即没有达到 medium 或更高 severity 的问题），请返回一个**空的 JSON 数组**：`[]`。

现在，请根据上述指令和格式要求，审查我提供的代码变更输入，并输出严格符合格式要求的 JSON 数组。
"""


def get_openai_code_review(structured_file_changes):
    """使用 OpenAI API 对结构化的代码变更进行 review (源自 GitHub 版本，通用性较好)"""
    client = get_openai_client()
    if not client:
        logger.warning("OpenAI 客户端不可用 (未初始化或初始化失败)。跳过审查。")
        return "[]"
    if not structured_file_changes:
        logger.info("未提供结构化变更以供审查。")
        return "[]"

    all_reviews = []
    current_model = app_configs.get("OPENAI_MODEL", "gpt-4o")

    for file_path, file_data in structured_file_changes.items():
        input_data = {
            "file_meta": {
                "path": file_data["path"],
                "old_path": file_data.get("old_path"),
                "lines_changed": file_data.get("lines_changed", len(file_data["changes"])),
                "context": file_data["context"]
            },
            "changes": file_data["changes"]
        }
        try:
            input_json_string = json.dumps(input_data, indent=2, ensure_ascii=False)
        except TypeError as te:
            logger.error(f"序列化文件 {file_path} 的输入数据时出错: {te}")
            logger.error(f"有问题的据结构: {input_data}")
            continue

        user_prompt_for_llm = f"\n\n```json\n{input_json_string}\n```\n"

        try:
            logger.info(f"正在发送文件审查请求: {file_path}...")
            # Ensure client is fresh if settings changed (get_openai_client handles this)
            client = get_openai_client()
            if not client:
                logger.warning(f"在审查 {file_path} 前 OpenAI 客户端变得不可用。将跳过此文件并继续处理其他文件。")
                continue

            review_json_str = execute_llm_chat_completion(
                client,
                current_model,
                DETAILED_REVIEW_SYSTEM_PROMPT,
                user_prompt_for_llm,
                "细粒度审查",
                response_format_type="json_object"
            )

            logger.info(f"-------------LLM 输出-----------")
            logger.info(f"文件 {file_path} 的 LLM 原始输出:")
            logger.info(f"{review_json_str}")
            logger.info(f"-------------LLM 输出-----------")

            try:
                parsed_output = json.loads(review_json_str)
                reviews_for_file = []
                if isinstance(parsed_output, list):
                    reviews_for_file = parsed_output
                elif isinstance(parsed_output, dict):  # Check if the dict contains a list
                    found_list = False
                    for key, value in parsed_output.items():
                        if isinstance(value, list):
                            reviews_for_file = value
                            found_list = True
                            logger.info(f"在 LLM 输出的键 '{key}' 下找到审查列表。")
                            break
                    if not found_list:
                        logger.warning(
                            f"警告: 文件 {file_path} 的 LLM 输出是一个字典，但未找到列表值。输出: {review_json_str}")
                        # Attempt to use the dict as a single review item if it matches structure,
                        # otherwise, it will be filtered out by validation below.
                        reviews_for_file = [parsed_output]
                else:
                    logger.warning(
                        f"警告: 文件 {file_path} 的 LLM 输出不是 JSON 列表或预期的字典。输出: {review_json_str}")

                valid_reviews_for_file = []
                for review in reviews_for_file:
                    if isinstance(review, dict) and all(
                            k in review for k in ["file", "lines", "category", "severity", "analysis", "suggestion"]):
                        if review.get("file") != file_path:
                            logger.warning(f"警告: 修正审查中的文件路径从 '{review.get('file')}' 为 '{file_path}'")
                            review["file"] = file_path
                        valid_reviews_for_file.append(review)
                    else:
                        logger.warning(f"警告: 跳过文件 {file_path} 的无效审查项结构: {review}")
                all_reviews.extend(valid_reviews_for_file)

            except json.JSONDecodeError as json_e:
                logger.error(f"错误: 解析来自 OpenAI 的文件 {file_path} 的 JSON 响应失败: {json_e}")
                logger.error(f"LLM 原始输出为: {review_json_str}")
        except Exception as e:
            logger.exception(f"从 OpenAI 获取文件 {file_path} 的代码审查时出错:")

    try:
        final_json_output = json.dumps(all_reviews, ensure_ascii=False, indent=2)
    except TypeError as te:
        logger.error(f"序列化最终审查列表时出错: {te}")
        logger.error(f"有问题的列表结构: {all_reviews}")
        final_json_output = "[]"

    return final_json_output


def get_openai_detailed_review_for_file(file_path: str, file_data: dict, client: OpenAI, model_name: str):
    """
    使用 OpenAI API 对单个文件的结构化代码变更进行详细审查。
    返回一个 Python 列表，其中包含该文件的审查意见字典。
    如果文件没有问题或发生错误，则返回空列表。
    """
    if not client:
        logger.warning(f"OpenAI 客户端不可用 (传递给 get_openai_detailed_review_for_file 时)。跳过文件 {file_path} 的审查。")
        return []
    if not file_data:
        logger.info(f"未提供文件 {file_path} 的数据以供详细审查。")
        return []

    input_data = {
        "file_meta": {
            "path": file_data.get("path", file_path), # Ensure path from file_data or argument
            "old_path": file_data.get("old_path"),
            "lines_changed": file_data.get("lines_changed", len(file_data.get("changes", []))),
            "context": file_data.get("context", {})
        },
        "changes": file_data.get("changes", [])
    }
    try:
        input_json_string = json.dumps(input_data, indent=2, ensure_ascii=False)
    except TypeError as te:
        logger.error(f"序列化文件 {file_path} 的输入数据时出错: {te}")
        logger.error(f"有问题的输入结构: {input_data}")
        return []

    user_prompt_for_llm = f"\n\n```json\n{input_json_string}\n```\n"

    try:
        logger.info(f"正在发送文件审查请求 (详细): {file_path} 给模型 {model_name}...")
        
        review_json_str = execute_llm_chat_completion(
            client,
            model_name,
            DETAILED_REVIEW_SYSTEM_PROMPT,
            user_prompt_for_llm,
            f"文件 {file_path} 的细粒度审查",
            response_format_type="json_object"
        )

        logger.info(f"-------------LLM 输出 (文件: {file_path})-----------")
        logger.info(f"{review_json_str}")
        logger.info(f"-------------LLM 输出结束 (文件: {file_path})-----------")

        try:
            parsed_output = json.loads(review_json_str)
            reviews_for_this_file = []
            if isinstance(parsed_output, list):
                reviews_for_this_file = parsed_output
            elif isinstance(parsed_output, dict):
                found_list = False
                for key, value in parsed_output.items():
                    if isinstance(value, list):
                        reviews_for_this_file = value
                        found_list = True
                        logger.info(f"在 LLM 输出的键 '{key}' 下找到文件 {file_path} 的审查列表。")
                        break
                if not found_list:
                    logger.warning(
                        f"警告: 文件 {file_path} 的 LLM 输出是一个字典，但未找到列表值。输出: {review_json_str}")
                    reviews_for_this_file = [parsed_output] # Try to treat as single item
            else:
                logger.warning(
                    f"警告: 文件 {file_path} 的 LLM 输出不是 JSON 列表或预期的字典。输出: {review_json_str}")
                return [] # Not a valid format

            valid_reviews = []
            for review in reviews_for_this_file:
                if isinstance(review, dict) and all(
                        k in review for k in ["file", "lines", "category", "severity", "analysis", "suggestion"]):
                    if review.get("file") != file_path:
                        logger.warning(f"警告: 修正审查中的文件路径从 '{review.get('file')}' 为 '{file_path}' (针对文件 {file_path})")
                        review["file"] = file_path
                    valid_reviews.append(review)
                else:
                    logger.warning(f"警告: 跳过文件 {file_path} 的无效审查项结构: {review}")
            return valid_reviews

        except json.JSONDecodeError as json_e:
            logger.error(f"错误: 解析来自 OpenAI 的文件 {file_path} 的 JSON 响应失败: {json_e}")
            logger.error(f"LLM 原始输出为: {review_json_str}")
            return []
    except Exception as e:
        logger.exception(f"从 OpenAI 获取文件 {file_path} 的详细代码审查时出错:")
        return []
