# AI Code Review Helper

AI Code Review Helper 是一款自动化代码审查工具，通过集成 GitHub 和 GitLab Webhook，利用大型语言模型（LLM）分析代码变更，并将审查意见反馈到 Pull Request 或 Merge Request，同时支持企业微信通知。

## 主要功能

- **Webhook 集成**: 支持 GitHub 和 GitLab，自动监听代码变更。
- **智能代码分析**:
    - **详细行级审查**: 对每次提交的完整 diff 进行分析，输出结构化的 JSON 审查意见，可定位到具体代码行。
    - **通用审查**: 对每个变更的文件进行单独分析，输出 Markdown 格式的审查意见。
- **自动化评论**: AI 审查意见自动发布到 PR/MR。
- **异步处理**: Webhook 请求被快速接受，实际的代码分析和评论在后台异步执行，提高系统响应速度和吞吐量。
- **配置管理**:
    - 环境变量基础配置。
    - Web 管理面板 (`/admin`)：动态管理 GitHub/GitLab 仓库/项目配置 (Webhook Secret, Access Token, GitLab 实例 URL)、LLM 配置 (模型、API Key、Base URL)、通知配置 (企业微信 Webhook URL)，并可查阅 AI 审查记录。管理面板的访问和操作受 Admin API Key 保护。
    - 安全 API 接口：编程方式管理配置。
    - Redis 持久化：存储仓库/项目配置、已处理的 Commit SHA 以及 AI 审查结果。全局应用配置（如 LLM 设置、通知设置）主要通过环境变量设定，管理面板的修改在内存中生效并优先于环境变量，服务重启后会从环境变量重新加载。
- **通知服务**: Code Review 摘要发送到企业微信。
- **防止重复处理**: Redis 记录已处理 Commit SHA，避免对同一 Commit 的重复审查。
- **友好提示**: AI 未发现问题时自动评论。
- **审查结果存储与查阅**: Redis 存储，管理面板查阅历史。
- **自动清理**: PR/MR 关闭或合并时，清理 Redis 相关记录。
- **结果有效期**: Redis 中审查结果默认7天后自动删除。
- **灵活部署**: 独立 Web 服务或 Docker 容器。

## 系统架构

应用通过以下模块协同工作：VCS Webhooks -> Webhook 快速响应与任务分发 -> 异步任务执行 (VCS 服务交互、LLM 服务调用) -> 结果处理 (评论、通知、存储) -> 配置管理 -> Web 应用 (Flask)。

## 安装与部署

### 前提条件
- Python 3.8+

### 本地启动
1.  **克隆仓库**:
    ```bash
    git clone https://github.com/dingyufei615/ai-code-review-helper.git
    cd ai-code-review-helper
    ```
2.  **安装依赖**: (建议使用虚拟环境)
    ```bash
    python -m venv venv
    source venv/bin/activate  # macOS/Linux 或 venv\Scripts\activate for Windows
    pip install -r requirements.txt
    ```
3.  **配置环境变量**: 核心配置通过环境变量设置 (详见下文)。至少需配置 `ADMIN_API_KEY` 和 OpenAI 相关变量 (如 `OPENAI_API_KEY`)。
4.  **启动服务**:
    ```bash
    python -m api.ai_code_review_helper
    ```
    服务默认启动于 `0.0.0.0:8088` (可通过 `SERVER_HOST` 和 `SERVER_PORT` 修改)。

### Docker 部署
1.  **拉取/构建镜像**:
    ```bash
    docker pull dingyufei/ai-code-review-helper:latest
    # 或 docker build -t ai-code-review-helper .
    ```
2.  **运行容器**:
    ```bash
    docker run -d -p 8088:8088 \
      -e ADMIN_API_KEY="your_strong_admin_api_key" \
      -e OPENAI_API_KEY="your_openai_api_key" \
      # ... 其他必要环境变量 (见下文) ...
      --name ai-review-app \
      dingyufei/ai-code-review-helper:latest
    ```
    管理面板: `http://localhost:8088/admin`。

## 配置

### 1. 环境变量 (部分关键)
-   `ADMIN_API_KEY`: **必需**。保护管理接口的密钥。
-   `OPENAI_API_KEY`: **必需**。OpenAI API 密钥。
-   `OPENAI_MODEL`: (默认: `gpt-4o`) 使用的 OpenAI 模型。
-   `OPENAI_API_BASE_URL`: (可选) OpenAI API 基础 URL。
-   `WECOM_BOT_WEBHOOK_URL`: (可选) 企业微信机器人 Webhook URL。
-   `REDIS_HOST`: **必需**。Redis 服务器地址。如果未配置或无法连接，服务将无法启动。
-   `REDIS_PORT`: (默认: `6379`) Redis 服务器端口。
-   `REDIS_PASSWORD`: (可选) Redis 密码。
-   `REDIS_DB`: (默认: `0`) Redis 数据库编号。
-   `REDIS_SSL_ENABLED`: (默认: `true`) 是否为 Redis 连接启用 SSL。
-   (更多变量如 `SERVER_HOST`, `SERVER_PORT`, `GITHUB_API_URL`, `GITLAB_INSTANCE_URL` 等请参考启动日志或源码。)

