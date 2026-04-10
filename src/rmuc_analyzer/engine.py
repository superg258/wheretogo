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


def infer_top16_counts_from_region_schools(
    region_schools: Dict[str, List[str]],
    national_records: Dict[str, NationalTierRecord],
) -> Dict[str, int]:
    counts = {region: 0 for region in REGION_ORDER}
    seen_top16_keys: Set[str] = set()

    for region in REGION_ORDER:
        for school in region_schools.get(region, []):
            key = normalize_school_name(school)
            if key in seen_top16_keys:
                continue

            rec = national_records.get(key)
            if rec is None or rec.tier not in TOP16_TIERS:
                continue

            counts[region] += 1
            seen_top16_keys.add(key)

    return counts


def infer_top16_counts_from_current_signup(
    snapshot: QingflowSnapshot,
    national_records: Dict[str, NationalTierRecord],
) -> Dict[str, int]:
    return infer_top16_counts_from_region_schools(snapshot.region_schools, national_records)


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


def estimate_resurrection_quotas_comprehensive(
    national_quota_result: QuotaResult,
    adjusted_region_schools: Dict[str, List[str]],
    national_records: Dict[str, NationalTierRecord],
    ranking_map: Dict[str, int],
    resurrection_total: int = 16,
    min_total_advancement: int = 8,
    max_total_advancement: int = 16,
    national_base_quota: int = 8,
    weight_history: float = 0.40,
    weight_rmu: float = 0.35,
    weight_national_excess: float = 0.65,
) -> Dict[str, int]:
    """
    基于官方规则综合考虑分配复活赛名额：
    1. 全国赛晋级名额（提高权重，且与复活赛名额反向关联）
    2. 上一赛季全国赛名单（仅统计全国赛队伍数量，不区分名次等级）
    3. RMU 积分榜（弱势赛区适度补偿）

    分配原则：
    - 复活赛按"补偿需求"分配（需求越高，复活赛越多）
    - 全国赛名额越多，补偿需求越低（反向关系）
    - 在满足总量与上限约束基础上，保证全国赛名额较少赛区的总晋级数不高于名额更多赛区
    - 保证每赛区 [国赛+复活赛] ∈ [8, 16]
    """
    regions = list(REGION_ORDER)

    national_map = {
        region: national_quota_result.items[region].total_quota
        for region in regions
    }

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

    # 预构建学校->战绩映射，避免重复遍历 national_records。
    tier_by_school: Dict[str, str] = {
        normalize_school_name(rec.school): rec.tier
        for rec in national_records.values()
    }

    # 1) 上赛季全国赛名单数量（仅计全国赛队伍，不区分等级）。
    national_list_count: Dict[str, int] = {}
    for region in regions:
        count = 0
        for school in adjusted_region_schools.get(region, []):
            tier = tier_by_school.get(normalize_school_name(school), "")
            if tier in ("冠军", "亚军", "季军", "殿军", "八强", "十六强", "三十二强"):
                count += 1
        national_list_count[region] = count

    # 2) RMU 综合指标：平均排名越靠前(数值越小)代表赛区更强。
    avg_rank_map: Dict[str, float] = {}
    for region in regions:
        ranks: List[int] = []
        for school in adjusted_region_schools.get(region, []):
            rank = ranking_map.get(normalize_school_name(school))
            if rank is not None:
                ranks.append(rank)
        avg_rank_map[region] = (sum(ranks) / len(ranks)) if ranks else 999.0

    def _normalize(value: float, min_v: float, max_v: float, neutral: float = 0.5) -> float:
        if max_v <= min_v:
            return neutral
        return (value - min_v) / (max_v - min_v)

    # 国赛名额负向项采用“超出基础名额”的部分，放大高名额赛区的副作用。
    national_excess_map = {
        r: max(0, national_map[r] - int(national_base_quota))
        for r in regions
    }

    quota_values = [national_excess_map[r] for r in regions]
    list_values = [national_list_count[r] for r in regions]
    rank_values = [avg_rank_map[r] for r in regions]

    min_q, max_q = min(quota_values), max(quota_values)
    min_l, max_l = min(list_values), max(list_values)
    min_r, max_r = min(rank_values), max(rank_values)

    # 3) 构造"一反两正"综合得分：
    # - 反向因素：全国赛名额（越多，复活赛倾向越少）
    # - 正向因素：上一赛季全国赛名单数量（越多，复活赛倾向越多）
    # - 正向因素：RMU综合强度（越强，复活赛倾向越多）
    # 为避免波动过大，以 1.0 为基线，仅对归一化偏离 0.5 的部分做加减。
    comprehensive_scores: Dict[str, float] = {}

    # 可配置权重：一反两正
    # H: 上赛季全国赛名单数量（正向）
    # R: RMU强度（正向）
    # N: 超出基础名额(national_base_quota)的全国赛名额（反向）
    w_h = float(weight_history)
    w_r = float(weight_rmu)
    w_n = float(weight_national_excess)

    for region in regions:
        n_norm = _normalize(float(national_excess_map[region]), float(min_q), float(max_q))
        h_norm = _normalize(float(national_list_count[region]), float(min_l), float(max_l))
        r_weak_norm = _normalize(avg_rank_map[region], min_r, max_r)
        r_strength_norm = 1.0 - r_weak_norm

        score = 1.0 + (
            w_h * (h_norm - 0.5)
            + w_r * (r_strength_norm - 0.5)
            - w_n * (n_norm - 0.5)
        )

        comprehensive_scores[region] = max(0.05, score)

    total_score = sum(comprehensive_scores.values())
    if total_score <= 0:
        comprehensive_scores = {region: 1.0 for region in regions}
        total_score = float(len(regions))

    exact_add = {
        region: (comprehensive_scores[region] / total_score) * remaining
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
                key=lambda r: (-remainders[r], -comprehensive_scores[r], REGION_ORDER.index(r)),
            )[0]
            current[target] += 1
            leftover -= 1

    # 4) 约束修正：全国赛名额较少的赛区，其总晋级不得高于名额更多赛区。
    # 即若 q_a < q_b，则 (q_a + r_a) <= (q_b + r_b)。
    def _totals() -> Dict[str, int]:
        return {region: national_map[region] + current[region] for region in regions}

    totals = _totals()
    changed = True
    guard = 0
    while changed and guard < 20:
        guard += 1
        changed = False
        for low in regions:
            for high in regions:
                if national_map[low] >= national_map[high]:
                    continue
                if totals[low] <= totals[high]:
                    continue
                if current[low] <= base_resurrection[low]:
                    continue

                recipients = [
                    r for r in regions
                    if national_map[r] > national_map[low]
                    and current[r] < cap_resurrection[r]
                ]
                if not recipients:
                    continue

                target = sorted(
                    recipients,
                    key=lambda r: (totals[r], -national_map[r], REGION_ORDER.index(r)),
                )[0]

                current[low] -= 1
                current[target] += 1
                totals = _totals()
                changed = True

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


