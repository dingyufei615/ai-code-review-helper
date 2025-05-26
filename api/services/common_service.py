from api.core_config import app_configs
import logging

logger = logging.getLogger(__name__)


def get_final_summary_comment_text() -> str:
    """
    生成通用的最终总结评论文本。
    """
    model_name = app_configs.get("OPENAI_MODEL", "gpt-4o")
    return f"本次AI代码审查已完成，审核模型:「{model_name}」 修改意见仅供参考，具体修改请根据实际场景进行调整。"
