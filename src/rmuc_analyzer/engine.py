from __future__ import annotations

import csv
from typing import Dict, Iterable, List, Optional, Set, Tuple

from rmuc_analyzer.constants import REGION_DISTANCE_FIELD, REGION_ORDER, TOP16_TIERS
from rmuc_analyzer.models import (
    DistanceRecord,
    NationalTierRecord,
    PressureItem,
    QingflowSnapshot,
    QuotaItem,
    QuotaResult,
    ReallocationMove,
)
from rmuc_analyzer.utils import normalize_school_name


def load_rmu_ranking(csv_path: str) -> Dict[str, int]:
    ranking: Dict[str, int] = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                school = normalize_school_name(row.get("school", ""))
                rank_raw = row.get("rank", "").strip()
                if not school or not rank_raw.isdigit():
                    continue
                ranking[school] = int(rank_raw)
    except FileNotFoundError:
        return {}

    return ranking


def fallback_ranking_from_national(national_records: Dict[str, NationalTierRecord]) -> Dict[str, int]:
    return {school_key: rec.rank_order for school_key, rec in national_records.items()}


def infer_top16_counts(
    national_records: Dict[str, NationalTierRecord],
    distance_map: Dict[str, DistanceRecord],
) -> Tuple[Dict[str, int], List[str]]:
    counts = {region: 0 for region in REGION_ORDER}
    missing: List[str] = []

    for school_key, record in national_records.items():
        if record.tier not in TOP16_TIERS:
            continue

        distance = distance_map.get(school_key)
        if distance is None:
            missing.append(record.school)
            continue

        region_distances = {
            "南部": distance.to_changsha,
            "东部": distance.to_jinan,
            "北部": distance.to_shenyang,
        }
        nearest_region = min(region_distances, key=region_distances.get)
        counts[nearest_region] += 1

    return counts, missing


def infer_top16_counts_from_regional_signup(
    national_records: Dict[str, NationalTierRecord],
    school_region_map: Dict[str, str],
) -> Tuple[Dict[str, int], List[str]]:
    counts = {region: 0 for region in REGION_ORDER}
    missing: List[str] = []

    for school_key, record in national_records.items():
        if record.tier not in TOP16_TIERS:
            continue

        region = school_region_map.get(school_key)
        if region not in counts:
            missing.append(record.school)
            continue

        counts[region] += 1

    return counts, missing


def infer_top16_counts_from_current_signup(
    snapshot: QingflowSnapshot,
    national_records: Dict[str, NationalTierRecord],
) -> Dict[str, int]:
    counts = {region: 0 for region in REGION_ORDER}
    seen_top16_keys: Set[str] = set()

    for region in REGION_ORDER:
        for school in snapshot.region_schools.get(region, []):
            key = normalize_school_name(school)
            if key in seen_top16_keys:
                continue

            rec = national_records.get(key)
            if rec is None or rec.tier not in TOP16_TIERS:
                continue

            counts[region] += 1
            seen_top16_keys.add(key)

    return counts


def compute_national_quotas(
    top16_counts: Dict[str, int],
    base_quota: int = 8,
    floating_quota_total: int = 4,
) -> QuotaResult:
    eligible_regions = [
        region for region in REGION_ORDER if top16_counts.get(region, 0) > 4
    ]

    floating_map = {region: 0 for region in REGION_ORDER}
    remainder_map = {region: 0.0 for region in REGION_ORDER}
    trace: List[str] = []

    if eligible_regions:
        denominator = sum(top16_counts[region] for region in eligible_regions)
        allocated = 0

        for region in eligible_regions:
            exact_value = (top16_counts[region] / denominator) * floating_quota_total
            floor_value = int(exact_value)
            floating_map[region] = floor_value
            remainder_map[region] = exact_value - floor_value
            allocated += floor_value

        remaining = floating_quota_total - allocated
        if remaining > 0:
            sorted_regions = sorted(
                eligible_regions,
                key=lambda r: (-remainder_map[r], REGION_ORDER.index(r)),
            )
            for idx in range(remaining):
                target = sorted_regions[idx]
                floating_map[target] += 1
                trace.append(
                    f"余数补位: {target} +1 (余数={remainder_map[target]:.4f})"
                )

    items = {
        region: QuotaItem(
            region=region,
            base_quota=base_quota,
            floating_quota=floating_map[region],
            total_quota=base_quota + floating_map[region],
            top16_count=top16_counts.get(region, 0),
            eligible=region in eligible_regions,
            remainder=remainder_map[region],
        )
        for region in REGION_ORDER
    }

    return QuotaResult(items=items, tie_break_trace=trace)


