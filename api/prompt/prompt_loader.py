import yaml
import os
import logging

logger = logging.getLogger(__name__)

_PROMPTS = None
# __file__ 是当前 prompt_loader.py 的路径
# os.path.dirname(__file__) 是 api/prompt/ 目录
# prompt_templates.yml 与 prompt_loader.py 在同一目录 api/prompt/ 下
_PROMPT_FILE_PATH = os.path.join(os.path.dirname(__file__), 'prompt_templates.yml')

def _load_prompts_if_needed():
    """按需加载 Prompt，仅在 _PROMPTS 为 None 时执行加载操作。"""
    global _PROMPTS
    if _PROMPTS is None:
        try:
            with open(_PROMPT_FILE_PATH, 'r', encoding='utf-8') as f:
                _PROMPTS = yaml.safe_load(f)
            if _PROMPTS:
                logger.info(f"Prompts loaded successfully from {_PROMPT_FILE_PATH}")
            else:
                logger.warning(f"Prompt file loaded but was empty or invalid: {_PROMPT_FILE_PATH}")
                _PROMPTS = {} # Ensure it's an empty dict, not None
        except FileNotFoundError:
            logger.error(f"CRITICAL: Prompt template file not found: {_PROMPT_FILE_PATH}. Prompts will not be available.")
            _PROMPTS = {} # Fallback to empty dict
        except yaml.YAMLError as e:
            logger.error(f"CRITICAL: Error parsing prompt template file {_PROMPT_FILE_PATH}: {e}. Prompts may be incomplete.")
            _PROMPTS = {} # Fallback
        except Exception as e:
            logger.error(f"CRITICAL: An unexpected error occurred while loading prompts from {_PROMPT_FILE_PATH}: {e}")
            _PROMPTS = {} # Fallback


def get_prompt(prompt_key: str, sub_key: str = 'system_prompt') -> str:
    """
    获取指定的 Prompt 内容。

    :param prompt_key: Prompt 的主键 (例如 'detailed_review')。
    :param sub_key: Prompt 的次级键 (默认为 'system_prompt')。
    :return: Prompt 字符串。如果找不到，则返回一个错误提示字符串。
    """
    _load_prompts_if_needed() # 确保 Prompts 已加载

    prompt_section = _PROMPTS.get(prompt_key, {})
    value = prompt_section.get(sub_key)

    if value is None:
        error_message = f"ERROR: Prompt for '{prompt_key}.{sub_key}' not found in '{_PROMPT_FILE_PATH}' or file failed to load."
        logger.error(error_message)
        # 对于关键的系统 Prompt，返回一个能明确指示错误的字符串，而不是 None 或空字符串
        # 这样调用者可以更容易地发现问题。
        return f"Error: Prompt '{prompt_key}.{sub_key}' could not be loaded. Check logs."
    return value

# Initialize prompts on module load
_load_prompts_if_needed()