def apply_reallocation_moves_to_region_schools(
    region_schools: Dict[str, List[str]],
    moves: Iterable[ReallocationMove],
) -> Dict[str, List[str]]:
    updated = {
        region: list(region_schools.get(region, []))
        for region in REGION_ORDER
    }

    for move in moves:
        if move.from_region not in updated or move.to_region not in updated:
            continue

        school_key = normalize_school_name(move.school)
        school_name = move.school

        donor_list = updated[move.from_region]
        remove_idx: Optional[int] = None
        for idx, school in enumerate(donor_list):
            if normalize_school_name(school) == school_key:
                remove_idx = idx
                break

        if remove_idx is not None:
            school_name = donor_list.pop(remove_idx)

        target_list = updated[move.to_region]
        exists_in_target = any(
            normalize_school_name(existing) == school_key
            for existing in target_list
        )
        if not exists_in_target:
            target_list.append(school_name)

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
    # 2) A调剂结束后，重新比较B/C剩余志愿数，再在二者之间继续调剂。
    ordered_regions = sorted(REGION_ORDER, key=lambda region: (counts[region], REGION_ORDER.index(region)))
    region_a, region_b, region_c = ordered_regions

    def _run_phase(target: str, donor_regions: List[str]) -> None:
        nonlocal remaining_required_moves

        deficit_raw = capacity - counts[target]
        deficit = deficit_raw

        # 报名未满时，仅输出“当前已确定必须调剂”的队伍数量：
        # 即把超容量赛区的溢出人数分配出去，不提前补齐全部缺口到32。
        if submitted < expected_total:
            deficit = min(deficit_raw, remaining_required_moves)

        if deficit <= 0:
            return

        candidates: List[Tuple[int, int, str, str, Optional[int]]] = []
        for donor in donor_regions:
            if submitted < expected_total and counts[donor] <= capacity:
                continue
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
        selected_count = 0

        for distance_km, _rank_sort, donor, school, rank_value in candidates:
            if selected_count >= deficit:
                break
            # 报名未满时仅允许从当前仍超容量的赛区继续调出，避免制造新的缺口。
            if submitted < expected_total and counts[donor] <= capacity:
                continue
            if school not in assignments[donor]:
                continue
            assignments[donor].remove(school)
            assignments[target].append(school)
            counts[donor] -= 1
            counts[target] += 1
            moved_keys.add(normalize_school_name(school))
            selected_count += 1

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

    _run_phase(region_a, [region_b, region_c])

    if submitted < expected_total and remaining_required_moves <= 0:
        return moves

    remaining_regions = [region_b, region_c]
    remaining_ordered = sorted(
        remaining_regions,
        key=lambda region: (counts[region], REGION_ORDER.index(region)),
    )
    _run_phase(remaining_ordered[0], [remaining_ordered[1]])

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
