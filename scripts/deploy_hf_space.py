"""One-command Hugging Face Space deployer.

GitHub Pages cannot run Flask, so if you want the dashboard's "立即刷新"
button to hit a live backend instead of a static snapshot you need to
host the Flask app on a platform that supports persistent processes.
Hugging Face Spaces (Docker SDK) is free, always-on, and requires no
credit card.

This script:
  1. Creates (or reuses) a Docker Space under your account.
  2. Stages the app source (src/, config/, data/) together with the
     Space-specific Dockerfile, wsgi.py, requirements.txt and README.md
     that live in ``deploy/hf_space/``.
  3. Uploads the staged tree to the Space via the Hugging Face Hub API.

After the push, HF builds the Docker image and boots gunicorn. First
build usually takes under a minute; the Space is then reachable at
``https://<user>-<space_name>.hf.space``.

Usage
-----

    pip install huggingface_hub
    export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx   # from https://huggingface.co/settings/tokens
    python scripts/deploy_hf_space.py --space your_username/wheretogo

Then, optionally, wire the GitHub Pages mirror to point at your new
Space by setting a repository variable:

    Settings → Secrets and variables → Actions → Variables → New variable
      Name:  LIVE_URL
      Value: https://your_username-wheretogo.hf.space/

The next Pages rebuild will inject a "⚡ 真·实时版本" button linking there.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HF_ASSETS = ROOT / "deploy" / "hf_space"
APP_DIRS = ("src", "config", "data")


def _stage(staging: Path) -> None:
    """Copy the HF Space assets + application source into ``staging``."""
    if not HF_ASSETS.is_dir():
        raise SystemExit(f"Missing HF assets directory: {HF_ASSETS}")

    for entry in HF_ASSETS.iterdir():
        dst = staging / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst)
        else:
            shutil.copy2(entry, dst)

    for name in APP_DIRS:
        src = ROOT / name
        if not src.is_dir():
            raise SystemExit(f"Missing application directory: {src}")
        shutil.copytree(src, staging / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--space",
        required=True,
        help="Target Hugging Face Space repo id, e.g. 'alice/wheretogo'",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HF_TOKEN"),
        help="HF access token with write scope. Defaults to $HF_TOKEN.",
    )
    parser.add_argument(
        "--commit-message",
        default="deploy: sync wheretogo source",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create the Space as private (default is public).",
    )
    args = parser.parse_args()

    if not args.token:
        sys.stderr.write(
            "error: Hugging Face token required. Pass --token or set HF_TOKEN.\n"
            "       Create one at https://huggingface.co/settings/tokens\n"
        )
        return 2

    try:
        from huggingface_hub import HfApi
    except ImportError:
        sys.stderr.write(
            "error: huggingface_hub is not installed.\n"
            "       Run: pip install huggingface_hub\n"
        )
        return 2

    api = HfApi(token=args.token)

    # 1. Ensure the Space exists (create if missing, reuse otherwise).
    api.create_repo(
        repo_id=args.space,
        repo_type="space",
        space_sdk="docker",
        private=args.private,
        exist_ok=True,
    )

    # 2. Stage all files that the Space's Docker build needs.
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "space"
        staging.mkdir()
        _stage(staging)

        # 3. Upload the staged tree.
        api.upload_folder(
            folder_path=str(staging),
            repo_id=args.space,
            repo_type="space",
            commit_message=args.commit_message,
        )

    user, _, space_name = args.space.partition("/")
    public_url = f"https://{user}-{space_name}.hf.space/"
    print(f"✓ Deployed to Hugging Face Space: {args.space}")
    print(f"  Space page:  https://huggingface.co/spaces/{args.space}")
    print(f"  Live URL:    {public_url}")
    print(
        "  (HF builds the Docker image asynchronously — give it ~30–60 "
        "seconds before the live URL is reachable.)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
