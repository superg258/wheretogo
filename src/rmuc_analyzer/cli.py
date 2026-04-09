from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from rmuc_analyzer.cache import load_snapshot, save_snapshot
from rmuc_analyzer.config import AnalyzerConfig
from rmuc_analyzer.constants import REGION_ORDER
from rmuc_analyzer.engine import (
    build_historical_highlights,
    compute_national_quotas,
    compute_pressure,
    fallback_ranking_from_national,
    infer_top16_counts_from_current_signup,
    load_rmu_ranking,
    predict_reallocation,
)
from rmuc_analyzer.models import QingflowSnapshot
from rmuc_analyzer.output import render_full_report
from rmuc_analyzer.sources.qingflow import parse_qingflow_snapshot
from rmuc_analyzer.sources.robomaster import (
    infer_overseas_priority_schools_2026,
    localize_announcement_sources,
    parse_distance_table_2026,
    parse_national_tiers_2025,
    parse_rmul_host_schools_2026,
    parse_teams_2026,
)
from rmuc_analyzer.utils import normalize_school_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RMUC 三赛区实时名额与调剂分析")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径，默认使用内置配置")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--interval", type=int, default=None, help="轮询间隔秒数，覆盖配置")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="最大轮询次数，0表示不限，仅在非 --once 模式有效",
    )
    return parser.parse_args()


def _build_runtime_notes(
    snapshot: QingflowSnapshot,
    expected_total_teams: int,
    priority_schools: List[str],
    top16_notes: List[str],
) -> List[str]:
    submitted = sum(snapshot.region_counts.get(region, 0) for region in REGION_ORDER)
    notes = list(top16_notes)

    notes.append(f"当前志愿总数: {submitted}/{expected_total_teams}")
    if submitted < expected_total_teams:
        notes.append("队伍尚未全部提交志愿，调剂预测为阶段性结果")

    if any(snapshot.region_counts.get(region, 0) > 32 for region in REGION_ORDER):
        notes.append("存在超容量赛区，已触发可能调剂分析")
    else:
        notes.append("当前未出现超容量赛区，暂无确定调剂对象")

    notes.append(f"优先学校(含RMUL/RMUC承办院校及海外队伍): {', '.join(priority_schools)}")

    for region in REGION_ORDER:
        visible = len(snapshot.region_schools.get(region, []))
        total = snapshot.region_counts.get(region, 0)
        if visible < total:
            notes.append(f"{region}赛区可识别学校数 {visible}/{total}，页面结构变化可能影响调剂名单完整性")

    if snapshot.stale:
        notes.append("本次使用缓存快照，非实时数据")

    return notes


