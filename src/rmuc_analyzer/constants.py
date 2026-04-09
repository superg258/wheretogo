from __future__ import annotations

REGION_ORDER = ("南部", "东部", "北部")
REGION_DISPLAY = {
    "南部": "南部赛区",
    "东部": "东部赛区",
    "北部": "北部赛区",
}
REGION_TARGET_CITY = {
    "南部": "长沙市",
    "东部": "济南市",
    "北部": "沈阳市",
}
REGION_DISTANCE_FIELD = {
    "南部": "to_changsha",
    "东部": "to_jinan",
    "北部": "to_shenyang",
}

TOP_TIERS_ALLOWED = (
    "冠军",
    "亚军",
    "季军",
    "殿军",
    "八强",
    "十六强",
    "三十二强",
)
TOP16_TIERS = (
    "冠军",
    "亚军",
    "季军",
    "殿军",
    "八强",
    "十六强",
)

DEFAULT_ANNOUNCEMENT_URLS = {
    "teams_2026": "https://www.robomaster.com/zh-CN/resource/pages/announcement/1909",
    "rules_2026": "https://www.robomaster.com/zh-CN/resource/pages/announcement/1910",
    "ranking_2025": "https://www.robomaster.com/zh-CN/resource/pages/announcement/1884",
    "rmul_hosts_2026": "https://www.robomaster.com/zh-CN/resource/pages/announcement/1903",
    "national_2025": "https://www.robomaster.com/zh-CN/resource/pages/announcement/1856",
    "regional_2025": "https://www.robomaster.com/zh-CN/resource/pages/announcement/1847",
}

DEFAULT_QINGFLOW_URL = (
    "https://qingflow.com/appView/e3bol1op1c02/shareView/e3bol20d1c02"
    "?exWsId=1SdM5_PB-NwUtnyPn2b8NA&qfVersion=mysql"
)

DEFAULT_PRIORITY_SCHOOLS = [
    "长沙理工大学",
    "齐鲁工业大学",
    "东北大学",
]
