from datetime import datetime, timezone

from rmuc_analyzer.engine import predict_reallocation
from rmuc_analyzer.models import DistanceRecord, QingflowSnapshot
from rmuc_analyzer.sources.robomaster import infer_overseas_priority_schools_2026
from rmuc_analyzer.models import TeamRecord


def test_reallocation_same_distance_uses_worse_rank_first():
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 4, "东部": 3, "北部": 2},
        region_schools={
            "南部": ["S1", "S2", "S3", "S4"],
            "东部": ["E1", "E2", "E3"],
            "北部": ["N1", "N2"],
        },
        stale=False,
    )

    distance_map = {
        "S1": DistanceRecord("S1", "A", 10, 20, 40),
        "S2": DistanceRecord("S2", "A", 10, 20, 30),
        "S3": DistanceRecord("S3", "A", 10, 20, 10),
        "S4": DistanceRecord("S4", "A", 10, 20, 10),
        "E1": DistanceRecord("E1", "B", 30, 10, 50),
        "E2": DistanceRecord("E2", "B", 30, 10, 60),
        "E3": DistanceRecord("E3", "B", 30, 10, 20),
        "N1": DistanceRecord("N1", "C", 80, 80, 0),
        "N2": DistanceRecord("N2", "C", 80, 80, 0),
    }

    ranking_map = {
        "S3": 50,
        "S4": 100,
    }

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map=distance_map,
        ranking_map=ranking_map,
        priority_schools=[],
        capacity=3,
        expected_total=9,
    )

    assert len(moves) == 1
    assert moves[0].school == "S4"
    assert moves[0].from_region == "南部"
    assert moves[0].to_region == "北部"


def test_no_reallocation_when_no_surplus_region():
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 3, "东部": 3, "北部": 3},
        region_schools={"南部": ["S1", "S2", "S3"], "东部": ["E1", "E2", "E3"], "北部": ["N1", "N2", "N3"]},
        stale=False,
    )

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map={},
        ranking_map={},
        priority_schools=[],
        capacity=3,
        expected_total=9,
    )

    assert moves == []


def test_priority_school_is_excluded_from_reallocation_candidates():
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 4, "东部": 4, "北部": 1},
        region_schools={
            "南部": ["A1", "A2", "A3", "A4"],
            "东部": ["E1", "E2", "E3", "E4"],
            "北部": ["N1"],
        },
        stale=False,
    )

    distance_map = {
        "A1": DistanceRecord("A1", "A", 10, 20, 1),
        "A2": DistanceRecord("A2", "A", 10, 20, 2),
        "A3": DistanceRecord("A3", "A", 10, 20, 10),
        "A4": DistanceRecord("A4", "A", 10, 20, 11),
        "E1": DistanceRecord("E1", "B", 20, 10, 3),
        "E2": DistanceRecord("E2", "B", 20, 10, 4),
        "E3": DistanceRecord("E3", "B", 20, 10, 12),
        "E4": DistanceRecord("E4", "B", 20, 10, 13),
        "N1": DistanceRecord("N1", "C", 80, 80, 0),
    }

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map=distance_map,
        ranking_map={},
        priority_schools=["A1"],
        capacity=3,
        expected_total=9,
    )

    moved_schools = {move.school for move in moves}
    assert "A1" not in moved_schools
    assert len(moves) == 2


def test_infer_overseas_priority_schools_from_city_and_name():
    teams = [
        TeamRecord(school="香港大学", team="A"),
        TeamRecord(school="香港科技大学", team="B"),
        TeamRecord(school="香港科技大学（广州）", team="BG"),
        TeamRecord(school="华南理工大学", team="C"),
    ]

    distance_map = {
        "香港大学": DistanceRecord("香港大学", "香港", 662, 1615, 2328),
        "香港科技大学": DistanceRecord("香港科技大学", "香港", 662, 1615, 2328),
        "香港科技大学(广州)": DistanceRecord("香港科技大学（广州）", "广州市", 564, 1549, 2281),
        "华南理工大学": DistanceRecord("华南理工大学", "广州市", 564, 1549, 2281),
    }

    overseas = infer_overseas_priority_schools_2026(teams, distance_map)

    assert "香港大学" in overseas
    assert "香港科技大学" in overseas
    assert "香港科技大学（广州）" not in overseas
    assert "华南理工大学" not in overseas


def test_reallocation_follows_a_then_b_c_stages_without_a_as_second_stage_donor():
    # 初始志愿数：南1、东4、北4（capacity=3）
    # 先补A=南部（从东/北调2支），再仅在东北之间继续调剂。
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 1, "东部": 4, "北部": 4},
        region_schools={
            "南部": ["S1"],
            "东部": ["E1", "E2", "E3", "E4"],
            "北部": ["N1", "N2", "N3", "N4"],
        },
        stale=False,
    )

    distance_map = {
        # 引导第一阶段优先从东部调往南部
        "E1": DistanceRecord("E1", "E", 10, 5, 50),
        "E2": DistanceRecord("E2", "E", 11, 5, 50),
        "E3": DistanceRecord("E3", "E", 12, 5, 50),
        "E4": DistanceRecord("E4", "E", 13, 5, 50),
        "N1": DistanceRecord("N1", "N", 100, 20, 5),
        "N2": DistanceRecord("N2", "N", 101, 20, 5),
        "N3": DistanceRecord("N3", "N", 102, 20, 5),
        "N4": DistanceRecord("N4", "N", 103, 20, 5),
        # 若错误允许A在第二阶段供给，会优先挑中S1（应被禁止）
        "S1": DistanceRecord("S1", "S", 1, 1, 100),
    }

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map=distance_map,
        ranking_map={},
        priority_schools=[],
        capacity=3,
        expected_total=9,
    )

    assert len(moves) == 3
    assert [m.to_region for m in moves[:2]] == ["南部", "南部"]
    assert moves[2].to_region == "东部"
    # 第二阶段只能在东/北之间调剂，不允许再从南部调出
    assert all(m.from_region != "南部" for m in moves)


def test_reallocation_in_incomplete_signup_only_marks_required_surplus_moves():
    # 报名未满(expected_total=12, submitted=7)时，东部仅超容量1支，
    # 只应标记1支“当前必须调剂”的队伍。
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 1, "东部": 4, "北部": 2},
        region_schools={
            "南部": ["S1"],
            "东部": ["E1", "E2", "E3", "E4"],
            "北部": ["N1", "N2"],
        },
        stale=False,
    )

    distance_map = {
        "E1": DistanceRecord("E1", "E", 10, 1, 100),
        "E2": DistanceRecord("E2", "E", 11, 1, 100),
        "E3": DistanceRecord("E3", "E", 12, 1, 100),
        "E4": DistanceRecord("E4", "E", 13, 1, 100),
        "N1": DistanceRecord("N1", "N", 20, 100, 1),
        "N2": DistanceRecord("N2", "N", 21, 100, 1),
        "S1": DistanceRecord("S1", "S", 1, 1, 1),
    }

    moves = predict_reallocation(
        snapshot=snapshot,
        distance_map=distance_map,
        ranking_map={},
        priority_schools=[],
        capacity=3,
        expected_total=12,
    )

    assert len(moves) == 1
    assert moves[0].from_region == "东部"
    assert moves[0].to_region == "南部"