def estimate_resurrection_quotas(
    national_quota_result: QuotaResult,
    region_counts: Dict[str, int],
    resurrection_total: int = 16,
    min_total_advancement: int = 8,
    max_total_advancement: int = 16,
) -> Dict[str, int]:
    """
    复活赛名额公开规则未给出精确权重，本函数用于网页展示的"模拟分配"。
    分配原则：
    1) 总数固定 resurrection_total。
    2) 单赛区（国赛+复活）不超过 max_total_advancement。
    3) 在可行空间内按当前志愿数占比做最大余数法分配。
    """
    regions = list(REGION_ORDER)
    national_map = {
        region: national_quota_result.items[region].total_quota
        for region in regions
    }

    # 最低总晋级约束在当前规则下通常由国赛基础名额已满足；保留以增强鲁棒性。
    base_resurrection = {
        region: max(0, min_total_advancement - national_map[region])
        for region in regions
    }
    cap_resurrection = {
        region: max(0, max_total_advancement - national_map[region])
        for region in regions
    }

    current = {
        region: min(base_resurrection[region], cap_resurrection[region])
        for region in regions
    }
    allocated = sum(current.values())
    remaining = resurrection_total - allocated

    if remaining <= 0:
        return current

    weights = {region: max(0, region_counts.get(region, 0)) for region in regions}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        weights = {region: 1 for region in regions}
        total_weight = len(regions)

    exact_add = {
        region: (weights[region] / total_weight) * remaining
        for region in regions
    }

    add_floor = {}
    used = 0
    for region in regions:
        room = cap_resurrection[region] - current[region]
        floor_value = int(exact_add[region])
        value = min(max(0, floor_value), max(0, room))
        add_floor[region] = value
        used += value

    for region in regions:
        current[region] += add_floor[region]

    leftover = remaining - used
    if leftover > 0:
        remainders = {
            region: exact_add[region] - int(exact_add[region])
            for region in regions
        }

        while leftover > 0:
            candidates = [
                region
                for region in regions
                if current[region] < cap_resurrection[region]
            ]
            if not candidates:
                break

            target = sorted(
                candidates,
                key=lambda r: (-remainders[r], -weights[r], REGION_ORDER.index(r)),
            )[0]
            current[target] += 1
            leftover -= 1

    return current


def build_effective_region_counts(
    region_counts: Dict[str, int],
    expected_total: int,
    minimum_per_region: int = 8,
) -> Dict[str, int]:
    counts = {region: max(0, int(region_counts.get(region, 0))) for region in REGION_ORDER}

    # 报名未满时，为避免早期分布过度偏斜，先按规则下限补到8再做后续调配估算。
    if sum(counts.values()) < expected_total:
        for region in REGION_ORDER:
            counts[region] = max(counts[region], minimum_per_region)

    return counts


def apply_reallocation_moves_to_counts(
    region_counts: Dict[str, int],
    moves: Iterable[ReallocationMove],
) -> Dict[str, int]:
    updated = {region: max(0, int(region_counts.get(region, 0))) for region in REGION_ORDER}

    for move in moves:
        if move.from_region not in updated or move.to_region not in updated:
            continue
        if updated[move.from_region] > 0:
            updated[move.from_region] -= 1
        updated[move.to_region] += 1

    return updated


def compute_pressure(snapshot: QingflowSnapshot, capacity: int = 32) -> Dict[str, PressureItem]:
    pressure: Dict[str, PressureItem] = {}
    for region in REGION_ORDER:
        volunteers = snapshot.region_counts.get(region, 0)
        deficit = max(0, capacity - volunteers)
        surplus = max(0, volunteers - capacity)
        pressure[region] = PressureItem(
            region=region,
            volunteers=volunteers,
            capacity=capacity,
            deficit=deficit,
            surplus=surplus,
        )
    return pressure


def _get_school_distance(
    school: str,
    target_region: str,
    distance_map: Dict[str, DistanceRecord],
) -> Optional[int]:
    key = normalize_school_name(school)
    distance = distance_map.get(key)
    if distance is None:
        return None

    field = REGION_DISTANCE_FIELD[target_region]
    return getattr(distance, field)


def _confidence_label(
    submitted: int,
    expected_total: int,
    has_ranking: bool,
) -> str:
    if submitted >= expected_total and has_ranking:
        return "高"
    if submitted >= int(expected_total * 0.7):
        return "中"
    return "低"


