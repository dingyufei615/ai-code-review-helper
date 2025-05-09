# AI Code Review Helper

AI Code Review Helper 是一款自动化代码审查工具，通过集成 GitHub 和 GitLab Webhook，利用大型语言模型（LLM）分析代码变更，并将审查意见反馈到 Pull Request 或 Merge Request，同时支持企业微信通知。

## 主要功能

- **Webhook 集成**: 支持 GitHub 和 GitLab，自动监听代码变更。
- **智能代码分析**:
    - **详细行级审查**: 结构化 JSON 输出，定位到具体代码行。
    - **通用审查**: Markdown 格式整体审查意见。
- **自动化评论**: AI 审查意见自动发布到 PR/MR。
- **配置管理**:
    - 环境变量基础配置。
    - Web 管理面板 (`/admin`)：动态管理仓库/项目、LLM 及通知设置。
    - 安全 API 接口：编程方式管理配置。
    - Redis 持久化 (可选)：存储配置和已处理 Commit。
- **通知服务**: Code Review 摘要发送到企业微信。
- **防止重复处理**: Redis 记录已处理 Commit SHA。
- **友好提示**: AI 未发现问题时自动评论。
- **审查结果存储与查阅**: Redis 存储，管理面板查阅历史。
- **自动清理**: PR/MR 关闭或合并时，清理 Redis 相关记录。
- **结果有效期**: Redis 中审查结果默认7天后自动删除。
- **灵活部署**: 独立 Web 服务或 Docker 容器。

## 系统架构

应用通过以下模块协同工作：VCS Webhooks -> Webhook 处理 -> VCS 服务 -> LLM 服务 -> 配置管理 -> 通知服务 -> Web 应用 (Flask)。

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
-   `REDIS_HOST`: (可选) Redis 服务器地址，用于持久化。
-   (更多变量如 `SERVER_HOST`, `SERVER_PORT`, `GITHUB_API_URL`, `GITLAB_INSTANCE_URL`, `REDIS_PORT`, `REDIS_PASSWORD` 等请参考启动日志或源码。)

### 2. 管理面板 (`/admin`)
浏览器访问 `http://<your_server_host>:<your_server_port>/admin`。首次访问需输入 `Admin API Key`。
功能包括：
- GitHub/GitLab 仓库/项目配置 (Webhook Secret, Access Token, GitLab 实例 URL)。
- LLM 配置 (覆盖环境变量)。
- 通知配置 (企业微信 Webhook URL)。
- Admin API Key 设置。
- AI 审查记录查阅。

**配置持久化**:
- **Redis**: 仓库/项目配置、已处理 Commit SHA、AI 审查结果。
- **内存**: 全局应用配置 (LLM, 通知) 通过面板修改后仅内存生效，优先于环境变量；重启后从环境变量恢复。建议通过环境变量设置全局配置。

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
-   **配置持久化**: 仓库/项目配置和审查结果依赖 Redis；全局配置建议通过环境变量管理。

## 贡献
本代码 90% 由 [Aider](https://github.com/Aider-AI/aider) + Gemini 协同完成。
欢迎提交 Pull Request 或 Issue。
