#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_review.lark import send_lark_text
from stock_review.providers.akshare_provider import AkshareProvider
from stock_review.providers.csv_provider import CsvProvider
from stock_review.providers.sample_provider import SampleProvider
from stock_review.report import write_candidate_csv, write_markdown_report
from stock_review.scoring import ReviewEngine


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_provider(config: dict):
    source = config.get("data_source", "sample")
    if source == "sample":
        return SampleProvider()
    if source == "csv":
        return CsvProvider(Path(config.get("csv_dir", "data")))
    if source == "akshare":
        return AkshareProvider(config)
    raise ValueError(f"Unsupported data_source: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description="A-share daily review system")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--date", required=True, help="Trading date, YYYY-MM-DD")
    parser.add_argument("--dry-run-lark", action="store_true", help="Print Lark payload instead of sending")
    args = parser.parse_args()

    config = load_config(args.config)
    provider = build_provider(config)
    snapshot = provider.load(args.date)

    engine = ReviewEngine(config)
    result = engine.run(snapshot)

    output_dir = Path(config.get("output_dir", "outputs")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = write_markdown_report(result, output_dir)
    csv_path = write_candidate_csv(result, output_dir)

    lark_summary = result.to_lark_summary(md_path.name, csv_path.name)
    lark_cfg = config.get("lark", {})
    if lark_cfg.get("enabled", True):
        send_lark_text(
            lark_summary,
            webhook_env=lark_cfg.get("webhook_env", "LARK_WEBHOOK_URL"),
            secret_env=lark_cfg.get("secret_env", "LARK_WEBHOOK_SECRET"),
            dry_run=args.dry_run_lark,
        )

    print(f"Markdown report: {md_path}")
    print(f"Candidate CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
