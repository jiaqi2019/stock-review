from __future__ import annotations

import csv
from pathlib import Path

from .models import ReviewResult, output_path


def write_markdown_report(result: ReviewResult, output_dir: Path) -> Path:
    path = output_path(output_dir, "daily-review", result.date, "md")
    lines: list[str] = [
        f"# 收盘复盘 {result.date}",
        "",
        "## 一、市场环境",
        "",
        f"- 状态：{result.market_status}",
        f"- 结论：{result.market_comment}",
    ]
    if result.data_warnings:
        lines.append("- 数据提示：" + "；".join(result.data_warnings))
    lines.extend([
        "",
        "## 二、主线板块",
        "",
        "| 板块 | 3日涨幅 | 今日涨幅 | 资金流入 | 成交额 | 样本数 | 涨停 | 涨停占比 | 5%+占比 | 3%+占比 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for sector in result.top_sectors:
        fund_flow = "不可用" if sector.fund_flow_billion < 0 else f"{sector.fund_flow_billion:.2f}亿"
        gain_3d = "不可用" if sector.gain_3d_pct < -900 else f"{sector.gain_3d_pct:.2f}%"
        breadth_reliable = not (sector.stock_count <= sector.limit_up_count and sector.limit_up_count > 0)
        limit_up_ratio = f"{sector.limit_up_ratio:.1f}%" if breadth_reliable else "样本不足"
        gain_5_ratio = f"{sector.gain_5_ratio:.1f}%" if breadth_reliable else "样本不足"
        gain_3_ratio = f"{sector.gain_3_ratio:.1f}%" if breadth_reliable else "样本不足"
        lines.append(
            f"| {sector.name} | {gain_3d} | {sector.gain_1d_pct:.2f}% | {fund_flow} | {sector.amount_billion:.2f}亿 | "
            f"{sector.stock_count} | {sector.limit_up_count} | {limit_up_ratio} | {gain_5_ratio} | {gain_3_ratio} |"
        )

    lines.extend([
        "",
        "## 三、明日候选池",
        "",
        "| 等级 | 股票 | 板块 | 分数 | 理由 | 风险 |",
        "|---|---|---|---:|---|---|",
    ])
    if not result.candidates:
        lines.append("| - | - | - | - | 暂无候选 | 等待更清晰买点 |")
    for item in result.candidates:
        lines.append(
            f"| {item.level} | {item.stock.name}({item.stock.code}) | {item.stock.sector} | {item.score:.1f} | "
            f"{'<br>'.join(item.reasons[:4])} | {'<br>'.join(item.risks[:4]) or '-'} |"
        )

    lines.extend(["", "## 四、次日交易计划", ""])
    for item in result.candidates[:8]:
        lines.append(f"### [{item.level}] {item.stock.name}({item.stock.code})")
        lines.append("")
        lines.append("买入触发：")
        for condition in item.buy_conditions:
            lines.append(f"- {condition}")
        lines.append("")
        lines.append("放弃/止损：")
        for condition in item.stop_conditions:
            lines.append(f"- {condition}")
        lines.append("")

    lines.extend([
        "## 五、规则备注",
        "",
        "- 候选不等于买入，次日必须满足触发条件。",
        "- 近5日涨幅高不再机械扣分；只有叠加加速、炸板、长上影、板块退潮时才扣分。",
        "- 龙虎榜一家独大、尾盘偷袭涨停、高位放量炸板会降低次日接力确定性。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_candidate_csv(result: ReviewResult, output_dir: Path) -> Path:
    path = output_path(output_dir, "candidates", result.date, "csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "level", "code", "name", "sector", "score", "reasons", "risks"])
        for item in result.candidates:
            writer.writerow(
                [
                    result.date,
                    item.level,
                    item.stock.code,
                    item.stock.name,
                    item.stock.sector,
                    item.score,
                    "；".join(item.reasons),
                    "；".join(item.risks),
                ]
            )
    return path
