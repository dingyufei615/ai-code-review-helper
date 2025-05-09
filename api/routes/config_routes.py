from flask import request, jsonify
import json 
import logging 
from api.app_factory import app
from flask import request, jsonify
import json 
import logging 
from api.app_factory import app
from api.core_config import (
    app_configs, github_repo_configs, gitlab_project_configs,
    REDIS_GITHUB_CONFIGS_KEY, REDIS_GITLAB_CONFIGS_KEY,
    get_all_reviewed_prs_mrs_keys, get_review_results, delete_review_results_for_pr_mr # 新增导入
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

    # 允许的 vcs_type 包括详细审查和通用审查
    allowed_vcs_types = ['github', 'gitlab', 'github_general', 'gitlab_general']
    if vcs_type not in allowed_vcs_types:
        return jsonify({"error": f"无效的 VCS 类型。支持的类型: {', '.join(allowed_vcs_types)}。"}), 400

    logger.info(f"请求审查结果: VCS={vcs_type}, ID={identifier}, PR/MR ID={pr_mr_id}, Commit SHA={commit_sha if commit_sha else '所有'}")

    results = get_review_results(vcs_type, identifier, pr_mr_id, commit_sha)

    # 情况1: Redis 服务出错 (get_review_results 会返回 None)
    # 注意: get_review_results 在获取所有 commits (commit_sha=None) 且 Redis 错误时返回 {}，
    # 但如果内部判断到 Redis 错误，它应该返回 None 或者一个明确的错误指示。
    # 假设 get_review_results 在 Redis 错误时总是返回 None，或者在获取所有 commits 时返回一个包含错误信息的特殊结构。
    # 当前 get_review_results 实现:
    # - commit_sha provided: returns None on error or not found.
    # - commit_sha not provided: returns {"commits": {...}, "project_name": "..."} or {} on error.
    # 为了简化，我们先判断 results 是否为 None，这明确表示了 get_review_results 遇到了问题或未找到特定 commit。

    if results is None: # 明确表示 Redis 错误或特定 commit 未找到
        if commit_sha:
            logger.info(f"未找到 {vcs_type}/{identifier}#{pr_mr_id} 针对 commit {commit_sha} 的审查结果。")
            return jsonify({"error": f"未找到针对 commit {commit_sha} 的审查结果。"}), 404
        else:
            # 如果是请求所有 commits (commit_sha is None) 且 results is None，
            # 这在当前 get_review_results 实现中不应该发生（它会返回 {}）。
            # 但如果发生了，则视为 Redis 错误。
            logger.error(f"从 Redis 获取 {vcs_type}/{identifier}#{pr_mr_id} 的所有审查结果时出错或未找到顶级键。")
            return jsonify({"error": "从 Redis 获取审查结果时出错或未找到该 PR/MR 的记录。"}), 500


    if commit_sha: # 请求特定 commit 的结果
        # results 在这里不为 None，意味着找到了该 commit 的数据
        return jsonify({"commit_sha": commit_sha, "review_data": results}), 200
    else: # 请求 PR/MR 的所有 commits 的结果 (results 是一个字典，可能包含 "commits" 和 "project_name")
        all_commits_reviews = results.get("commits", {})
        project_name = results.get("project_name")

        if not all_commits_reviews and not project_name:
             # 如果 "commits" 和 "project_name" 都不存在，且 results 不是 None (例如是 {})
             # 这意味着 PR/MR 的 Redis key 可能存在，但里面是空的，或者 get_review_results 返回了空字典表示未找到
            logger.info(f"未找到 {vcs_type}/{identifier}#{pr_mr_id} 的任何审查结果，或结果为空。")
            # 返回空数据而不是404，因为父记录可能存在但无内容
            response_data = {
                "pr_mr_id": pr_mr_id,
                "all_reviews_by_commit": {},
                "display_identifier": identifier # 默认显示标识符
            }
            if vcs_type == 'gitlab' and project_name: # 即使 all_commits_reviews 为空，也可能想显示项目名
                response_data["project_name"] = project_name
                response_data["display_identifier"] = project_name
            return jsonify(response_data), 200

        response_data = {
            "pr_mr_id": pr_mr_id,
            "all_reviews_by_commit": all_commits_reviews
        }
        if project_name:
            response_data["project_name"] = project_name
        
        if vcs_type == 'gitlab' and project_name:
            response_data["display_identifier"] = project_name
        else:
            response_data["display_identifier"] = identifier

        return jsonify(response_data), 200


@app.route('/config/review_results/<string:vcs_type>/<path:identifier>/<string:pr_mr_id>', methods=['DELETE'])
@require_admin_key
def delete_specific_review_results_for_pr_mr(vcs_type, identifier, pr_mr_id):
    """
    删除特定 PR/MR 的所有 AI 审查结果。
    """
    # 允许的 vcs_type 包括详细审查和通用审查
    allowed_vcs_types = ['github', 'gitlab', 'github_general', 'gitlab_general']
    if vcs_type not in allowed_vcs_types:
        return jsonify({"error": f"无效的 VCS 类型。支持的类型: {', '.join(allowed_vcs_types)}。"}), 400

    logger.info(f"请求删除审查结果: VCS={vcs_type}, ID={identifier}, PR/MR ID={pr_mr_id}")

    try:
        # delete_review_results_for_pr_mr 内部会处理 Redis 客户端不可用的情况并记录日志
        # 它不返回特定的成功/失败状态，但如果 Redis 操作失败会记录错误。
        # 我们假设如果函数执行没有抛出异常，操作就被认为是“已尝试”。
        delete_review_results_for_pr_mr(vcs_type, identifier, pr_mr_id)
        # core_config.delete_review_results_for_pr_mr 内部会打印日志，
        # 表明是成功删除还是未找到键。对于前端来说，200 OK 意味着请求被处理了。
        return jsonify({"message": f"{vcs_type.upper()} {identifier} #{pr_mr_id} 的审查结果删除请求已处理。"}), 200
    except Exception as e:
        logger.error(f"删除 {vcs_type} {identifier} #{pr_mr_id} 的审查结果时发生意外错误: {e}", exc_info=True)
        return jsonify({"error": "删除审查结果时发生服务器内部错误。"}), 500
