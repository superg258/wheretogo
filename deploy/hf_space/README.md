---
title: Wheretogo RMUC 2026
emoji: "\U0001F3C6"
colorFrom: yellow
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Real-time RMUC 2026 three-region registration analyzer
---

# RMUC 2026 三赛区分析器（Hugging Face Space 版）

This Space is the always-on version of
[wheretogo](https://github.com/superg258/wheretogo). It runs the original
Flask backend under gunicorn, so every request re-scrapes the qingflow
registration data and the RoboMaster announcements in real time — the
"立即刷新" button returns fresh numbers within seconds.

## Differences from the GitHub Pages mirror

| | GitHub Pages mirror | This Space |
|---|---|---|
| Refresh granularity | ~30 min (cron rebuild) | per-request |
| Backend process | none (static files) | gunicorn + Flask |
| Refresh button behavior | reloads a static JSON snapshot | hits live `/api/analysis` |
| Best for | casual viewers | real-time decisions |

## API

```
GET /               → full dashboard with initial data baked in
GET /api/analysis   → JSON payload (regions, quotas, reallocation predictions)
```

## Source

[github.com/superg258/wheretogo](https://github.com/superg258/wheretogo)
