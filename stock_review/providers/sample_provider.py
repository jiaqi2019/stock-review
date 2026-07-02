from __future__ import annotations

from ..models import LhbRecord, MarketSnapshot, Sector, Stock


class SampleProvider:
    def load(self, date: str) -> MarketSnapshot:
        sectors = [
            Sector("AI算力", 2.8, 9.6, 36.5, 980.0, 7, 84, 22),
            Sector("机器人", 1.9, 7.8, 18.2, 420.0, 5, 48, 19),
            Sector("电力设备", 0.7, 4.2, 6.1, 510.0, 2, 56, 44),
            Sector("消费电子", -0.3, 6.4, -2.0, 390.0, 1, 30, 51),
        ]
        stocks = [
            Stock("000001", "示例科技A", "AI算力", 18.2, 9.98, 24.0, 38.0, 18.5, 12.0, 2.1, True, 2, "AI算力订单增长", False, True, True),
            Stock("002001", "示例光模块", "AI算力", 42.6, 5.2, 18.3, 28.5, 32.0, 8.5, 1.7, False, 0, "AI硬件景气延续", False, True, True),
            Stock("603001", "示例服务器", "AI算力", 31.4, 10.0, 16.0, 21.0, 9.2, 7.1, 1.5, True, 1, "服务器订单", False, True, True),
            Stock("605001", "示例机器人", "机器人", 15.8, 10.0, 12.5, 18.0, 5.6, 6.0, 1.8, True, 1, "机器人政策催化", False, True, True),
            Stock("600001", "示例尾盘板", "AI算力", 12.1, 10.0, 10.0, 32.0, 4.2, 18.0, 2.9, True, 1, "题材补涨", False, True, True, False, False, True),
            Stock("000002", "示例炸板股", "机器人", 22.0, 4.1, 14.2, 31.0, 11.0, 16.0, 3.4, False, 0, "机器人补涨", False, True, True, True, True),
            Stock("300001", "创业板示例", "AI算力", 51.0, 12.0, 30.0, 45.0, 25.0, 11.0, 2.0, False, 0, "排除创业板", False, True, True),
        ]
        lhb = [
            LhbRecord("000001", 1.2, 3.5, 2.3, 28, 0.2, "多席位合力"),
            LhbRecord("600001", 0.8, 1.5, 0.7, 55, 0.0, "一家独大"),
            LhbRecord("000002", 0.4, 1.8, 1.4, 22, -0.3, "机构卖出"),
        ]
        return MarketSnapshot(date, 10200.0, 68, 8, 3100, 1700, 5, sectors, stocks, lhb)
