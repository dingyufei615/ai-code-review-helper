import json
from openai import OpenAI
from api.core_config import app_configs
import logging

logger = logging.getLogger(__name__)
openai_client = None


def initialize_openai_client():
    """根据 app_configs 初始化或重新初始化全局 OpenAI 客户端。"""
    global openai_client
    try:
        current_base_url = app_configs.get("OPENAI_API_BASE_URL")
        current_api_key = app_configs.get("OPENAI_API_KEY")
        current_model = app_configs.get("OPENAI_MODEL")

        # 检查 API Key 是否有效（非空且不是占位符）
        if not current_api_key or current_api_key == "xxxx-xxxx-xxxx-xxxx":
            logger.warning(
                "警告: OpenAI API Key 未配置或为占位符。OpenAI 客户端将不会初始化。")
            openai_client = None
            return

        if current_base_url and current_base_url != "https://api.openai.com/v1" and not current_base_url.endswith(
                '/v1'):
            # 确保 Ollama 等兼容 API 的 URL 格式正确
            if not current_base_url.endswith('/api') and not current_base_url.endswith('/'):  # 常见Ollama路径
                corrected_base_url = current_base_url.rstrip('/') + '/v1'  # 尝试附加 /v1
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


def get_openai_code_review(structured_file_changes):
    """使用 OpenAI API 对结构化的代码变更进行 review (源自 GitHub 版本，通用性较好)"""
    client = get_openai_client()
    if not client:
        logger.warning("OpenAI 客户端不可用 (未初始化或初始化失败)。跳过审查。")
        return "[]"
    if not structured_file_changes:
        logger.info("未提供结构化变更以供审查。")
        return "[]"

    system_prompt = """
# 角色
你现在是专业的代码审查专家。你的核心职责是深入分析提供的代码变更，发现其中潜在的错误、安全隐患、性能问题、设计缺陷或不符合最佳实践的地方。你的审查结果必须**极度严格**地遵守后续指定的 JSON 输出格式要求，**不包含**任何额外的解释性文字、代码块标记（如 ```json ... ```）或其他非 JSON 内容。

# 审查维度及判断标准（按优先级排序）
1.  **正确性与健壮性**：代码是否能正确处理预期输入和边界情况？是否存在潜在的空指针、资源泄漏、并发问题？错误处理是否恰当？
2.  **安全性**：是否存在安全漏洞，如注入、XSS、不安全的依赖、敏感信息泄露？
3.  **可读性与可维护性**：命名是否规范？是否存在魔法数字或硬编码字符串？代码是否易于理解和修改？
4.  **性能**：是否存在明显的性能瓶颈？是否有不必要的计算或资源消耗？算法或数据结构是否最优？
5.  **设计与架构**：代码是否遵循良好的设计原则（如 SOLID）？模块化和封装是否合理？
6.  **最佳实践**：是否遵循了语言或框架的最佳实践？是否有更简洁或 Pythonic/Java-idiomatic 的写法？

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

## 示例输出 1 (对应示例输入 1 的正确 JSON 输出数组)
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

## 示例输出 2 (对应示例输入 2 的正确 JSON 输出数组)
```json
[]
```

# 输出格式
你的输出必须严格按照以下 JSON 格式输出一个审查结果JSON数组。数组中的每个对象代表一个具体的审查意见。
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
- **不允许**在 JSON 之外有任何其他内容。

如果提供的文件变更中没有发现任何需要反馈的问题（即没有达到 medium 或更高 severity 的问题），请返回一个**空的 JSON 数组**：`[]`。

现在，请根据上述指令和格式要求，审查我提供的代码变更输入，并输出严格符合格式要求的 JSON 数组。
"""
    all_reviews = []

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

        prompt = f"""

```json
{input_json_string}
```
"""
        try:
            logger.info(f"正在发送文件审查请求: {file_path}...")
            current_model = app_configs.get("OPENAI_MODEL", "gpt-4o")
            client = get_openai_client()  # Ensure client is fresh if settings changed
            if not client:
                logger.warning(f"在审查 {file_path} 前 OpenAI 客户端变得不可用。跳过。")
                return "[]"  # Or handle per-file error appropriately

            response = client.chat.completions.create(
                model=current_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            review_json_str = response.choices[0].message.content.strip()
            logger.info(f"-------------LLM 输出-----------")
            logger.info(f"文件 {file_path} 的 LLM 原始输出:")
            logger.info(f"{review_json_str}")
            logger.info(f"-------------LLM 输出-----------")

            try:
                parsed_output = json.loads(review_json_str)
                reviews_for_file = []
                if isinstance(parsed_output, list):
                    reviews_for_file = parsed_output
                elif isinstance(parsed_output, dict):
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
                        reviews_for_file = [parsed_output]  # Attempt to use the dict as a single review item
                else:
                    logger.warning(
                        f"警告: 文件 {file_path} 的 LLM 输出不是 JSON 列表或预期的字典。输出: {review_json_str}")

                valid_reviews_for_file = []
                for review in reviews_for_file:  # Ensure reviews_for_file is iterable
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