def predict_reallocation(
    snapshot: QingflowSnapshot,
    distance_map: Dict[str, DistanceRecord],
    ranking_map: Dict[str, int],
    priority_schools: Iterable[str],
    capacity: int = 32,
    expected_total: int = 96,
) -> List[ReallocationMove]:
    counts = {region: snapshot.region_counts.get(region, 0) for region in REGION_ORDER}

    # 基于青流可见学校名单做预测；若页面结构变化导致名单不完整，预测会偏保守。
    assignments = {
        region: list(snapshot.region_schools.get(region, []))
        for region in REGION_ORDER
    }

    priority_keys: Set[str] = {normalize_school_name(s) for s in priority_schools}
    moved_keys: Set[str] = set()
    submitted = sum(counts.values())
    has_ranking = bool(ranking_map)
    moves: List[ReallocationMove] = []
    total_surplus = sum(max(0, counts[region] - capacity) for region in REGION_ORDER)
    remaining_required_moves = total_surplus

    # 仅当出现超容量赛区时，才存在明确的“被调剂”对象。
    if total_surplus <= 0:
        return moves

    # 规则顺序：
    # 1) 先按志愿数从少到多确定 A<B<C，先补A（从B/C调剂）。
    # 2) A调剂结束后，仅在B/C之间继续调剂，直至两者满足容量。
    ordered_regions = sorted(REGION_ORDER, key=lambda region: (counts[region], REGION_ORDER.index(region)))
    region_a, region_b, region_c = ordered_regions

    adjustment_phases = [
        (region_a, [region_b, region_c]),
    ]

    remaining_regions = [region_b, region_c]
    remaining_ordered = sorted(
        remaining_regions,
        key=lambda region: (counts[region], REGION_ORDER.index(region)),
    )
    adjustment_phases.append((remaining_ordered[0], [remaining_ordered[1]]))

    for target, donor_regions in adjustment_phases:
        deficit_raw = capacity - counts[target]
        deficit = deficit_raw

        # 报名未满时，仅输出“当前已确定必须调剂”的队伍数量：
        # 即把超容量赛区的溢出人数分配出去，不提前补齐全部缺口到32。
        if submitted < expected_total:
            deficit = min(deficit_raw, remaining_required_moves)

        if deficit <= 0:
            continue

        candidates: List[Tuple[int, int, str, str, Optional[int]]] = []
        for donor in donor_regions:
            for school in assignments[donor]:
                school_key = normalize_school_name(school)
                if school_key in moved_keys:
                    continue
                if school_key in priority_keys:
                    continue

                distance_km = _get_school_distance(school, target, distance_map)
                if distance_km is None:
                    continue

                rank_value = ranking_map.get(school_key)
                # 同城时按积分榜排名靠后者优先调剂（数值越大越靠后）。
                rank_for_sort = rank_value if rank_value is not None else -1
                candidates.append((distance_km, -rank_for_sort, donor, school, rank_value))

        candidates.sort(key=lambda item: (item[0], item[1], REGION_ORDER.index(item[2]), item[3]))
        selected = candidates[:deficit]

        for distance_km, _rank_sort, donor, school, rank_value in selected:
            if school not in assignments[donor]:
                continue
            assignments[donor].remove(school)
            assignments[target].append(school)
            counts[donor] -= 1
            counts[target] += 1
            moved_keys.add(normalize_school_name(school))

            moves.append(
                ReallocationMove(
                    school=school,
                    from_region=donor,
                    to_region=target,
                    distance_km=distance_km,
                    ranking_value=rank_value,
                    confidence=_confidence_label(submitted, expected_total, has_ranking),
                    reason="志愿优先录取+地理就近调剂",
                )
            )
            if remaining_required_moves > 0:
                remaining_required_moves -= 1

        if submitted < expected_total and remaining_required_moves <= 0:
            break

    return moves


def build_historical_highlights(
    snapshot: QingflowSnapshot,
    national_records: Dict[str, NationalTierRecord],
) -> Dict[str, str]:
    highlights: Dict[str, str] = {}
    for region in REGION_ORDER:
        for school in snapshot.region_schools.get(region, []):
            key = normalize_school_name(school)
            record = national_records.get(key)
            if record is None:
                continue

            if record.in_top32 and record.tier != "-":
                label = record.tier
            elif record.is_resurrection_team:
                label = "复活赛"
            else:
                label = record.award_level or "-"

            highlights[school] = label
    return highlights
