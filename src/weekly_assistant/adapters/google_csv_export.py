import ssl
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def export_url(spreadsheet_id: str, gid: str) -> str:
    query = urlencode({"tqx": "out:csv", "gid": gid})
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?{query}"


def download_sheet_csv(spreadsheet_id: str, gid: str, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = export_url(spreadsheet_id, gid)
    req = Request(url, headers={"User-Agent": "python-urllib"})
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=30, context=ctx) as response:
        body = response.read()
    path.write_bytes(body)
    return path
