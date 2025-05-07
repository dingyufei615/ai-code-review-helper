# AI Code Review Helper

AI Code Review Helper 是一个旨在自动化代码审查流程的工具。它通过集成版本控制系统（如 GitHub 和 GitLab）的 Webhook，利用大型语言模型（LLM）对代码变更进行分析，并将审查意见反馈到相应的 Pull Request 或 Merge Request 中。此外，它还支持将审查结果通知到企业微信等通讯工具。

## 主要功能

- **Webhook 集成**: 支持 GitHub 和 GitLab 的 Webhook，自动监听代码提交和变更事件。
- **智能代码分析**: 利用可配置的大型语言模型（如 OpenAI GPT 系列）对代码变更进行审查。
- **自动化评论**: 将 AI 生成的审查意见自动发布到 GitHub PR 或 GitLab MR 的评论中。
- **配置管理**:
    - 通过环境变量进行基础配置。
    - 提供 Web 管理面板 (`/admin`)，用于动态管理仓库/项目配置、LLM 参数以及通知设置。
    - 提供安全的 API 接口，用于通过编程方式管理配置。
    - 支持 Redis 进行配置持久化 (可选，主要用于仓库/项目配置和已处理 Commit 记录)。
- **通知服务**: 支持将 Code Review 完成的摘要信息发送到企业微信机器人。
- **防止重复处理**: 利用 Redis 记录已处理的 Commit SHA，避免对同一代码变更（如 PR 的 `synchronize` 事件）重复进行审查。
- **友好提示**: 当 AI 未发现代码问题时，会自动在 PR/MR 中发表表示检查通过的评论。
- **灵活部署**: 可以作为独立的 Web 服务运行，或通过 Docker 容器部署。

## 系统架构概览

1.  **VCS Webhooks**: GitHub/GitLab 在 PR/MR 事件发生时（如创建、更新）调用本应用部署的 Webhook URL。
2.  **Webhook 处理模块 (`webhook_routes.py`)**: 接收并验证来自 VCS 的请求，解析事件内容。
3.  **VCS 服务模块 (`vcs_service.py`)**: 与 GitHub/GitLab API 交互，获取代码变更详情 (diffs)，并在审查后发布评论。
4.  **LLM 服务模块 (`llm_service.py`)**: 将代码变更发送给配置的 LLM 进行分析，获取审查建议。
5.  **配置模块 (`core_config.py`, `config_routes.py`)**: 管理应用的全局配置（如 LLM API Key）、各仓库/项目的特定配置（如 Webhook Secret, VCS Token）以及 Redis 持久化逻辑。
6.  **通知模块 (`notification_service.py`)**: 将审查摘要发送到配置的通知渠道。
7.  **Web 应用 (`ai_code_review_helper.py`, `app_factory.py`)**: 基于 Flask 框架构建的 Web 服务，承载 Webhook 接口和管理面板。

## 安装与启动

### 前提条件

- Python 3.8+

### 步骤

1.  **克隆仓库** 
    ```bash
    git clone https://github.com/dingyufei615/ai-code-review-helper.git
    cd ai-code-review-helper
    ```

2.  **安装依赖**
    建议在虚拟环境中安装：
    ```bash
    python -m venv venv
    source venv/bin/activate  # macOS/Linux
    # venv\Scripts\activate    # Windows
    pip install -r requirements.txt 
    ```

3.  **配置环境变量**
    核心配置通过环境变量设置。请参考下面的 **环境变量** 部分。至少需要配置 `ADMIN_API_KEY`。为了使 AI 审查功能正常工作，还需要配置 OpenAI 相关的环境变量，如 `OPENAI_API_KEY`。

4.  **启动服务**
    ```bash
    python -m api.ai_code_review_helper
    ```
    服务默认启动在 `0.0.0.0:8088`。您可以通过环境变量 `SERVER_HOST` 和 `SERVER_PORT` 修改。

## 使用 Docker 部署

您可以使用 Docker 来运行此应用。镜像已发布到 Docker Hub。

1.  **拉取 Docker 镜像**:
    ```bash
    docker pull dingyufei/ai-code-review-helper:latest
    ```
    (或者，如果您想自行构建，可以在项目根目录下运行 `docker build -t ai-code-review-helper .`)

