from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import pickle
import time
from typing import Any

from ..models import LhbRecord, MarketSnapshot, Sector, Stock

UNAVAILABLE_PCT = -999.0


class AkshareProvider:
    def __init__(self, config: dict):
        try:
            import akshare as ak
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "AkShare provider requires akshare and pandas. Install with: "
                "python3 -m pip install akshare pandas"
            ) from exc
        self.ak = ak
        self.pd = pd
        self.config = config
        self.ak_cfg = config.get("akshare", {})

    def load(self, date: str) -> MarketSnapshot:
        trade_date = date.replace("-", "")
        warnings: list[str] = []
        market_scope = "full"
        if datetime.strptime(trade_date, "%Y%m%d").weekday() >= 5:
            warnings.append("日期可能不是交易日，请确认是否使用上一交易日数据")
        limit_pool = self._limit_up_pool(trade_date)
        try:
            stock_spot = self._stock_spot()
            quotes = self._build_stocks(stock_spot)
        except Exception as exc:
            print(f"AKShare warning: 全市场行情获取失败，降级使用涨停池: {type(exc).__name__}: {exc}")
            warnings.append("全市场行情不可用，已降级为涨停池数据")
            market_scope = "limit_pool"
            quotes = self._stocks_from_limit_pool(limit_pool)
        quotes = self._attach_limit_up_info(quotes, trade_date, limit_pool)
        quotes = self._promote_uncategorized_momentum(quotes)
        sectors = self._build_sectors(trade_date, quotes, limit_pool)
        lhb_records = self._build_lhb(trade_date)
        quotes = self._attach_history_metrics(quotes, sectors, trade_date)
        sectors = self._attach_sector_limit_up_counts(sectors, quotes)
        sectors = self._refresh_sector_metrics_from_stocks(sectors, quotes)
        if any(sector.fund_flow_billion < 0 for sector in sectors):
            warnings.append("板块资金流接口不可用，资金流字段显示为不可用")
        return MarketSnapshot(
            date=date,
            total_amount_billion=sum(x.amount_billion for x in quotes),
            limit_up_count=sum(1 for x in quotes if x.is_limit_up),
            limit_down_count=self._limit_down_count(trade_date, quotes),
            advancers=sum(1 for x in quotes if x.gain_1d_pct > 0),
            decliners=sum(1 for x in quotes if x.gain_1d_pct < 0),
            max_board_count=max((x.board_count for x in quotes), default=0),
            sectors=sectors,
            stocks=quotes,
            lhb_records=lhb_records,
            market_scope=market_scope,
            data_warnings=warnings,
        )

    def _stock_spot(self):
        errors = []
        for fn_name in ("stock_zh_a_spot_em", "stock_zh_a_spot"):
            fn = getattr(self.ak, fn_name)
            for attempt in range(3):
                try:
                    return self._cache_df(f"stock_spot_{fn_name}_{datetime.now().strftime('%Y%m%d')}", fn)
                except Exception as exc:
                    errors.append(f"{fn_name}#{attempt + 1}: {type(exc).__name__}: {exc}")
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("全市场现货接口均失败；" + " | ".join(errors[-4:]))

    def _build_stocks(self, df) -> list[Stock]:
        rows = df.to_dict("records")
        sector_by_code = self._stock_sector_map() if self.config.get("akshare", {}).get("enable_full_industry_map", False) else {}
        amount_col = _pick_col(df, ["成交额", "成交额(元)", "amount"])
        change_col = _pick_col(df, ["涨跌幅", "涨跌幅%", "changepercent"])
        volume_ratio_col = _find_col(df, ["量比"])
        turnover_col = _find_col(df, ["换手率", "turnoverratio"])
        close_col = _pick_col(df, ["最新价", "收盘", "close"])
        code_col = _pick_col(df, ["代码", "symbol", "code"])
        name_col = _pick_col(df, ["名称", "name"])

        stocks: list[Stock] = []
        for row in rows:
            code = _normalize_code(row.get(code_col, ""))
            name = str(row.get(name_col, ""))
            amount_billion = _to_float(row.get(amount_col)) / 100000000 if amount_col else 0.0
            gain_1d = _to_float(row.get(change_col))
            stocks.append(
                Stock(
                    code=code,
                    name=name,
                    sector=sector_by_code.get(code, "未分类"),
                    close=_to_float(row.get(close_col)),
                    gain_1d_pct=gain_1d,
                    gain_3d_pct=UNAVAILABLE_PCT,
                    gain_5d_pct=UNAVAILABLE_PCT,
                    amount_billion=amount_billion,
                    turnover_pct=_to_float(row.get(turnover_col)) if turnover_col else 0.0,
                    volume_ratio=_to_float(row.get(volume_ratio_col)) if volume_ratio_col else 0.0,
                    is_limit_up=gain_1d >= 9.8,
                    board_count=0,
                    limit_up_reason="",
                    is_st=("ST" in name.upper() or "退" in name),
                    above_ma5=False,
                    above_ma10=False,
                    long_upper_shadow=False,
                )
            )
        return stocks

    def _build_sectors(self, trade_date: str, stocks: list[Stock], limit_pool=None) -> list[Sector]:
        sector_rows = []
        try:
            if self.config.get("akshare", {}).get("enable_full_industry_map", False):
                sector_rows = self.ak.stock_board_industry_name_em().to_dict("records")
        except Exception as exc:
            print(f"AKShare warning: 行业板块列表获取失败，使用个股所属行业聚合: {type(exc).__name__}: {exc}")
            sector_rows = []
        fund_by_sector = self._sector_fund_flow()
        fund_unavailable = fund_by_sector is None
        if fund_by_sector is None:
            fund_by_sector = {}
        stocks_by_sector: dict[str, list[Stock]] = {}
        for stock in stocks:
            stocks_by_sector.setdefault(stock.sector, []).append(stock)

        sectors: list[Sector] = []
        seen = set()
        for row in sector_rows:
            name = str(row.get("板块名称") or row.get("名称") or row.get("行业") or "")
            if not name:
                continue
            seen.add(name)
            sector_stocks = stocks_by_sector.get(name, [])
            gain_1d = _to_float(row.get("涨跌幅"))
            gain_3d = self._sector_3d_gain(name, trade_date)
            amount = sum(x.amount_billion for x in sector_stocks)
            sectors.append(
                Sector(
                    name=name,
                    gain_1d_pct=gain_1d,
                    gain_3d_pct=gain_3d,
                    fund_flow_billion=-1.0 if fund_unavailable else fund_by_sector.get(name, 0.0),
                    amount_billion=amount,
                    limit_up_count=sum(1 for x in sector_stocks if x.is_limit_up),
                    advancers=sum(1 for x in sector_stocks if x.gain_1d_pct > 0),
                    decliners=sum(1 for x in sector_stocks if x.gain_1d_pct < 0),
                )
            )
        for name, sector_stocks in stocks_by_sector.items():
            if name in seen or name == "未分类":
                continue
            sectors.append(
                Sector(
                    name=name,
                    gain_1d_pct=_avg([x.gain_1d_pct for x in sector_stocks]),
                    gain_3d_pct=_avg_available([x.gain_3d_pct for x in sector_stocks]),
                    fund_flow_billion=-1.0 if fund_unavailable else fund_by_sector.get(name, 0.0),
                    amount_billion=sum(x.amount_billion for x in sector_stocks),
                    limit_up_count=sum(1 for x in sector_stocks if x.is_limit_up),
                    advancers=sum(1 for x in sector_stocks if x.gain_1d_pct > 0),
                    decliners=sum(1 for x in sector_stocks if x.gain_1d_pct < 0),
                )
            )
        if not sectors and limit_pool is not None:
            sectors = self._sectors_from_limit_pool(limit_pool)
        return sectors

    def _build_lhb(self, trade_date: str) -> list[LhbRecord]:
        try:
            df = self.ak.stock_lhb_detail_daily_sina(date=trade_date)
        except Exception:
            try:
                df = self.ak.stock_lhb_detail_em(start_date=trade_date, end_date=trade_date)
            except Exception as exc:
                print(f"AKShare warning: 龙虎榜获取失败，跳过龙虎榜评分: {type(exc).__name__}: {exc}")
                return []
        records: list[LhbRecord] = []
        for row in df.to_dict("records"):
            code = _normalize_code(row.get("代码") or row.get("股票代码") or row.get("code") or "")
            if not code:
                continue
            buy = _first_number(row, ["买入额", "买入金额", "龙虎榜买入额"])
            sell = _first_number(row, ["卖出额", "卖出金额", "龙虎榜卖出额"])
            net = _first_number(row, ["净买额", "净额"])
            if net == 0 and (buy or sell):
                net = buy - sell
            records.append(
                LhbRecord(
                    code=code,
                    net_buy_billion=net / 100000000,
                    buy_total_billion=buy / 100000000,
                    sell_total_billion=sell / 100000000,
                    top_buyer_share_pct=_first_number(row, ["买一占比", "买一净买占比", "top_buyer_share_pct"]) or -1.0,
                    institution_net_billion=_first_number(row, ["机构净买额", "机构净额", "institution_net_billion"]) / 100000000,
                    note=str(row.get("解读") or row.get("上榜原因") or ""),
                )
            )
        return records

    def _limit_up_pool(self, trade_date: str):
        try:
            return self._cache_df(f"zt_pool_{trade_date}", lambda: self.ak.stock_zt_pool_em(date=trade_date))
        except Exception as exc:
            print(f"AKShare warning: 涨停池获取失败: {type(exc).__name__}: {exc}")
            return self.pd.DataFrame()

    def _limit_down_count(self, trade_date: str, stocks: list[Stock]) -> int:
        try:
            df = self._cache_df(f"dt_pool_{trade_date}", lambda: self.ak.stock_zt_pool_dtgc_em(date=trade_date))
            return len(df)
        except Exception as exc:
            print(f"AKShare warning: 跌停池获取失败，使用涨跌幅估算: {type(exc).__name__}: {exc}")
            return sum(1 for x in stocks if x.gain_1d_pct <= -9.8)

    def _attach_limit_up_info(self, stocks: list[Stock], trade_date: str, pool=None) -> list[Stock]:
        if pool is None:
            pool = self._limit_up_pool(trade_date)
        if pool is None or pool.empty:
            return stocks
        info_by_code: dict[str, dict[str, Any]] = {}
        for row in pool.to_dict("records"):
            code = _normalize_code(row.get("代码") or row.get("股票代码") or "")
            if code:
                info_by_code[code] = row
        updated = []
        for stock in stocks:
            info = info_by_code.get(stock.code)
            if not info:
                updated.append(stock)
                continue
            reason = str(info.get("涨停原因类别") or info.get("涨停原因") or info.get("所属行业") or stock.limit_up_reason)
            board_count = _parse_board_count(info.get("连板数") or info.get("几天几板") or stock.board_count)
            updated.append(
                replace(
                    stock,
                    sector=str(info.get("所属行业") or stock.sector),
                    is_limit_up=True,
                    board_count=board_count,
                    limit_up_reason=reason,
                    high_volume_failed_board=_to_float(info.get("炸板次数")) > 0,
                    late_limit_up=_is_late_limit_up(info.get("首次封板时间")),
                    one_word_board=_is_one_word_board(info),
                )
            )
        return updated

    def _promote_uncategorized_momentum(self, stocks: list[Stock]) -> list[Stock]:
        volume_ratio_min = float(self.config.get("thresholds", {}).get("volume_ratio_min", 1.3))
        min_amount = float(self.config.get("market", {}).get("min_amount_billion", 1.0))
        updated: list[Stock] = []
        for stock in stocks:
            if stock.sector != "未分类" or stock.is_limit_up:
                updated.append(stock)
                continue
            is_momentum = stock.amount_billion >= min_amount and (
                (stock.volume_ratio >= volume_ratio_min and stock.gain_1d_pct >= 3)
                or stock.gain_1d_pct >= 5
            )
            updated.append(replace(stock, sector="未分类异动") if is_momentum else stock)
        return updated

    def _stocks_from_limit_pool(self, pool) -> list[Stock]:
        if pool is None or pool.empty:
            return []
        stocks: list[Stock] = []
        for row in pool.to_dict("records"):
            code = _normalize_code(row.get("代码") or row.get("股票代码") or "")
            name = str(row.get("名称") or "")
            if not code:
                continue
            stocks.append(
                Stock(
                    code=code,
                    name=name,
                    sector=str(row.get("所属行业") or "涨停池"),
                    close=_to_float(row.get("最新价")),
                    gain_1d_pct=_to_float(row.get("涨跌幅")),
                    gain_3d_pct=UNAVAILABLE_PCT,
                    gain_5d_pct=UNAVAILABLE_PCT,
                    amount_billion=_to_float(row.get("成交额")) / 100000000,
                    turnover_pct=_to_float(row.get("换手率")),
                    volume_ratio=0.0,
                    is_limit_up=True,
                    board_count=_parse_board_count(row.get("连板数") or row.get("涨停统计")),
                    limit_up_reason=str(row.get("所属行业") or ""),
                    is_st=("ST" in name.upper() or "退" in name),
                    high_volume_failed_board=_to_float(row.get("炸板次数")) > 0,
                    late_limit_up=_is_late_limit_up(row.get("首次封板时间")),
                    one_word_board=_is_one_word_board(row),
                )
            )
        return stocks

    def _sectors_from_limit_pool(self, pool) -> list[Sector]:
        if pool is None or pool.empty:
            return []
        grouped: dict[str, list[dict]] = {}
        for row in pool.to_dict("records"):
            grouped.setdefault(str(row.get("所属行业") or "涨停池"), []).append(row)
        sectors: list[Sector] = []
        for name, rows in grouped.items():
            sectors.append(
                Sector(
                    name=name,
                    gain_1d_pct=_avg([_to_float(row.get("涨跌幅")) for row in rows]),
                    gain_3d_pct=UNAVAILABLE_PCT,
                    fund_flow_billion=-1.0,
                    amount_billion=sum(_to_float(row.get("成交额")) for row in rows) / 100000000,
                    limit_up_count=len(rows),
                    advancers=len(rows),
                    decliners=0,
                )
            )
        return sectors

    def _attach_sector_limit_up_counts(self, sectors: list[Sector], stocks: list[Stock]) -> list[Sector]:
        counts: dict[str, int] = {}
        for stock in stocks:
            if stock.is_limit_up:
                counts[stock.sector] = counts.get(stock.sector, 0) + 1
        return [replace(sector, limit_up_count=counts.get(sector.name, sector.limit_up_count)) for sector in sectors]

    def _refresh_sector_metrics_from_stocks(self, sectors: list[Sector], stocks: list[Stock]) -> list[Sector]:
        stocks_by_sector: dict[str, list[Stock]] = {}
        for stock in stocks:
            stocks_by_sector.setdefault(stock.sector, []).append(stock)

        refreshed: list[Sector] = []
        known = {sector.name for sector in sectors}
        for sector in sectors:
            sector_stocks = stocks_by_sector.get(sector.name, [])
            if not sector_stocks:
                refreshed.append(sector)
                continue
            available_3d = [x.gain_3d_pct for x in sector_stocks if x.gain_3d_pct > -900]
            refreshed.append(
                replace(
                    sector,
                    gain_1d_pct=_avg([x.gain_1d_pct for x in sector_stocks]),
                    gain_3d_pct=_avg(available_3d) if available_3d else sector.gain_3d_pct,
                    amount_billion=sum(x.amount_billion for x in sector_stocks),
                    limit_up_count=sum(1 for x in sector_stocks if x.is_limit_up),
                    advancers=sum(1 for x in sector_stocks if x.gain_1d_pct > 0),
                    decliners=sum(1 for x in sector_stocks if x.gain_1d_pct < 0),
                    **_sector_breadth(sector_stocks),
                )
            )

        for name, sector_stocks in stocks_by_sector.items():
            if name in known:
                continue
            refreshed.append(
                Sector(
                    name=name,
                    gain_1d_pct=_avg([x.gain_1d_pct for x in sector_stocks]),
                    gain_3d_pct=_avg_available([x.gain_3d_pct for x in sector_stocks]),
                    fund_flow_billion=-1.0,
                    amount_billion=sum(x.amount_billion for x in sector_stocks),
                    limit_up_count=sum(1 for x in sector_stocks if x.is_limit_up),
                    advancers=sum(1 for x in sector_stocks if x.gain_1d_pct > 0),
                    decliners=sum(1 for x in sector_stocks if x.gain_1d_pct < 0),
                    **_sector_breadth(sector_stocks),
                )
            )
        return refreshed

    def _attach_history_metrics(self, stocks: list[Stock], sectors: list[Sector], trade_date: str) -> list[Stock]:
        sector_rank = {sector.name: idx for idx, sector in enumerate(sorted(sectors, key=lambda s: s.gain_3d_pct, reverse=True))}
        volume_ratio_min = float(self.config.get("thresholds", {}).get("volume_ratio_min", 1.3))
        need_history = {
            stock.code
            for stock in stocks
            if stock.is_limit_up
            or stock.sector == "未分类异动"
            or stock.volume_ratio >= volume_ratio_min
            or sector_rank.get(stock.sector, 999) < int(self.config.get("thresholds", {}).get("top_sector_count", 10))
        }
        updated = []
        for stock in stocks:
            if stock.code not in need_history:
                updated.append(stock)
                continue
            gain_3d, gain_5d, above_ma5, above_ma10, long_upper = self._history_metrics(stock.code, trade_date)
            updated.append(
                replace(
                    stock,
                    gain_3d_pct=gain_3d,
                    gain_5d_pct=gain_5d,
                    above_ma5=above_ma5,
                    above_ma10=above_ma10,
                    long_upper_shadow=long_upper,
                )
            )
        return updated

    def _stock_sector_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        try:
            boards = self.ak.stock_board_industry_name_em()
        except Exception:
            return mapping
        for row in boards.to_dict("records"):
            sector = str(row.get("板块名称") or row.get("名称") or "")
            symbol = str(row.get("板块代码") or row.get("代码") or "")
            if not sector or not symbol:
                continue
            try:
                cons = self.ak.stock_board_industry_cons_em(symbol=symbol)
            except Exception:
                continue
            for item in cons.to_dict("records"):
                code = _normalize_code(item.get("代码") or item.get("股票代码") or "")
                if code and code not in mapping:
                    mapping[code] = sector
        return mapping

    def _sector_fund_flow(self) -> dict[str, float] | None:
        try:
            df = self._cache_df(
                f"sector_fund_flow_{datetime.now().strftime('%Y%m%d')}",
                lambda: self.ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"),
            )
        except Exception as exc:
            print(f"AKShare warning: 板块资金流获取失败，报告显示为不可用: {type(exc).__name__}: {exc}")
            return None
        result: dict[str, float] = {}
        for row in df.to_dict("records"):
            name = str(row.get("名称") or row.get("板块") or row.get("行业") or "")
            flow = _first_number(row, ["今日主力净流入-净额", "主力净流入-净额", "净额"])
            if name:
                result[name] = flow / 100000000
        return result

    def _cache_df(self, key: str, loader):
        if not self.ak_cfg.get("cache_enabled", True):
            return loader()
        cache_dir = Path(self.ak_cfg.get("cache_dir", ".cache/akshare"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{key}.pkl"
        if path.exists():
            try:
                with open(path, "rb") as f:
                    return pickle.load(f)
            except Exception:
                path.unlink(missing_ok=True)
        data = loader()
        try:
            with open(path, "wb") as f:
                pickle.dump(data, f)
        except Exception:
            pass
        return data

    def _sector_3d_gain(self, sector_name: str, trade_date: str) -> float:
        try:
            df = self.ak.stock_board_industry_hist_em(symbol=sector_name, period="日k", start_date=_days_before(trade_date, 10), end_date=trade_date)
        except Exception:
            return UNAVAILABLE_PCT
        return _window_gain(df, 3)

    def _history_metrics(self, code: str, trade_date: str) -> tuple[float, float, bool, bool, bool]:
        df = None
        start_date = _days_before(trade_date, 20)
        symbol = _market_symbol(code)
        for loader in (
            lambda: self.ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=trade_date, adjust=""),
            lambda: self.ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=trade_date, adjust=""),
            lambda: self.ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start_date, end_date=trade_date, adjust="", timeout=8),
        ):
            try:
                df = loader()
                if df is not None and len(df) >= 2:
                    break
            except Exception:
                df = None
        if df is None or len(df) < 2:
            return UNAVAILABLE_PCT, UNAVAILABLE_PCT, False, False, False
        close_col = _pick_col(df, ["收盘", "close"])
        high_col = _pick_col(df, ["最高", "high"])
        open_col = _pick_col(df, ["开盘", "open"])
        gain_3d = _window_gain(df, 3)
        gain_5d = _window_gain(df, 5)
        closes = [_to_float(x) for x in df[close_col].tail(10).tolist()]
        last_close = closes[-1]
        ma5 = sum(closes[-5:]) / min(5, len(closes))
        ma10 = sum(closes[-10:]) / min(10, len(closes))
        last = df.tail(1).to_dict("records")[0]
        high = _to_float(last.get(high_col))
        open_price = _to_float(last.get(open_col))
        long_upper = high > 0 and last_close > 0 and (high - max(open_price, last_close)) / last_close >= 0.04
        return gain_3d, gain_5d, last_close >= ma5, last_close >= ma10, long_upper


