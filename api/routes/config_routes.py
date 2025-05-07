from flask import request, jsonify
import json # 新增导入
from api.app_factory import app
from api.core_config import (
    app_configs, github_repo_configs, gitlab_project_configs,
    REDIS_GITHUB_CONFIGS_KEY, REDIS_GITLAB_CONFIGS_KEY
)
import api.core_config as core_config_module # 访问 redis_client 的推荐方式
from api.utils import require_admin_key
from api.services.llm_service import initialize_openai_client


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
            print(f"GitHub configuration for {repo_full_name} saved to Redis.")
        except Exception as e:
            print(f"Error saving GitHub config for {repo_full_name} to Redis: {e}")
            # 继续执行，至少内存中已更新

    print(f"GitHub configuration added/updated for repository: {repo_full_name}")
    return jsonify({"message": f"Configuration for GitHub repository {repo_full_name} added/updated."}), 200


@app.route('/config/github/repo/<path:repo_full_name>', methods=['DELETE'])
@require_admin_key
def delete_github_repo_config(repo_full_name):
    if repo_full_name in github_repo_configs:
        del github_repo_configs[repo_full_name]
        if core_config_module.redis_client:
            try:
                core_config_module.redis_client.hdel(REDIS_GITHUB_CONFIGS_KEY, repo_full_name)
                print(f"GitHub configuration for {repo_full_name} deleted from Redis.")
            except Exception as e:
                print(f"Error deleting GitHub config for {repo_full_name} from Redis: {e}")
                # 继续执行，至少内存中已删除
        print(f"GitHub configuration deleted for repository: {repo_full_name}")
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

    if not project_id or not secret or not token: # instance_url 是可选的
        return jsonify({"error": "Missing required fields: project_id, secret, token"}), 400
    
    project_id_str = str(project_id)
    config_data = {"secret": secret, "token": token}
    if instance_url: # 只有当用户提供时才存储
        config_data["instance_url"] = instance_url
    
    gitlab_project_configs[project_id_str] = config_data
    if core_config_module.redis_client:
        try:
            core_config_module.redis_client.hset(REDIS_GITLAB_CONFIGS_KEY, project_id_str, json.dumps(config_data))
            print(f"GitLab configuration for project {project_id_str} saved to Redis.")
        except Exception as e:
            print(f"Error saving GitLab config for project {project_id_str} to Redis: {e}")
            # 继续执行，至少内存中已更新
            
    print(f"GitLab configuration added/updated for project ID: {project_id_str}. Instance URL: {instance_url if instance_url else 'Default'}")
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
                print(f"GitLab configuration for project {project_id_str} deleted from Redis.")
            except Exception as e:
                print(f"Error deleting GitLab config for project {project_id_str} from Redis: {e}")
                # 继续执行，至少内存中已删除
        print(f"GitLab configuration deleted for project ID: {project_id_str}")
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
        print("OpenAI related configuration updated, re-initializing OpenAI client...")
        initialize_openai_client()

    if updated_keys:
        print(f"Global settings updated for keys: {', '.join(updated_keys)}")
        # Here you might want to persist app_configs to a file or database if needed beyond memory storage
        return jsonify({"message": f"Global settings updated for: {', '.join(updated_keys)}"}), 200
    else:
        return jsonify({"message": "No settings were updated or values provided matched existing configuration."}), 200
