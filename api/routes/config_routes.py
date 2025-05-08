from flask import request, jsonify
import json 
import logging 
from api.app_factory import app
from api.core_config import (
    app_configs, github_repo_configs, gitlab_project_configs,
    REDIS_GITHUB_CONFIGS_KEY, REDIS_GITLAB_CONFIGS_KEY,
    get_all_reviewed_prs_mrs_keys, get_review_results # 新增导入
)
import api.core_config as core_config_module  # 访问 redis_client 的推荐方式
from api.utils import require_admin_key
from api.services.llm_service import initialize_openai_client

logger = logging.getLogger(__name__)


# GitHub Configuration Management
@app.route('/config/github/repo', methods=['POST'])
@require_admin_key
def add_or_update_github_repo_config():
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    repo_full_name = data.get('repo_full_name')
    secret = data.get('secret')
    token = data.get('token')
    if not repo_full_name or not secret or not token:
        return jsonify({"error": "Missing required fields: repo_full_name, secret, token"}), 400

    config_data = {"secret": secret, "token": token}
    github_repo_configs[repo_full_name] = config_data

    if core_config_module.redis_client:
        try:
            core_config_module.redis_client.hset(REDIS_GITHUB_CONFIGS_KEY, repo_full_name, json.dumps(config_data))
            logger.info(f"GitHub 配置 {repo_full_name} 已保存到 Redis。")
        except Exception as e:
            logger.error(f"保存 GitHub 配置 {repo_full_name} 到 Redis 时出错: {e}")
            # 继续执行，至少内存中已更新

    logger.info(f"为仓库添加/更新了 GitHub 配置: {repo_full_name}")
    return jsonify({"message": f"Configuration for GitHub repository {repo_full_name} added/updated."}), 200


@app.route('/config/github/repo/<path:repo_full_name>', methods=['DELETE'])
@require_admin_key
def delete_github_repo_config(repo_full_name):
    if repo_full_name in github_repo_configs:
        del github_repo_configs[repo_full_name]
        if core_config_module.redis_client:
            try:
                core_config_module.redis_client.hdel(REDIS_GITHUB_CONFIGS_KEY, repo_full_name)
                logger.info(f"GitHub 配置 {repo_full_name} 已从 Redis 删除。")
            except Exception as e:
                logger.error(f"从 Redis 删除 GitHub 配置 {repo_full_name} 时出错: {e}")
                # 继续执行，至少内存中已删除
        logger.info(f"为仓库删除了 GitHub 配置: {repo_full_name}")
        return jsonify({"message": f"Configuration for GitHub repository {repo_full_name} deleted."}), 200
    return jsonify({"error": f"Configuration for GitHub repository {repo_full_name} not found."}), 404


@app.route('/config/github/repos', methods=['GET'])
@require_admin_key
def list_github_repo_configs():
    return jsonify({"configured_github_repositories": list(github_repo_configs.keys())}), 200


# GitLab Configuration Management
@app.route('/config/gitlab/project', methods=['POST'])
@require_admin_key
def add_or_update_gitlab_project_config():
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    project_id = data.get('project_id')
    secret = data.get('secret')
    token = data.get('token')
    instance_url = data.get('instance_url')  # 新增

    if not project_id or not secret or not token:  # instance_url 是可选的
        return jsonify({"error": "Missing required fields: project_id, secret, token"}), 400

    project_id_str = str(project_id)
    config_data = {"secret": secret, "token": token}
    if instance_url:  # 只有当用户提供时才存储
        config_data["instance_url"] = instance_url

    gitlab_project_configs[project_id_str] = config_data
    if core_config_module.redis_client:
        try:
            core_config_module.redis_client.hset(REDIS_GITLAB_CONFIGS_KEY, project_id_str, json.dumps(config_data))
            logger.info(f"GitLab 配置 {project_id_str} 已保存到 Redis。")
        except Exception as e:
            logger.error(f"保存 GitLab 配置 {project_id_str} 到 Redis 时出错: {e}")
            # 继续执行，至少内存中已更新

    logger.info(
        f"为项目 ID 添加/更新了 GitLab 配置: {project_id_str}。实例 URL: {instance_url if instance_url else '默认'}")
    return jsonify({"message": f"Configuration for GitLab project {project_id_str} added/updated."}), 200


@app.route('/config/gitlab/project/<string:project_id>', methods=['DELETE'])
@require_admin_key
def delete_gitlab_project_config(project_id):
    project_id_str = str(project_id)
    if project_id_str in gitlab_project_configs:
        del gitlab_project_configs[project_id_str]
        if core_config_module.redis_client:
            try:
                core_config_module.redis_client.hdel(REDIS_GITLAB_CONFIGS_KEY, project_id_str)
                logger.info(f"GitLab 配置 {project_id_str} 已从 Redis 删除。")
            except Exception as e:
                logger.error(f"从 Redis 删除 GitLab 配置 {project_id_str} 时出错: {e}")
                # 继续执行，至少内存中已删除
        logger.info(f"为项目 ID 删除了 GitLab 配置: {project_id_str}")
        return jsonify({"message": f"Configuration for GitLab project {project_id_str} deleted."}), 200
    return jsonify({"error": f"Configuration for GitLab project {project_id_str} not found."}), 404


