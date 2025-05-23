# AI Code Review Helper

一个基于 LLM 的自动化代码审查助手。通过 GitHub/GitLab Webhook 监听 PR/MR 变更，调用 AI 分析代码，并将审查意见自动评论到 PR/MR，同时支持多种通知渠道。

[点击观看B站演示视频](https://www.bilibili.com/video/BV1TF7ozaEPv)
> 由于 GitHub 的限制，无法直接嵌入视频播放器。

## 主要功能

- **多平台支持**: 集成 GitHub 和 GitLab Webhook，监听 Pull Request / Merge Request 事件。
- **智能审查模式**:
    - **详细审查 (`/github_webhook`, `/gitlab_webhook`)**: AI 对每个变更文件进行分析，旨在找出具体问题。审查意见会以结构化的形式（例如，定位到特定代码行、问题分类、严重程度、分析和建议）逐条评论到 PR/MR。AI 模型会输出 JSON 格式的分析结果，系统再将其转换为多条独立的评论。
    - **通用审查 (`/github_webhook_general`, `/gitlab_webhook_general`)**: AI 对每个变更文件进行整体性分析，并为每个文件生成一个 Markdown 格式的总结性评论。
- **自动化流程**:
    - 自动将 AI 审查意见（详细模式下为多条，通用模式下为每个文件一条）发布到 PR/MR。
    - 在所有文件审查完毕后，自动在 PR/MR 中发布一条总结性评论。
    - 即便 AI 未发现任何值得报告的问题，也会发布相应的友好提示和总结评论。
    - 异步处理审查任务，快速响应 Webhook。
    - 通过 Redis 防止对同一 Commit 的重复审查。
- **灵活配置**:
    - 通过环境变量设置基础配置。
    - 提供 Web 管理面板 (`/admin`) 和 API (`/config/*`)，用于管理：
        - GitHub/GitLab 仓库/项目的 Webhook Secret 和 Access Token。
        - LLM 参数（API Key, Base URL, Model）。
        - 通知 Webhook URL（企业微信、自定义 Webhook）。
        - 查看 AI 审查历史记录。
    - 使用 Redis 持久化存储配置和审查结果。
- **通知与记录**:
    - 将审查摘要（包含 PR/MR 链接、分支信息、审查结果概要）发送到企业微信和自定义 Webhook。
    - 在 Redis 中存储审查结果（默认7天过期），支持通过管理面板查阅，并自动清理已关闭/合并 PR/MR 的记录。
- **部署**: 支持 Docker 部署或直接运行 Python 应用。

## 🚀 快速开始

### 🐳 快速启动部署
```bash
# 使用官方镜像
docker run -d -p 8088:8088 \
  -e ADMIN_API_KEY="your-key" \
  -e OPENAI_API_BASE_URL="https://api.openai.com/v1" \
  -e OPENAI_API_KEY="your-key" \
  -e OPENAI_MODEL="gpt-4o" \
  -e REDIS_HOST="your-redis-host" \
  -e REDIS_PASSWORD="your-redis-pwd"
  --name ai-code-review-helper \
  dingyufei/ai-code-review-helper:latest
```

> 📌 必需环境变量：
> - `ADMIN_API_KEY` - 管理后台密码 默认值：change_this_unified_secret_key
> - `OPENAI_API_KEY` - AI服务密钥  
> - `REDIS_HOST` - Redis地址

## 配置

### 1. 环境变量 (部分关键)
-   `ADMIN_API_KEY`: **必需**。保护管理接口的密钥，默认值为`change_this_unified_secret_key` 请务必修改。
-   `OPENAI_API_KEY`: **必需**。OpenAI API 密钥。
-   `OPENAI_MODEL`: (默认: `gpt-4o`) 使用的 OpenAI 模型。
-   `OPENAI_API_BASE_URL`: (可选) OpenAI API 基础 URL，格式为：http(s)://xxxx/v1 默认值：https://api.openai.com/v1
-   `WECOM_BOT_WEBHOOK_URL`: (可选) 企业微信机器人 Webhook URL。
-   `REDIS_HOST`: **必需**。Redis 服务器地址。如果未配置或无法连接，服务将无法启动。
-   `REDIS_PORT`: (默认: `6379`) Redis 服务器端口。
-   `REDIS_PASSWORD`: (可选) Redis 密码。
-   `REDIS_DB`: (默认: `0`) Redis 数据库编号。
-   `REDIS_SSL_ENABLED`: (默认: `true`) 是否为 Redis 连接启用 SSL。设为 `false` 以禁用 SSL。
-   (更多变量如 `SERVER_HOST`, `SERVER_PORT`, `GITHUB_API_URL`, `GITLAB_INSTANCE_URL` 等请参考启动日志或源码。)

### 2. 管理面板与 API
- **管理面板 (`/admin`)**: 提供 Web 界面，用于：
    - 配置 GitHub 仓库和 GitLab 项目的 Webhook Secret 及 Access Token。
    - 管理全局 LLM 参数（OpenAI API Key, Base URL, Model）。
    - 设置全局通知 Webhook URL（企业微信机器人、自定义 Webhook）。
    - 查看和管理历史 AI 审查记录。
    - 所有管理操作均需通过环境变量 `ADMIN_API_KEY` 设置的密钥进行验证。
- **配置 API**: 提供 RESTful API (`/config/*`) 用于以编程方式管理上述配置，同样需要 `X-Admin-API-Key` 请求头进行认证。

**配置持久化**:
- **Redis (必需)**: 存储仓库/项目配置、已处理的 Commit SHA、AI 审查结果（默认7天过期，关闭/合并 PR/MR 后其关联记录也会被清理）。服务强依赖 Redis 运行。
- **环境变量**: 主要用于加载全局配置（如 LLM Key/URL, Redis 连接信息, Admin API Key）。通过管理面板对这些全局配置的修改在运行时立即生效，但若服务重启，它们会从环境变量重新加载。因此，建议通过环境变量来管理需要持久化的全局配置。

## 使用方法

1.  **启动服务**: 使用 Docker 或直接运行 Python 应用，确保已配置必要的环境变量（`ADMIN_API_KEY`, `OPENAI_API_KEY`, `REDIS_HOST` 等）。
2.  **配置仓库/项目**: 通过管理面板 (`/admin`) 或 API 添加目标 GitHub 仓库或 GitLab 项目的配置，包括 Webhook Secret 和具有读写 PR/MR 评论权限的 Access Token。
3.  **设置 Webhook**: 在 GitHub/GitLab 的仓库/项目设置中添加 Webhook：
    - **Payload URL**: 指向你的服务地址和相应的 Webhook 端点（见下文）。
    - **Content type**: `application/json`。
    - **Secret**: 填入上一步在管理面板中配置的 Webhook Secret。
    - **Events**: GitHub 选择 "Pull requests"，GitLab 勾选 "Merge request events"。
4.  **触发审查**: 创建或更新 PR/MR，服务将自动进行代码审查。

### 审查模式选择
- **详细审查** (端点: `/github_webhook`, `/gitlab_webhook`):
    - AI 尝试对每个文件的变更进行细致分析，目标是生成针对具体代码行（如果 AI 能定位）的、结构化的审查意见。
    - 每个问题会作为一条独立的评论发布到 PR/MR 的对应代码行（如果可能）或作为整体评论的一部分。
    - 此模式下，AI 模型被指示输出 JSON 格式的分析结果，系统随后将这些结果解析并转换为 PR/MR 中的评论。
    - 适用于指令遵循能力较强、能输出稳定 JSON 的模型 (如 GPT-4 系列)。如果评论不理想或缺失，请检查应用日志中 LLM 的原始输出是否符合预期的 JSON 格式。
- **通用审查** (端点: `/github_webhook_general`, `/gitlab_webhook_general`):
    - AI 对每个变更文件的整体内容进行分析，并为该文件生成一个单一的 Markdown 格式的审查评论。
    - 此模式适用于需要对文件变更进行宏观评估，或者当详细审查模式的模型输出不稳定时。

### 开发模式
```bash
# 1. 克隆仓库
git clone https://github.com/dingyufei615/ai-code-review-helper.git
cd ai-code-review-helper

# 2. 创建并激活虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量 (参考 .env.example 或 配置 部分)

# 5. 启动服务
python -m api.ai_code_review_helper

# 6. 运行测试 (可选)
python -m unittest discover tests
```

## 注意事项
- **安全**: 务必使用强 `ADMIN_API_KEY`，并妥善保管所有 Token 和 Secret。
- **成本**: 注意所使用 LLM 服务的 API 调用成本。
- **日志**: 服务会在控制台输出详细运行日志，便于排查问题。
- **Redis 依赖**: 服务强依赖 Redis 进行配置和结果存储。

## 贡献
本代码 90% 由 [Aider](https://github.com/Aider-AI/aider) + Gemini 协同完成。
欢迎提交 Pull Request 或 Issue。
