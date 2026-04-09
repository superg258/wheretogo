from datetime import datetime, timezone

from rmuc_analyzer.engine import (
    apply_reallocation_moves_to_region_schools,
    compute_national_quotas,
    infer_top16_counts_from_current_signup,
    infer_top16_counts_from_region_schools,
    infer_top16_counts_from_regional_signup,
)
from rmuc_analyzer.models import NationalTierRecord, QingflowSnapshot, ReallocationMove


def test_floating_quota_threshold_gt_four():
    counts = {"南部": 4, "东部": 5, "北部": 7}
    result = compute_national_quotas(counts)

    assert result.items["南部"].floating_quota == 0
    assert result.items["东部"].floating_quota == 2
    assert result.items["北部"].floating_quota == 2
    assert sum(item.total_quota for item in result.items.values()) == 28


def test_largest_remainder_tie_break_uses_event_order():
    counts = {"南部": 6, "东部": 5, "北部": 6}
    result = compute_national_quotas(counts)

    # 南部与北部余数并列时，按举办时间顺序优先南部。
    assert result.items["南部"].floating_quota == 2
    assert result.items["东部"].floating_quota == 1
    assert result.items["北部"].floating_quota == 1


def test_infer_top16_counts_from_regional_signup():
    records = {
        "A": NationalTierRecord("A校", "A队", "冠军", 1),
        "B": NationalTierRecord("B校", "B队", "八强", 6),
        "C": NationalTierRecord("C校", "C队", "十六强", 12),
        "D": NationalTierRecord("D校", "D队", "-", 40),
    }
    signup_map = {
        "A": "南部",
        "B": "东部",
        "C": "北部",
    }

    counts, missing = infer_top16_counts_from_regional_signup(records, signup_map)

    assert counts == {"南部": 1, "东部": 1, "北部": 1}
    assert missing == []


def test_infer_top16_counts_from_regional_signup_missing_school_mapping():
    records = {
        "A": NationalTierRecord("A校", "A队", "冠军", 1),
        "B": NationalTierRecord("B校", "B队", "十六强", 16),
    }
    signup_map = {
        "A": "南部",
    }

    counts, missing = infer_top16_counts_from_regional_signup(records, signup_map)

    assert counts == {"南部": 1, "东部": 0, "北部": 0}
    assert missing == ["B校"]


def test_infer_top16_counts_from_current_signup():
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 2, "东部": 2, "北部": 1},
        region_schools={
            "南部": ["A校", "X校"],
            "东部": ["B校", "C校"],
            "北部": ["D校"],
        },
        stale=False,
    )

    records = {
        "A校": NationalTierRecord("A校", "A队", "冠军", 1),
        "B校": NationalTierRecord("B校", "B队", "十六强", 16),
        "C校": NationalTierRecord("C校", "C队", "-", 40),
        "D校": NationalTierRecord("D校", "D队", "八强", 7),
    }

    counts = infer_top16_counts_from_current_signup(snapshot, records)

    assert counts == {"南部": 1, "东部": 1, "北部": 1}


def test_infer_top16_counts_from_current_signup_deduplicates_school():
    snapshot = QingflowSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url="test",
        region_counts={"南部": 1, "东部": 1, "北部": 0},
        region_schools={
            "南部": ["A校"],
            "东部": ["A校"],
            "北部": [],
        },
        stale=False,
    )

    records = {
        "A校": NationalTierRecord("A校", "A队", "十六强", 12),
    }

    counts = infer_top16_counts_from_current_signup(snapshot, records)

    assert sum(counts.values()) == 1


def test_apply_reallocation_moves_to_region_schools_updates_membership():
    region_schools = {
        "南部": ["S1"],
        "东部": ["E1", "E2"],
        "北部": ["N1"],
    }
    moves = [
        ReallocationMove(
            school="E2",
            from_region="东部",
            to_region="南部",
            distance_km=100,
            ranking_value=20,
            confidence="中",
            reason="test",
        )
    ]

    adjusted = apply_reallocation_moves_to_region_schools(region_schools, moves)

    assert "E2" not in adjusted["东部"]
    assert "E2" in adjusted["南部"]


def test_infer_top16_counts_from_region_schools_after_reallocation():
    records = {
        "S1": NationalTierRecord("S1", "S", "冠军", 1),
        "E2": NationalTierRecord("E2", "E", "八强", 8),
        "N1": NationalTierRecord("N1", "N", "十六强", 16),
    }
    original = {
        "南部": ["S1"],
        "东部": ["E2"],
        "北部": ["N1"],
    }
    moves = [
        ReallocationMove(
            school="E2",
            from_region="东部",
            to_region="南部",
            distance_km=100,
            ranking_value=20,
            confidence="中",
            reason="test",
        )
    ]

    adjusted = apply_reallocation_moves_to_region_schools(original, moves)
    counts = infer_top16_counts_from_region_schools(adjusted, records)

    assert counts == {"南部": 2, "东部": 0, "北部": 1}
