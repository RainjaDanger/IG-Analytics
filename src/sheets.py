import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

POST_SHEET = "Post Performance"
WEEKLY_SHEET = "Weekly Metrics"

POST_HEADERS = [
    "Post URL", "Likes", "Comments", "Views", "Reach", "Saves",
    "Type", "Media URL", "Thumbnail URL", "Post Date", "Caption",
    "Thumbnail", "Days Live", "Score", "Hook", "Format"
]

WEEKLY_HEADERS = [
    "Week Start", "Week End", "Total Reach (Impressions)", "Organic Reach",
    "Profile Visits", "Followers", "Net New Follows"
]


class SheetsClient:
    def __init__(self, creds_json, spreadsheet_id):
        creds = Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
        self.service = build("sheets", "v4", credentials=creds)
        self.ss_id = spreadsheet_id
        self.sheets = self.service.spreadsheets()

    def _existing_sheet_names(self):
        meta = self.sheets.get(spreadsheetId=self.ss_id).execute()
        return {s["properties"]["title"] for s in meta["sheets"]}

    def ensure_sheets_exist(self):
        existing = self._existing_sheet_names()
        requests = []

        for name in [POST_SHEET, WEEKLY_SHEET]:
            if name not in existing:
                requests.append({"addSheet": {"properties": {"title": name}}})

        if requests:
            self.sheets.batchUpdate(
                spreadsheetId=self.ss_id,
                body={"requests": requests}
            ).execute()
            print(f"  Created new tabs: {[r['addSheet']['properties']['title'] for r in requests]}")

        # Add headers if rows are empty
        for name, headers in [(POST_SHEET, POST_HEADERS), (WEEKLY_SHEET, WEEKLY_HEADERS)]:
            result = self.sheets.values().get(
                spreadsheetId=self.ss_id,
                range=f"'{name}'!A1:A1"
            ).execute()
            if not result.get("values"):
                self.sheets.values().update(
                    spreadsheetId=self.ss_id,
                    range=f"'{name}'!A1",
                    valueInputOption="USER_ENTERED",
                    body={"values": [headers]}
                ).execute()
                print(f"  Added headers to '{name}'")

    def get_existing_post_urls(self):
        """Returns {permalink: row_number} for all existing posts."""
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
                # Update only API-sourced columns (A:K), never touch Hook/Format (O:P)
                updates.append({
                    "range": f"'{POST_SHEET}'!A{row_num}:K{row_num}",
                    "values": [data_cols]
                })
            else:
                row_num = next_row
                next_row += 1
                formula_cols = [
                    f"=IMAGE(I{row_num})",
                    f"=MAX(1,INT(TODAY()-J{row_num}))",
                    f'=IFERROR(((B{row_num}*10)+(C{row_num}*20)+D{row_num})/N{row_num},"")',
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
        ]]

        range_str = (
            f"'{WEEKLY_SHEET}'!A{target_row}:G{target_row}"
            if target_row
            else f"'{WEEKLY_SHEET}'!A{len(rows) + 1}:G{len(rows) + 1}"
        )

        self.sheets.values().update(
            spreadsheetId=self.ss_id,
            range=range_str,
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()