def _pick_col(df, names: list[str]) -> str:
    for name in names:
        if name in df.columns:
            return name
    raise KeyError(f"Missing expected columns: {names}; actual columns: {list(df.columns)}")


def _find_col(df, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "nan", "None"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _normalize_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _parse_board_count(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if text.replace(".", "", 1).isdigit():
        return int(float(text))
    if "天" in text and "板" in text:
        between = text.split("天", 1)[1].split("板", 1)[0]
        digits = "".join(ch for ch in between if ch.isdigit())
        return int(digits) if digits else 0
    if "/" in text:
        right = text.split("/")[-1]
        digits = "".join(ch for ch in right if ch.isdigit())
        return int(digits) if digits else 0
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def _first_number(row: dict, keys: list[str]) -> float:
    for key in keys:
        if key in row:
            return _to_float(row.get(key))
    return 0.0


def _window_gain(df, days: int) -> float:
    if df is None or len(df) < days:
        return UNAVAILABLE_PCT
    close_col = _pick_col(df, ["收盘", "close"])
    closes = [_to_float(x) for x in df[close_col].tail(days).tolist()]
    if len(closes) < 2 or closes[0] == 0:
        return UNAVAILABLE_PCT
    return (closes[-1] / closes[0] - 1) * 100


def _days_before(trade_date: str, days: int) -> str:
    dt = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=days * 2)
    return dt.strftime("%Y%m%d")


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _avg_available(values: list[float]) -> float:
    available = [value for value in values if value > -900]
    return _avg(available) if available else UNAVAILABLE_PCT


def _sector_breadth(stocks: list[Stock]) -> dict[str, float | int]:
    count = len(stocks)
    if count == 0:
        return {"stock_count": 0, "limit_up_ratio": 0.0, "gain_5_ratio": 0.0, "gain_3_ratio": 0.0}
    return {
        "stock_count": count,
        "limit_up_ratio": sum(1 for stock in stocks if stock.is_limit_up) / count * 100,
        "gain_5_ratio": sum(1 for stock in stocks if stock.gain_1d_pct >= 5) / count * 100,
        "gain_3_ratio": sum(1 for stock in stocks if stock.gain_1d_pct >= 3) / count * 100,
    }


def _market_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def _is_late_limit_up(value: Any) -> bool:
    text = str(value or "")
    if not text.isdigit():
        return False
    return int(text[:4]) >= 1450


def _is_one_word_board(row: dict) -> bool:
    first = str(row.get("首次封板时间") or "")
    last = str(row.get("最后封板时间") or "")
    failed_count = _to_float(row.get("炸板次数"))
    if not first.isdigit():
        return False
    return int(first[:4]) <= 930 and (not last.isdigit() or int(last[:4]) <= 930) and failed_count == 0