2.  **运行 Docker 容器**:
    ```bash
    docker run -d \
      -p 8088:8088 \
      -e ADMIN_API_KEY="your_strong_admin_api_key" \
      -e OPENAI_API_KEY="your_openai_api_key" \
      -e OPENAI_MODEL="gpt-4o" \
      -e OPENAI_API_BASE_URL="https://api.openai.com/v1" \
      -e GITHUB_API_URL="https://api.github.com" \
      -e GITLAB_INSTANCE_URL="https://gitlab.com" \
      -e WECOM_BOT_WEBHOOK_URL="your_wecom_bot_webhook_url" \
      -e REDIS_HOST="your_redis_host" \
      -e REDIS_PORT="6379" \
      -e REDIS_PASSWORD="your_redis_password" \
      -e REDIS_SSL_ENABLED="false" \
      -e REDIS_DB="0" \
      --name ai-review-app \
      dingyufei/ai-code-review-helper:latest
    ```
    **参数说明**:
    -   `-d`: 后台运行容器。
    -   `-p 8088:8088`: 将主机的 8088 端口映射到容器的 8088 端口。
    -   `-e VAR_NAME="value"`: 设置必要的环境变量。请务必替换为您的实际值。
        -   `ADMIN_API_KEY`: **必需**。自定义一串字符，用于保护管理接口。
        -   `OPENAI_API_KEY`: **必需**。您的 OpenAI API 密钥。
        -   `OPENAI_MODEL`: 使用的 OpenAI 模型 (例如 `gpt-4o`, `gpt-3.5-turbo`)。
        -   `OPENAI_API_BASE_URL`: (可选) OpenAI API 基础 URL。
        -   `GITHUB_API_URL`: (可选) GitHub API URL。
        -   `GITLAB_INSTANCE_URL`: (可选) GitLab 实例 URL (全局默认)。
        -   `WECOM_BOT_WEBHOOK_URL`: (可选) 企业微信机器人 Webhook URL。
        -   `REDIS_HOST`: (可选) Redis 服务器地址。如果提供，则配置将持久化到 Redis。
        -   `REDIS_PORT`: (可选) Redis 服务器端口 (默认: `6379`)。
        -   `REDIS_PASSWORD`: (可选) Redis 密码。
        -   `REDIS_SSL_ENABLED`: (可选) 是否为 Redis 连接启用 SSL (默认: `false`)。
        -   `REDIS_DB`: (可选) Redis 数据库编号 (默认: `0`)。
    -   `--name ai-review-app`: 为容器指定一个名称。

    部署后，应用将通过 `http://localhost:8088` (或您映射的主机端口) 访问。管理面板位于 `http://localhost:8088/admin`。

## 配置

### 1. 环境变量

以下是关键的环境变量，用于应用的基础配置：

-   `SERVER_HOST`: 应用监听的主机地址 (默认: `0.0.0.0`)。
-   `SERVER_PORT`: 应用监听的端口 (默认: `8088`)。
-   `ADMIN_API_KEY`: 访问配置管理 API 和管理面板的密钥 (**重要**: 请务必修改默认值 `change_this_unified_secret_key` 为一个强密钥)。
-   `OPENAI_API_BASE_URL`: OpenAI API 的基础 URL (默认: `https://api.openai.com/v1`)。可用于对接兼容 OpenAI API 的其他 LLM 服务 (如 Ollama)。
-   `OPENAI_API_KEY`: 您的 OpenAI API 密钥。
-   `OPENAI_MODEL`: 使用的 OpenAI 模型 (默认: `gpt-4o`)。
-   `GITHUB_API_URL`: GitHub API 的基础 URL (默认: `https://api.github.com`)。
-   `GITLAB_INSTANCE_URL`: 您的 GitLab 实例 URL (默认: `https://gitlab.com`)。此为全局默认值，可在单个项目配置中通过管理面板或 API 进行覆盖。
-   `WECOM_BOT_WEBHOOK_URL`: 企业微信机器人的 Webhook URL。如果为空，则禁用企业微信通知。
-   `REDIS_HOST`: (可选) Redis 服务器的主机名或 IP 地址。如果设置此项，应用将尝试使用 Redis 进行配置持久化。
-   `REDIS_PORT`: (可选) Redis 服务器端口 (默认: `6379`)。
-   `REDIS_PASSWORD`: (可选) 连接 Redis 所需的密码。
-   `REDIS_SSL_ENABLED`: (可选) 是否对 Redis 连接启用 SSL。设为 `true` 以启用 (默认: `false`)。
-   `REDIS_DB`: (可选) 要使用的 Redis 数据库编号 (默认: `0`)。

