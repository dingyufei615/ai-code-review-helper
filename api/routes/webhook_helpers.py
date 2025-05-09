import logging
from api.core_config import save_review_results

logger = logging.getLogger(__name__)


def _save_review_results_and_log(vcs_type: str, identifier: str, pr_mr_id: str, commit_sha: str, review_json_string: str, project_name_for_gitlab: str = None):
    """统一保存审查结果到 Redis 并记录日志。"""
    if not commit_sha:
        logger.warning(f"警告: {vcs_type.capitalize()} {identifier}#{pr_mr_id} 的 commit_sha 为空。无法保存审查结果。")
        return

    try:
        # 统一处理所有 github* 和 gitlab* 类型
        if vcs_type.startswith('github'): # 包括 'github' 和 'github_general'
            save_review_results(vcs_type, identifier, pr_mr_id, commit_sha, review_json_string)
        elif vcs_type.startswith('gitlab'): # 包括 'gitlab' 和 'gitlab_general'
            save_review_results(vcs_type, identifier, pr_mr_id, commit_sha, review_json_string, project_name=project_name_for_gitlab)
        else:
            logger.error(f"未知的 VCS 类型 '{vcs_type}'，无法保存审查结果。")
            return
        
        # 日志记录已在 save_review_results 内部处理，这里可以不再重复记录成功信息，
        # 但保留一个简短的调用日志可能有用。
        logger.info(f"{vcs_type.capitalize()}: 审查结果保存调用完成，针对 commit {commit_sha}。")

    except Exception as e:
        # save_review_results 内部已经有错误日志，这里可以捕获更通用的错误或决定是否需要额外日志
        logger.error(f"调用 save_review_results 时发生意外错误 ({vcs_type} {identifier}#{pr_mr_id}, Commit: {commit_sha}): {e}")
