# 云端完整看板（Streamlit）部署说明

把 8502 电脑完整看板搬到云端，**独立于现有手机版云端服务**（five10500-put-credit-spread-radar），互不影响。

## 云端包含什么

- **完整 Streamlit 看板**（与本地 8502 同款）：标的概览表、Top 排行榜（可排序/过滤）、报警记录、扫描历史、实时获取数据按钮
- **后台持续扫描器**（10~20 秒间隔），打开即有实时数据
- 专业评分 V1.3：POP 盈利概率 / EV 期望收益 等全部指标

## 目录结构

```
cloud-dashboard/
├── Dockerfile          # Streamlit + akshare 镜像
├── requirements.txt
├── start.sh            # 后台扫描 + 前台 Streamlit
├── render.yaml         # Render Blueprint（可选一键部署）
├── run_scanner.py      # 扫描器入口
├── dashboard/app.py    # 完整看板
├── core/ data_sources/ storage/ util.py config.yaml   # 与手机版同一套核心
```

## 部署方式（二选一）

### 方式 A：Render Blueprint（推荐，最少点击）
1. 打开 https://dashboard.render.com
2. New + → **Blueprint**
3. Connect GitHub → 选择本仓库 `510500-put-credit-spread-radar`
3. 找到本目录的 `render.yaml` → 点击 Create Resources
4. 等构建完成，得到 `https://put-spread-dashboard-cloud.onrender.com`

> 注意：Blueprint 会创建服务名为 `put-spread-dashboard-cloud` 的新服务，不影响现有手机版服务。

### 方式 B：手动新建 Web Service
1. Render Dashboard → **New + → Web Service**
2. Connect GitHub → 本仓库
3. **Root Directory 填 `cloud-dashboard`**（关键！）
4. 环境自动识别 Docker
5. Name 随意，Instance Type 选 **Free**
6. Deploy，等 2-5 分钟

## 免费档注意事项

- 免费档 15 分钟无访问会休眠，再次打开等 30~60 秒自动唤醒
- 每个免费 Web Service 独立计 750 小时/月
- 非交易时段（09:30-11:30 / 13:00-15:00 之外）行情源不更新，显示最新收盘数据，属正常

## 验证

- 页面标题含「云端完整版」
- 有「🔄 实时获取数据」按钮
- 标的概览表显示 9 只标的现价/状态
