# 使用官方 Python 运行时作为父镜像
FROM python:3.9-slim

# 设置推荐的环境变量，防止生成 .pyc 文件，并确保 Python 输出直接到终端
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 将依赖文件复制到容器中
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 将项目代码复制到容器中
# api 目录包含所有应用逻辑和 Flask 入口
COPY ./api ./api

# 应用程序监听的端口 (从 api/core_config.py 可知默认是 8088)
EXPOSE 8088

# 运行应用的命令
# 使用 -m 选项将 api.ai_code_review_helper 作为模块运行，
# 这会将工作目录 /app 添加到 sys.path，从而正确解析 "from api.xxx" 这样的导入。
CMD ["python", "-m", "api.ai_code_review_helper"]
