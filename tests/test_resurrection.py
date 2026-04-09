from rmuc_analyzer.engine import (
    apply_reallocation_moves_to_counts,
    build_effective_region_counts,
    compute_national_quotas,
    estimate_resurrection_quotas,
)
from rmuc_analyzer.models import ReallocationMove


def test_estimate_resurrection_quota_sum_and_bounds():
    national = compute_national_quotas({"南部": 5, "东部": 7, "北部": 3})
    region_counts = {"南部": 22, "东部": 26, "北部": 17}

    resurrection = estimate_resurrection_quotas(national, region_counts)

    assert sum(resurrection.values()) == 16

    for region, value in resurrection.items():
        assert value >= 0
        total = national.items[region].total_quota + value
        assert total <= 16
        assert total >= 8


def test_build_effective_region_counts_prefills_to_eight_when_incomplete():
    counts = {"南部": 2, "东部": 24, "北部": 7}

    effective = build_effective_region_counts(counts, expected_total=96, minimum_per_region=8)

    assert effective == {"南部": 8, "东部": 24, "北部": 8}


def test_apply_reallocation_moves_to_counts_updates_regions():
    counts = {"南部": 8, "东部": 30, "北部": 8}
    moves = [
        ReallocationMove(
            school="A",
            from_region="东部",
            to_region="北部",
            distance_km=100,
            ranking_value=10,
            confidence="中",
            reason="test",
        ),
        ReallocationMove(
            school="B",
            from_region="东部",
            to_region="南部",
            distance_km=110,
            ranking_value=11,
            confidence="中",
            reason="test",
        ),
    ]

    adjusted = apply_reallocation_moves_to_counts(counts, moves)

    assert adjusted == {"南部": 9, "东部": 28, "北部": 9}
