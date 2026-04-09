# RMUC 2026 三赛区分析器

面向 RMUC 三赛区报名态势的实时分析工具，支持网页看板和命令行两种使用方式。

如果你是第一次接触 Python 项目，按下面的「3 分钟上手」一步一步做就能跑起来。

## 你能用它做什么

1. 实时查看三赛区报名人数与容量压力。
2. 估算国赛名额（基础名额 + 浮动名额）。
3. 估算复活赛名额（模拟值，仅用于态势分析）。
4. 预测可能被调剂的队伍和去向。
5. 在网页里看到“调入/调出(预测)”和虚化展示。

## 3 分钟上手（新手推荐）

### 1) 安装 Python 环境依赖

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

说明：
1. `playwright install chromium` 不是每次都要执行，首次安装环境执行一次即可。
2. 即使 Playwright 不可用，程序也会先尝试 API 链路抓取。

### 2) 启动网页看板

```bash
python run_web.py
```

然后打开浏览器访问：

```text
http://127.0.0.1:8000
```

### 3) 如果你想看命令行报告

```bash
python run.py --once --config config/config.json
```

## 两种运行方式怎么选

### A. 网页看板（推荐日常使用）

```bash
python run_web.py
```

特点：
1. 页面自动刷新（60 秒）。
2. 直接看到三赛区卡片和学校表格。
3. 支持查看调剂状态（调入/调出预测）。

注意：`run_web.py` 默认使用代码内置配置，不会自动读取 `config/config.json`。

如果你希望网页使用 `config/config.json`，请改用：

```bash
PYTHONPATH=src python -c "from rmuc_analyzer.web import create_app; create_app('config/config.json').run(host='0.0.0.0', port=8000, debug=False)"
```

### B. CLI 文本报告（适合日志、服务器）

单次运行：

```bash
python run.py --once --config config/config.json
```

轮询运行：

```bash
python run.py --config config/config.json --interval 60
```

常用参数：
1. `--once` 只运行一轮。
2. `--interval` 轮询间隔秒数。
3. `--max-iterations` 最大轮询次数。
4. `--config` 指定配置文件。

## 第一次配置（建议照抄）

先复制示例配置：

```bash
cp config/config.example.json config/config.json
```

重点改这几个字段：
1. `qingflow_url`：你的青流分享链接。
2. `priority_schools`：你希望保留志愿优先的学校名单。
3. `announcement_local_only`：是否只用本地公告（离线模式）。

## 配置项说明（小白版）

配置文件：`config/config.example.json`

1. `poll_interval_sec`：CLI 默认轮询秒数。
2. `expected_total_teams`：预期报名总队伍数（默认 96）。
3. `capacity_per_region`：每赛区容量（默认 32）。
4. `qingflow_url`：实时报名数据来源。
5. `announcement_urls`：公告链接（1909/1910/1884/1856/1847）。
6. `announcement_local_dir`：本地公告 HTML 存放目录。
7. `announcement_local_only`：
   - `false`：优先联网更新，失败再用本地。
   - `true`：只读本地，不联网。
8. `manual_top16_counts`：手动覆盖 16 强分布（一般留 `null`）。
9. `priority_schools`：优先名单（当前仅来自这个配置项）。
10. `rmu_ranking_csv`：积分榜 CSV 路径。
11. `cache_file`：实时抓取缓存文件路径。
12. `request_timeout_sec`：网络超时时间（秒）。

## 程序规则口径（避免误解）

1. 优先名单来源：仅 `priority_schools`（不自动引入 RMUL/海外规则）。
2. 调剂预测只在出现超容量赛区时触发。
3. 国赛与复活赛名额按当前口径实时估算，不代表官方最终结果。
4. 报名未满时，复活赛估算会先做最低约束补齐后再分配。

## 网页字段怎么读

1. `国赛名额`：按规则估算的该赛区国赛晋级数。
2. `复活赛名额`：模拟估算值。
3. `去年16强(调剂后)`：按预测调剂后归属统计。
4. `当前志愿`：当前赛区已报名志愿人数。
5. `调剂后估算`：把预测调剂应用后的人数。
6. `调剂状态`：
   - `调入(预测)`：预测会被调入本赛区。
   - `调出(预测)`：预测会从本赛区调出。

## API 用法

接口：`GET /api/analysis`

本地示例：

```bash
curl http://127.0.0.1:8000/api/analysis
```

返回主要字段：
1. `generated_at`：生成时间。
2. `total_submitted`：当前总报名数。
3. `expected_total`：预期总队伍数。
4. `regions`：三赛区详细数据。
5. `notes`：口径说明与运行提示。

## 常见问题（先看这里）

### 1) 网页打不开或端口占用

现象：提示 `Address already in use`。

处理：
1. 停掉旧进程后重启。
2. 或改端口启动（自定义启动命令）。

### 2) 改了 `config/config.json`，网页没变化

原因：`python run_web.py` 默认不读这个文件。

处理：使用上面的自定义启动命令，让网页显式读取配置。

### 3) 抓取失败

程序会按以下顺序回退：

```text
API -> requests文本解析 -> Playwright渲染解析 -> 本地缓存快照
```

如果你需要离线运行：
1. 把公告 HTML 放在 `data/announcements`。
2. 设置 `announcement_local_only=true`。

### 4) 页面里有“空位”

这是设计行为：每赛区固定展示 32 行，便于观察容量缺口。

## 本地测试

```bash
PYTHONPATH=src pytest -q
```

## 项目结构（你最常用）

1. `run_web.py`：网页入口。
2. `run.py`：CLI 入口。
3. `config/config.example.json`：配置模板。
4. `src/rmuc_analyzer/`：核心逻辑代码。
5. `tests/`：单元测试。

## 开源前建议清单

1. 检查 `config/config.json` 是否包含敏感地址或私有数据。
2. 清理 `.cache/` 下临时快照。
3. 确认 `data/announcements/` 是否允许公开分发。
4. 补充 `LICENSE` 文件（建议 MIT/Apache-2.0）。
5. 在仓库首页放一张运行截图（提升可读性）。

## 免责声明

本项目用于态势分析与辅助决策，最终录取与调剂结果请以官方公告为准。
