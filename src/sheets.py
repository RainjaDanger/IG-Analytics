import json
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

POST_SHEET = "Post Performance"
WEEKLY_SHEET = "Weekly Post Metrics"
STORY_SHEET = "Weekly Story Metrics"
INSIGHTS_SHEET = "AI Insights"

POST_HEADERS = [
    "Post URL", "Likes", "Comments", "Views", "Reach", "Saves",
    "Type", "Media URL", "Thumbnail URL", "Post Date", "Caption",
    "Thumbnail", "Days Live", "Score", "Hook", "Format"
]

WEEKLY_HEADERS = [
    "Week Start", "Week End", "Total Reach", "Organic Reach",
    "Profile Visits", "Followers", "Net New Follows", "Posts Published"
]

STORY_HEADERS = ["Week Start", "Week End", "Story Reach"]

INSIGHTS_HEADERS = ["Week", "Analysis", "Generated At"]


class SheetsClient:
    def __init__(self, creds_json, spreadsheet_id):
        creds = Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
        self.service = build("sheets", "v4", credentials=creds)
        self.ss_id = spreadsheet_id
        self.sheets = self.service.spreadsheets()

    def ensure_sheets_exist(self):
        meta = self.sheets.get(spreadsheetId=self.ss_id).execute()
        existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

        requests = []

        # Rename legacy "Weekly Metrics" tab if it exists
        if "Weekly Metrics" in existing and WEEKLY_SHEET not in existing:
            requests.append({
                "updateSheetProperties": {
                    "properties": {"sheetId": existing["Weekly Metrics"], "title": WEEKLY_SHEET},
                    "fields": "title"
                }
            })
            existing[WEEKLY_SHEET] = existing.pop("Weekly Metrics")
            print(f"  Renamed 'Weekly Metrics' → '{WEEKLY_SHEET}'")

        # Create any missing sheets
        for name in [POST_SHEET, WEEKLY_SHEET, STORY_SHEET, INSIGHTS_SHEET]:
            if name not in existing:
                requests.append({"addSheet": {"properties": {"title": name}}})

        if requests:
            self.sheets.batchUpdate(
                spreadsheetId=self.ss_id, body={"requests": requests}
            ).execute()

        # Ensure correct headers on all sheets
        for name, headers in [
            (POST_SHEET, POST_HEADERS),
            (WEEKLY_SHEET, WEEKLY_HEADERS),
            (STORY_SHEET, STORY_HEADERS),
            (INSIGHTS_SHEET, INSIGHTS_HEADERS),
        ]:
            col_letter = chr(64 + len(headers))
            result = self.sheets.values().get(
                spreadsheetId=self.ss_id,
                range=f"'{name}'!A1:{col_letter}1"
            ).execute()
            current = result.get("values", [[]])[0] if result.get("values") else []
            if current != headers:
                self.sheets.values().update(
                    spreadsheetId=self.ss_id,
                    range=f"'{name}'!A1",
                    valueInputOption="USER_ENTERED",
                    body={"values": [headers]}
                ).execute()
                print(f"  Updated headers on '{name}'")

    def apply_score_formatting(self):
        meta = self.sheets.get(
            spreadsheetId=self.ss_id,
            fields="sheets(properties(sheetId,title),conditionalFormats)"
        ).execute()

        sheet_id = None
        existing_indices = []
        score_col = POST_HEADERS.index("Score")

        for s in meta["sheets"]:
            if s["properties"]["title"] == POST_SHEET:
                sheet_id = s["properties"]["sheetId"]
                for i, rule in enumerate(s.get("conditionalFormats", [])):
                    for r in rule.get("ranges", []):
                        if r.get("startColumnIndex") == score_col:
                            existing_indices.append(i)
                            break
                break

        if sheet_id is None:
            return

        requests = []
        for idx in sorted(existing_indices, reverse=True):
            requests.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": idx}})

        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": score_col,
                        "endColumnIndex": score_col + 1,
                    }],
                    "gradientRule": {
                        "minpoint": {"color": {"red": 1.0, "green": 1.0, "blue": 1.0}, "type": "MIN"},
                        "maxpoint": {"color": {"red": 0.204, "green": 0.659, "blue": 0.325}, "type": "MAX"},
                    }
                },
                "index": 0
            }
        })

        self.sheets.batchUpdate(
            spreadsheetId=self.ss_id, body={"requests": requests}
        ).execute()

    def sort_posts_by_score(self):
        meta = self.sheets.get(
            spreadsheetId=self.ss_id,
            fields="sheets(properties(sheetId,title))"
        ).execute()

        sheet_id = next(
            (s["properties"]["sheetId"] for s in meta["sheets"]
             if s["properties"]["title"] == POST_SHEET), None
        )
        if sheet_id is None:
            return

        score_col = POST_HEADERS.index("Score")
        self.sheets.batchUpdate(
            spreadsheetId=self.ss_id,
            body={"requests": [{
                "sortRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(POST_HEADERS),
                    },
                    "sortSpecs": [{"dimensionIndex": score_col, "sortOrder": "DESCENDING"}]
                }
            }]}
        ).execute()

    def get_existing_post_urls(self):
        result = self.sheets.values().get(
            spreadsheetId=self.ss_id,
            range=f"'{POST_SHEET}'!A:A"
        ).execute()
        rows = result.get("values", [])
        return {row[0]: i + 1 for i, row in enumerate(rows) if i > 0 and row}

    def upsert_posts(self, posts_data):
        existing = self.get_existing_post_urls()

        result = self.sheets.values().get(
            spreadsheetId=self.ss_id,
            range=f"'{POST_SHEET}'!A:A"
        ).execute()
        next_row = len(result.get("values", [])) + 1

        updates = []

        for post in posts_data:
            url = post["permalink"]
            data_cols = [
                url,
                post["likes"],
                post["comments"],
                post["views"],
                post["reach"],
                post["saves"],
                post["media_type"],
                post["media_url"],
                post["thumb_url"],
                post["post_date"],
                post["caption"],
            ]

            if url in existing:
                row_num = existing[url]
            else:
                row_num = next_row
                next_row += 1

            formula_cols = [
                f"=IMAGE(I{row_num})",
                f"=MAX(1,INT(TODAY()-DATEVALUE(J{row_num})))",
                f'=IFERROR(((B{row_num}*10)+(C{row_num}*20)+D{row_num})/M{row_num},"")',
            ]
            updates.append({
                "range": f"'{POST_SHEET}'!A{row_num}:N{row_num}",
                "values": [data_cols + formula_cols]
            })

        if updates:
            self.sheets.values().batchUpdate(
                spreadsheetId=self.ss_id,
                body={"valueInputOption": "USER_ENTERED", "data": updates}
            ).execute()

        added = sum(1 for p in posts_data if p["permalink"] not in existing)
        updated = len(posts_data) - added
        return added, updated

    def get_previous_follower_count(self, week_start_date):
        result = self.sheets.values().get(
            spreadsheetId=self.ss_id,
            range=f"'{WEEKLY_SHEET}'!A:H"
        ).execute()
        rows = result.get("values", [])
        best_date = None
        best_followers = None
        for row in rows[1:]:
            if not row or len(row) < 6:
                continue
            row_date = self._parse_week_date(row[0])
            row_as_date = row_date.date()
            if row_as_date >= week_start_date:
                continue
            if best_date is None or row_as_date > best_date:
                try:
                    best_date = row_as_date
                    best_followers = int(row[5])
                except (ValueError, IndexError):
                    pass
        return best_followers

    def upsert_weekly_row(self, week_data):
        result = self.sheets.values().get(
            spreadsheetId=self.ss_id,
            range=f"'{WEEKLY_SHEET}'!A:A"
        ).execute()
        rows = result.get("values", [])

        target_row = None
        for i, row in enumerate(rows):
            if i == 0:
                continue
            if row and row[0] == week_data["week_start"]:
                target_row = i + 1
                break

        values = [[
            week_data["week_start"],
            week_data["week_end"],
            week_data["reach_total"],
            week_data["organic_reach"],
            week_data["profile_views"],
            week_data["followers"],
            week_data["net_follows"],
            week_data["posts_published"],
        ]]

        range_str = (
            f"'{WEEKLY_SHEET}'!A{target_row}:H{target_row}"
            if target_row
            else f"'{WEEKLY_SHEET}'!A{len(rows) + 1}:H{len(rows) + 1}"
        )

        self.sheets.values().update(
            spreadsheetId=self.ss_id,
            range=range_str,
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()

    def upsert_weekly_story_row(self, story_data):
        result = self.sheets.values().get(
            spreadsheetId=self.ss_id,
            range=f"'{STORY_SHEET}'!A:A"
        ).execute()
        rows = result.get("values", [])

        target_row = None
        for i, row in enumerate(rows):
            if i == 0:
                continue
            if row and row[0] == story_data["week_start"]:
                target_row = i + 1
                break

        values = [[
            story_data["week_start"],
            story_data["week_end"],
            story_data["story_reach"],
        ]]

        range_str = (
            f"'{STORY_SHEET}'!A{target_row}:C{target_row}"
            if target_row
            else f"'{STORY_SHEET}'!A{len(rows) + 1}:C{len(rows) + 1}"
        )

        self.sheets.values().update(
            spreadsheetId=self.ss_id,
            range=range_str,
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()

    def _parse_week_date(self, week_str):
        """Parse 'Mon-DD' (e.g. 'Apr-27') to a datetime, handling year-wrap."""
        try:
            d = datetime.strptime(week_str, "%b-%d").replace(year=datetime.now().year)
            if d > datetime.now() + timedelta(days=180):
                d = d.replace(year=d.year - 1)
            return d
        except Exception:
            return datetime.min

    def sort_weekly_by_date(self):
        meta = self.sheets.get(
            spreadsheetId=self.ss_id,
            fields="sheets(properties(sheetId,title))"
        ).execute()
        sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

        requests = []
        for sheet_name in [WEEKLY_SHEET, STORY_SHEET]:
            sheet_id = sheet_ids.get(sheet_name)
            if sheet_id is None:
                continue
            requests.append({
                "sortRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 0,
                    },
                    "sortSpecs": [{"dimensionIndex": 0, "sortOrder": "ASCENDING"}]
                }
            })

        if requests:
            self.sheets.batchUpdate(
                spreadsheetId=self.ss_id,
                body={"requests": requests}
            ).execute()

    def write_ai_insights(self, week_start, analysis):
        result = self.sheets.values().get(
            spreadsheetId=self.ss_id,
            range=f"'{INSIGHTS_SHEET}'!A:A"
        ).execute()
        rows = result.get("values", [])

        target_row = None
        for i, row in enumerate(rows):
            if i == 0:
                continue
            if row and row[0] == week_start:
                target_row = i + 1
                break

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        values = [[week_start, analysis, generated_at]]

        range_str = (
            f"'{INSIGHTS_SHEET}'!A{target_row}:C{target_row}"
            if target_row
            else f"'{INSIGHTS_SHEET}'!A{len(rows) + 1}:C{len(rows) + 1}"
        )

        self.sheets.values().update(
            spreadsheetId=self.ss_id,
            range=range_str,
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()
