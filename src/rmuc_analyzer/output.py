from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional

from rmuc_analyzer.constants import REGION_ORDER
from rmuc_analyzer.models import PressureItem, QingflowSnapshot, QuotaResult, ReallocationMove


def _line() -> str:
    return "-" * 72


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def render_diff(previous: Optional[QingflowSnapshot], current: QingflowSnapshot) -> List[str]:
    if previous is None:
        return ["首次快照，无历史差分。"]

    lines: List[str] = []
    for region in REGION_ORDER:
        old_value = previous.region_counts.get(region, 0)
        new_value = current.region_counts.get(region, 0)
        if old_value == new_value:
            continue
        sign = "+" if (new_value - old_value) >= 0 else ""
        lines.append(f"{region}: {old_value} -> {new_value} ({sign}{new_value - old_value})")

    if not lines:
        return ["赛区人数无变化。"]
    return lines


def render_quota_table(quota_result: QuotaResult) -> List[str]:
    lines = [
        "赛区  | 去年16强计数 | 浮动名额 | 预计国赛名额",
        "----- | ------------ | -------- | ------------",
    ]
    for region in REGION_ORDER:
        item = quota_result.items[region]
        lines.append(
            f"{region}  | {item.top16_count:>12} | {item.floating_quota:>8} | {item.total_quota:>12}"
        )

    total_quota = sum(item.total_quota for item in quota_result.items.values())
    lines.append(f"合计预计国赛名额: {total_quota}")
    if quota_result.tie_break_trace:
        lines.append("浮动名额补位轨迹:")
        lines.extend([f"- {entry}" for entry in quota_result.tie_break_trace])
    return lines


def render_resurrection_table(
    quota_result: QuotaResult,
    resurrection_quotas: Dict[str, int],
) -> List[str]:
    lines = [
        "赛区  | 预计国赛名额 | 预计复活赛名额 | 预计总晋级",
        "----- | ------------ | -------------- | ----------",
    ]
    total_resurrection = 0
    for region in REGION_ORDER:
        national_quota = quota_result.items[region].total_quota
        resurrection_quota = int(resurrection_quotas.get(region, 0))
        total_resurrection += resurrection_quota
        lines.append(
            f"{region}  | {national_quota:>12} | {resurrection_quota:>14} | {national_quota + resurrection_quota:>10}"
        )

    lines.append(f"合计预计复活赛名额: {total_resurrection}")
    return lines


def render_pressure_table(pressure: Dict[str, PressureItem]) -> List[str]:
    lines = [
        "赛区  | 当前志愿 | 容量 | 缺口 | 超额",
        "----- | -------- | ---- | ---- | ----",
    ]
    for region in REGION_ORDER:
        item = pressure[region]
        lines.append(
            f"{region}  | {item.volunteers:>8} | {item.capacity:>4} | {item.deficit:>4} | {item.surplus:>4}"
        )
    return lines


def render_reallocation(moves: Iterable[ReallocationMove]) -> List[str]:
    rows = list(moves)
    if not rows:
        return ["当前没有出现可确认的超容量赛区，暂无明确被调剂学校。"]

    lines = [
        "学校 | 原赛区 -> 目标赛区 | 到目标距离(km) | 排名值 | 置信度",
        "---- | ------------------- | -------------- | ------ | ------",
    ]
    for move in rows:
        ranking_display = str(move.ranking_value) if move.ranking_value is not None else "未知"
        lines.append(
            f"{move.school} | {move.from_region} -> {move.to_region} | {move.distance_km:>14} | {ranking_display:>6} | {move.confidence}"
        )
    return lines


def render_highlights(highlights: Dict[str, str]) -> List[str]:
    if not highlights:
        return ["当前志愿队伍中未匹配到去年国赛32强。"]

    lines = ["学校 | 去年国赛名次层级", "---- | ----------------"]
    for school, tier in sorted(highlights.items(), key=lambda item: item[0]):
        lines.append(f"{school} | {tier}")
    return lines


def render_full_report(
    snapshot: QingflowSnapshot,
    quota_result: QuotaResult,
    resurrection_quotas: Optional[Dict[str, int]],
    pressure: Dict[str, PressureItem],
    moves: Iterable[ReallocationMove],
    highlights: Dict[str, str],
    notes: Optional[List[str]] = None,
    previous_snapshot: Optional[QingflowSnapshot] = None,
) -> str:
    report_lines: List[str] = []
    report_lines.append(_line())
    report_lines.append("RMUC 2026 三赛区实时分析")
    report_lines.append(f"采集时间(UTC): {_fmt_time(snapshot.fetched_at)}")
    report_lines.append(f"数据来源: {snapshot.source_url}")
    report_lines.append(_line())

    report_lines.append("[增量变化]")
    report_lines.extend(render_diff(previous_snapshot, snapshot))
    report_lines.append(_line())

    report_lines.append("[国赛名额估算]")
    report_lines.extend(render_quota_table(quota_result))
    report_lines.append(_line())

    if resurrection_quotas is not None:
        report_lines.append("[复活赛名额估算]")
        report_lines.extend(render_resurrection_table(quota_result, resurrection_quotas))
        report_lines.append(_line())

    report_lines.append("[赛区容量压力]")
    report_lines.extend(render_pressure_table(pressure))
    report_lines.append(_line())

    report_lines.append("[可能调剂学校及去向]")
    report_lines.extend(render_reallocation(moves))
    report_lines.append(_line())

    report_lines.append("[去年国赛队伍名次标注]")
    report_lines.extend(render_highlights(highlights))
    report_lines.append(_line())

    if notes:
        report_lines.append("[说明]")
        report_lines.extend(f"- {note}" for note in notes)
        report_lines.append(_line())

    return "\n".join(report_lines)
