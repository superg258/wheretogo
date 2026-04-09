from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests
from bs4 import BeautifulSoup

from rmuc_analyzer.constants import TOP16_TIERS, TOP_TIERS_ALLOWED
from rmuc_analyzer.models import DistanceRecord, NationalTierRecord, TeamRecord
from rmuc_analyzer.utils import clean_text, normalize_school_name, parse_int

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_ANNOUNCEMENT_ID_PATTERN = re.compile(r"/announcement/(\d+)")
_REGIONAL_2025_TO_2026 = {
    "南部赛区": "南部",
    "中部赛区": "东部",
    "东部赛区": "北部",
}


def _is_remote_source(source: str) -> bool:
    value = source.strip().lower()
    return value.startswith("https://") or value.startswith("http://")


def _read_local_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"本地公告文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def _fetch_remote_html(url: str, timeout_sec: int = 20) -> str:
    response = requests.get(url, timeout=timeout_sec, headers=_HEADERS)
    response.raise_for_status()
    return response.text


def _announcement_local_filename(key: str, source_url: str) -> str:
    match = _ANNOUNCEMENT_ID_PATTERN.search(source_url)
    if match:
        return f"{key}_{match.group(1)}.html"
    safe_key = re.sub(r"[^a-zA-Z0-9_-]+", "_", key).strip("_") or "announcement"
    return f"{safe_key}.html"


def localize_announcement_sources(
    announcement_sources: Dict[str, str],
    root_dir: Path,
    timeout_sec: int = 20,
    local_dir: str = "data/announcements",
    local_only: bool = False,
) -> Dict[str, str]:
    target_dir = Path(local_dir)
    if not target_dir.is_absolute():
        target_dir = root_dir / target_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    localized: Dict[str, str] = {}
    for key, source in announcement_sources.items():
        source_value = source.strip()
        if not source_value:
            raise ValueError(f"公告来源为空: {key}")

        if _is_remote_source(source_value):
            local_path = target_dir / _announcement_local_filename(key, source_value)

            if local_only:
                if not local_path.exists():
                    raise FileNotFoundError(
                        f"公告本地化模式已开启，且缺少本地文件: {local_path}"
                    )
            else:
                try:
                    html = _fetch_remote_html(source_value, timeout_sec)
                    local_path.write_text(html, encoding="utf-8")
                except Exception:
                    if not local_path.exists():
                        raise

            localized[key] = str(local_path)
            continue

        local_path = Path(source_value)
        if not local_path.is_absolute():
            local_path = root_dir / local_path
        if not local_path.exists():
            raise FileNotFoundError(f"公告本地文件不存在: {local_path}")
        localized[key] = str(local_path)

    return localized


def fetch_html(source: str, timeout_sec: int = 20) -> str:
    source_value = source.strip()
    if not source_value:
        raise ValueError("公告来源不能为空")

    if _is_remote_source(source_value):
        return _fetch_remote_html(source_value, timeout_sec)

    file_source = source_value[7:] if source_value.startswith("file://") else source_value
    return _read_local_file(Path(file_source))


def _extract_tables(html: str) -> List[List[List[str]]]:
    soup = BeautifulSoup(html, "html.parser")
    tables: List[List[List[str]]] = []
    for table in soup.find_all("table"):
        rows: List[List[str]] = []
        for tr in table.find_all("tr"):
            cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _find_table_with_headers(tables: Sequence[List[List[str]]], required_headers: Sequence[str]) -> Optional[List[List[str]]]:
    for rows in tables:
        inspect_rows = rows[:5]
        for idx, row in enumerate(inspect_rows):
            row_joined = "|".join(row)
            if all(header in row_joined for header in required_headers):
                return rows[idx:]
    return None


