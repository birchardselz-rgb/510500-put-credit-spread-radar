# 云端完整看板(Streamlit) —— Render / HF Spaces 通用镜像
# 独立服务, 不影响现有手机版云端(five10500-put-credit-spread-radar)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PORT=8501

# 先装依赖(利用构建缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目(排除日志/数据/缓存)
COPY . .
RUN rm -rf data logs __pycache__ 2>/dev/null || true

EXPOSE 8501
CMD ["bash", "start.sh"]
