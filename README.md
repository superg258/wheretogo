# RMUC 2026 三赛区实时分析器

用于跟踪 RMUC 2026 三赛区志愿提交态势，提供 CLI 报告与网页看板两种形态。

当前版本可输出：

1. 各赛区预计国赛名额（基础名额 + 浮动名额）。
2. 各赛区容量压力（当前志愿人数 vs 32席位）。
3. 可能调剂去向预测（仅在出现超容量赛区时触发）。
4. 去年全国赛成绩标注（含三十二强层级与复活赛标记）。

## 当前实现概览

- 双入口：
  - CLI：轮询输出文本报告。
  - Web：三赛区并排看板 + `/api/analysis` JSON接口。
- 数据源：
  - 1909：2026完整形态通过名单（学校基表）。
  - 1910：规则与距离表。
  - 1884：高校积分榜（启动时抓取并刷新本地CSV）。
  - 1856：2025全国赛完整成绩（不仅前32）。
  - 青流分享看板：当前志愿赛区分布与学校列表。
- 优先策略：
  - 配置内置 RMUC 承办院校。
  - 自动并入 RMUL 承办院校（1903）。
  - 自动并入海外队伍优先。

## 核心规则

- 国赛名额（总28）：
  - 每赛区基础8。
  - “上一赛季16强数量”按今年当前志愿中去年的16强队伍实际报名赛区实时统计。
  - 浮动4按“去年16强占比 + 最大余数法”分配。
  - 仅“去年16强数量 > 4”的赛区参与浮动分配。
  - 余数并列按赛区顺序处理：南部 -> 东部 -> 北部。
- 复活赛名额（总16）：
  - 采用模拟估算逻辑（用于态势分析，不代表官方最终分配）。
  - 报名总数未满时，先将各赛区估算人数补到8后再参与分配。
  - 若存在预测调配，则按调配后的赛区人数再分配复活赛名额。
- 调剂预测：
  - 仅在出现超容量赛区时生成候选。
  - 报名未满时，仅标出“当前因超容量而必须调剂”的队伍，不提前补齐全部缺口。
  - 报名满额后，按规则阶段流程完成A/B/C赛区容量补齐调剂。
  - 候选按地理就近 + 积分排序规则筛选。
  - 优先队伍默认不作为被调剂候选。

## 青流采集链路（已更新）

当前默认链路：

1. 优先调用青流公开接口：
   - `GET /api/view/{shareViewId}/lane/baseInfo`
   - `POST /api/view/{shareViewId}/lane/boardViewFilter`
2. 自动翻页聚合（`pageNum`），避免仅取到每赛区前20条。
3. 若API链路失败，回退到页面文本解析（requests）。
4. 若仍失败，继续回退 Playwright 渲染解析。
5. 实时链路都失败时，使用本地缓存快照。

## 公告本地化处理（新增）

所有 `announcement_urls` 现在都会先进行本地化处理，再进入解析流程：

1. 默认模式（`announcement_local_only=false`）：
  - 启动时尝试联网更新公告HTML。
  - 自动落地到 `announcement_local_dir`（默认 `data/announcements`）。
  - 若联网失败且本地已有同名文件，则自动使用本地文件继续运行。
2. 纯本地模式（`announcement_local_only=true`）：
  - 完全不联网。
  - 只读取 `announcement_local_dir` 中的本地公告文件。
  - 若缺少文件会直接报错，便于发现本地数据不完整。

默认文件命名示例：

- `teams_2026_1909.html`
- `rules_2026_1910.html`
- `ranking_2025_1884.html`
- `rmul_hosts_2026_1903.html`
- `national_2025_1856.html`

## 快速开始

1. 创建并激活虚拟环境（可选）：

```bash
python -m venv .venv
source .venv/bin/activate
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 安装 Playwright 浏览器运行时（建议安装，用于回退链路）：

```bash
python -m playwright install chromium
```

4. 准备配置文件（可选）：

```bash
cp config/config.example.json config/config.json
```

## 运行方式

### 启动网页看板

```bash
python run_web.py
```

默认监听 `http://127.0.0.1:8000`（实际绑定 `0.0.0.0:8000`）。

### CLI 一次性运行

```bash
python run.py --once --config config/config.json
```

### CLI 轮询运行

```bash
python run.py --config config/config.json --interval 60
```

常用参数：

- `--once`：只跑一轮。
- `--interval`：轮询秒数。
- `--max-iterations`：最大轮询次数（非 `--once` 下生效）。
- `--config`：配置文件路径。

## 网页看板说明

- 三赛区卡片展示（移动端自动单列）。
- 每赛区显示：
  - 去年16强已报名数（实时）
  - 国赛名额
  - 复活赛名额（模拟）
  - 当前志愿人数
- 学校列表排序：先去年国赛排名，再积分排名。
- 成绩列展示：
  - 前三十二强显示层级（如三十二强、十六强等）。
  - 去年二等奖且不在前三十二强显示为“复活赛”。
- 每赛区固定展示32席位，不足部分以“空位”补齐。
- 前端60秒自动刷新，也支持手动刷新。

## 配置项

见 `config/config.example.json`：

- `poll_interval_sec`：CLI默认轮询周期。
- `expected_total_teams`：预期总队伍数（当前96）。
- `capacity_per_region`：单赛区容量（当前32）。
- `qingflow_url`：青流分享链接。
- `announcement_urls`：公告来源链接集合（1909/1910/1884/1903/1856等）。
- `announcement_local_dir`：公告HTML本地目录（会自动创建）。
- `announcement_local_only`：是否仅使用本地公告文件（true时不联网）。
- `manual_top16_counts`：手动覆盖去年16强赛区分布。
- `priority_schools`：手动配置的优先学校初始列表。
- `rmu_ranking_csv`：本地积分榜CSV路径。
- `cache_file`：青流快照缓存文件路径。
- `request_timeout_sec`：抓取超时时间（秒）。

## API

- `GET /api/analysis`
  - 返回当前分析结果（JSON）：
    - `generated_at`
    - `total_submitted`
    - `expected_total`
    - `regions[]`（含每赛区学校行、名额、志愿人数）
    - `notes`

## 测试

```bash
PYTHONPATH=src pytest -q
```

当前测试覆盖：名额分配、调剂逻辑、复活赛标注、高亮输出。

## 常见问题

### 1) 页面看起来还是旧数据

通常是旧进程仍在占用端口。先停止旧服务，再重新启动 `python run_web.py`。

### 2) 启动时报端口被占用

说明 `8000` 已被其他进程占用。可先释放端口，或用自定义启动方式改端口运行。

### 3) 青流抓取失败

程序会按“API -> requests文本 -> Playwright渲染 -> 缓存快照”逐级回退。若要启用渲染回退，请确认已安装 Playwright 与 Chromium。

## 说明

本项目用于实时态势分析与预测辅助，最终录取与调剂结果以组委会公告为准。
