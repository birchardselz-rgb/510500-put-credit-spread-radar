# 510500 Mobile Radar — 手机端 Put 信用价差实时看板

独立新项目，**不修改** 510500_put_scanner 的任何文件：本服务只读共享数据库
(`../510500_put_scanner/data/scanner.db`)，把电脑端扫描结果用手机友好的方式实时展示，
并通过 ngrok 暴露公网地址，让手机用**手机互联网**（流量/任意网络）即可访问。

> ⚠️ 只读展示 + 提醒，**绝不自动下单**。数据真实性取决于电脑端扫描器是否在运行。

## 快速开始（手机互联网访问）

1. 先确保电脑端扫描器有数据：在 `510500_put_scanner` 运行过 `run_scanner.py`
   或点击看板「实时获取数据」（数据入库到 `data/scanner.db`）。
2. 双击本目录 `start_phone.ps1`（推荐）或 `start_phone.bat`：
   - 自动启动本地手机服务（端口 8503，用真实 Python312 隐藏窗口运行）
   - 自动启动 **cloudflared** 公网隧道（免费、无需账号，与你机器上已有的
     ngrok 隧道互不冲突）
   - 自动把 **https://xxxx.trycloudflare.com** 公网地址写入 `phone_url.txt`
3. 手机浏览器打开该地址（用流量也能访问），即可实时查看。
4. 需要实时更新时：电脑端扫描器持续运行（或定期点看板按钮），手机页面每 5 秒自动刷新。

> 注意：公网地址每次重启隧道都会变化，**以 `phone_url.txt` 最新内容为准**；
> 电脑必须保持开机；电脑端扫描器停止后，页面数据停留在最后一次扫描结果
> （顶部会显示"行情陈旧/数据时间"）。

## 开机自启 + 自动自愈（已配置好，无需手动）

- **开机自启**：`启动文件夹` 已放两个快捷方式
  - `510500MobileRadar.lnk` → `start_phone.ps1 -Auto`（启动服务+隧道）
  - `RadarWatchdogLoop.lnk` → `watchdog_loop.ps1`（后台每 120 秒自检）
- **崩溃自愈**：`watchdog_loop.ps1` 每 2 分钟检查一次：8503 服务或 cloudflared
  任一掉线 → 自动调 `start_phone.ps1` 拉起，并把最新公网地址刷新进 `phone_url.txt`。
  服务用 `Start-Process` 独立启动，**不依附于任何会话**，不会再因会话结束被杀。
- 日志：`phone_start.log` / `watchdog.log`；想立即重拉：双击 `start_phone.ps1`。

## 局域网访问（可选，更快）

手机连同一 WiFi，打开 `http://<电脑IP>:8503`（见 start_server.bat 说明）。

## 手动启动（分开）

| 步骤 | 命令 | 作用 |
|---|---|---|
| 1 | `python mobile_server.py --port 8503` | 本地手机服务 |
| 2 | 另开窗口 `cloudflared.exe tunnel --url http://localhost:8503` | 公网隧道（读取其窗口的 https 地址） |

或直接用 `start_phone.bat` 一步到位。

## API

- `GET /` — 手机端 HTML 页面（移动优先、自动刷新）
- `GET /api/latest` — JSON：各标的最新扫描 + Top 候选 + 报警 + 最近扫描 + 交易时段

返回 JSON 示例字段：`scans.{code}.{spot, spot_time, data_fresh, source, n_contracts, n_spreads}`
`candidates[].{sell_strike, buy_strike, width, credit, max_profit, max_loss, breakeven,
safety_margin, reward_risk, sell_delta, sell_iv, score, tier, expire_month, underlying, spot}`
`session.{state, label, server_time}`

## 配置

- 默认数据库：`../510500_put_scanner/data/scanner.db`（只读）
- 可用 `--db <路径>` 或环境变量 `SCANNER_MOBILE_DB` 指定其他数据库
- 端口默认 8503，可用 `--port` 或 `SCANNER_MOBILE_PORT` 修改

## 依赖

纯 Python 标准库，零第三方依赖（`requirements.txt` 仅为占位）。

## 停止

关闭 `start_phone.bat` 窗口及两个子窗口（MobileServer-8503 / CfTunnel）即可。

## 安全提示

- 公网地址任何人拿到都能查看页面（数据为公开行情，无隐私）；如需更安全可加访问口令（后续版本）。
- cloudflared 免费隧道地址每次启动变化；如需**固定域名/电脑可关机**，请用下面的云部署方案。

## 云部署（24/7 在线，电脑可关机，推荐长期使用）

见 `CLOUD_DEPLOY.md`：用 Dockerfile 把本项目 + 一个轻量扫描循环打包，
一键部署到 Hugging Face Spaces / Render / Railway，获得固定公网域名。