### 2. 管理面板 (`/admin`)
浏览器访问 `http://<your_server_host>:<your_server_port>/admin`。首次访问或 Cookie 失效时，会提示输入 `Admin API Key` (该 Key 本身通过环境变量 `ADMIN_API_KEY` 设置，面板仅用于验证和临时保存于 Cookie)。
功能包括：
- **GitHub/GitLab 配置**: 添加、查看和删除各仓库/项目的 Webhook Secret、Access Token 以及 GitLab 项目特定的实例 URL。
- **LLM 配置**: 查看和修改 OpenAI API Base URL、API Key 和模型名称。这些修改在当前运行时优先于环境变量，服务重启后会恢复为环境变量的设置。
- **通知配置**: 查看和修改企业微信机器人的 Webhook URL。
- **AI 审查记录查阅**: 查看已完成的 AI 审查结果列表，并可点击查看特定 PR/MR 在不同 Commit 下的详细审查意见。

**配置持久化**:
- **Redis**: **必需**。用于存储仓库/项目配置 (如 Webhook Secret, Token)、已处理 Commit SHA 的集合、AI 审查结果 (包含详细评论内容，默认7天过期)。如果 Redis 未配置或无法连接，服务将无法启动。
- **内存与环境变量**: 全局应用配置 (如 OpenAI API Key/URL/Model, 企业微信 Webhook URL, Redis 连接参数等) 主要通过环境变量在服务启动时加载。管理面板对这些全局配置的修改仅在当前运行时内存中生效，并优先于环境变量；服务重启后将从环境变量重新加载。因此，对于需要持久化的全局配置，建议直接修改环境变量并重启服务。

### 3. 配置 API
通过 API 端点管理配置，需 `X-Admin-API-Key` 请求头。
-   `/config/global_settings` (GET, POST)
-   `/config/github/repo` (POST), `/config/github/repos` (GET), `/config/github/repo/<owner>/<repo>` (DELETE)
-   `/config/gitlab/project` (POST), `/config/gitlab/projects` (GET), `/config/gitlab/project/<project_id>` (DELETE)
-   `/config/review_results/list` (GET), `/config/review_results/<vcs_type>/<identifier>/<pr_mr_id>` (GET, 可选 `?commit_sha=<sha>`)

## 使用方法

1.  **启动并配置服务**: 确保服务运行，并通过环境变量或管理面板/API 完成必要配置 (Admin API Key, LLM Keys, 仓库/项目的 Webhook Secret 和 Access Token)。
    -   **GitHub Access Token**: 生成具有 `repo` (或更细粒度如 `Contents: Read-only` 和 `Pull requests: Read & write`) 权限的 PAT。

2.  **在 GitHub/GitLab 中设置 Webhook**:
    -   **GitHub**: 仓库 `Settings` -> `Webhooks` -> `Add webhook`。
        -   **Payload URL**: `http://<your_server_host>:<your_server_port>/github_webhook` (详细审查) 或 `/github_webhook_general` (通用审查)。
        -   **Content type**: `application/json`。
        -   **Secret**: 在管理面板中配置的 `Webhook Secret`。
        -   **Events**: 选择 "Pull requests"。
    -   **GitLab**: 项目 `Settings` -> `Webhooks`。
        -   **URL**: `http://<your_server_host>:<your_server_port>/gitlab_webhook` (详细审查) 或 `/gitlab_webhook_general` (通用审查)。
        -   **Secret token**: 在管理面板中配置的 `Webhook Secret`。
        -   **Trigger**: 勾选 "Merge request events"。

3.  **触发 Code Review**: 在配置的仓库/项目中创建或更新 PR/MR。应用将获取变更、调用 LLM 分析、发布评论，并发送通知。

## API 端点
-   `/admin`: 管理面板。
-   `/github_webhook`: GitHub 详细审查 Webhook。
-   `/gitlab_webhook`: GitLab 详细审查 Webhook。
-   `/github_webhook_general`: GitHub 通用审查 Webhook。
-   `/gitlab_webhook_general`: GitLab 通用审查 Webhook。
-   `/config/*`: 配置管理 API (详见上文)。

## 注意事项
-   **安全**: 妥善保管 `ADMIN_API_KEY`、Access Token 和 Webhook Secret。
-   **LLM 成本**: 关注商业 LLM 服务费用。
-   **日志**: 应用在控制台输出详细日志。
-   **配置持久化**: 服务运行**依赖 Redis** 进行仓库/项目配置和审查结果的存储。全局配置建议通过环境变量管理。

## 贡献
本代码 90% 由 [Aider](https://github.com/Aider-AI/aider) + Gemini 协同完成。
欢迎提交 Pull Request 或 Issue。
