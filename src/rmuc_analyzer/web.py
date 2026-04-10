from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request

from rmuc_analyzer.cache import load_snapshot, save_snapshot
from rmuc_analyzer.config import AnalyzerConfig
from rmuc_analyzer.constants import REGION_DISPLAY, REGION_ORDER
from rmuc_analyzer.engine import (
    apply_reallocation_moves_to_counts,
    apply_reallocation_moves_to_region_schools,
    build_effective_region_counts,
    compute_national_quotas,
    estimate_resurrection_quotas_comprehensive,
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
    parse_rmul_host_schools_2026,
    parse_teams_2026,
)
from rmuc_analyzer.utils import normalize_school_name
from rmuc_analyzer.models import QingflowSnapshot


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
    merged = {normalize_school_name(s): s for s in priority_schools}

    rmul_url = announcement_sources.get("rmul_hosts_2026")
    if rmul_url:
        try:
            rmul_hosts = parse_rmul_host_schools_2026(rmul_url, config.request_timeout_sec)
            for school in rmul_hosts:
                key = normalize_school_name(school)
                if key not in merged:
                    merged[key] = school
                    priority_schools.append(school)
            notes.append(f"优先: 配置 priority_schools + RMUL承办院校(1903)共{len(rmul_hosts)}所")
        except Exception as exc:
            notes.append(f"优先: RMUL承办院校解析失败，已回退为仅配置优先名单({exc})")
    else:
        notes.append("优先: 未配置RMUL承办院校公告链接，已使用仅配置优先名单")

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


def _build_simulation_context(
    snapshot: QingflowSnapshot,
    runtime: AnalyzerRuntime,
) -> Dict[str, Any]:
    schools: List[Dict[str, str]] = []
    seen_keys: set[str] = set()

    for region in REGION_ORDER:
        for school in snapshot.region_schools.get(region, []):
            school_key = normalize_school_name(school)
            if school_key in seen_keys:
                continue
            seen_keys.add(school_key)
            schools.append(
                {
                    "school": school,
                    "region": region,
                    "region_display": REGION_DISPLAY[region],
                }
            )

    schools.sort(
        key=lambda item: _school_sort_key(
            item["school"],
            runtime.national_records,
            runtime.ranking_map,
        )
    )

    return {
        "regions": [
            {
                "region": region,
                "region_display": REGION_DISPLAY[region],
            }
            for region in REGION_ORDER
        ],
        "schools": schools,
        "total_schools": len(schools),
    }


