import requests
from requests.exceptions import HTTPError
from datetime import datetime

BASE = "https://graph.facebook.com/v21.0"


class InstagramClient:
    def __init__(self, token, user_id, app_id, app_secret):
        self.token = token.strip()  # remove any accidental whitespace/newlines
        self.user_id = user_id
        self.app_id = app_id
        self.app_secret = app_secret

    def _get(self, path, params=None):
        p = {**(params or {}), "access_token": self.token}
        r = requests.get(f"{BASE}/{path}", params=p, timeout=30)
        r.raise_for_status()
        return r.json()

    def token_days_remaining(self):
        try:
            data = self._get("debug_token", {
                "input_token": self.token,
                "access_token": f"{self.app_id}|{self.app_secret}"
            })
            expires_at = data.get("data", {}).get("expires_at", 0)
            if not expires_at:
                return 999
            return (datetime.fromtimestamp(expires_at) - datetime.now()).days
        except Exception as e:
            print(f"  Could not check token expiry: {e}")
            return 999

    def refresh_token(self):
        r = requests.get(f"{BASE}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": self.token,
        }, timeout=30)
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return self.token

    def get_posts(self):
        posts = []
        fields = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
        url = f"{BASE}/{self.user_id}/media"
        params = {"fields": fields, "limit": 100, "access_token": self.token}

        while url:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            posts.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = None  # next URL already includes all params

        return posts

    def get_post_insights(self, media_id, media_type):
        if media_type == "REEL":
            metrics = "reach,saved,video_views"
        elif media_type == "VIDEO":
            metrics = "reach,saved"
        elif media_type == "CAROUSEL_ALBUM":
            metrics = "reach,saved"
        else:
            metrics = "reach,saved"

        try:
            data = self._get(f"{media_id}/insights", {"metric": metrics})
            result = {}
            for item in data.get("data", []):
                name = item["name"]
                values = item.get("values", [{}])
                result[name] = values[0].get("value", 0) if values else item.get("value", 0)
            return result
        except HTTPError as e:
            try:
                detail = e.response.json().get("error", {})
                print(f"  Insights error for {media_id} ({media_type}): [{detail.get('code')}] {detail.get('message')}")
            except Exception:
                print(f"  Insights error for {media_id} ({media_type}): {e.response.status_code} {e.response.text[:200]}")
            return {}
        except Exception as e:
            print(f"  Insights error for {media_id} ({media_type}): {e}")
            return {}

    def get_account_insights(self):
        try:
            data = self._get(f"{self.user_id}/insights", {
                "metric": "reach,impressions,profile_views",
                "period": "week",
            })
            result = {}
            for metric in data.get("data", []):
                vals = metric.get("values", [])
                if vals:
                    result[metric["name"]] = vals[-1]["value"]
            return result
        except HTTPError as e:
            try:
                detail = e.response.json().get("error", {})
                print(f"  Account insights error: [{detail.get('code')}] {detail.get('message')}")
            except Exception:
                print(f"  Account insights error: {e.response.status_code} {e.response.text[:200]}")
            return {}
        except Exception as e:
            print(f"  Account insights error: {e}")
            return {}

    def get_follower_count(self):
        try:
            data = self._get(self.user_id, {"fields": "followers_count"})
            return data.get("followers_count", 0)
        except Exception as e:
            print(f"  Follower count error: {e}")
            return 0
