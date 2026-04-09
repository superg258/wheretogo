from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template

from rmuc_analyzer.cache import load_snapshot, save_snapshot
from rmuc_analyzer.config import AnalyzerConfig
from rmuc_analyzer.constants import REGION_DISPLAY, REGION_ORDER
from rmuc_analyzer.engine import (
    apply_reallocation_moves_to_counts,
    apply_reallocation_moves_to_region_schools,
    build_effective_region_counts,
    compute_national_quotas,
    estimate_resurrection_quotas,
    fallback_ranking_from_national,
    infer_top16_counts_from_region_schools,
    load_rmu_ranking,
    predict_reallocation,
)
from rmuc_analyzer.sources.qingflow import parse_qingflow_snapshot
from rmuc_analyzer.sources.robomaster import (
    localize_announcement_sources,
    parse_distance_table_2026,
    parse_national_tiers_2025,
    parse_rmu_ranking_2025,
    parse_teams_2026,
)
from rmuc_analyzer.utils import normalize_school_name


@dataclass
class AnalyzerRuntime:
    root_dir: Path
    config: AnalyzerConfig
    cache_file: Path
    teams: List[Any]
    known_school_names: List[str]
    distance_map: Dict[str, Any]
    national_records: Dict[str, Any]
    ranking_map: Dict[str, int]
    priority_schools: List[str]
    static_notes: List[str]