def parse_teams_2026(announcement_url: str, timeout_sec: int = 20) -> List[TeamRecord]:
    html = fetch_html(announcement_url, timeout_sec)
    tables = _extract_tables(html)
    table = _find_table_with_headers(tables, ["序号", "学校名称", "队伍名称"])
    if table is None:
        raise ValueError("未在1909公告中找到队伍名单表")

    teams: List[TeamRecord] = []
    seen = set()

    for row in table[1:]:
        if len(row) < 3:
            continue
        serial = parse_int(row[0])
        if serial is None:
            continue

        school = clean_text(row[1])
        team = clean_text(row[2])
        if not school:
            continue

        school_key = normalize_school_name(school)
        if school_key in seen:
            continue
        seen.add(school_key)
        teams.append(TeamRecord(school=school, team=team))

    if not teams:
        raise ValueError("1909公告解析失败，未得到队伍数据")

    return teams


def parse_distance_table_2026(rule_url: str, timeout_sec: int = 20) -> Dict[str, DistanceRecord]:
    html = fetch_html(rule_url, timeout_sec)
    tables = _extract_tables(html)
    table = _find_table_with_headers(tables, ["学校名称", "城市", "到长沙市直线距离"])
    if table is None:
        raise ValueError("未在1910公告中找到距离表")

    distance_map: Dict[str, DistanceRecord] = {}

    for row in table[1:]:
        if len(row) < 5:
            continue

        school = clean_text(row[0])
        city = clean_text(row[1])
        to_changsha = parse_int(row[2])
        to_jinan = parse_int(row[3])
        to_shenyang = parse_int(row[4])

        if not school or to_changsha is None or to_jinan is None or to_shenyang is None:
            continue

        distance_map[normalize_school_name(school)] = DistanceRecord(
            school=school,
            city=city,
            to_changsha=to_changsha,
            to_jinan=to_jinan,
            to_shenyang=to_shenyang,
        )

    if not distance_map:
        raise ValueError("1910公告距离表解析失败，未得到有效数据")

    return distance_map


def parse_rmul_host_schools_2026(rmul_url: str, timeout_sec: int = 20) -> List[str]:
    html = fetch_html(rmul_url, timeout_sec)
    tables = _extract_tables(html)
    table = _find_table_with_headers(tables, ["站点", "承办单位"])
    if table is None:
        raise ValueError("未在1903公告中找到RMUL站点承办单位表")

    hosts: List[str] = []
    seen = set()

    for row in table[1:]:
        if len(row) >= 5:
            station = clean_text(row[1])
            host = clean_text(row[4])
        elif len(row) >= 4:
            station = clean_text(row[0])
            host = clean_text(row[3])
        else:
            continue

        # 部分公告单元格存在版式插入空白，如“珠 海”“中国 科学技术大学”。
        host = "".join(host.split())

        if "站" not in station:
            continue
        if not host:
            continue

        key = normalize_school_name(host)
        if key in seen:
            continue
        seen.add(key)
        hosts.append(host)

    if not hosts:
        raise ValueError("1903公告解析失败，未得到RMUL承办院校")

    return hosts


def parse_regional_signup_regions_2025(regional_url: str, timeout_sec: int = 20) -> Dict[str, str]:
    html = fetch_html(regional_url, timeout_sec)
    tables = _extract_tables(html)
    table = _find_table_with_headers(tables, ["排名", "学校", "队伍", "奖项"])
    if table is None:
        raise ValueError("未在1847公告中找到区域赛名单表")

    school_region_map: Dict[str, str] = {}
    current_region_2026: Optional[str] = None

    for row in table:
        if len(row) == 1 and "赛区获奖名单" in row[0]:
            title = clean_text(row[0])
            current_region_2026 = None
            for region_2025, region_2026 in _REGIONAL_2025_TO_2026.items():
                if region_2025 in title:
                    current_region_2026 = region_2026
                    break
            continue

        if not row:
            continue
        if row[0] == "排名":
            continue
        if current_region_2026 is None:
            continue

        # 1847存在rowspan导致“排名”列可能缺失，行宽会在4/5列之间波动。
        school = ""
        if len(row) >= 5:
            school = clean_text(row[1])
        elif len(row) >= 4:
            school = clean_text(row[0])

        if not school:
            continue

        key = normalize_school_name(school)
        if key not in school_region_map:
            school_region_map[key] = current_region_2026

    if not school_region_map:
        raise ValueError("1847公告解析失败，未得到学校赛区报名映射")

    return school_region_map