def _apply_simulation_changes(
    snapshot: QingflowSnapshot,
    changes: List[Dict[str, Any]],
) -> Tuple[QingflowSnapshot, Dict[str, Any]]:
    region_schools = {
        region: list(snapshot.region_schools.get(region, []))
        for region in REGION_ORDER
    }

    school_index: Dict[str, Tuple[str, str]] = {}
    for region in REGION_ORDER:
        for school in region_schools[region]:
            school_key = normalize_school_name(school)
            if school_key in school_index:
                continue
            school_index[school_key] = (region, school)

    errors: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    final_changes: Dict[str, Dict[str, Any]] = {}

    for idx, item in enumerate(changes):
        if not isinstance(item, dict):
            errors.append({"index": idx, "reason": "改动项必须是对象"})
            continue

        school_value = item.get("school")
        to_region_value = item.get("to_region")
        school = school_value.strip() if isinstance(school_value, str) else ""
        to_region = to_region_value.strip() if isinstance(to_region_value, str) else ""

        if not school:
            errors.append({"index": idx, "reason": "school 不能为空"})
            continue
        if to_region not in REGION_ORDER:
            errors.append({"index": idx, "school": school, "reason": "to_region 非法"})
            continue

        school_key = normalize_school_name(school)
        if school_key not in school_index:
            errors.append({"index": idx, "school": school, "reason": "学校不在当前已报名列表"})
            continue

        prev = final_changes.get(school_key)
        if prev is not None:
            ignored.append(
                {
                    "index": prev["index"],
                    "school": prev["school"],
                    "reason": "同一学校重复提交，已被后续改动覆盖",
                }
            )

        canonical_school = school_index[school_key][1]
        final_changes[school_key] = {
            "index": idx,
            "school": canonical_school,
            "to_region": to_region,
        }

    if errors:
        simulation_meta = {
            "requested_count": len(changes),
            "valid_count": len(final_changes),
            "applied_count": 0,
            "ignored_count": len(ignored),
            "ignored": ignored,
            "errors": errors,
            "applied": [],
        }
        return snapshot, simulation_meta

    applied: List[Dict[str, str]] = []
    for item in sorted(final_changes.values(), key=lambda data: data["index"]):
        school = item["school"]
        to_region = item["to_region"]
        school_key = normalize_school_name(school)
        from_region, school_name = school_index[school_key]

        if from_region == to_region:
            ignored.append(
                {
                    "index": item["index"],
                    "school": school_name,
                    "reason": "目标赛区与当前赛区相同，已忽略",
                }
            )
            continue

        donor_list = region_schools[from_region]
        remove_idx: Optional[int] = None
        for idx, donor_school in enumerate(donor_list):
            if normalize_school_name(donor_school) == school_key:
                remove_idx = idx
                break
        if remove_idx is None:
            errors.append(
                {
                    "index": item["index"],
                    "school": school_name,
                    "reason": "学校在源赛区中不存在",
                }
            )
            continue

        moved_school = donor_list.pop(remove_idx)

        target_list = region_schools[to_region]
        exists_in_target = any(
            normalize_school_name(target_school) == school_key
            for target_school in target_list
        )
        if not exists_in_target:
            target_list.append(moved_school)

        school_index[school_key] = (to_region, moved_school)
        applied.append(
            {
                "school": moved_school,
                "from_region": from_region,
                "to_region": to_region,
            }
        )

    simulated_counts = {
        region: len(region_schools[region])
        for region in REGION_ORDER
    }
    simulated_snapshot = QingflowSnapshot(
        fetched_at=snapshot.fetched_at,
        source_url=snapshot.source_url,
        region_counts=simulated_counts,
        region_schools=region_schools,
        stale=snapshot.stale,
    )

    simulation_meta = {
        "requested_count": len(changes),
        "valid_count": len(final_changes),
        "applied_count": len(applied),
        "ignored_count": len(ignored),
        "ignored": ignored,
        "errors": errors,
        "applied": applied,
    }
    return simulated_snapshot, simulation_meta


