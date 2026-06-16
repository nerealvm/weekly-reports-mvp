import importlib.util
from urllib.parse import quote

from weekly_assistant.config.settings import Settings
from weekly_assistant.integrations.base import IntegrationStatus
from weekly_assistant.utils.http import JsonHttpClient


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class GoogleSheetsAdapter:
    name = "google_sheets"

    def __init__(self, settings: Settings, http_client: JsonHttpClient | None = None):
        self.settings = settings
        self.http_client = http_client or JsonHttpClient()

    def status(self) -> IntegrationStatus:
        missing = []
        if not self.settings.google_sheets_spreadsheet_id:
            missing.append("GOOGLE_SHEETS_SPREADSHEET_ID")
        has_direct_token = bool(self.settings.google_oauth_access_token)
        has_service_account = bool(self.settings.google_application_credentials) and _google_auth_available()
        if not has_direct_token and not has_service_account:
            missing.append("GOOGLE_OAUTH_ACCESS_TOKEN or GOOGLE_APPLICATION_CREDENTIALS+google-auth")
        return IntegrationStatus(
            name=self.name,
            configured=not missing,
            mode="read/write sheet adapter",
            missing=tuple(missing),
            notes="Uses Sheets values API and batchUpdate; service accounts require optional google-auth extra.",
        )

    def read_values(self, a1_range: str) -> list[list[str]]:
        url = self._values_url(a1_range)
        response = self.http_client.request("GET", url, headers=self._auth_headers())
        return response.get("values", [])

    def update_values(self, a1_range: str, values: list[list[str]], value_input_option: str = "USER_ENTERED") -> dict:
        url = f"{self._values_url(a1_range)}?valueInputOption={quote(value_input_option)}"
        payload = {"range": a1_range, "majorDimension": "ROWS", "values": values}
        return self.http_client.request("PUT", url, headers=self._auth_headers(), payload=payload)

    def append_values(
        self,
        a1_range: str,
        values: list[list[str]],
        value_input_option: str = "USER_ENTERED",
        insert_data_option: str = "INSERT_ROWS",
    ) -> dict:
        url = (
            f"{self._values_url(a1_range)}:append"
            f"?valueInputOption={quote(value_input_option)}&insertDataOption={quote(insert_data_option)}"
        )
        payload = {"range": a1_range, "majorDimension": "ROWS", "values": values}
        return self.http_client.request("POST", url, headers=self._auth_headers(), payload=payload)

    def spreadsheet_metadata(self, fields: str = "sheets.properties(title,sheetId)") -> dict:
        spreadsheet_id = self.settings.google_sheets_spreadsheet_id
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        return self.http_client.request("GET", url, headers=self._auth_headers(), params={"fields": fields})

    def batch_update(self, requests: list[dict]) -> dict:
        spreadsheet_id = self.settings.google_sheets_spreadsheet_id
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
        return self.http_client.request("POST", url, headers=self._auth_headers(), payload={"requests": requests})

    def sheet_properties(self) -> list[dict]:
        metadata = self.spreadsheet_metadata("sheets.properties(sheetId,title,index)")
        return [sheet.get("properties", {}) for sheet in metadata.get("sheets", [])]

    def duplicate_sheet(self, source_title: str, new_title: str) -> dict:
        properties = self.sheet_properties()
        by_title = {sheet.get("title", ""): sheet for sheet in properties}
        if new_title in by_title:
            return {"skipped": True, "properties": by_title[new_title]}
        source = by_title.get(source_title)
        if not source:
            raise ValueError(f"Source sheet not found: {source_title}")
        result = self.batch_update(
            [
                {
                    "duplicateSheet": {
                        "sourceSheetId": source["sheetId"],
                        "insertSheetIndex": int(source.get("index", 0)) + 1,
                        "newSheetName": new_title,
                    }
                }
            ]
        )
        properties = result.get("replies", [{}])[0].get("duplicateSheet", {}).get("properties", {})
        return {"skipped": False, "properties": properties, "result": result}

    def _values_url(self, a1_range: str) -> str:
        spreadsheet_id = self.settings.google_sheets_spreadsheet_id
        encoded_range = quote(a1_range, safe="")
        return f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _access_token(self) -> str:
        if self.settings.google_oauth_access_token:
            return self.settings.google_oauth_access_token
        if self.settings.google_application_credentials:
            return _service_account_token(self.settings.google_application_credentials)
        raise RuntimeError("Google Sheets integration is not configured.")


def _google_auth_available() -> bool:
    try:
        return importlib.util.find_spec("google.oauth2.service_account") is not None
    except ModuleNotFoundError:
        return False


def _service_account_token(credentials_path: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError("Install optional dependency: pip install -e '.[google]'") from exc

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=[SHEETS_SCOPE],
    )
    credentials.refresh(Request())
    return credentials.token
