import re
import hmac
import hashlib
from functools import wraps
from flask import request, abort
from api.core_config import ADMIN_API_KEY
import logging

logger = logging.getLogger(__name__)


def parse_single_file_diff(diff_text, file_path, old_file_path=None):
    """
    解析单个文件的 unified diff 格式文本，提取变更信息。
    返回包含该文件变更详情和上下文的字典。
    """
    file_changes = {
        "path": file_path,
        "old_path": old_file_path,
        "changes": [],
        "context": {"old": [], "new": []},
        "lines_changed": 0
    }

    old_line_num_start = 0
    new_line_num_start = 0
    old_line_num_current = 0
    new_line_num_current = 0
    hunk_context_lines = []

    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('--- ') or line.startswith('+++ '):
            i += 1
            continue
        elif line.startswith('@@ '):
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if match:
                old_line_num_start = int(match.group(1))
                new_line_num_start = int(match.group(3))
                old_line_num_current = old_line_num_start
                new_line_num_current = new_line_num_start
                if hunk_context_lines:  # 将上一个 hunk 的上下文添加到 file_changes
                    file_changes["context"]["old"].extend(hunk_context_lines)
                    file_changes["context"]["new"].extend(hunk_context_lines)
                    hunk_context_lines = []  # 为新的 hunk 重置
            else:
                logger.warning(f"警告: 无法解析 {file_path} 中的 hunk 标头: {line}")
                old_line_num_start = 0  # 重置行号计数器
                new_line_num_start = 0
                old_line_num_current = 0
                new_line_num_current = 0
        elif line.startswith('+'):
            file_changes["changes"].append({
                "type": "add",
                "old_line": None,
                "new_line": new_line_num_current,
                "content": line[1:]
            })
            new_line_num_current += 1
        elif line.startswith('-'):
            file_changes["changes"].append({
                "type": "delete",
                "old_line": old_line_num_current,
                "new_line": None,
                "content": line[1:]
            })
            old_line_num_current += 1
        elif line.startswith(' '):  # Context line
            hunk_context_lines.append(f"{old_line_num_current} -> {new_line_num_current}: {line[1:]}")
            old_line_num_current += 1
            new_line_num_current += 1
        i += 1

    if hunk_context_lines:  # 添加最后一个 hunk 的上下文
        file_changes["context"]["old"].extend(hunk_context_lines)
        file_changes["context"]["new"].extend(hunk_context_lines)

    limit = 20  # 限制上下文行数
    file_changes["context"]["old"] = "\n".join(file_changes["context"]["old"][-limit:])
    file_changes["context"]["new"] = "\n".join(file_changes["context"]["new"][-limit:])
    file_changes["lines_changed"] = len([c for c in file_changes["changes"] if c['type'] in ['add', 'delete']])

    return file_changes


def require_admin_key(f):
    """装饰器：验证请求头中是否包含正确的 Admin API Key"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-Admin-API-Key')
        if not api_key or not hmac.compare_digest(api_key, ADMIN_API_KEY):
            logger.warning("检测到对配置端点的未授权访问尝试。")
            abort(401, "未授权: X-Admin-API-Key 请求头无效或缺失。")
        return f(*args, **kwargs)

    return decorated_function


def verify_github_signature(req, secret):
    """验证 GitHub Webhook 签名 (HMAC-SHA256)"""
    signature_header = req.headers.get('X-Hub-Signature-256')
    if not signature_header:
        logger.error("错误: X-Hub-Signature-256 请求头缺失。")
        return False

    sha_name, signature = signature_header.split('=', 1)
    if sha_name != 'sha256':
        logger.error(f"错误: 签名使用不支持的算法 {sha_name}。")
        return False

    if not secret:
        logger.error("错误: 此仓库未配置 Webhook secret。")
        return False

    mac = hmac.new(secret.encode('utf-8'), msg=req.data, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        logger.error("错误: 无效的 X-Hub-Signature-256。")
        return False

    return True


def verify_gitlab_signature(req, secret):
    """验证 GitLab Webhook 签名 (使用项目特定的 Secret)"""
    gitlab_token = req.headers.get('X-Gitlab-Token')
    if not gitlab_token:
        logger.error("错误: X-Gitlab-Token 请求头缺失。")
        return False
    if not secret:
        logger.error("错误: 此项目未配置 Webhook secret。")
        return False

    if not hmac.compare_digest(gitlab_token, secret):
        logger.error(f"错误: 无效的 X-Gitlab-Token。")
        return False
    return True
