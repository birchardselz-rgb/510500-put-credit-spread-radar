# 云部署指南（24/7 在线，电脑可关机）

本目录已是**完整可部署包**：`cloud_wsgi.py`(PythonAnywhere 专用 WSGI 入口, 内置后台扫描线程)
+ `cloud_entry.py`(Docker/HF 通用入口) + `Dockerfile` + 云版 `config.yaml` + `requirements.txt`
+ 扫描引擎副本(core/data_sources/storage)。部署用现成 `deploy_510500_mobile.zip`(51KB) 即可。

> 数据源 tonghuashun 只需 requests + PyYAML(镜像轻量)；期权盘口仅交易时段有实时值,
> 收盘后显示"行情陈旧"属正常。云端扫描线程每 10 秒扫双标的写 data/scanner.db, 手机页只读展示。

---

## 方案一：PythonAnywhere（免费、Web 应用常驻不睡觉、无信用卡）—— 推荐

1. 注册/登录 https://www.pythonanywhere.com （免费 Beginner 档；注册后若需邮箱确认，先点确认链接）
2. 顶部 **Web** 标签 → **Add a new web app** → Next → 选 **Manual configuration** → Python 3.11
   → 完成（创建后地址为 `https://<用户名>.pythonanywhere.com`）
3. 顶部 **Files** 标签 → 上传 `deploy_510500_mobile.zip` 到 Home 目录
4. 顶部 **Consoles** → 开一个 **Bash** 控制台，执行：
   ```bash
   cd ~
   unzip deploy_510500_mobile.zip -d radar
   mkvirtualenv radar --python=python3.11
   pip install requests pyyaml
   ```
5. **Web** 标签 → 点 Web 应用的 **WSGI configuration file** 链接，把内容替换为：
   ```python
   import sys
   sys.path.insert(0, '/home/<你的用户名>/radar')
   from cloud_wsgi import application
   ```
   （替换后保存）
6. 回到 **Web** 标签 → 点绿色 **Reload** 按钮
7. 手机浏览器打开 `https://<你的用户名>.pythonanywhere.com` 即可实时查看（电脑可关机）

> 免费档注意：每日 CPU 限额 + 偶尔因安全限制拦截频繁操作（"Rate limit exceeded" 属正常，
> 等几分钟即可）；Web 应用进程常驻，后台扫描线程随之持续运行。

---

## 方案二：Hugging Face Spaces（免费、需信用卡否、但本网络环境可能被拦 418）

1. 登录 https://huggingface.co/new-space，SDK 选 **Docker**，创建 Space
2. Files → Add file → 上传 `deploy_510500_mobile.zip`（HF 自动解压）
3. 构建完访问 `https://<用户名>-<空间名>.hf.space`
> 注意：免费 Space 约 48 小时无人访问会休眠（休眠时扫描暂停，手机一开即唤醒）。

## 方案三：Render（免费但 15 分钟无访问即休眠，不太适合，备选）

- Web Service → 上传本目录 → Build: Docker → 端口 8503。

---

## 本地验证（任一方式先跑通）

```bash
pip install -r requirements.txt
python cloud_entry.py        # 通用入口: 扫描线程 + 手机服务(8503), 打开 http://localhost:8503
# 或 WSGI 本地冒烟:
python -c "from wsgiref.simple_server import make_server; import cloud_wsgi; make_server('0.0.0.0',8505,cloud_wsgi.application).serve_forever()"
# 打开 http://localhost:8505 验证
```
