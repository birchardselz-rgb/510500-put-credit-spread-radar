# 手机端实时看板 云端部署镜像(HF Spaces / Render / Railway)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PORT=8503

# 先装依赖(利用构建缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目(排除日志/数据/二进制)
COPY . .
RUN rm -rf data logs __pycache__ 2>/dev/null || true

EXPOSE 8503
CMD ["python", "cloud_entry.py"]
