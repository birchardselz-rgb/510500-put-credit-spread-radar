#!/bin/bash
# start.sh - 云端完整看板启动脚本
# 1) 后台启动持续扫描器(保证打开看板即有实时数据)
# 2) 前台运行 Streamlit 完整看板
set -e
mkdir -p data logs

# 后台持续扫描(10 秒间隔, 与本地一致)
nohup python run_scanner.py --interval 20 > logs/scanner.out 2>&1 &
echo "[start.sh] background scanner started (pid $!)"

# 前台 Streamlit(绑定 Render 提供的 $PORT)
exec streamlit run dashboard/app.py \
  --server.port "${PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false \
  --server.maxUploadSize 10