def _write_ranking_csv(csv_path: Path, rows: List[Dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["school", "rank", "score"])
        writer.writeheader()
        writer.writerows(rows)


def _build_runtime(root_dir: Path, config: AnalyzerConfig) -> AnalyzerRuntime:
    announcement_sources = localize_announcement_sources(
        config.announcement_urls,
        root_dir=root_dir,
        timeout_sec=config.request_timeout_sec,
        local_dir=config.announcement_local_dir,
        local_only=config.announcement_local_only,
    )

    teams = parse_teams_2026(announcement_sources["teams_2026"], config.request_timeout_sec)
    distance_map = parse_distance_table_2026(announcement_sources["rules_2026"], config.request_timeout_sec)
    national_records = parse_national_tiers_2025(announcement_sources["national_2025"], config.request_timeout_sec)

    ranking_csv = config.resolve_path(root_dir, config.rmu_ranking_csv)
    ranking_map = load_rmu_ranking(str(ranking_csv))
    notes: List[str] = []

    ranking_url = announcement_sources.get("ranking_2025")
    if ranking_url:
        try:
            ranking_rows = parse_rmu_ranking_2025(ranking_url, config.request_timeout_sec)
            _write_ranking_csv(ranking_csv, ranking_rows)
            ranking_map = load_rmu_ranking(str(ranking_csv))
            notes.append(f"积分榜来源: 已从1884公告抓取并写入本地 {ranking_csv}")
        except Exception as exc:
            notes.append(f"积分榜抓取失败，继续使用本地CSV: {exc}")

    if not ranking_map:
        ranking_map = fallback_ranking_from_national(national_records)
        notes.append("积分榜来源: 未提供RMU积分榜CSV，已使用去年国赛顺位兜底")

    priority_schools = list(config.priority_schools)
    notes.append("优先: 仅使用配置 priority_schools")

    return AnalyzerRuntime(
        root_dir=root_dir,
        config=config,
        cache_file=config.resolve_path(root_dir, config.cache_file),
        teams=teams,
        known_school_names=[team.school for team in teams],
        distance_map=distance_map,
        national_records=national_records,
        ranking_map=ranking_map,
        priority_schools=priority_schools,
        static_notes=notes,
    )


def _snapshot_with_cache(runtime: AnalyzerRuntime):
    notes: List[str] = []
    try:
        snapshot = parse_qingflow_snapshot(
            runtime.config.qingflow_url,
            known_schools=runtime.known_school_names,
            timeout_sec=runtime.config.request_timeout_sec,
        )
        save_snapshot(runtime.cache_file, snapshot)
    except Exception as exc:
        cached = load_snapshot(runtime.cache_file)
        if cached is None:
            raise RuntimeError(f"青流抓取失败且无缓存: {exc}") from exc
        snapshot = cached
        notes.append(f"青流抓取失败，已回退缓存: {exc}")
    return snapshot, notes


def _school_sort_key(school: str, national_records: Dict[str, Any], ranking_map: Dict[str, int]):
    key = normalize_school_name(school)
    rec = national_records.get(key)
    nat_missing = 1 if rec is None else 0
    nat_rank = rec.rank_order if rec is not None else 10**9
    point_rank = ranking_map.get(key, 10**9)
    return (nat_missing, nat_rank, point_rank, school)


def _format_performance(rec: Optional[Any]) -> str:
    if rec is None:
        return "-"
    if rec.in_top32 and rec.tier != "-":
        return rec.tier
    if rec.is_resurrection_team:
        return "复活赛"
    return rec.award_level or "-"


def _build_payload(runtime: AnalyzerRuntime) -> Dict[str, Any]:
    snapshot, runtime_notes = _snapshot_with_cache(runtime)

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map=runtime.distance_map,
        ranking_map=runtime.ranking_map,
        priority_schools=runtime.priority_schools,
        capacity=runtime.config.capacity_per_region,
        expected_total=runtime.config.expected_total_teams,
    )

    if runtime.config.manual_top16_counts:
        top16_counts = {region: int(runtime.config.manual_top16_counts.get(region, 0)) for region in REGION_ORDER}
        quota_source_note = "国赛名额来源: manual_top16_counts配置"
    else:
        adjusted_region_schools = apply_reallocation_moves_to_region_schools(snapshot.region_schools, moves)
        top16_counts = infer_top16_counts_from_region_schools(adjusted_region_schools, runtime.national_records)
        if moves:
            quota_source_note = "国赛名额来源: 预测调剂后去年的16强实际报名数（实时）"
        else:
            quota_source_note = "国赛名额来源: 当前志愿中去年的16强实际报名数（实时）"

    quota_result = compute_national_quotas(top16_counts)

    effective_counts = build_effective_region_counts(
        snapshot.region_counts,
        expected_total=runtime.config.expected_total_teams,
        minimum_per_region=8,
    )
    effective_counts = apply_reallocation_moves_to_counts(effective_counts, moves)

    resurrection = estimate_resurrection_quotas(
        quota_result,
        effective_counts,
        resurrection_total=16,
        min_total_advancement=8,
        max_total_advancement=16,
    )

    move_by_school = {normalize_school_name(m.school): m for m in moves}
    move_in_by_region: Dict[str, List[Any]] = {region: [] for region in REGION_ORDER}
    for move in moves:
        if move.to_region in move_in_by_region:
            move_in_by_region[move.to_region].append(move)

    priority_key_set = {normalize_school_name(s) for s in runtime.priority_schools}

    regions_payload: List[Dict[str, Any]] = []
    for region in REGION_ORDER:
        schools = sorted(
            list(snapshot.region_schools.get(region, [])),
            key=lambda s: _school_sort_key(s, runtime.national_records, runtime.ranking_map),
        )

        school_rows: List[Dict[str, Any]] = []
        for school in schools:
            key = normalize_school_name(school)
            rec = runtime.national_records.get(key)
            point_rank = runtime.ranking_map.get(key)
            move = move_by_school.get(key)

            is_moved_out = bool(move and move.from_region == region)
            reallocation_status = "调出(预测)" if is_moved_out else "-"
            reallocation_hint = f"-> {move.to_region}赛区" if is_moved_out else "-"

            school_rows.append(
                {
                    "sort_index": 0,
                    "school": school,
                    "national_rank": rec.rank_order if rec else None,
                    "performance": _format_performance(rec),
                    "point_rank": point_rank,
                    "priority": key in priority_key_set,
                    "reallocation_status": reallocation_status,
                    "reallocation_hint": reallocation_hint,
                    "ghost": is_moved_out,
                    "empty": False,
                }
            )

        incoming_moves = sorted(
            move_in_by_region.get(region, []),
            key=lambda move: _school_sort_key(move.school, runtime.national_records, runtime.ranking_map),
        )
        for move in incoming_moves:
            key = normalize_school_name(move.school)
            rec = runtime.national_records.get(key)
            point_rank = runtime.ranking_map.get(key)
            school_rows.append(
                {
                    "sort_index": 0,
                    "school": move.school,
                    "national_rank": rec.rank_order if rec else None,
                    "performance": _format_performance(rec),
                    "point_rank": point_rank,
                    "priority": key in priority_key_set,
                    "reallocation_status": "调入(预测)",
                    "reallocation_hint": f"来自{move.from_region}赛区",
                    "ghost": True,
                    "empty": False,
                }
            )

        # 网页固定展示32席位，便于观察缺口。
        target_slots = runtime.config.capacity_per_region
        for _ in range(len(school_rows) + 1, target_slots + 1):
            school_rows.append(
                {
                    "sort_index": 0,
                    "school": "空位",
                    "national_rank": None,
                    "performance": "-",
                    "point_rank": None,
                    "priority": False,
                    "reallocation_status": "-",
                    "reallocation_hint": "-",
                    "ghost": False,
                    "empty": True,
                }
            )

        for idx, row in enumerate(school_rows, start=1):
            row["sort_index"] = idx

        national_quota = quota_result.items[region].total_quota
        resurrection_quota = resurrection.get(region, 0)
        volunteers = snapshot.region_counts.get(region, 0)
        projected_volunteers = effective_counts.get(region, volunteers)

        regions_payload.append(
            {
                "region": region,
                "region_display": REGION_DISPLAY[region],
                "top16_signed_count": top16_counts[region],
                "national_quota": national_quota,
                "resurrection_quota": resurrection_quota,
                "volunteers": volunteers,
                "projected_volunteers": projected_volunteers,
                "capacity": runtime.config.capacity_per_region,
                "schools": school_rows,
            }
        )

    total_submitted = sum(snapshot.region_counts.get(region, 0) for region in REGION_ORDER)
    notes = list(runtime.static_notes)
    notes.extend(runtime_notes)
    notes.append(
        "本轮16强实时计数: "
        f"南部={top16_counts['南部']}、东部={top16_counts['东部']}、北部={top16_counts['北部']}"
    )
    notes.append(quota_source_note)
    if moves:
        notes.append("名额估算口径: 国赛与复活赛均按预测调剂后分布计算")
    else:
        notes.append("名额估算口径: 当前无调剂，国赛与复活赛按现有报名分布计算")
    if total_submitted < runtime.config.expected_total_teams:
        notes.append("复活赛估算口径: 报名未满时先按赛区补足8，再叠加预测调配后进行分配")
    elif moves:
        notes.append("复活赛估算口径: 已按预测调配后的赛区人数进行分配")
    notes.append("复活赛名额为模拟估算，官方最终分配以组委会公告为准")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_submitted": total_submitted,
        "expected_total": runtime.config.expected_total_teams,
        "regions": regions_payload,
        "notes": notes,
    }


def create_app(config_path: Optional[str] = None) -> Flask:
    root_dir = Path(__file__).resolve().parents[2]
    config = AnalyzerConfig.load(config_path, root_dir)
    runtime = _build_runtime(root_dir, config)

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
    )

    @app.get("/")
    def index():
        payload = _build_payload(runtime)
        return render_template("index.html", initial_payload=payload)

    @app.get("/api/analysis")
    def api_analysis():
        return jsonify(_build_payload(runtime))

    return app


def main() -> int:
    app = create_app()
    app.run(host="0.0.0.0", port=8000, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
