import json
from openai import OpenAI
from api.core_config import app_configs

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
            print(
                "Warning: OpenAI API Key is not configured or is a placeholder. OpenAI client will not be initialized.")
            openai_client = None
            return

        if current_base_url and current_base_url != "https://api.openai.com/v1" and not current_base_url.endswith(
                '/v1'):
            # 确保 Ollama 等兼容 API 的 URL 格式正确
            if not current_base_url.endswith('/api') and not current_base_url.endswith('/'):  # 常见Ollama路径
                corrected_base_url = current_base_url.rstrip('/') + '/v1'  # 尝试附加 /v1
                print(
                    f"Correcting OpenAI API base URL from '{current_base_url}' to '{corrected_base_url}' for OpenAI library compatibility.")
                current_base_url = corrected_base_url
            else:
                print(f"Using custom OpenAI API base URL: {current_base_url}")

        if current_base_url and current_base_url != "https://api.openai.com/v1":
            print(f"Initializing OpenAI client with custom base URL: {current_base_url}")
            openai_client = OpenAI(
                base_url=current_base_url,
                api_key=current_api_key
            )
        else:
            print("Initializing OpenAI client with default OpenAI API endpoint.")
            openai_client = OpenAI(
                api_key=current_api_key
            )
        print(f"OpenAI client initialized/re-initialized. Model to be used: {current_model}")
    except Exception as e:
        print(f"Error initializing OpenAI client: {e}")
        print(
            "Please ensure OpenAI API Key is set and Base URL (if used) is correct via admin panel or environment variables.")
        openai_client = None


def get_openai_client():
    """获取 OpenAI 客户端实例，如果未初始化则尝试初始化。"""
    global openai_client
    if openai_client is None:
        print("OpenAI client is None, attempting to initialize...")
        initialize_openai_client()
    return openai_client


def get_openai_code_review(structured_file_changes):
    """使用 OpenAI API 对结构化的代码变更进行 review (源自 GitHub 版本，通用性较好)"""
    client = get_openai_client()
    if not client:
        print("OpenAI client not available (not initialized or failed to initialize). Skipping review.")
        return "[]"
    if not structured_file_changes:
        print("No structured changes provided to review.")
        return "[]"

    system_prompt = """
# 角色
你是专业的代码审查专家，擅长发现代码中的问题并提供改进建议。你的审查结果必须严格遵守输出格式要求。

# 审查维度及判断标准（按优先级排序）
1.  **正确性与健壮性**：代码是否能正确处理预期输入和边界情况？是否存在潜在的空指针、资源泄漏、并发问题？错误处理是否恰当？
2.  **安全性**：是否存在安全漏洞，如注入、XSS、不安全的依赖、敏感信息泄露？
3.  **可读性与可维护性**：命名是否规范？是否存在魔法数字或硬编码字符串？
4.  **性能**：是否存在明显的性能瓶颈？是否有不必要的计算或资源消耗？算法或数据结构是否最优？
5.  **设计与架构**：代码是否遵循良好的设计原则（如 SOLID）？模块化和封装是否合理？
6.  **最佳实践**：是否遵循了语言或框架的最佳实践？是否有更简洁或 Pythonic/Java-idiomatic 的写法？

# 输入数据格式
输入是一个 JSON 对象，包含单个文件的变更信息：
{
    "file_meta": {
        "path": "当前文件路径",
        "old_path": "原文件路径（重命名时存在，否则为null）",
        "lines_changed": "变更行数统计（仅add/delete）",
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
- old_line：content 在原文件中的行号，为null表示新增。
- new_line：content 在新文件中的行号，为null表示删除。
- context 包含变更区域附近的代码行，用于理解变更背景。

# 输出格式
1. 严格按照以下 JSON 格式输出一个审查结果JSON数组。数组中的每个对象代表一个具体的审查意见。不需要反馈小问题和吹毛求疵之处，只检查错误和可能存在安全隐患的地方。
[{"file":"文件路径","lines":{"old":原文件行号或null,"new":新文件行号或null},"category":"问题分类","severity":"严重程度(critical/high/medium/low)","analysis":"结合上下文的简短分析和审查意见(1-2句话简洁说明)","suggestion":"该位置纠正后的代码"}]
2. **行号处理规则**：
   - 如果是针对**新增**的代码行提出的建议，请将 `lines.old` 设为 `null`，`lines.new` 设为该新增代码在**新文件**中的行号 (对应输入 `changes` 中的 `new_line`)。
   - 如果是针对**删除**的代码行提出的建议（例如，指出删除不当或有更好替代方案），请将 `lines.old` 设为该删除代码在**原文件**中的行号 (对应输入 `changes` 中的 `old_line`)，`lines.new` 设为 `null`。
   - 如果建议涉及**修改**某行（即同时关联旧行和新行），优先关联到**新文件**的行号 (`lines.old=null`, `lines.new=新行号`)。
   - 如果建议是针对整个文件或无法精确到具体变更行，可以将 `lines` 设为 `{"old": null, "new": null}`。
   - **行号必须精确匹配输入数据 `changes` 中提供的具体变更行号**。请务必确保 `lines.old` 或 `lines.new` 至少有一个与输入 `changes` 数组中某项的 `old_line` 或 `new_line` 匹配。
3. 输出必须是**完整且合法的 JSON 字符串数组**。绝对不能包含任何 JSON 以外的解释性文字、代码块标记（如 ```json ... ```）、注释或任何其他非 JSON 内容。
4. **问题分类 (category)**：从 [正确性, 安全性, 性能, 设计, 最佳实践] 中选择最合适的。
5. **严重程度 (severity)**：根据问题潜在影响评估，从 [critical, high, medium, low] 中选择。
6. **分析 (analysis)**：简洁说明为什么这是一个问题，结合代码上下文。限制在 100 字以内，使用中文。
7. **建议 (suggestion)**：可直接接受使用的代码。
8. 如果某个文件没有发现任何问题，请不要为该文件生成任何输出对象。如果所有文件都没有问题，请返回一个空数组 `[]`。
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
            print(f"Error serializing input data for file {file_path}: {te}")
            print(f"Problematic data structure: {input_data}")
            continue

        prompt = f"""
