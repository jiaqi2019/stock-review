from __future__ import annotations

import csv
from pathlib import Path

from ..models import LhbRecord, MarketSnapshot, Sector, Stock


class CsvProvider:
    def __init__(self, csv_dir: Path):
        self.csv_dir = csv_dir

    def load(self, date: str) -> MarketSnapshot:
        sectors = [self._sector(row) for row in self._read("sectors.csv")]
        stocks = [self._stock(row) for row in self._read("quotes.csv")]
        lhb_path = self.csv_dir / "lhb.csv"
        lhb = [self._lhb(row) for row in self._read("lhb.csv")] if lhb_path.exists() else []
        market = self._market(date, sectors, stocks)
        market.lhb_records = lhb
        return market

    def _read(self, filename: str):
        path = self.csv_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing CSV file: {path}")
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def _sector(self, row: dict) -> Sector:
        return Sector(
            name=row["sector"],
            gain_1d_pct=_f(row.get("gain_1d_pct")),
            gain_3d_pct=_f(row.get("gain_3d_pct")),
            fund_flow_billion=_f(row.get("fund_flow_billion")),
            amount_billion=_f(row.get("amount_billion")),
            limit_up_count=int(_f(row.get("limit_up_count"))),
            advancers=int(_f(row.get("advancers"))),
            decliners=int(_f(row.get("decliners"))),
        )

    def _stock(self, row: dict) -> Stock:
        return Stock(
            code=row["code"].zfill(6),
            name=row["name"],
            sector=row["sector"],
            close=_f(row.get("close")),
            gain_1d_pct=_f(row.get("gain_1d_pct")),
            gain_3d_pct=_f(row.get("gain_3d_pct")),
            gain_5d_pct=_f(row.get("gain_5d_pct")),
            amount_billion=_f(row.get("amount_billion")),
            turnover_pct=_f(row.get("turnover_pct")),
            volume_ratio=_f(row.get("volume_ratio")),
            is_limit_up=_b(row.get("is_limit_up")),
            board_count=int(_f(row.get("board_count"))),
            limit_up_reason=row.get("limit_up_reason", ""),
            is_st=_b(row.get("is_st")),
            above_ma5=_b(row.get("above_ma5", "1")),
            above_ma10=_b(row.get("above_ma10", "1")),
            long_upper_shadow=_b(row.get("long_upper_shadow")),
            high_volume_failed_board=_b(row.get("high_volume_failed_board")),
            late_limit_up=_b(row.get("late_limit_up")),
            one_word_board=_b(row.get("one_word_board")),
        )

    def _lhb(self, row: dict) -> LhbRecord:
        return LhbRecord(
            code=row["code"].zfill(6),
            net_buy_billion=_f(row.get("net_buy_billion")),
            buy_total_billion=_f(row.get("buy_total_billion")),
            sell_total_billion=_f(row.get("sell_total_billion")),
            top_buyer_share_pct=_f(row.get("top_buyer_share_pct")),
            institution_net_billion=_f(row.get("institution_net_billion")),
            note=row.get("note", ""),
        )

    def _market(self, date: str, sectors: list[Sector], stocks: list[Stock]) -> MarketSnapshot:
        return MarketSnapshot(
            date=date,
            total_amount_billion=sum(s.amount_billion for s in stocks),
            limit_up_count=sum(1 for s in stocks if s.is_limit_up),
            limit_down_count=sum(1 for s in stocks if s.gain_1d_pct <= -9.8),
            advancers=sum(1 for s in stocks if s.gain_1d_pct > 0),
            decliners=sum(1 for s in stocks if s.gain_1d_pct < 0),
            max_board_count=max((s.board_count for s in stocks), default=0),
            sectors=sectors,
            stocks=stocks,
        )


def _f(value) -> float:
    if value in (None, ""):
        return 0.0
    return float(str(value).replace("%", "").replace(",", ""))


def _b(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y", "是")
