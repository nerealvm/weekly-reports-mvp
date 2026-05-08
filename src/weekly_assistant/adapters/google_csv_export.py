from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def export_url(spreadsheet_id: str, gid: str) -> str:
    query = urlencode({"tqx": "out:csv", "gid": gid})
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?{query}"


def download_sheet_csv(spreadsheet_id: str, gid: str, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = export_url(spreadsheet_id, gid)
    with urlopen(url, timeout=30) as response:
        body = response.read()
    path.write_bytes(body)
    return path
