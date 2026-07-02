# A-share Daily Review System

收盘后复盘系统：筛选沪深主板候选股，生成 Markdown/CSV 报告，并把核心结论推送到 Lark 群机器人。

## 快速运行

```bash
cd /Users/bytedance/Documents/Codex/2026-07-02/wo/stock_review_system
python3 review.py --config config.example.json --date 2026-07-02 --dry-run-lark
```

输出：

```text
../outputs/daily-review-YYYY-MM-DD.md
../outputs/candidates-YYYY-MM-DD.csv
```

## Lark 机器人推送

把群机器人 webhook 配到环境变量：

```bash
export LARK_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/xxxx'
python3 review.py --config config.example.json --date 2026-07-02
```

如果机器人配置了签名校验，再配置：

```bash
export LARK_WEBHOOK_SECRET='xxxxx'
```

## 数据源

当前实现包含：

- `sample`：内置示例数据，用于验证系统流程
- `csv`：读取本地 CSV，适合先从东方财富、同花顺、Choice 等导出数据
- `akshare`：直接从 AKShare 拉取 A 股行情、行业板块、涨停池、龙虎榜等数据

CSV 模式需要在 config 中配置：

```json
{
  "data_source": "csv",
  "csv_dir": "data.example"
}
```

文件名：

```text
quotes.csv
sectors.csv
limit_ups.csv
lhb.csv
```

试跑 CSV 模板：

```bash
python3 review.py --config config.csv.example.json --date 2026-07-02 --dry-run-lark
```

## AKShare 模式

先安装依赖：

```bash
uv sync
```

运行：

```bash
uv run python review.py --config config.akshare.example.json --date 2026-07-02 --dry-run-lark
```

AKShare 部分上游接口可能因源站限流或字段变化失败；系统会尽量降级，例如龙虎榜失败时仍生成行情和板块报告。

## 设计原则

系统不直接输出“必买”，而是输出候选池和次日触发条件：

- A 类：重点观察
- B 类：低吸观察
- C 类：跟踪但暂不买
- D 类：剔除

核心规则已经处理“强趋势上涨不能机械扣分”：

- 涨幅高但趋势健康：不扣或加分
- 涨幅高叠加加速、爆量炸板、板块分歧：扣分
