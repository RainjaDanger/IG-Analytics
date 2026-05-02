import os
from datetime import datetime, timedelta, date

from instagram import InstagramClient
from sheets import SheetsClient


def update_github_secret(new_token):
    """Re-stores the refreshed token into GitHub Actions secrets."""
    import base64
    import requests

    github_token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")

    if not github_token or not repo:
        print("  Set GITHUB_TOKEN and GITHUB_REPO to enable auto-refresh")
        return

    try:
        from nacl import encoding, public

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        pk = requests.get(
            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
            headers=headers, timeout=10
        ).json()

        pub_key = public.PublicKey(pk["key"].encode(), encoding.Base64Encoder())
        encrypted = base64.b64encode(
            public.SealedBox(pub_key).encrypt(new_token.encode())
        ).decode()

        requests.put(
            f"https://api.github.com/repos/{repo}/actions/secrets/INSTAGRAM_ACCESS_TOKEN",
            headers=headers,
            json={"encrypted_value": encrypted, "key_id": pk["key_id"]},
            timeout=10
        ).raise_for_status()

        print("  Token auto-refreshed and saved to GitHub secrets")
    except Exception as e:
        print(f"  Auto-refresh failed: {e}")
        print("  Manually update INSTAGRAM_ACCESS_TOKEN in your repo secrets")


def get_week_bounds():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)             # Sunday
    since_ts = int(datetime.combine(week_start, datetime.min.time()).timestamp())
    until_ts = int(datetime.combine(week_end + timedelta(days=1), datetime.min.time()).timestamp())
    return week_start, week_end, since_ts, until_ts


def main():
    ig = InstagramClient(
        token=os.environ["INSTAGRAM_ACCESS_TOKEN"],
        user_id=os.environ["INSTAGRAM_USER_ID"],
        app_id=os.environ["META_APP_ID"],
        app_secret=os.environ["META_APP_SECRET"],
    )
    sheets = SheetsClient(
        creds_json=os.environ["GOOGLE_CREDENTIALS_JSON"],
        spreadsheet_id=os.environ["SPREADSHEET_ID"],
    )

    # Token refresh check
    days_left = ig.token_days_remaining()
    print(f"Token expires in {days_left} days")
    if days_left < 10:
        print("Refreshing token...")
        new_token = ig.refresh_token()
        update_github_secret(new_token)

    # Ensure Post Performance and Weekly Metrics tabs exist
    print("Checking sheet structure...")
    sheets.ensure_sheets_exist()

    # --- Posts ---
    print("Fetching posts from Instagram...")
    posts = ig.get_posts()
    print(f"Found {len(posts)} posts, fetching insights...")

    posts_data = []
    for i, post in enumerate(posts):
        media_type = post.get("media_type", "IMAGE")
        insights = ig.get_post_insights(post["id"], media_type)

        if media_type in ("VIDEO", "REEL"):
            views = insights.get("video_views", 0)
            thumb_url = post.get("thumbnail_url") or post.get("media_url", "")
        else:
            views = insights.get("impressions", 0)
            thumb_url = post.get("media_url", "")

        posts_data.append({
            "permalink":  post.get("permalink", ""),
            "likes":      post.get("like_count", 0),
            "comments":   post.get("comments_count", 0),
            "views":      views,
            "reach":      insights.get("reach", 0),
            "saves":      insights.get("saved", 0),
            "media_type": media_type,
            "media_url":  post.get("media_url", ""),
            "thumb_url":  thumb_url,
            "post_date":  post.get("timestamp", "")[:10],
            "caption":    post.get("caption", ""),
        })

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(posts)} processed")

    added, updated = sheets.upsert_posts(posts_data)
    print(f"Posts: {added} new, {updated} updated")

    # --- Weekly metrics ---
    print("Fetching weekly account metrics...")
    week_start, week_end, _, _ = get_week_bounds()

    account = ig.get_account_insights()
    followers = ig.get_follower_count()

    prev_followers = sheets.get_previous_follower_count(week_start.strftime("%b-%d"))
    net_follows = (followers - prev_followers) if prev_followers is not None else 0

    sheets.upsert_weekly_row({
        "week_start":    week_start.strftime("%b-%d"),
        "week_end":      week_end.strftime("%b-%d"),
        "reach_total":   account.get("impressions", 0),
        "organic_reach": account.get("reach", 0),
        "profile_views": account.get("profile_views", 0),
        "followers":     followers,
        "net_follows":   net_follows,
    })
    print(f"Weekly metrics: {week_start.strftime('%b %d')} – {week_end.strftime('%b %d')}")
    print("Sync complete!")


if __name__ == "__main__":
    main()
