FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POWERCONTEXT_HOME=/data

# PowerContext 1.4.1 + 嵌入式 seekdb 依赖（pylibseekdb / pyobvector / jieba ...）
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" "powercontext[cli,server,seekdb]==1.4.1"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
