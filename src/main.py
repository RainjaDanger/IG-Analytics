import os
import sys
from datetime import datetime, timedelta, date

import thumbnails
from instagram import InstagramClient
from sheets import SheetsClient

TOKEN_WARN_DAYS = 14


def get_week_bounds():
    override = os.environ.get("WEEK_START_DATE")
    if override:
        week_start = date.fromisoformat(override)
    else:
        today = date.today()
        # Always capture the most recently completed Mon-Sun week.
        # Works correctly whether the runner sees Sunday (cron) or Mon+ (manual).
        days_since_sunday = (today.weekday() + 1) % 7
        last_sunday = today - timedelta(days=days_since_sunday)
        week_start = last_sunday - timedelta(days=6)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def generate_ai_insights(posts_data, week_start, week_end):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic

        scored = []
        for p in posts_data:
            try:
                days = max(1, (date.today() - date.fromisoformat(p["post_date"])).days)
                score = ((p["likes"] * 10) + (p["comments"] * 20) + p["views"]) / days
                if score > 0:
                    scored.append({**p, "_score": score, "_days": days})
            except Exception:
                pass

        top = sorted(scored, key=lambda x: x["_score"], reverse=True)[:5]
        if not top:
            return None

        posts_text = ""
        for i, p in enumerate(top, 1):
            caption = (p["caption"] or "")[:200]
            posts_text += (
                f"\nPost {i} — {p['media_type']} (score {p['_score']:.0f})\n"
                f"Likes: {p['likes']} | Comments: {p['comments']} | "
                f"Views: {p['views']} | Reach: {p['reach']} | Saves: {p['saves']} | "
                f"Days live: {p['_days']}\n"
                f"Caption: {caption}{'...' if len(p['caption'] or '') > 200 else ''}\n"
            )

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    f"You are analyzing Instagram performance data for a fitness/performance coaching account. "
                    f"Here are the top 5 posts for the week of {week_start} to {week_end}:\n"
                    f"{posts_text}\n"
                    "In under 300 words, cover:\n"
                    "1. What these posts have in common (format, content themes, tone)\n"
                    "2. What's driving their performance (saves, comments, reach patterns)\n"
                    "3. 2-3 specific recommendations for next week's content\n\n"
                    "Be direct and practical."
                )
            }]
        )
        return response.content[0].text
    except Exception as e:
        print(f"  AI insights error: {e}")
        return None


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

    # Token expiry check (long-lived user tokens can't be extended; a System User token never expires)
    days_left = ig.token_days_remaining()
    if days_left is None:
        print("Token never expires")
    else:
        print(f"Token expires in {days_left} days")

    # Ensure all tabs exist with correct structure
    print("Checking sheet structure...")
    sheets.ensure_sheets_exist()
    sheets.apply_score_formatting()

    # --- Posts ---
    print("Fetching posts from Instagram...")
    posts = ig.get_posts()
    print(f"Found {len(posts)} posts, fetching insights...")

    week_start, week_end = get_week_bounds()

    posts_data = []
    for i, post in enumerate(posts):
        media_type = post.get("media_type", "IMAGE")
        post_date = post.get("timestamp", "")[:10]
        age_days = (date.today() - date.fromisoformat(post_date)).days if post_date else 0
        insights = ig.get_post_insights(post["id"], media_type) if age_days <= 730 else {}

        if media_type == "REEL":
            views = insights.get("video_views", 0)
        else:
            views = insights.get("reach", 0)

        # Videos need their cover image; a carousel's media_url is its first
        # slide, which may itself be a video, so use that slide's cover instead
        source = post
        if media_type == "CAROUSEL_ALBUM":
            source = (post.get("children", {}).get("data") or [post])[0]
        if source.get("media_type") in ("VIDEO", "REEL"):
            image_url = source.get("thumbnail_url", "")
        else:
            image_url = source.get("media_url", "")
        thumb_url = thumbnails.ensure_thumbnail(post["id"], image_url) or ""

        posts_data.append({
            "permalink":  post.get("permalink", ""),
            "likes":      post.get("like_count", 0),
            "comments":   post.get("comments_count", 0),
            "reshares":   insights.get("shares", 0),
            "views":      views,
            "reach":      insights.get("reach", 0),
            "saves":      insights.get("saved", 0),
            "media_type": media_type,
            "media_url":  post.get("media_url", ""),
            "thumb_url":  thumb_url,
            "post_date":  post_date,
            "caption":    post.get("caption", ""),
        })

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(posts)} processed")

    print("Publishing thumbnails...")
    thumbnails.publish()

    added, updated = sheets.upsert_posts(posts_data)
    print(f"Posts: {added} new, {updated} updated")

    print("Sorting posts by score...")
    sheets.sort_posts_by_score()

    # Count posts published in this specific week only
    posts_this_week = sum(
        1 for p in posts
        if week_start.isoformat() <= p.get("timestamp", "")[:10] <= week_end.isoformat()
    )

    # --- Weekly metrics ---
    print("Fetching weekly account metrics...")
    account = ig.get_account_insights()
    followers = ig.get_follower_count()

    prev_followers = sheets.get_previous_follower_count(week_start)
    net_follows = (followers - prev_followers) if prev_followers is not None else 0

    sheets.upsert_weekly_row({
        "week_start":      week_start.strftime("%b-%d"),
        "week_end":        week_end.strftime("%b-%d"),
        "reach_total":     account.get("reach", 0),
        "organic_reach":   account.get("organic_reach", 0),
        "profile_views":   account.get("profile_views", 0),
        "followers":       followers,
        "net_follows":     net_follows,
        "posts_published": posts_this_week,
    })

    # --- Story metrics ---
    sheets.upsert_weekly_story_row({
        "week_start":  week_start.strftime("%b-%d"),
        "week_end":    week_end.strftime("%b-%d"),
        "story_reach": account.get("story_reach", 0),
    })

    print("Sorting weekly sheets by date...")
    sheets.sort_weekly_by_date()

    # --- AI Insights ---
    print("Generating AI insights...")
    analysis = generate_ai_insights(
        posts_data,
        week_start.strftime("%b %d"),
        week_end.strftime("%b %d")
    )
    if analysis:
        sheets.write_ai_insights(week_start.strftime("%b-%d"), analysis)
        print("  AI insights written")
    else:
        print("  AI insights skipped (no API key or no scored posts)")

    print(f"Weekly metrics: {week_start.strftime('%b %d')} – {week_end.strftime('%b %d')}")
    print("Sync complete!")

    # Fail the run (so GitHub emails a notification) while the token is close to expiry
    if days_left is not None and days_left < TOKEN_WARN_DAYS:
        print(f"::error::Instagram access token expires in {days_left} days. "
              "Replace INSTAGRAM_ACCESS_TOKEN with a new token (ideally a non-expiring System User token).")
        sys.exit(1)


if __name__ == "__main__":
    main()
