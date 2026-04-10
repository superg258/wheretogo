from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from rmuc_analyzer.constants import (
    DEFAULT_ANNOUNCEMENT_URLS,
    DEFAULT_PRIORITY_SCHOOLS,
    DEFAULT_QINGFLOW_URL,
)


@dataclass
class AnalyzerConfig:
    poll_interval_sec: int = 60
    expected_total_teams: int = 96
    capacity_per_region: int = 32
    qingflow_url: str = DEFAULT_QINGFLOW_URL
    announcement_urls: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ANNOUNCEMENT_URLS))
    announcement_local_dir: str = "data/announcements"
    announcement_local_only: bool = False
    manual_top16_counts: Optional[Dict[str, int]] = None
    priority_schools: List[str] = field(default_factory=lambda: list(DEFAULT_PRIORITY_SCHOOLS))
    rmu_ranking_csv: str = "data/rmu_ranking.csv"
    cache_file: str = ".cache/latest_qingflow_snapshot.json"
    request_timeout_sec: int = 20
    resurrection_weight_history: float = 0.40
    resurrection_weight_rmu: float = 0.35
    resurrection_weight_national_excess: float = 0.65
    resurrection_national_base_quota: int = 8
    resurrection_weight_history: float = 0.40
    resurrection_weight_rmu: float = 0.35
    resurrection_weight_national_excess: float = 0.65
    resurrection_national_base_quota: int = 8

    @staticmethod
    def load(config_path: Optional[str], root_dir: Path) -> "AnalyzerConfig":
        cfg = AnalyzerConfig()
        if not config_path:
            return cfg

        path = Path(config_path)
        if not path.is_absolute():
            path = root_dir / path

        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        for key, value in raw.items():
            if not hasattr(cfg, key):
                continue
            setattr(cfg, key, value)

        return cfg

    def resolve_path(self, root_dir: Path, path_like: str) -> Path:
        path = Path(path_like)
        if path.is_absolute():
            return path
        return root_dir / path