def infer_overseas_priority_schools_2026(
    teams: List[TeamRecord],
    distance_map: Dict[str, DistanceRecord],
) -> List[str]:
    overseas_city_markers = {
        "香港",
        "澳门",
        "台北",
        "台中",
        "高雄",
        "新潟",
    }
    overseas_name_markers = (
        "香港",
        "澳门",
        "新潟",
        "University",
    )

    overseas: List[str] = []
    seen = set()

    for team in teams:
        school = clean_text(team.school)
        if not school:
            continue

        school_key = normalize_school_name(school)
        distance = distance_map.get(school_key)

        is_overseas = False
        if distance is not None:
            city = clean_text(distance.city)
            is_overseas = any(marker in city for marker in overseas_city_markers)
        else:
            # 仅在缺少城市信息时使用名称兜底，避免“香港科技大学（广州）”这类误判。
            if any(marker in school for marker in overseas_name_markers):
                is_overseas = True

        if not is_overseas:
            continue

        if school_key in seen:
            continue
        seen.add(school_key)
        overseas.append(school)

    return overseas


def parse_rmu_ranking_2025(ranking_url: str, timeout_sec: int = 20) -> List[Dict[str, str]]:
    html = fetch_html(ranking_url, timeout_sec)
    tables = _extract_tables(html)
    table = _find_table_with_headers(tables, ["排名", "学校名称", "积分"])
    if table is None:
        raise ValueError("未在1884公告中找到积分榜表")

    ranking_rows: List[Dict[str, str]] = []
    seen = set()

    for row in table[1:]:
        if len(row) < 3:
            continue

        # 1884表格通常为: 排名 | 学校中文 | 学校英文 | 积分
        rank_value = parse_int(row[0])
        school = clean_text(row[1])
        score = clean_text(row[-1])

        if rank_value is None or not school:
            continue

        key = normalize_school_name(school)
        if key in seen:
            continue
        seen.add(key)

        ranking_rows.append(
            {
                "school": school,
                "rank": str(rank_value),
                "score": score,
            }
        )

    if not ranking_rows:
        raise ValueError("1884公告解析失败，未得到积分榜数据")

    return ranking_rows


def parse_national_tiers_2025(national_url: str, timeout_sec: int = 20) -> Dict[str, NationalTierRecord]:
    html = fetch_html(national_url, timeout_sec)
    tables = _extract_tables(html)
    table = _find_table_with_headers(tables, ["排名", "学校名称", "队伍名称", "奖项"])
    if table is None:
        raise ValueError("未在1856公告中找到全国赛名单表")

    records: Dict[str, NationalTierRecord] = {}
    current_tier: Optional[str] = None
    rank_order = 1

    for row in table[1:]:
        if len(row) < 3:
            continue

        # 该表在“排名”列为空时，HTML通常会省略空单元格，导致列左移。
        if len(row) >= 4:
            rank_cell = clean_text(row[0])
            school = clean_text(row[1])
            team = clean_text(row[2])
            award_level = clean_text(row[3])
        else:
            rank_cell = ""
            school = clean_text(row[0])
            team = clean_text(row[1])
            award_level = clean_text(row[2])

        if rank_cell in TOP_TIERS_ALLOWED:
            current_tier = rank_cell

        if not school:
            continue

        key = normalize_school_name(school)
        if key in records:
            continue

        in_top32 = rank_order <= 32
        tier = current_tier if in_top32 and current_tier in TOP_TIERS_ALLOWED else "-"
        is_resurrection_team = (award_level == "二等奖") and (not in_top32)

        records[key] = NationalTierRecord(
            school=school,
            team=team,
            tier=tier,
            rank_order=rank_order,
            award_level=award_level or "-",
            in_top32=in_top32,
            is_resurrection_team=is_resurrection_team,
        )
        rank_order += 1

    if not records:
        raise ValueError("1856公告解析失败，未得到去年国赛队伍")

    return records


def extract_top16_school_keys(national_records: Dict[str, NationalTierRecord]) -> List[str]:
    return [
        key
        for key, item in national_records.items()
        if item.tier in TOP16_TIERS
    ]