请根据之前定义的角色、审查维度和输出格式，对以下文件变更进行审查。请务必严格按照要求的 JSON 数组格式返回审查结果，不要包含任何其他文字。

```json
{input_json_string}
```
"""
        try:
            print(f"Sending review request for file: {file_path}...")
            current_model = app_configs.get("OPENAI_MODEL", "gpt-4o")
            client = get_openai_client()  # Ensure client is fresh if settings changed
            if not client:
                print(f"OpenAI client became unavailable before reviewing {file_path}. Skipping.")
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
            print(f"-------------LLM Output-----------")
            print(f"LLM Raw Output for {file_path}:")
            print(f"{review_json_str}")
            print(f"-------------LLM Output-----------")

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
                            print(f"Found review list under key '{key}' in LLM output.")
                            break
                    if not found_list:
                        print(
                            f"Warning: LLM output for {file_path} is a dict, but no list value found. Output: {review_json_str}")
                        reviews_for_file = [parsed_output]
                else:
                    print(
                        f"Warning: LLM output for {file_path} is not a JSON list or expected dict. Output: {review_json_str}")

                valid_reviews_for_file = []
                for review in reviews_for_file:
                    if isinstance(review, dict) and all(
                            k in review for k in ["file", "lines", "category", "severity", "analysis", "suggestion"]):
                        if review.get("file") != file_path:
                            print(f"Warning: Fixing file path in review from '{review.get('file')}' to '{file_path}'")
                            review["file"] = file_path
                        valid_reviews_for_file.append(review)
                    else:
                        print(f"Warning: Skipping invalid review item structure for {file_path}: {review}")
                all_reviews.extend(valid_reviews_for_file)

            except json.JSONDecodeError as json_e:
                print(f"Error: Failed to parse JSON response from OpenAI for file {file_path}: {json_e}")
                print(f"LLM Raw Output was: {review_json_str}")
        except Exception as e:
            print(f"Error getting code review from OpenAI for file {file_path}: {e}")

    try:
        final_json_output = json.dumps(all_reviews, ensure_ascii=False, indent=2)
    except TypeError as te:
        print(f"Error serializing final review list: {te}")
        print(f"Problematic list structure: {all_reviews}")
        final_json_output = "[]"

    return final_json_output
