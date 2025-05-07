import os
import json
import redis
import logging

logger = logging.getLogger(__name__)

# --- 全局配置 ---
# 服务器配置
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8088"))  # 应用端口 (统一端口)

# 配置管理 API Key (用于保护配置接口)
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "change_this_unified_secret_key")  # 强烈建议修改此默认值

# --- 应用可配置项 (内存字典，初始值从环境变量加载，可被 API 修改) ---
app_configs = {
    "OPENAI_API_BASE_URL": os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "xxxx-xxxx-xxxx-xxxx"),
    "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "gpt-4o"),
    "GITHUB_API_URL": os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    "GITLAB_INSTANCE_URL": os.environ.get("GITLAB_INSTANCE_URL", "https://gitlab.com"),
    "WECOM_BOT_WEBHOOK_URL": os.environ.get("WECOM_BOT_WEBHOOK_URL", ""),
    # Redis 配置 (新增)
    "REDIS_HOST": os.environ.get("REDIS_HOST", None),
    "REDIS_PORT": int(os.environ.get("REDIS_PORT", "6379")),
    "REDIS_PASSWORD": os.environ.get("REDIS_PASSWORD", None),
    "REDIS_SSL_ENABLED": os.environ.get("REDIS_SSL_ENABLED", "true").lower() == "true",
    "REDIS_DB": int(os.environ.get("REDIS_DB", "0")),
}
# --- ---

# --- Redis 客户端实例 ---
redis_client = None
REDIS_KEY_PREFIX = "ai_code_review_helper:"
REDIS_GITHUB_CONFIGS_KEY = f"{REDIS_KEY_PREFIX}github_repo_configs"
REDIS_GITLAB_CONFIGS_KEY = f"{REDIS_KEY_PREFIX}gitlab_project_configs"
REDIS_PROCESSED_COMMITS_SET_KEY = f"{REDIS_KEY_PREFIX}processed_commits_set"


def init_redis_client():
    """初始化全局 Redis 客户端。"""
    global redis_client
    redis_host = app_configs.get("REDIS_HOST")
    if redis_host:
        try:
            logger.info(f"尝试连接到 Redis: {redis_host}:{app_configs.get('REDIS_PORT')}")
            redis_client = redis.Redis(
                host=redis_host,
                port=app_configs.get("REDIS_PORT"),
                password=app_configs.get("REDIS_PASSWORD"),
                ssl=app_configs.get("REDIS_SSL_ENABLED"),
                db=app_configs.get("REDIS_DB"),
                socket_connect_timeout=5  # 5 seconds timeout
            )
            redis_client.ping()  # 验证连接
            logger.info("成功连接到 Redis。")
        except redis.exceptions.ConnectionError as e:
            logger.error(f"连接 Redis 出错: {e}。将回退到内存存储。")
            redis_client = None
        except Exception as e:
            logger.error(f"Redis 初始化期间发生意外错误: {e}。将回退到内存存储。")
            redis_client = None
    else:
        logger.info("Redis 未配置 (REDIS_HOST 未设置)。配置将使用内存存储。")


def load_configs_from_redis():
    """如果 Redis 可用，则从 Redis 加载配置到内存中。"""
    global github_repo_configs, gitlab_project_configs
    if redis_client:
        try:
            # 加载 GitHub 配置
            github_data_raw = redis_client.hgetall(REDIS_GITHUB_CONFIGS_KEY)
            for key_raw, value_raw in github_data_raw.items():
                try:
                    key = key_raw.decode('utf-8')
                    value_str = value_raw.decode('utf-8')
                    github_repo_configs[key] = json.loads(value_str)
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    logger.error(f"解码/解析 GitHub 配置时出错，键: {key_raw}: {e}")
            if github_data_raw:
                logger.info(f"从 Redis 加载了 {len(github_repo_configs)} 个 GitHub 配置。")

            # 加载 GitLab 配置
            gitlab_data_raw = redis_client.hgetall(REDIS_GITLAB_CONFIGS_KEY)
            for key_raw, value_raw in gitlab_data_raw.items():
                try:
                    key = key_raw.decode('utf-8')
                    value_str = value_raw.decode('utf-8')
                    gitlab_project_configs[key] = json.loads(value_str)
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    logger.error(f"解码/解析 GitLab 配置时出错，键: {key_raw}: {e}")
            if gitlab_data_raw:
                logger.info(f"从 Redis 加载了 {len(gitlab_project_configs)} 个 GitLab 配置。")
        except redis.exceptions.RedisError as e:
            logger.error(f"从 Redis 加载配置时 Redis 出错: {e}。内存中的配置可能不完整。")
        except Exception as e:
            logger.error(f"从 Redis 加载配置时发生意外错误: {e}。")
    else:
        logger.info("Redis 客户端不可用。跳过从 Redis 加载配置。")


def _get_processed_commit_key(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str) -> str:
    """生成用于存储已处理 commit 的唯一键。"""
    return f"{vcs_type}:{identifier}:{pr_mr_id}:{commit_sha}"


def is_commit_processed(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str) -> bool:
    """检查指定的 commit 是否已经被处理过。"""
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法检查提交是否已处理。假定未处理。")
        return False
    if not commit_sha:  # 如果 commit_sha 为空，则不应视为已处理
        logger.warning(f"警告: commit_sha 为空，针对 {vcs_type}:{identifier}:{pr_mr_id}。假定未处理。")
        return False

    key = _get_processed_commit_key(vcs_type, identifier, str(pr_mr_id), commit_sha)
    try:
        return redis_client.sismember(REDIS_PROCESSED_COMMITS_SET_KEY, key)
    except redis.exceptions.RedisError as e:
        logger.error(f"检查提交 {key} 是否已处理时 Redis 出错: {e}。假定未处理。")
        return False


def mark_commit_as_processed(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str):
    """将指定的 commit 标记为已处理。"""
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法标记提交为已处理。")
        return
    if not commit_sha:  # 如果 commit_sha 为空，则不应标记
        logger.warning(f"警告: commit_sha 为空，针对 {vcs_type}:{identifier}:{pr_mr_id}。跳过标记为已处理。")
        return

    key = _get_processed_commit_key(vcs_type, identifier, str(pr_mr_id), commit_sha)
    try:
        redis_client.sadd(REDIS_PROCESSED_COMMITS_SET_KEY, key)
        logger.info(f"成功标记提交 {key} 为已处理。")
    except redis.exceptions.RedisError as e:
        logger.error(f"标记提交 {key} 为已处理时 Redis 出错: {e}")


# --- 仓库/项目特定配置存储 (内存字典, 会被 Redis 数据填充) ---
# GitHub 仓库配置
# key: repository_full_name (string, e.g., "owner/repo"), value: {"secret": "webhook_secret", "token": "github_access_token"}
github_repo_configs = {}

# GitLab 项目配置
# key: project_id (string), value: {"secret": "webhook_secret", "token": "gitlab_access_token", "instance_url": "custom_instance_url"}
gitlab_project_configs = {}
# --- ---
