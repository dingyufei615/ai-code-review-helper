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
REDIS_REVIEW_RESULTS_KEY_PREFIX = f"{REDIS_KEY_PREFIX}review_results:"


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
    global github_repo_configs, gitlab_project_configs # 确保修改的是全局变量
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
    # global redis_client # redis_client is already global
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
    # global redis_client # redis_client is already global
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


def remove_processed_commit_entries_for_pr_mr(vcs_type: str, identifier: str, pr_mr_id: str):
    """当 PR/MR 关闭或合并时，移除其所有相关的已处理 commit 条目。"""
    # global redis_client # redis_client is already global
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法移除已处理的提交条目。")
        return

    pr_mr_key_prefix = f"{vcs_type}:{identifier}:{str(pr_mr_id)}:"
    keys_to_remove_batch = []
    total_removed_count = 0
    batch_size = 100  # 每批删除100个key

    try:
        # 使用 SSCAN 迭代集合成员以避免阻塞
        for member_bytes in redis_client.sscan_iter(REDIS_PROCESSED_COMMITS_SET_KEY):
            member_str = member_bytes.decode('utf-8')
            if member_str.startswith(pr_mr_key_prefix):
                keys_to_remove_batch.append(member_str)

            if len(keys_to_remove_batch) >= batch_size:
                removed_in_batch = redis_client.srem(REDIS_PROCESSED_COMMITS_SET_KEY, *keys_to_remove_batch)
                total_removed_count += removed_in_batch
                logger.debug(
                    f"批处理：为 {vcs_type} {identifier} #{pr_mr_id} 从 Redis 中移除了 {removed_in_batch} 个条目。")
                keys_to_remove_batch = []  # 重置批处理列表

        # 处理最后一批不足 batch_size 的 key
        if keys_to_remove_batch:
            removed_in_batch = redis_client.srem(REDIS_PROCESSED_COMMITS_SET_KEY, *keys_to_remove_batch)
            total_removed_count += removed_in_batch
            logger.debug(
                f"最后批处理：为 {vcs_type} {identifier} #{pr_mr_id} 从 Redis 中移除了 {removed_in_batch} 个条目。")

        if total_removed_count > 0:
            logger.info(
                f"为 {vcs_type} {identifier} #{pr_mr_id} 从 Redis 中总共移除了 {total_removed_count} 个已处理的 commit 条目。")
        else:
            logger.info(
                f"在 Redis 中未找到与 {vcs_type} {identifier} #{pr_mr_id} 相关的已处理 commit 条目。")

    except redis.exceptions.RedisError as e:
        logger.error(
            f"为 {vcs_type} {identifier} #{pr_mr_id} 移除已处理的 commit 条目时 Redis 出错: {e}")
    except Exception as e:
        logger.error(
            f"为 {vcs_type} {identifier} #{pr_mr_id} 移除已处理的 commit 条目时发生意外错误: {e}")

    # 同时删除关联的审查结果
    delete_review_results_for_pr_mr(vcs_type, identifier, pr_mr_id)


def _get_review_results_redis_key(vcs_type: str, identifier: str, pr_mr_id: str) -> str:
    """生成用于存储特定 PR/MR 的 AI 审查结果的 Redis Key。"""
    return f"{REDIS_REVIEW_RESULTS_KEY_PREFIX}{vcs_type}:{identifier}:{str(pr_mr_id)}"