### 2. 管理面板

启动服务后，可以通过浏览器访问管理面板：`http://<your_server_host>:<your_server_port>/admin` (例如 `http://localhost:8088/admin`)。（为方便调试，管理页面写的较为简单）

首次访问或 Cookie 失效时，会提示输入 `Admin API Key`。

管理面板提供以下配置功能：

-   **GitHub 仓库配置**:
    -   添加/更新 GitHub 仓库的 `仓库全名 (owner/repo)`、`Webhook Secret` 和 `GitHub Access Token`。
    -   查看和删除已配置的仓库。
-   **GitLab 项目配置**:
    -   添加/更新 GitLab 项目的 `项目 ID`、`Webhook Secret`、`GitLab Access Token` 和可选的 `GitLab Instance URL` (如果特定项目需要指向不同的 GitLab 实例)。
    -   查看和删除已配置的项目。
-   **LLM 配置**:
    -   配置 `OpenAI API Base URL`、`OpenAI API Key` 和 `OpenAI Model`。这些设置会覆盖环境变量中的对应值，并优先于环境变量。
-   **通知配置**:
    -   配置 `企业微信机器人 Webhook URL`。

**配置持久化**:
- 如果配置了 Redis (`REDIS_HOST` 等环境变量)，通过管理面板或 API 进行的 GitHub 仓库配置和 GitLab 项目配置将**保存到 Redis** 中，服务重启后依然有效。
- 全局应用配置（如 LLM 设置、通知设置）通过管理面板或 API 修改后，目前仅在**内存中生效**，并优先于环境变量。服务重启后，这些全局配置会恢复到环境变量指定的值。为了持久化全局配置，建议主要通过环境变量进行设置。

### 3. 配置 API

除了管理面板，也可以通过 API 端点管理配置。所有配置 API 都需要 `X-Admin-API-Key` 请求头。

-   **全局配置**:
    -   `GET /config/global_settings`: 获取当前全局应用配置。
    -   `POST /config/global_settings`: 更新全局应用配置。请求体为 JSON 对象，包含要更新的键值对，例如：
        ```json
        {
            "OPENAI_MODEL": "gpt-4-turbo",
            "WECOM_BOT_WEBHOOK_URL": "your_new_wecom_url"
        }
        ```
-   **GitHub 仓库配置**:
    -   `GET /config/github/repos`: 列出已配置的 GitHub 仓库。
    -   `POST /config/github/repo`: 添加或更新一个 GitHub 仓库的配置。请求体：
        ```json
        {
            "repo_full_name": "owner/repo",
            "secret": "YOUR_GH_WEBHOOK_SECRET",
            "token": "YOUR_GITHUB_TOKEN"
        }
        ```
    -   `DELETE /config/github/repo/<owner>/<repo>`: 删除指定仓库的配置。
-   **GitLab 项目配置**:
    -   `GET /config/gitlab/projects`: 列出已配置的 GitLab 项目。
    -   `POST /config/gitlab/project`: 添加或更新一个 GitLab 项目的配置。请求体：
        ```json
        {
            "project_id": 123,
            "secret": "YOUR_GL_WEBHOOK_SECRET",
            "token": "YOUR_GITLAB_TOKEN",
            "instance_url": "https://gitlab.example.com"
        }
        ```
    -   `DELETE /config/gitlab/project/<project_id>`: 删除指定项目的配置。

## 使用方法

