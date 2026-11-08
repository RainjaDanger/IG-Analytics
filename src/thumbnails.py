"""Keep permanent copies of post thumbnails in the repo.

Instagram's media URLs are signed and expire within days, so an =IMAGE()
pointing at them goes blank between weekly syncs. Instead we download each
thumbnail once, commit it under thumbnails/, and point the sheet at the
copy served from GitHub.
"""
import os
import subprocess
from io import BytesIO

import requests
from PIL import Image

THUMB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thumbnails")
MAX_SIZE = (320, 320)


def public_url(media_id):
    repo = os.environ.get("GITHUB_REPOSITORY", "RainjaDanger/IG-Analytics")
    return f"https://raw.githubusercontent.com/{repo}/main/thumbnails/{media_id}.jpg"


def ensure_thumbnail(media_id, source_url):
    """Save a resized JPEG for this post if we don't have one yet.

    Returns the permanent URL, or None if there's no copy and the download failed.
    """
    path = os.path.join(THUMB_DIR, f"{media_id}.jpg")
    if os.path.exists(path):
        return public_url(media_id)
    if not source_url:
        return None
    try:
        r = requests.get(source_url, timeout=30)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        img.thumbnail(MAX_SIZE)
        os.makedirs(THUMB_DIR, exist_ok=True)
        img.save(path, "JPEG", quality=80)
        return public_url(media_id)
    except Exception as e:
        print(f"  Thumbnail error for {media_id}: {e}")
        return None


def publish():
    """Commit and push new thumbnails so their URLs resolve before the sheet loads them."""
    if not os.environ.get("GITHUB_ACTIONS"):
        print("  Not running in GitHub Actions; skipping thumbnail push")
        return
    git = ["git", "-c", "user.name=github-actions[bot]",
           "-c", "user.email=41898283+github-actions[bot]@users.noreply.github.com"]
    subprocess.run(git + ["add", THUMB_DIR], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
        print("  No new thumbnails")
        return
    subprocess.run(git + ["commit", "-m", "Add post thumbnails"], check=True)
    subprocess.run(git + ["push"], check=True)
    print("  New thumbnails pushed")
