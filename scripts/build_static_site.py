"""Render the Flask dashboard to a static site under ``docs/``.

Produces ``docs/index.html`` (the Jinja template rendered with the current
payload baked in) and ``docs/data.json`` (the same payload for in-browser
auto-refresh). The output is suitable for hosting on GitHub Pages — GitHub
Pages cannot run Flask, so we pre-render the dashboard and turn the "refresh"
button into a re-fetch of ``./data.json``.

Usage
-----

    PYTHONPATH=src python scripts/build_static_site.py --config config/config.json

Optional ``--live-url`` argument:

    PYTHONPATH=src python scripts/build_static_site.py \\
        --config config/config.json \\
        --live-url https://<your-hf-space>.hf.space/

When ``--live-url`` is provided, the built static page gets an extra
"⚡ 真·实时版本" button pointing to that URL and a subtitle note explaining
that this page is a snapshot. This is useful if you deployed a companion
Flask backend (for example on a Hugging Face Docker Space) and want
viewers who land on the GitHub Pages mirror to jump to the always-on
version for per-request live data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import rmuc_analyzer.web as web  # noqa: E402
from rmuc_analyzer.config import AnalyzerConfig  # noqa: E402


# --- Template patches (must match src/rmuc_analyzer/templates/index.html) ---

ORIGINAL_FETCH = 'fetch("/api/analysis", { cache: "no-store" })'
STATIC_FETCH = 'fetch("./data.json?t=" + Date.now(), { cache: "no-store" })'

ORIGINAL_TOOLBAR = (
    '<div class="toolbar">\n'
    '          <button class="btn" id="refreshBtn" type="button">立即刷新</button>\n'
    '        </div>'
)

ORIGINAL_SUB = (
    '<p class="sub">排序规则：先按去年国赛排名，再按积分排名；'
    '被预测调剂的队伍会虚化并标注调入/调出状态。</p>'
)


def _toolbar_with_live(live_url: str) -> str:
    return (
        '<div class="toolbar">\n'
        '          <button class="btn" id="refreshBtn" type="button">刷新快照</button>\n'
        f'          <a class="btn" href="{live_url}" target="_blank" rel="noopener" '
        'style="background:#ffd4b8;">⚡ 真·实时版本</a>\n'
        '        </div>'
    )


def _sub_with_live(live_url: str) -> str:
    return (
        '<p class="sub">排序规则：先按去年国赛排名，再按积分排名；'
        '被预测调剂的队伍会虚化并标注调入/调出状态。'
        '<br><span style="opacity:0.85;font-size:12px;">'
        '本页面为 GitHub Pages 静态快照（由定时 Actions 重建）。'
        '需要秒级实时数据请点击右上角「⚡ 真·实时版本」前往 '
        f'<a href="{live_url}" target="_blank" rel="noopener" '
        'style="color:#ffd4b8;">实时后端</a>。'
        '</span></p>'
    )


def build_payload(config_path: str) -> dict:
    config = AnalyzerConfig.load(config_path, ROOT)
    runtime = web._build_runtime(ROOT, config)
    return web._build_payload(runtime)


def render_html(payload: dict, live_url: str | None) -> str:
    template_dir = ROOT / "src" / "rmuc_analyzer" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)
    html = env.get_template("index.html").render(initial_payload=payload)

    if ORIGINAL_FETCH not in html:
        raise RuntimeError(
            "Template fetch line not found; static patch is out of sync with "
            "src/rmuc_analyzer/templates/index.html"
        )
    html = html.replace(ORIGINAL_FETCH, STATIC_FETCH)

    if live_url:
        if ORIGINAL_TOOLBAR not in html or ORIGINAL_SUB not in html:
            raise RuntimeError(
                "Template toolbar or subtitle block not found; --live-url "
                "injection is out of sync with index.html"
            )
        html = html.replace(ORIGINAL_TOOLBAR, _toolbar_with_live(live_url))
        html = html.replace(ORIGINAL_SUB, _sub_with_live(live_url))

    return html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.json")
    parser.add_argument("--out-dir", default="docs")
    parser.add_argument(
        "--live-url",
        default=None,
        help="Optional URL of an always-on Flask backend (e.g. a Hugging Face "
        "Space) to link from the snapshot page.",
    )
    args = parser.parse_args()

    payload = build_payload(args.config)
    html = render_html(payload, args.live_url)

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / ".nojekyll").write_text("")

    live_note = f" (live URL: {args.live_url})" if args.live_url else ""
    print(
        f"Built static site: regions={len(payload['regions'])}, "
        f"submitted={payload['total_submitted']}/{payload['expected_total']}"
        f"{live_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
