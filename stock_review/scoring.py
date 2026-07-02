from __future__ import annotations

from .models import Candidate, MarketSnapshot, ReviewResult, Stock, sector_map


class ReviewEngine:
    def __init__(self, config: dict):
        self.config = config
        self.thresholds = config.get("thresholds", {})
        self.market_cfg = config.get("market", {})
        self.weights = config.get("weights", {})

    def run(self, snapshot: MarketSnapshot) -> ReviewResult:
        market_status, market_comment, market_score = self._score_market(snapshot)
        top_sectors = self._select_top_sectors(snapshot)
        candidates = self._score_candidates(snapshot, top_sectors, market_score)
        candidates.sort(key=lambda x: x.score, reverse=True)
        limit = int(self.thresholds.get("candidate_limit", 20))
        candidates = candidates[:limit]
        return ReviewResult(snapshot.date, market_status, market_comment, top_sectors, candidates, snapshot.data_warnings)

    def _score_market(self, snapshot: MarketSnapshot) -> tuple[str, str, float]:
        if snapshot.market_scope != "full":
            return (
                "数据降级，市场环境不可判断",
                "全市场行情不可用，本次只用于题材/涨停池观察，不应据此判断整体仓位。",
                0.0,
            )
        score = 0.0
        if snapshot.limit_up_count >= 60:
            score += 5
        elif snapshot.limit_up_count >= 35:
            score += 3
        if snapshot.limit_down_count <= 10:
            score += 3
        elif snapshot.limit_down_count >= 30:
            score -= 4
        if snapshot.advancers > snapshot.decliners:
            score += 3
        else:
            score -= 2
        if snapshot.max_board_count >= 4:
            score += 2
        if snapshot.total_amount_billion >= 9000:
            score += 2

        if score >= 10:
            return "强势，可参与", "短线情绪较好，允许关注主线核心和低位补涨。", score
        if score >= 5:
            return "中性偏强，只做主线", "市场有赚钱效应，但追高需要看板块延续和个股承接。", score
        if score >= 0:
            return "中性偏弱，控制仓位", "只看辨识度最高的核心票，弱转强失败要放弃。", score
        return "弱势，建议防守", "跌停或下跌家数压力较大，候选只跟踪不急于出手。", score

    def _select_top_sectors(self, snapshot: MarketSnapshot):
        count = int(self.thresholds.get("top_sector_count", 10))
        ranked = sorted(snapshot.sectors, key=self._sector_sort_key, reverse=True)
        filtered = [
            s
            for s in ranked
            if s.gain_1d_pct >= 0 and (s.fund_flow_billion > 0 or s.limit_up_count > 0 or self._available(s.gain_3d_pct) > 0)
        ]
        return filtered[:count]

    def _sector_sort_key(self, sector):
        gain_3d = self._available(sector.gain_3d_pct)
        fund_flow = max(0.0, sector.fund_flow_billion)
        composite = (
            min(20.0, gain_3d) * 0.45
            + min(10.0, sector.limit_up_count) * 1.0
            + min(20.0, sector.limit_up_ratio) * 0.65
            + min(40.0, sector.gain_5_ratio) * 0.18
            + min(60.0, sector.gain_3_ratio) * 0.10
            + min(80.0, sector.amount_billion) * 0.08
            + min(30.0, fund_flow) * 0.25
        )
        return (composite, sector.limit_up_ratio, sector.gain_5_ratio, sector.limit_up_count, sector.amount_billion)

    def _score_candidates(self, snapshot: MarketSnapshot, top_sectors, market_score: float) -> list[Candidate]:
        sector_by_name = sector_map(top_sectors)
        lhb_by_code = {x.code: x for x in snapshot.lhb_records}
        candidates: list[Candidate] = []
        for stock in snapshot.stocks:
            if stock.sector not in sector_by_name:
                continue
            if not self._is_main_board(stock.code):
                continue
            if self.market_cfg.get("exclude_st", True) and stock.is_st:
                continue
            if stock.amount_billion < float(self.market_cfg.get("min_amount_billion", 1.0)):
                continue

            sector = sector_by_name[stock.sector]
            score = 0.0
            reasons: list[str] = []
            risks: list[str] = []

            sector_3d_weight = self._w("sector_3d_strength", 15)
            score += min(sector_3d_weight, max(0, self._available(sector.gain_3d_pct) / 8 * sector_3d_weight))
            if self._available(sector.gain_3d_pct) > 0:
                reasons.append(f"板块3日涨幅{sector.gain_3d_pct:.1f}%")

            fund_weight = self._w("sector_fund_flow", 15)
            score += min(fund_weight, max(0, sector.fund_flow_billion / 20 * fund_weight))
            if sector.fund_flow_billion > 0:
                reasons.append(f"板块资金净流入{sector.fund_flow_billion:.1f}亿")

            structure_weight = self._w("sector_limit_up_structure", 15)
            breadth_reliable = self._breadth_reliable(sector)
            if breadth_reliable:
                structure_score = min(
                    structure_weight,
                    sector.limit_up_ratio * 0.45 + sector.gain_5_ratio * 0.18 + sector.gain_3_ratio * 0.08,
                )
            else:
                structure_score = min(structure_weight, sector.limit_up_count * 1.5)
            score += structure_score
            if breadth_reliable and (sector.limit_up_count >= 3 or sector.gain_5_ratio >= 10):
                reasons.append(f"板块扩散：涨停{sector.limit_up_ratio:.1f}%，5%+{sector.gain_5_ratio:.1f}%，3%+{sector.gain_3_ratio:.1f}%")
            elif sector.limit_up_count >= 3:
                reasons.append(f"板块涨停{sector.limit_up_count}只，扩散比例样本不足")

            score += self._stock_status_score(stock, reasons)
            score += self._reason_score(stock, reasons)
            score += self._lhb_score(stock, lhb_by_code, reasons, risks)
            score += self._price_volume_score(stock, reasons, risks)
            score += self._trend_adjustment(stock, sector, reasons, risks)
            score -= self._risk_penalty(stock, sector, risks)

            score += max(0, min(5, market_score / 2))
            level = self._level(score, risks)
            if level != "D":
                candidates.append(
                    Candidate(
                        stock=stock,
                        sector=sector,
                        score=round(score, 1),
                        level=level,
                        reasons=reasons,
                        risks=risks,
                        buy_conditions=self._buy_conditions(stock),
                        stop_conditions=self._stop_conditions(stock),
                    )
                )
        return candidates

    def _stock_status_score(self, stock: Stock, reasons: list[str]) -> float:
        weight = self._w("stock_status", 15)
        if stock.board_count >= 3:
            reasons.append(f"板块高辨识度连板，{stock.board_count}连板")
            return weight
        if stock.is_limit_up:
            reasons.append("涨停确认强度")
            return weight * 0.73
        if stock.volume_ratio >= float(self.thresholds.get("volume_ratio_min", 1.3)) and stock.gain_1d_pct > 3:
            reasons.append(f"日内放量走强，量比{stock.volume_ratio:.2f}")
            return weight * 0.40
        return max(0, self._available(stock.gain_3d_pct) / 10 * weight * 0.47)

    def _reason_score(self, stock: Stock, reasons: list[str]) -> float:
        weight = self._w("reason_sustainability", 10)
        text = stock.limit_up_reason
        if not text:
            return weight * 0.20
        durable_words = ["业绩", "订单", "涨价", "政策", "国产替代", "AI", "算力", "半导体", "机器人", "电力"]
        score = weight * 0.50
        if any(word in text for word in durable_words):
            score += weight * 0.50
            reasons.append(f"逻辑具备持续性：{text}")
        else:
            reasons.append(f"涨停原因：{text}")
        return min(weight, score)

    def _lhb_score(self, stock: Stock, lhb_by_code, reasons: list[str], risks: list[str]) -> float:
        record = lhb_by_code.get(stock.code)
        if not record:
            return 0
        weight = self._w("lhb_quality", 10)
        score = 0.0
        if record.net_buy_billion > 0:
            score += min(weight * 0.40, record.net_buy_billion / max(stock.amount_billion, 0.1) * weight * 2)
            reasons.append(f"龙虎榜净买入{record.net_buy_billion:.2f}亿")
        if record.institution_net_billion > 0:
            score += weight * 0.30
            reasons.append("机构净买入")
        if record.top_buyer_share_pct >= 45:
            score -= weight * 0.50
            risks.append(f"龙虎榜一家独大，买一占比{record.top_buyer_share_pct:.0f}%")
        elif 0 < record.top_buyer_share_pct <= 30 and record.net_buy_billion > 0:
            score += weight * 0.30
            reasons.append("龙虎榜多席位合力")
        return score

    def _price_volume_score(self, stock: Stock, reasons: list[str], risks: list[str]) -> float:
        weight = self._w("price_volume", 5)
        score = 0.0
        if stock.volume_ratio >= float(self.thresholds.get("volume_ratio_min", 1.3)):
            score += weight * 0.40
            reasons.append(f"量比{stock.volume_ratio:.2f}超过阈值")
        if stock.turnover_pct >= 5:
            score += weight * 0.30
        if self._available(stock.gain_5d_pct) >= 0 and stock.above_ma5 and stock.above_ma10:
            score += weight * 0.30
            reasons.append("站上5日/10日线")
        if stock.long_upper_shadow:
            score -= 3
            risks.append("长上影，追高资金承接不足")
        return score

    def _trend_adjustment(self, stock: Stock, sector, reasons: list[str], risks: list[str]) -> float:
        high_5d = float(self.thresholds.get("high_5d_gain_pct", 25.0))
        if stock.gain_5d_pct < -900:
            return 0
        if stock.gain_5d_pct < high_5d:
            return 0
        healthy = stock.above_ma5 and stock.above_ma10 and not stock.high_volume_failed_board and not stock.long_upper_shadow
        if healthy and sector.fund_flow_billion > 0 and sector.limit_up_count >= 2:
            bonus = float(self.thresholds.get("healthy_trend_bonus", 5))
            reasons.append(f"5日涨幅{stock.gain_5d_pct:.1f}%但趋势健康，不机械扣分")
            return bonus
        risks.append(f"5日涨幅{stock.gain_5d_pct:.1f}%且趋势质量转弱")
        return -float(self.thresholds.get("high_gain_risk_penalty", 10))

    def _risk_penalty(self, stock: Stock, sector, risks: list[str]) -> float:
        penalty = 0.0
        if stock.high_volume_failed_board:
            penalty += 15
            risks.append("高位放量炸板")
        if stock.late_limit_up:
            penalty += 5
            risks.append("尾盘偷袭涨停，强度验证不足")
        if sector.limit_up_count >= 10 and self._available(sector.gain_3d_pct) >= 12:
            penalty += 10
            risks.append("板块可能进入高潮，次日分化概率升高")
        if stock.one_word_board and stock.gain_5d_pct >= 25:
            penalty += 8
            risks.append("连续加速或一字板，换手不足")
        return penalty

    def _buy_conditions(self, stock: Stock) -> list[str]:
        return [
            "所属板块竞价和开盘后继续强于大盘",
            "核心龙头不低开快速走弱",
            "个股放量承接，不能冲高回落",
            "优先等分歧承接或平台突破，不无条件追高",
        ]

    def _stop_conditions(self, stock: Stock) -> list[str]:
        return [
            "跌破昨日关键承接位或涨停价",
            "板块龙头开盘核按钮且无修复",
            "放量长上影或炸板后无法回封",
        ]

    def _level(self, score: float, risks: list[str]) -> str:
        serious = any("高位放量炸板" in x or "板块可能进入高潮" in x for x in risks)
        if score >= 70 and not serious:
            return "A"
        if score >= 55:
            return "B"
        if score >= 42:
            return "C"
        return "D"

    def _is_main_board(self, code: str) -> bool:
        prefixes = tuple(self.market_cfg.get("main_board_prefixes", ["000", "001", "002", "003", "600", "601", "603", "605"]))
        return code.startswith(prefixes)

    def _w(self, name: str, default: float) -> float:
        return float(self.weights.get(name, default))

    def _available(self, value: float) -> float:
        return 0.0 if value < -900 else value

    def _breadth_reliable(self, sector) -> bool:
        return sector.stock_count > 0 and not (sector.limit_up_count > 0 and sector.stock_count <= sector.limit_up_count)
