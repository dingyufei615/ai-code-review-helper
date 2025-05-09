from flask import Flask
from concurrent.futures import ThreadPoolExecutor
import logging

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=20) # 您可以根据需要调整 max_workers

# 获取 app_factory 模块的 logger，如果主应用中配置了日志，它会继承配置
# 或者，如果希望它有独立的日志行为，可以单独配置
logger_app_factory = logging.getLogger(__name__)

def handle_async_task_exception(future):
    """
    处理 ThreadPoolExecutor 提交的异步任务中未捕获的异常。
    此函数作为 Future 对象的完成回调。
    """
    try:
        exception = future.exception()
        if exception:
            # 使用 logger 记录异常和堆栈跟踪
            logger_app_factory.error(
                f"后台异步任务执行失败。",
                exc_info=exception  # 这会自动包含堆栈跟踪
            )
            # 这里可以根据需要添加其他错误处理逻辑，例如发送通知
    except Exception as e:
        # 捕获回调函数本身可能发生的任何错误
        logger_app_factory.error(
            f"处理异步任务异常的回调函数自身发生错误: {e}",
            exc_info=True
        )
