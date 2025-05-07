from flask import request, abort, jsonify
import json
from api.app_factory import app
from api.core_config import github_repo_configs, gitlab_project_configs, app_configs
from api.utils import verify_github_signature, verify_gitlab_signature
from api.services.vcs_service import get_github_pr_changes, add_github_pr_comment, get_gitlab_mr_changes, add_gitlab_mr_comment
from api.services.llm_service import get_openai_code_review
from api.services.notification_service import send_to_wecom_bot


@app.route('/github_webhook', methods=['POST'])
def github_webhook():
    """处理 GitHub Webhook 请求"""
    try:
        payload_data = request.get_json()
        if payload_data is None: raise ValueError("Request body is empty or not valid JSON")
    except Exception as e:
        print(f"Error parsing JSON payload for GitHub: {e}")
        abort(400, "Invalid JSON payload")

    repo_info = payload_data.get('repository', {})
    repo_full_name = repo_info.get('full_name')

    if not repo_full_name:
        print("Error: Missing repository.full_name in GitHub payload.")
        abort(400, "Missing repository.full_name in GitHub payload")

    config = github_repo_configs.get(repo_full_name)
    if not config:
        print(f"Error: Configuration not found for GitHub repository: {repo_full_name}")
        abort(404,
              f"Configuration for GitHub repository {repo_full_name} not found. Please configure it via the /config/github/repo endpoint.")

    webhook_secret = config.get('secret')
    access_token = config.get('token')

    if not verify_github_signature(request, webhook_secret):
        abort(401, "GitHub signature verification failed.")

    event_type = request.headers.get('X-GitHub-Event')
    if event_type != "pull_request":
        print(f"GitHub: Ignoring event type: {event_type}")
        return "Event ignored", 200

    action = payload_data.get('action')
    pr_data = payload_data.get('pull_request', {})
    pr_state = pr_data.get('state')

    if pr_state != 'open' or action not in ['opened', 'reopened', 'synchronize']:
        print(f"GitHub: Ignoring PR action '{action}' or state '{pr_state}'.")
        return "PR action/state ignored", 200

    owner = repo_info.get('owner', {}).get('login')
    repo_name = repo_info.get('name')
    pull_number = pr_data.get('number')
    pr_title = pr_data.get('title')
    pr_html_url = pr_data.get('html_url')
    head_sha = pr_data.get('head', {}).get('sha')
    repo_web_url = repo_info.get('html_url')
    pr_source_branch = pr_data.get('head', {}).get('ref')
    pr_target_branch = pr_data.get('base', {}).get('ref')

    if not all([owner, repo_name, pull_number, head_sha]):
        print("Error: Missing essential PR information in GitHub payload.")
        abort(400, "Missing essential PR information in GitHub payload")

    print(f"\n--- Received GitHub Pull Request Hook ---")
    print(f"Repository: {repo_full_name}, PR Number: {pull_number}, Title: {pr_title}")
    print(f"State: {pr_state}, Action: {action}, Head SHA: {head_sha}, PR URL: {pr_html_url}")

    print("GitHub: Fetching and parsing PR changes...")
    structured_changes = get_github_pr_changes(owner, repo_name, pull_number, access_token)

    if structured_changes is None:
        print("GitHub: Failed to get or parse diff content. Aborting review.")
        return "Failed to get/parse diff", 200
    if not structured_changes:
        print("GitHub: No changes detected after parsing. No review needed.")
        return "No changes detected", 200

    print("GitHub: Sending changes to OpenAI for review...")
    review_result_json = get_openai_code_review(structured_changes)

    print("\n--- GitHub: AI Code Review Result (JSON) ---")
    print(review_result_json)
    print("--- End of GitHub Review JSON ---\n")

    reviews = []
    try:
        parsed_data = json.loads(review_result_json)
        if isinstance(parsed_data, list): reviews = parsed_data
        print(f"GitHub: Successfully parsed {len(reviews)} review items from JSON.")
    except json.JSONDecodeError as e:
        print(f"GitHub: Error parsing review result JSON: {e}. Raw: {review_result_json[:500]}")

    if reviews:
        print(f"GitHub: Attempting to add {len(reviews)} review comments to PR...")
        comments_added, comments_failed = 0, 0
        for review in reviews:
            if isinstance(review, dict) and "file" in review:
                file_path = review["file"]
                if file_path in structured_changes and "old_path" in structured_changes[file_path]:
                    review["old_path"] = structured_changes[file_path]["old_path"]
            if isinstance(review, dict):
                success = add_github_pr_comment(owner, repo_name, pull_number, access_token, review, head_sha)
                if success:
                    comments_added += 1
                else:
                    comments_failed += 1
            else:
                print(f"GitHub: Skipping invalid review item: {review}");
                comments_failed += 1
        print(f"GitHub: Finished adding comments: {comments_added} succeeded, {comments_failed} failed.")
    else:
        print("GitHub: No review suggestions from AI. Posting 'all clear' comment.")
        no_issues_review = {
            "file": "Overall PR Status",
            "severity": "INFO",
            "category": "General",
            "analysis": "AI Code Review 已完成，无审查建议。",
            "suggestion": "Looks good!",
            "lines": {}  # Ensures it's a general PR comment
        }
        add_github_pr_comment(owner, repo_name, pull_number, access_token, no_issues_review, head_sha)

    if app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        print("GitHub: Sending summary notification to WeCom bot...")
        if not reviews: # reviews is the list of parsed review items
            review_summary_line = "AI Code Review 已完成，无审查建议。"
        else:
            review_summary_line = f"AI Code Review 已完成，共生成 {len(reviews)} 条审查建议。请前往 Pull Request 查看详情。"

        summary_content = f"""**AI代码审查完成 (GitHub)**

> 仓库: [{repo_full_name}]({repo_web_url})
> PR: [{pr_title}]({pr_html_url}) (#{pull_number})
> 分支: `{pr_source_branch}` → `{pr_target_branch}`

{review_summary_line}
"""
        send_to_wecom_bot(summary_content)

    return "GitHub Webhook processed successfully", 200


