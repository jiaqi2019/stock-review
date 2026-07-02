from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Sector:
    name: str
    gain_1d_pct: float
    gain_3d_pct: float
    fund_flow_billion: float
    amount_billion: float
    limit_up_count: int = 0
    advancers: int = 0
    decliners: int = 0


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    sector: str
    close: float
    gain_1d_pct: float
    gain_3d_pct: float
    gain_5d_pct: float
    amount_billion: float
    turnover_pct: float
    volume_ratio: float
    is_limit_up: bool = False
    board_count: int = 0
    limit_up_reason: str = ""
    is_st: bool = False
    above_ma5: bool = True
    above_ma10: bool = True
    long_upper_shadow: bool = False
    high_volume_failed_board: bool = False
    late_limit_up: bool = False
    one_word_board: bool = False


@dataclass(frozen=True)
class LhbRecord:
    code: str
    net_buy_billion: float
    buy_total_billion: float
    sell_total_billion: float
    top_buyer_share_pct: float
    institution_net_billion: float = 0.0
    note: str = ""


@dataclass
class MarketSnapshot:
    date: str
    total_amount_billion: float
    limit_up_count: int
    limit_down_count: int
    advancers: int
    decliners: int
    max_board_count: int
    sectors: list[Sector]
    stocks: list[Stock]
    lhb_records: list[LhbRecord] = field(default_factory=list)
    market_scope: str = "full"
    data_warnings: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    stock: Stock
    sector: Sector
    score: float
    level: str
    reasons: list[str]
    risks: list[str]
    buy_conditions: list[str]
    stop_conditions: list[str]


@dataclass
class ReviewResult:
    date: str
    market_status: str
    market_comment: str
    top_sectors: list[Sector]
    candidates: list[Candidate]
    data_warnings: list[str] = field(default_factory=list)

    def to_lark_summary(self, markdown_name: str, csv_name: str) -> str:
        lines = [
            f"收盘复盘 {self.date}",
            f"市场状态：{self.market_status}",
            self.market_comment,
        ]
        if self.data_warnings:
            lines.append("数据提示：" + "；".join(self.data_warnings[:3]))
        lines.extend(["", "主线板块："])
        for sector in self.top_sectors[:5]:
            fund_flow = "不可用" if sector.fund_flow_billion < 0 else f"{sector.fund_flow_billion:.2f}亿"
            gain_3d = "不可用" if sector.gain_3d_pct < -900 else f"{sector.gain_3d_pct:.2f}%"
            lines.append(
                f"- {sector.name}: 3日{gain_3d}，今日资金{fund_flow}，涨停{sector.limit_up_count}只"
            )
        lines.append("")
        lines.append("明日候选池：")
        if not self.candidates:
            lines.append("- 暂无 A/B 类候选，建议等待更清晰买点")
        for item in self.candidates[:8]:
            lines.append(
                f"- [{item.level}] {item.stock.name}({item.stock.code}) {item.stock.sector} 分数{item.score:.1f}: "
                + "；".join(item.reasons[:2])
            )
        lines.extend(["", f"报告：{markdown_name}", f"候选：{csv_name}", "提示：候选不等于买入，次日需满足触发条件。"])
        return "\n".join(lines)


def sector_map(sectors: Iterable[Sector]) -> dict[str, Sector]:
    return {sector.name: sector for sector in sectors}


def output_path(output_dir: Path, prefix: str, date: str, suffix: str) -> Path:
    return output_dir / f"{prefix}-{date}.{suffix}"