def save_review_results(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str, review_json_string: str, project_name: str = None):
    """将 AI 审查结果保存到 Redis。"""
    # global redis_client # redis_client is already global
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法保存 AI 审查结果。")
        return
    if not commit_sha:
        logger.warning(f"警告: commit_sha 为空，针对 {vcs_type}:{identifier}:{pr_mr_id}。跳过保存审查结果。")
        return

    redis_key = _get_review_results_redis_key(vcs_type, identifier, pr_mr_id)
    try:
        # 使用 pipeline 保证原子性
        pipe = redis_client.pipeline()
        pipe.hset(redis_key, commit_sha, review_json_string)
        if vcs_type == 'gitlab' and project_name:
            # 仅在首次或需要更新时设置项目名称
            # 如果 _project_name 已存在且不同，可以选择是否覆盖，这里简单覆盖
            pipe.hset(redis_key, "_project_name", project_name)
        
        # 为审查结果设置过期时间，例如7天，以避免无限增长
        pipe.expire(redis_key, 60 * 60 * 24 * 7) # 7 days
        pipe.execute()
        
        log_msg = f"成功将 {vcs_type} {identifier} #{pr_mr_id} (commit: {commit_sha}) 的审查结果保存到 Redis。"
        if vcs_type == 'gitlab' and project_name:
            log_msg += f" 项目名称: {project_name}。"
        logger.info(log_msg)
    except redis.exceptions.RedisError as e:
        logger.error(f"保存 AI 审查结果到 Redis 时出错 (Key: {redis_key}, Commit: {commit_sha}): {e}")