@app.route('/gitlab_webhook', methods=['POST'])
def gitlab_webhook():
    """处理 GitLab Webhook 请求"""
    try:
        data = request.get_json()
        if data is None: raise ValueError("Request body is empty or not valid JSON")
    except Exception as e:
        print(f"Error parsing JSON payload for GitLab: {e}")
        abort(400, "Invalid JSON payload")

    project_data = data.get('project', {})
    project_id = project_data.get('id')
    project_web_url = project_data.get('web_url')
    mr_attrs = data.get('object_attributes', {})
    mr_iid = mr_attrs.get('iid')
    mr_title = mr_attrs.get('title')
    mr_url = mr_attrs.get('url')
    last_commit = mr_attrs.get('last_commit', {})
    head_sha_payload = last_commit.get('id')

    if not project_id or not mr_iid:
        print("Error: Missing project_id or mr_iid in GitLab payload.")
        abort(400, "Missing project_id or mr_iid in GitLab payload")

    project_id_str = str(project_id)
    config = gitlab_project_configs.get(project_id_str)
    if not config:
        print(f"Error: Configuration not found for GitLab project ID: {project_id_str}")
        abort(404,
              f"Configuration for GitLab project {project_id_str} not found. Please configure it via the /config/gitlab/project endpoint.")

    webhook_secret = config.get('secret')
    access_token = config.get('token')

    if not verify_gitlab_signature(request, webhook_secret):
        abort(401, "GitLab signature verification failed.")

    event_type = request.headers.get('X-Gitlab-Event')
    if event_type != "Merge Request Hook":  # GitLab uses "Merge Request Hook" or "Note Hook" etc.
        print(f"GitLab: Ignoring event type: {event_type}")
        return "Event ignored", 200

    mr_action = mr_attrs.get('action')
    mr_state = mr_attrs.get('state')
    # Supported actions: open, reopen, update (when new commits are pushed)
    # GitLab MR actions: open, reopen, update, close, merge, approve, unapprove
    # We are interested in 'open', 'reopened', and 'update' (if source branch changes)
    # 'update' action is triggered when new commits are pushed to the source branch of an open MR.
    if mr_state not in ['opened', 'reopened'] and mr_action != 'update':
        print(f"GitLab: Ignoring MR action '{mr_action}' or state '{mr_state}'.")
        return "MR action/state ignored", 200

    # For 'update' action, ensure it's not just a metadata update without new commits.
    # The 'last_commit' object changes when new code is pushed.
    # A simple check is if 'oldrev' is present and different from 'last_commit.id' for 'update' action.
    # However, GitLab's 'update' action for MR hooks usually implies a new commit.
    # If 'action' is 'update', it means commits were pushed or MR was rebased.

    print(f"\n--- Received GitLab Merge Request Hook ---")
    print(f"Project ID: {project_id_str}, MR IID: {mr_iid}, Title: {mr_title}")
    print(f"State: {mr_state}, Action: {mr_action}, MR URL: {mr_url}")

    print("GitLab: Fetching and parsing MR changes...")
    structured_changes, position_info = get_gitlab_mr_changes(project_id_str, mr_iid, access_token)

    if position_info is None: position_info = {}
    if head_sha_payload and not position_info.get("head_sha"):
        position_info["head_sha"] = head_sha_payload
        print(f"GitLab: Using head_sha from webhook payload: {head_sha_payload}")
    if not all(k in position_info for k in ["base_sha", "start_sha", "head_sha"]):
        print("GitLab: Warning: Missing crucial commit SHA information for precise comment positioning.")

    if structured_changes is None:
        print("GitLab: Failed to get or parse diff content. Aborting review.")
        return "Failed to get/parse diff", 200
    if not structured_changes:
        print("GitLab: No changes detected after parsing. No review needed.")
        return "No changes detected", 200

    print("GitLab: Sending changes to OpenAI for review...")
    review_result_json = get_openai_code_review(structured_changes)

    print("\n--- GitLab: AI Code Review Result (JSON) ---")
    print(review_result_json)
    print("--- End of GitLab Review JSON ---\n")

    reviews = []
    try:
        parsed_data = json.loads(review_result_json)
        if isinstance(parsed_data, list): reviews = parsed_data
        print(f"GitLab: Successfully parsed {len(reviews)} review items from JSON.")
    except json.JSONDecodeError as e:
        print(f"GitLab: Error parsing review result JSON: {e}. Raw: {review_result_json[:500]}")

    if reviews:
        print(f"GitLab: Attempting to add {len(reviews)} review comments to MR...")
        comments_added, comments_failed = 0, 0
        for review in reviews:
            if isinstance(review, dict) and "file" in review:
                file_path = review["file"]
                if file_path in structured_changes:
                    review["old_path"] = structured_changes[file_path].get("old_path")
            if isinstance(review, dict):
                success = add_gitlab_mr_comment(project_id_str, mr_iid, access_token, review, position_info)
                if success:
                    comments_added += 1
                else:
                    comments_failed += 1
            else:
                print(f"GitLab: Skipping invalid review item: {review}");
                comments_failed += 1
        print(f"GitLab: Finished adding comments: {comments_added} succeeded, {comments_failed} failed.")
    else:
        print("GitLab: No review suggestions from AI. Posting 'all clear' comment.")
        no_issues_review = {
            "file": "Overall MR Status",
            "severity": "INFO",
            "category": "General",
            "analysis": "AI code review completed. All checks passed, no suggestions found.",
            "suggestion": "Looks good!",
            "lines": {}  # Ensures it's a general MR comment
        }
        add_gitlab_mr_comment(project_id_str, mr_iid, access_token, no_issues_review, position_info)

    if app_configs.get("WECOM_BOT_WEBHOOK_URL"):
        print("GitLab: Sending summary notification to WeCom bot...")
        project_name = project_data.get('name', project_id_str)
        mr_source_branch = mr_attrs.get('source_branch')
        mr_target_branch = mr_attrs.get('target_branch')

        if not reviews: # reviews is the list of parsed review items
            review_summary_line = "AI Code Review 已完成，无审查建议。"
        else:
            review_summary_line = f"AI分析完成，共生成 {len(reviews)} 条审查建议。请前往 Merge Request 查看详情。"

        summary_content = f"""**AI代码审查完成 (GitLab)**

> 项目: [{project_name}]({project_web_url})
> MR: [{mr_title}]({mr_url}) (#{mr_iid})
> 分支: `{mr_source_branch}` → `{mr_target_branch}`

{review_summary_line}
"""
        send_to_wecom_bot(summary_content)

    return "GitLab Webhook processed successfully", 200