def main() -> int:
    args = parse_args()
    root_dir = Path(__file__).resolve().parents[2]

    config = AnalyzerConfig.load(args.config, root_dir)
    interval = args.interval if args.interval is not None else config.poll_interval_sec

    cache_file = config.resolve_path(root_dir, config.cache_file)
    ranking_csv = config.resolve_path(root_dir, config.rmu_ranking_csv)
    announcement_sources = localize_announcement_sources(
        config.announcement_urls,
        root_dir=root_dir,
        timeout_sec=config.request_timeout_sec,
        local_dir=config.announcement_local_dir,
        local_only=config.announcement_local_only,
    )

    print("初始化静态数据...", flush=True)
    teams = parse_teams_2026(announcement_sources["teams_2026"], config.request_timeout_sec)
    distance_map = parse_distance_table_2026(announcement_sources["rules_2026"], config.request_timeout_sec)
    national_records = parse_national_tiers_2025(announcement_sources["national_2025"], config.request_timeout_sec)
    known_school_names = [team.school for team in teams]

    priority_school_notes: List[str] = []
    configured_priority = list(config.priority_schools)
    all_priority_schools = list(configured_priority)

    rmul_hosts_url = announcement_sources.get("rmul_hosts_2026")
    if rmul_hosts_url:
        try:
            rmul_hosts = parse_rmul_host_schools_2026(rmul_hosts_url, config.request_timeout_sec)
            merged = {normalize_school_name(s): s for s in all_priority_schools}
            for school in rmul_hosts:
                key = normalize_school_name(school)
                if key not in merged:
                    merged[key] = school
                    all_priority_schools.append(school)
            priority_school_notes.append(f"已纳入RMUL承办院校优先名单: {', '.join(rmul_hosts)}")
        except Exception as exc:
            priority_school_notes.append(f"RMUL承办院校解析失败，使用配置优先名单: {exc}")
    else:
        priority_school_notes.append("未配置RMUL承办院校公告链接，使用配置优先名单")

    overseas_priority = infer_overseas_priority_schools_2026(teams, distance_map)
    if overseas_priority:
        merged = {normalize_school_name(s): s for s in all_priority_schools}
        for school in overseas_priority:
            key = normalize_school_name(school)
            if key not in merged:
                merged[key] = school
                all_priority_schools.append(school)
        priority_school_notes.append(f"已纳入海外队伍优先名单: {', '.join(overseas_priority)}")
    else:
        priority_school_notes.append("未识别到海外队伍，未追加海外优先名单")

    ranking_map = load_rmu_ranking(str(ranking_csv))
    if not ranking_map:
        ranking_map = fallback_ranking_from_national(national_records)
        ranking_source_note = "积分榜来源: 未提供RMU排行榜CSV，使用去年国赛名次作为替代排序"
    else:
        ranking_source_note = "积分榜来源: 本地CSV"

    previous_snapshot: Optional[QingflowSnapshot] = load_snapshot(cache_file)

    iteration = 0
    while True:
        iteration += 1
        loop_notes = [ranking_source_note]
        loop_notes.extend(top16_notes)
        loop_notes.extend(priority_school_notes)

        try:
            snapshot = parse_qingflow_snapshot(
                config.qingflow_url,
                known_schools=known_school_names,
                timeout_sec=config.request_timeout_sec,
            )
            save_snapshot(cache_file, snapshot)
        except Exception as exc:
            cached = load_snapshot(cache_file)
            if cached is None:
                raise RuntimeError(f"青流抓取失败且没有缓存: {exc}") from exc
            snapshot = cached
            loop_notes.append(f"青流抓取失败，已回退缓存: {exc}")

        if config.manual_top16_counts:
            top16_counts = {region: int(config.manual_top16_counts.get(region, 0)) for region in REGION_ORDER}
            loop_notes.append("16强分布来源: 配置覆盖(manual_top16_counts)")
        else:
            top16_counts = infer_top16_counts_from_current_signup(snapshot, national_records)
            loop_notes.append("16强分布来源: 当前志愿中去年的16强实际报名数(实时)")
        loop_notes.append(
            "本轮16强实时计数: "
            f"南部={top16_counts['南部']}、东部={top16_counts['东部']}、北部={top16_counts['北部']}"
        )

        quota_result = compute_national_quotas(top16_counts)

        pressure = compute_pressure(snapshot, capacity=config.capacity_per_region)
        moves = predict_reallocation(
            snapshot=snapshot,
            distance_map=distance_map,
            ranking_map=ranking_map,
            priority_schools=all_priority_schools,
            capacity=config.capacity_per_region,
            expected_total=config.expected_total_teams,
        )
        highlights = build_historical_highlights(snapshot, national_records)

        loop_notes.extend(
            _build_runtime_notes(
                snapshot=snapshot,
                expected_total_teams=config.expected_total_teams,
                priority_schools=all_priority_schools,
                top16_notes=[],
            )
        )

        output = render_full_report(
            snapshot=snapshot,
            quota_result=quota_result,
            pressure=pressure,
            moves=moves,
            highlights=highlights,
            notes=loop_notes,
            previous_snapshot=previous_snapshot,
        )

        print(output, flush=True)
        previous_snapshot = snapshot

        if args.once:
            break
        if args.max_iterations > 0 and iteration >= args.max_iterations:
            break

        time.sleep(max(1, interval))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
        raise SystemExit(130)
    except Exception as exc:
        print(f"运行失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