@app.route('/config/gitlab/projects', methods=['GET'])
@require_admin_key
def list_gitlab_project_configs():
    return jsonify({"configured_gitlab_projects": list(gitlab_project_configs.keys())}), 200


# --- Global Application Configuration Management ---
@app.route('/config/global_settings', methods=['GET'])
@require_admin_key
def get_global_settings():
    # Return a copy of app_configs. Sensitive keys like actual API keys might be masked if needed,
    # but for admin interface, they are usually shown.
    # Exclude ADMIN_API_KEY itself as it's not managed here.
    settings_to_return = {k: v for k, v in app_configs.items()}
    return jsonify(settings_to_return), 200


@app.route('/config/global_settings', methods=['POST'])
@require_admin_key
def update_global_settings():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()

    updated_keys = []
    openai_config_changed = False
    for key in app_configs.keys():  # Only update keys that are defined in app_configs
        if key in data:
            if app_configs[key] != data[key]:  # Check if value actually changed
                app_configs[key] = data[key]
                updated_keys.append(key)
                if key in ["OPENAI_API_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"]:
                    openai_config_changed = True

    if openai_config_changed:
        logger.info("OpenAI 相关配置已更新，正在重新初始化 OpenAI 客户端...")
        initialize_openai_client()

    if updated_keys:
        logger.info(f"全局设置已更新，涉及键: {', '.join(updated_keys)}")
        # Here you might want to persist app_configs to a file or database if needed beyond memory storage
        return jsonify({"message": f"Global settings updated for: {', '.join(updated_keys)}"}), 200
    else:
        return jsonify({"message": "No settings were updated or values provided matched existing configuration."}), 200


# --- AI Code Review Results Endpoints ---
@app.route('/config/review_results/list', methods=['GET'])
@require_admin_key
def list_reviewed_prs_mrs():
    """列出所有已存储 AI 审查结果的 PR/MR。"""
    reviewed_items = get_all_reviewed_prs_mrs_keys()
    if reviewed_items is None: # 可能因为 Redis 错误返回 None
        return jsonify({"error": "无法从 Redis 获取审查结果列表。"}), 500
    return jsonify({"reviewed_pr_mr_list": reviewed_items}), 200


@app.route('/config/review_results/<string:vcs_type>/<path:identifier>/<string:pr_mr_id>', methods=['GET'])
@require_admin_key
def get_specific_review_results(vcs_type, identifier, pr_mr_id):
    """
    获取特定 PR/MR 的 AI 审查结果。
    可以通过查询参数 ?commit_sha=<sha> 来获取特定 commit 的结果。
    """
    commit_sha = request.args.get('commit_sha', None)
    
    # 验证 vcs_type 是否有效
    if vcs_type not in ['github', 'gitlab']:
        return jsonify({"error": "无效的 VCS 类型。只支持 'github' 或 'gitlab'。"}), 400

    logger.info(f"请求审查结果: VCS={vcs_type}, ID={identifier}, PR/MR ID={pr_mr_id}, Commit SHA={commit_sha if commit_sha else '所有'}")

    results = get_review_results(vcs_type, identifier, pr_mr_id, commit_sha)

    if results is None and commit_sha: # 特定 commit 未找到
        return jsonify({"error": f"未找到针对 commit {commit_sha} 的审查结果。"}), 404
    if not results and not commit_sha: # PR/MR 整体未找到结果 (空字典也算找到)
         # get_review_results 在找不到 key 时返回 {}，所以这里需要区分 None 和 {}
        if results is None: # 意味着 Redis 错误
            return jsonify({"error": "从 Redis 获取审查结果时出错。"}), 500
        # 如果是空字典，表示该 PR/MR 的 key 存在，但没有 commit 的审查结果，或者所有 commit 的结果都被清除了
        # 这种情况应该返回空列表或空对象，而不是 404
        logger.info(f"未找到 {vcs_type}/{identifier}#{pr_mr_id} 的审查结果，或结果为空。")


    # 如果是获取特定 commit 的结果，且 results 不是 None (即找到了)
    if commit_sha and results is not None:
        return jsonify({"commit_sha": commit_sha, "review_data": results}), 200
    # 如果是获取 PR/MR 的所有 commits 的结果
    elif not commit_sha:
        # results 此时是一个字典 {"commits": {commit_sha: review_data, ...}, "project_name": "optional_name"}
        response_data = {
            "pr_mr_id": pr_mr_id,
            "all_reviews_by_commit": results.get("commits", {}) 
        }
        if "project_name" in results:
            response_data["project_name"] = results["project_name"]
        
        # identifier in the URL for GitLab is project_id. If we have project_name,
        # we can also add it here for convenience, though display_name in the list view handles this.
        # For GitHub, identifier is owner/repo, which is already the display name.
        if vcs_type == 'gitlab' and "project_name" in results:
            response_data["display_identifier"] = results["project_name"]
        else:
            response_data["display_identifier"] = identifier # Default to identifier from URL

        return jsonify(response_data), 200
    
    # 理论上不应该到这里，但作为保险
    return jsonify({"error": "无法检索审查结果。"}), 500
