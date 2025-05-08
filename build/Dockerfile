FROM harbor.qc.host.dxy/library/miniconda3
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV REQUESTS_CA_BUNDLE=/usr/local/share/ca-certificates/ca2024.pem
ENV SSL_CERT_FILE=/usr/local/share/ca-certificates/ca2024.pem

ENV PATH /usr/local/bin:$PATH

# set dxy proxy
# conda proxy setting.
#RUN sh -c "echo 'default_channels:\n\
#  - https://nexus.k8s.qc.host.dxy/repository/anaconda/pkgs/msys2/\n\
#  - https://nexus.k8s.qc.host.dxy/repository/anaconda/pkgs/pro/\n\
#  - https://nexus.k8s.qc.host.dxy/repository/anaconda/pkgs/r/\n\
#  - https://nexus.k8s.qc.host.dxy/repository/anaconda/pkgs/free/\n\
#  - https://nexus.k8s.qc.host.dxy/repository/anaconda/pkgs/main/' > ~/.condarc" && cat ~/.condarc

RUN mkdir -p ~/.pip && sh -c "echo '[global]\n\
index-url = https://nexus.k8s.qc.host.dxy/repository/pypi-aliyun/simple\n\
extra-index-url = https://pypiserver.k8s.qc.host.dxy/simple/\n\
trusted-host = nexus.k8s.qc.host.dxy\n\
               pypiserver.k8s.qc.host.dxy nexus.k8s.qc.host.dxy' > ~/.pip/pip.conf"

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
CMD ["python", "-m", "api.ai_code_review_helper"]