1.  **启动并配置服务**: 确保服务已运行，并通过环境变量或管理面板/API 完成了必要的配置（Admin API Key, LLM Keys, 目标仓库/项目的 Webhook Secret 和 Access Token）。

    -   **准备 GitHub Access Token**:
        为了让本应用能够读取 Pull Request 的变更内容并在 PR 中发表评论，您需要生成一个 GitHub Personal Access Token (PAT)。
        -   访问 GitHub -> `Settings` -> `Developer settings` -> `Personal access tokens` -> `Tokens (classic)`。
        -   点击 `Generate new token` (或 `Generate new token (classic)`)。
        -   **Note**: 给 Token 起一个描述性的名字，例如 `ai-code-review-helper-token`。
        -   **Expiration**: 根据您的安全策略选择合适的过期时间。
        -   **Select scopes**: 为了最小化权限，请仅勾选必要的权限。对于此应用，通常需要以下权限：
            -   `repo`: 完全控制私有仓库。如果您只用于公共仓库，可能只需要 `public_repo`。
                -   更细致地，如果您希望进一步限制，可以尝试仅勾选 `repo:status`, `repo_deployment`, `public_repo`, 和 `write:discussion` (如果需要评论 PR discussion) 以及 `pull_requests:write` (用于在 PR 中创建评论)。最核心的是能够读取 PR diff 和写入 PR 评论。**`repo` 权限是最简单直接的，但权限较大。请根据您的实际需求和安全评估进行选择。** 经过测试，为了能够读取 diff 和发表评论，至少需要 `repo` 范围下的 `Contents: Read-only` 和 `Pull requests: Read & write`。如果您的仓库是私有的，则需要完整的 `repo` 权限。
        -   点击 `Generate token`。
        -   **重要**: 生成 Token 后，请立即复制并妥善保存它。关闭页面后您将无法再次看到该 Token。
        -   此 Token 将用于后续在管理面板中配置 GitHub 仓库。

2.  **在 GitHub/GitLab 中设置 Webhook**:
    -   **GitHub**:
        -   进入目标 GitHub 仓库页面。
        -   点击 `Settings` (仓库设置)。
        -   在左侧导航栏中，选择 `Webhooks`。
        -   点击 `Add webhook` 按钮。
        -   **Payload URL**: 填入您的 AI Code Review Helper 服务暴露的 GitHub Webhook 地址，例如 `http://<your_server_host>:<your_server_port>/github_webhook`。
        -   **Content type**: 选择 `application/json`。
        -   **Secret**: 填入您为此仓库在 AI Code Review Helper 管理面板中配置的 `Webhook Secret`。这个 Secret 用于验证 Webhook 请求的来源。
        -   **Which events would you like to trigger this webhook?**: 选择 "Let me select individual events."。
            -   在展开的事件列表中，**仅勾选 "Pull requests"**。应用目前只处理 Pull Request 相关的事件。
        -   确保 "Active" 复选框已勾选。
        -   点击 `Add webhook`。
    -   **GitLab**:
        -   进入项目的 `Settings` -> `Webhooks`。
        -   **URL**: `http://<your_server_host>:<your_server_port>/gitlab_webhook`
        -   **Secret token**: 填入您为此项目在管理面板/API中配置的 `Webhook Secret`。
        -   **Trigger**: 勾选 "Merge request events"。
        -   点击 "Add webhook"。

3.  **触发 Code Review**:
    当在已配置的 GitHub 仓库中创建或更新 Pull Request，或在已配置的 GitLab 项目中创建或更新 Merge Request 时，相应的 Webhook 会被触发。
    AI Code Review Helper 将会：
    -   获取代码变更。
    -   调用 LLM进行分析。
    -   将审查意见作为评论发布到 PR/MR。
    -   （如果配置了）发送通知到企业微信。

## API 端点

-   `/admin`: 管理面板 HTML 页面。
-   `/github_webhook`: GitHub Webhook 接收端点。
-   `/gitlab_webhook`: GitLab Webhook 接收端点。
-   `/config/*`: (如上所述) 配置管理 API 端点，受 `X-Admin-API-Key` 保护。

## 注意事项

-   **安全性**: `ADMIN_API_KEY` 和各种 `Access Token`、`Webhook Secret` 是敏感信息，请妥善保管。确保应用部署在安全的环境中。
-   **LLM 成本**: 使用 OpenAI 等商业 LLM 服务会产生费用，请关注您的 API 调用量和相关成本。
-   **错误处理与日志**: 应用会在控制台输出详细的日志信息，包括请求处理、API 调用、错误等。请检查日志以进行故障排除。
-   **配置持久化**:
    - GitHub 和 GitLab 的仓库/项目配置（如 `secret`, `token`, `instance_url`）在配置 Redis 后，会通过管理面板或 API 持久化到 Redis。
    - 全局应用配置（如 OpenAI Key, WeCom URL）通过管理面板或 API 修改后，当前版本仅在内存中生效，并优先于环境变量。服务重启后，这些全局配置会从环境变量重新加载。建议通过环境变量管理核心全局配置。

## 贡献
本代码 90% 由[Aider](https://github.com/Aider-AI/aider) + Gemini协同完成。
欢迎提交 Pull Request 或 Issue 来改进此项目。