def _build_payload(
    runtime: AnalyzerRuntime,
    snapshot_override: Optional[QingflowSnapshot] = None,
    runtime_notes_override: Optional[List[str]] = None,
    payload_mode: str = "baseline",
) -> Dict[str, Any]:
    if snapshot_override is None:
        snapshot, runtime_notes = _snapshot_with_cache(runtime)
    else:
        snapshot = snapshot_override
        runtime_notes = list(runtime_notes_override or [])

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map=runtime.distance_map,
        ranking_map=runtime.ranking_map,
        priority_schools=runtime.priority_schools,
        capacity=runtime.config.capacity_per_region,
        expected_total=runtime.config.expected_total_teams,
    )

    adjusted_region_schools = apply_reallocation_moves_to_region_schools(snapshot.region_schools, moves)

    if runtime.config.manual_top16_counts:
        top16_counts = {region: int(runtime.config.manual_top16_counts.get(region, 0)) for region in REGION_ORDER}
        quota_source_note = "国赛名额来源: manual_top16_counts配置"
    else:
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

    resurrection = estimate_resurrection_quotas_comprehensive(
        quota_result,
        adjusted_region_schools,
        runtime.national_records,
        runtime.ranking_map,
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

        base_rows: List[Dict[str, Any]] = []
        for school in schools:
            key = normalize_school_name(school)
            rec = runtime.national_records.get(key)
            point_rank = runtime.ranking_map.get(key)
            move = move_by_school.get(key)
            row_sort_key = _school_sort_key(school, runtime.national_records, runtime.ranking_map)

            is_moved_out = bool(move and move.from_region == region)
            reallocation_status = "调出(预测)" if is_moved_out else "-"
            reallocation_hint = f"-> {move.to_region}赛区" if is_moved_out else "-"

            base_rows.append(
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
                    "_rank_sort_key": row_sort_key,
                    # 调出学校保留在原赛区原位置展示，但不参与编号。
                    "_indexless": is_moved_out,
                }
            )

        incoming_rows: List[Dict[str, Any]] = []
        incoming_moves = sorted(
            move_in_by_region.get(region, []),
            key=lambda move: _school_sort_key(move.school, runtime.national_records, runtime.ranking_map),
        )
        for move in incoming_moves:
            key = normalize_school_name(move.school)
            rec = runtime.national_records.get(key)
            point_rank = runtime.ranking_map.get(key)
            row_sort_key = _school_sort_key(move.school, runtime.national_records, runtime.ranking_map)
            incoming_rows.append(
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
                    "_rank_sort_key": row_sort_key,
                    "_indexless": False,
                }
            )

        # 调入学校按排名插入到应在位置；调出学校维持在原有位置。
        active_rows = [row for row in base_rows if not row["_indexless"]]
        incoming_rows.sort(key=lambda row: row["_rank_sort_key"])

        merged_active_rows: List[Dict[str, Any]] = []
        active_idx = 0
        incoming_idx = 0
        while active_idx < len(active_rows) and incoming_idx < len(incoming_rows):
            if active_rows[active_idx]["_rank_sort_key"] <= incoming_rows[incoming_idx]["_rank_sort_key"]:
                merged_active_rows.append(active_rows[active_idx])
                active_idx += 1
            else:
                merged_active_rows.append(incoming_rows[incoming_idx])
                incoming_idx += 1

        if active_idx < len(active_rows):
            merged_active_rows.extend(active_rows[active_idx:])
        if incoming_idx < len(incoming_rows):
            merged_active_rows.extend(incoming_rows[incoming_idx:])

        school_rows: List[Dict[str, Any]] = []
        merged_idx = 0
        for row in base_rows:
            if row["_indexless"]:
                school_rows.append(row)
                continue

            if merged_idx < len(merged_active_rows):
                school_rows.append(merged_active_rows[merged_idx])
                merged_idx += 1

        if merged_idx < len(merged_active_rows):
            school_rows.extend(merged_active_rows[merged_idx:])

        # 网页固定展示32个可编号席位，便于观察缺口。
        target_slots = runtime.config.capacity_per_region
        numbered_rows = sum(1 for row in school_rows if not row["_indexless"])
        while numbered_rows < target_slots:
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
                    "_rank_sort_key": (1, 10**9, 10**9, "空位"),
                    "_indexless": False,
                }
            )
            numbered_rows += 1

        display_idx = 1
        for row in school_rows:
            if row["_indexless"]:
                row["sort_index"] = ""
            else:
                row["sort_index"] = display_idx
                display_idx += 1
            row.pop("_rank_sort_key", None)
            row.pop("_indexless", None)

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
        "mode": payload_mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_submitted": total_submitted,
        "expected_total": runtime.config.expected_total_teams,
        "regions": regions_payload,
        "notes": notes,
        "simulation_context": _build_simulation_context(snapshot, runtime),
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

    @app.post("/api/simulate")
    def api_simulate():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "请求体必须是 JSON 对象"}), 400

        changes = payload.get("changes")
        if not isinstance(changes, list):
            return jsonify({"error": "changes 字段必须是数组"}), 400

        baseline_snapshot, runtime_notes = _snapshot_with_cache(runtime)
        baseline = _build_payload(
            runtime,
            snapshot_override=baseline_snapshot,
            runtime_notes_override=runtime_notes,
            payload_mode="baseline",
        )

        simulated_snapshot, simulation_meta = _apply_simulation_changes(
            baseline_snapshot,
            changes,
        )
        if simulation_meta["errors"]:
            return (
                jsonify(
                    {
                        "error": "模拟参数校验失败",
                        "simulation_meta": simulation_meta,
                    }
                ),
                422,
            )

        simulated_notes = list(runtime_notes)
        simulated_notes.append(f"模拟模式: 已应用{simulation_meta['applied_count']}条赛区改动")
        if simulation_meta["ignored_count"]:
            simulated_notes.append(f"模拟模式: 已忽略{simulation_meta['ignored_count']}条无效或重复改动")

        simulated = _build_payload(
            runtime,
            snapshot_override=simulated_snapshot,
            runtime_notes_override=simulated_notes,
            payload_mode="simulated",
        )

        return jsonify(
            {
                "baseline": baseline,
                "simulated": simulated,
                "simulation_meta": simulation_meta,
            }
        )

    return app


def main() -> int:
    app = create_app()
    app.run(host="0.0.0.0", port=8000, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