def get_review_results(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str = None):
    """从 Redis 获取 AI 审查结果。"""
    # global redis_client # redis_client is already global
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法获取 AI 审查结果。")
        return None if commit_sha else {}

    redis_key = _get_review_results_redis_key(vcs_type, identifier, pr_mr_id)
    try:
        if commit_sha:
            result_bytes = redis_client.hget(redis_key, commit_sha)
            if result_bytes:
                return json.loads(result_bytes.decode('utf-8'))
            return None
        else: # 获取 PR/MR 的所有 commits 的审查结果
            all_results_bytes = redis_client.hgetall(redis_key)
            decoded_results = {}
            project_name_for_pr_mr = None
            for field_bytes, value_bytes in all_results_bytes.items():
                field_str = field_bytes.decode('utf-8')
                try:
                    if field_str == "_project_name":
                        project_name_for_pr_mr = value_bytes.decode('utf-8')
                    else: # 这是一个 commit sha
                        decoded_results[field_str] = json.loads(value_bytes.decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    logger.error(f"解码/解析 Redis 中的审查结果时出错 (Key: {redis_key}, Field: {field_str}): {e}")
            
            # 将项目名称（如果存在）添加到返回结果中，但不作为 commit 结果的一部分
            # API 端点将决定如何使用它
            final_result = {"commits": decoded_results}
            if project_name_for_pr_mr:
                final_result["project_name"] = project_name_for_pr_mr
            return final_result
    except redis.exceptions.RedisError as e:
        logger.error(f"从 Redis 获取 AI 审查结果时出错 (Key: {redis_key}): {e}")
        return None if commit_sha else {}
    except json.JSONDecodeError as e:
        logger.error(f"解析从 Redis 获取的 AI 审查结果 JSON 时出错 (Key: {redis_key}): {e}")
        return None if commit_sha else {}


def get_all_reviewed_prs_mrs_keys():
    """获取所有已存储 AI 审查结果的 PR/MR 的 Redis Key 列表。"""
    # global redis_client # redis_client is already global
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法获取已审查的 PR/MR 列表。")
        return []
    
    keys = []
    try:
        # 使用 SCAN 迭代匹配的 key，以避免阻塞
        cursor = '0'
        while cursor != 0:
            cursor, current_keys = redis_client.scan(cursor=cursor, match=f"{REDIS_REVIEW_RESULTS_KEY_PREFIX}*", count=100)
            keys.extend([key.decode('utf-8') for key in current_keys])
        
        # 从 Key 中提取可读的 PR/MR 标识符
        # 例如: "ai_code_review_helper:review_results:github:owner/repo:123" -> "github:owner/repo:123"
        # 或者可以返回更结构化的信息
        identifiers = []
        for key in keys:
            try:
                # "ai_code_review_helper:review_results:github:owner/repo:123"
                parts = key.split(':')
                if len(parts) >= 4: # prefix, vcs_type, identifier, pr_mr_id (identifier can contain ':')
                    vcs_type_full = parts[2] # e.g., "github", "gitlab", "github_general", "gitlab_general"
                    pr_mr_id = parts[-1]
                    identifier_parts = parts[3:-1]
                    identifier_str = ":".join(identifier_parts)

                    display_identifier = identifier_str
                    display_vcs_type_prefix = vcs_type_full.upper()

                    # 统一处理 GitLab 项目名称获取
                    if vcs_type_full.startswith('gitlab'):
                        try:
                            project_name_bytes = redis_client.hget(key, "_project_name")
                            if project_name_bytes:
                                display_identifier = project_name_bytes.decode('utf-8')
                                logger.debug(f"找到 GitLab 项目名称 '{display_identifier}' 用于 Key '{key}' (VCS Type: {vcs_type_full})")
                            else:
                                logger.debug(f"未在 Key '{key}' 中找到 GitLab 项目名称 (VCS Type: {vcs_type_full})，将使用 ID '{identifier_str}'。")
                        except Exception as e_proj_name:
                            logger.error(f"从 Redis Key '{key}' (VCS Type: {vcs_type_full}) 获取项目名称时出错: {e_proj_name}")
                    
                    # 规范化显示名称中的类型
                    if vcs_type_full == "github_general":
                        display_vcs_type_prefix = "GITHUB (General)"
                    elif vcs_type_full == "gitlab_general":
                        display_vcs_type_prefix = "GITLAB (General)"
                    elif vcs_type_full == "github":
                         display_vcs_type_prefix = "GITHUB (Detailed)"
                    elif vcs_type_full == "gitlab":
                         display_vcs_type_prefix = "GITLAB (Detailed)"


                    identifiers.append({
                        "key": key, 
                        "vcs_type": vcs_type_full, # 存储原始的 vcs_type，例如 github_general
                        "identifier": identifier_str, 
                        "pr_mr_id": pr_mr_id,
                        "display_name": f"{display_vcs_type_prefix}: {display_identifier} #{pr_mr_id}"
                    })
            except Exception as e:
                logger.error(f"解析审查结果 Redis Key '{key}' 时出错: {e}")
        return identifiers
    except redis.exceptions.RedisError as e:
        logger.error(f"从 Redis 获取所有已审查的 PR/MR 列表时出错: {e}")
        return []


def delete_review_results_for_pr_mr(vcs_type: str, identifier: str, pr_mr_id: str):
    """删除特定 PR/MR 的所有 AI 审查结果。"""
    # global redis_client # redis_client is already global
    if not redis_client:
        logger.warning("Redis 客户端不可用，无法删除 AI 审查结果。")
        return

    redis_key = _get_review_results_redis_key(vcs_type, identifier, pr_mr_id)
    try:
        deleted_count = redis_client.delete(redis_key)
        if deleted_count > 0:
            logger.info(f"成功从 Redis 删除 {vcs_type} {identifier} #{pr_mr_id} 的 AI 审查结果 (Key: {redis_key})。")
        else:
            logger.info(f"在 Redis 中未找到 {vcs_type} {identifier} #{pr_mr_id} 的 AI 审查结果以供删除 (Key: {redis_key})。")
    except redis.exceptions.RedisError as e:
        logger.error(f"从 Redis 删除 AI 审查结果时出错 (Key: {redis_key}): {e}")


# --- 仓库/项目特定配置存储 (内存字典, 会被 Redis 数据填充) ---
# GitHub 仓库配置
# key: repository_full_name (string, e.g., "owner/repo"), value: {"secret": "webhook_secret", "token": "github_access_token"}
github_repo_configs = {}

# GitLab 项目配置
# key: project_id (string), value: {"secret": "webhook_secret", "token": "gitlab_access_token", "instance_url": "custom_instance_url"}
gitlab_project_configs = {}
# --- ---
