import argparse
import csv
import json
import re
import tempfile
import threading
import uuid
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from weekly_assistant.adapters.csv_adapter import load_weekly_rows
from weekly_assistant.config.settings import Settings, load_settings
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter
from weekly_assistant.services.weekly_view_model import build_weekly_view_model, view_model_to_dict
from weekly_assistant.utils.http import JsonHttpError


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
FEEDBACK_DIR = Path("exports/viewer_feedback")
FEEDBACK_SHEET_NAME = "Weekly Feedback"
FEEDBACK_HEADERS = [
    "comment_id",
    "created_at",
    "week_label",
    "source_sheet",
    "topic_id",
    "topic_title",
    "field",
    "field_label",
    "comment",
    "author",
    "status",
]
COMMENT_FIELD_LABELS = {
    "topic": "Тема",
    "result": "Результат",
    "milestones": "Вехи",
    "ball_side": "Мяч",
    "open_question": "Вопрос к Евгению",
}


class WeeklyViewerStore:
    def __init__(
        self,
        settings: Settings,
        *,
        sheet_name: str,
        week_label: str,
        feedback_path: Path | None = None,
        sheets_adapter_factory: Callable[[Settings], GoogleSheetsAdapter] | None = None,
        async_sheet_sync: bool = True,
    ):
        self.settings = settings
        self.sheet_name = sheet_name
        self.week_label = week_label
        self.feedback_path = feedback_path or FEEDBACK_DIR / f"{_safe_filename(sheet_name)}.json"
        self._feedback_lock = threading.Lock()
        self._sheets_adapter_factory = sheets_adapter_factory or GoogleSheetsAdapter
        self._async_sheet_sync = async_sheet_sync

    def report(self) -> dict:
        adapter = self._sheets_adapter_factory(self.settings)
        values = adapter.read_values(f"'{self.sheet_name}'!A:T")
        rows = _load_rows_from_values(values)
        model = build_weekly_view_model(rows, week_label=self.week_label, source_sheet=self.sheet_name)
        payload = view_model_to_dict(model)
        payload["feedback"] = self.feedback()
        return payload

    def feedback(self) -> dict:
        with self._feedback_lock:
            local_comments = self._read_comments_unlocked()
        sheet_comments, sheet_status = self._read_sheet_comments()
        return _feedback_summary(_merge_comments(sheet_comments, local_comments), sheet=sheet_status)

    def add_comment(self, payload: dict) -> dict:
        topic_id = _clean_text(payload.get("topic_id", ""))
        topic_title = _clean_text(payload.get("topic_title", ""))
        field = _clean_text(payload.get("field", ""))
        text = _clean_text(payload.get("text", ""))
        author = _clean_text(payload.get("author", "")) or "Евгений"
        if not topic_id:
            raise ValueError("topic_id is required")
        if field not in COMMENT_FIELD_LABELS:
            raise ValueError("field is invalid")
        if not text:
            raise ValueError("comment text is required")

        comment = {
            "id": f"C-{uuid.uuid4().hex[:10]}",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "topic_id": topic_id,
            "topic_title": topic_title,
            "field": field,
            "field_label": COMMENT_FIELD_LABELS[field],
            "text": text,
            "author": author,
            "status": "open",
            "sheet_sync": "pending",
        }
        with self._feedback_lock:
            comments = self._read_comments_unlocked()
            comments.append(comment)
            self._write_comments_unlocked(comments)

        if self._async_sheet_sync:
            sheet_status = {"status": "queued", "sheet_name": FEEDBACK_SHEET_NAME}
            self._start_sheet_sync(comment["id"])
        else:
            sheet_status = self._sync_comment_to_sheet(comment["id"])
            with self._feedback_lock:
                comment = _find_comment(self._read_comments_unlocked(), comment["id"]) or comment
        with self._feedback_lock:
            response_comments = self._read_comments_unlocked()
        return {"comment": comment, "feedback": _feedback_summary(response_comments, sheet=sheet_status), "sheet": sheet_status}

    def _read_comments_unlocked(self) -> list[dict]:
        if not self.feedback_path.exists():
            return []
        with self.feedback_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        comments = payload.get("comments", []) if isinstance(payload, dict) else payload
        if not isinstance(comments, list):
            return []
        return [comment for comment in comments if isinstance(comment, dict)]

    def _write_comments_unlocked(self, comments: list[dict]) -> None:
        self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sheet_name": self.sheet_name,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "comments": comments,
        }
        tmp_path = self.feedback_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        tmp_path.replace(self.feedback_path)

    def _read_sheet_comments(self) -> tuple[list[dict], dict]:
        try:
            values = self._sheets_adapter_factory(self.settings).read_values(_sheet_range(FEEDBACK_SHEET_NAME, "A:K"))
        except JsonHttpError as exc:
            return [], {"status": "unavailable", "sheet_name": FEEDBACK_SHEET_NAME, "message": _short_error(exc)}
        except Exception as exc:
            return [], {"status": "unavailable", "sheet_name": FEEDBACK_SHEET_NAME, "message": str(exc)}
        return _comments_from_sheet_values(values), {"status": "available", "sheet_name": FEEDBACK_SHEET_NAME}

    def _append_comment_to_sheet(self, comment: dict) -> dict:
        try:
            adapter = self._sheets_adapter_factory(self.settings)
            self._ensure_feedback_sheet(adapter)
            adapter.append_values(_sheet_range(FEEDBACK_SHEET_NAME, "A:K"), [_comment_to_sheet_row(comment, self.week_label, self.sheet_name)])
            return {"status": "saved", "sheet_name": FEEDBACK_SHEET_NAME}
        except JsonHttpError as exc:
            return {"status": "failed", "sheet_name": FEEDBACK_SHEET_NAME, "message": _short_error(exc)}
        except Exception as exc:
            return {"status": "failed", "sheet_name": FEEDBACK_SHEET_NAME, "message": str(exc)}

    def _start_sheet_sync(self, comment_id: str) -> None:
        thread = threading.Thread(target=self._sync_comment_to_sheet, args=(comment_id,), daemon=True)
        thread.start()

    def _sync_comment_to_sheet(self, comment_id: str) -> dict:
        with self._feedback_lock:
            comment = _find_comment(self._read_comments_unlocked(), comment_id)
        if not comment:
            return {"status": "failed", "sheet_name": FEEDBACK_SHEET_NAME, "message": "comment not found"}

        sheet_status = self._append_comment_to_sheet(comment)
        updated_comment = {
            **comment,
            "sheet_sync": sheet_status["status"],
        }
        if sheet_status.get("message"):
            updated_comment["sheet_error"] = sheet_status["message"]
        else:
            updated_comment.pop("sheet_error", None)
        with self._feedback_lock:
            comments = _replace_comment(self._read_comments_unlocked(), updated_comment)
            self._write_comments_unlocked(comments)
        return sheet_status

    def _ensure_feedback_sheet(self, adapter: GoogleSheetsAdapter) -> None:
        metadata = adapter.spreadsheet_metadata()
        sheet_titles = {sheet.get("properties", {}).get("title") for sheet in metadata.get("sheets", [])}
        if FEEDBACK_SHEET_NAME not in sheet_titles:
            adapter.batch_update([{"addSheet": {"properties": {"title": FEEDBACK_SHEET_NAME}}}])
            adapter.update_values(_sheet_range(FEEDBACK_SHEET_NAME, "A1:K1"), [FEEDBACK_HEADERS])
            return
        header = adapter.read_values(_sheet_range(FEEDBACK_SHEET_NAME, "A1:K1"))
        if not header or header[0][: len(FEEDBACK_HEADERS)] != FEEDBACK_HEADERS:
            adapter.update_values(_sheet_range(FEEDBACK_SHEET_NAME, "A1:K1"), [FEEDBACK_HEADERS])


class WeeklyViewerHandler(BaseHTTPRequestHandler):
    store: WeeklyViewerStore

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send_text(INDEX_HTML, "text/html; charset=utf-8")
            elif path == "/viewer.css":
                self._send_text(APP_CSS, "text/css; charset=utf-8")
            elif path == "/viewer.js":
                self._send_text(APP_JS, "application/javascript; charset=utf-8")
            elif path == "/api/report":
                self._send_json(self.store.report())
            elif path == "/api/feedback":
                self._send_json(self.store.feedback())
            else:
                self.send_error(404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/comment":
                self._send_json(self.store.add_comment(self._read_json()))
            else:
                self.send_error(404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def log_message(self, format: str, *args) -> None:
        print(f"[viewer] {self.address_string()} - {format % args}")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekly-viewer")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sheet-name", default="")
    parser.add_argument("--week-label", default="")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    sheet_name = args.sheet_name or settings.google_sheets_weekly_sheet_name
    week_label = args.week_label or _week_label_from_sheet_name(sheet_name) or _default_week_label()
    store = WeeklyViewerStore(settings, sheet_name=sheet_name, week_label=week_label)
    handler = type("ConfiguredWeeklyViewerHandler", (WeeklyViewerHandler,), {"store": store})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Weekly viewer running: {url}")
    print(f"Source sheet: {sheet_name}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping weekly viewer.")
    return 0


def _load_rows_from_values(values: list[list[str]]):
    if not values:
        return []
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix=".csv", delete=False) as file:
        writer = csv.writer(file)
        writer.writerows(values)
        path = Path(file.name)
    try:
        return load_weekly_rows(path)
    finally:
        path.unlink(missing_ok=True)


def _default_week_label() -> str:
    return date.today().strftime("%d.%m.%Y")


def _week_label_from_sheet_name(sheet_name: str) -> str:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", sheet_name)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{day}.{month}.{year}"


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", value.strip())
    return text.strip("._") or "weekly_viewer_feedback"


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _feedback_summary(comments: list[dict], sheet: dict | None = None) -> dict:
    open_comments = [comment for comment in comments if comment.get("status", "open") == "open"]
    by_key: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    for comment in open_comments:
        topic_id = str(comment.get("topic_id", ""))
        field = str(comment.get("field", ""))
        if not topic_id or not field:
            continue
        by_key[f"{topic_id}:{field}"] = by_key.get(f"{topic_id}:{field}", 0) + 1
        by_topic[topic_id] = by_topic.get(topic_id, 0) + 1
    return {
        "count": len(open_comments),
        "comments": open_comments,
        "by_key": by_key,
        "by_topic": by_topic,
        "sheet": sheet or {},
    }


def _merge_comments(*comment_groups: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    anonymous: list[dict] = []
    for comments in comment_groups:
        for comment in comments:
            comment_id = str(comment.get("id") or "")
            if not comment_id:
                anonymous.append(comment)
                continue
            merged[comment_id] = {**merged.get(comment_id, {}), **comment}
    return sorted([*anonymous, *merged.values()], key=lambda item: str(item.get("created_at", "")))


def _replace_comment(comments: list[dict], replacement: dict) -> list[dict]:
    comment_id = replacement.get("id")
    replaced = False
    next_comments = []
    for comment in comments:
        if comment.get("id") == comment_id:
            next_comments.append(replacement)
            replaced = True
        else:
            next_comments.append(comment)
    if not replaced:
        next_comments.append(replacement)
    return next_comments


def _find_comment(comments: list[dict], comment_id: str) -> dict | None:
    for comment in comments:
        if comment.get("id") == comment_id:
            return comment
    return None


def _sheet_range(sheet_name: str, cells: str) -> str:
    escaped_name = sheet_name.replace("'", "''")
    return f"'{escaped_name}'!{cells}"


def _comment_to_sheet_row(comment: dict, week_label: str, source_sheet: str) -> list[str]:
    return [
        str(comment.get("id", "")),
        str(comment.get("created_at", "")),
        week_label,
        source_sheet,
        str(comment.get("topic_id", "")),
        str(comment.get("topic_title", "")),
        str(comment.get("field", "")),
        str(comment.get("field_label", "")),
        str(comment.get("text", "")),
        str(comment.get("author", "")),
        str(comment.get("status", "open")),
    ]


def _comments_from_sheet_values(values: list[list[str]]) -> list[dict]:
    if len(values) <= 1:
        return []
    header = values[0]
    index = {name: header.index(name) for name in FEEDBACK_HEADERS if name in header}
    comments = []
    for row in values[1:]:
        comment_id = _row_value(row, index, "comment_id")
        text = _row_value(row, index, "comment")
        topic_id = _row_value(row, index, "topic_id")
        field = _row_value(row, index, "field")
        if not comment_id or not text or not topic_id or not field:
            continue
        comments.append(
            {
                "id": comment_id,
                "created_at": _row_value(row, index, "created_at"),
                "week_label": _row_value(row, index, "week_label"),
                "source_sheet": _row_value(row, index, "source_sheet"),
                "topic_id": topic_id,
                "topic_title": _row_value(row, index, "topic_title"),
                "field": field,
                "field_label": _row_value(row, index, "field_label") or COMMENT_FIELD_LABELS.get(field, field),
                "text": text,
                "author": _row_value(row, index, "author") or "Евгений",
                "status": _row_value(row, index, "status") or "open",
                "sheet_sync": "saved",
            }
        )
    return comments


def _row_value(row: list[str], index: dict[str, int], name: str) -> str:
    position = index.get(name)
    if position is None or position >= len(row):
        return ""
    return str(row[position]).strip()


def _short_error(exc: JsonHttpError) -> str:
    body = exc.body.replace("\n", " ")
    return f"status={exc.status}: {body[:220]}"


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Weekly для Евгения</title>
  <link rel="stylesheet" href="/viewer.css">
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <h1>Weekly для Евгения</h1>
        <p id="subtitle">Загрузка отчета...</p>
      </div>
      <div class="actions">
        <button id="copyQuestionsBtn" type="button">Вопросы</button>
        <button id="copySyncBtn" type="button">Sync</button>
        <button id="copyFeedbackBtn" type="button">Комментарии</button>
        <button id="refreshBtn" type="button">Обновить</button>
      </div>
    </header>

    <section id="metrics" class="metrics" aria-label="Сводка"></section>

    <section class="controls" aria-label="Фильтры отчета">
      <div class="tabs" id="tabs">
        <button class="active" data-filter="all" type="button">Все</button>
        <button data-filter="sync" type="button">Нужен sync</button>
        <button data-filter="questions" type="button">Вопросы</button>
        <button data-filter="movement" type="button">Есть движение</button>
        <button data-filter="no_movement" type="button">Без движения</button>
      </div>
      <div class="search-wrap">
        <input id="search" placeholder="Поиск по теме, результату, вехе">
      </div>
    </section>

    <section class="content">
      <aside class="summary-panel">
        <h2>Что требует внимания</h2>
        <div id="attentionList" class="attention-list"></div>
      </aside>
      <section class="report-list" id="reportList" aria-label="Строки weekly"></section>
    </section>
  </main>
  <div id="commentModal" class="modal hidden" role="dialog" aria-modal="true" aria-labelledby="commentTitle">
    <div class="modal-backdrop" data-close-comment></div>
    <section class="modal-card">
      <header class="modal-head">
        <div>
          <h2 id="commentTitle">Комментарий</h2>
          <p id="commentContext">—</p>
        </div>
        <button id="commentCloseBtn" type="button">Закрыть</button>
      </header>
      <div id="existingComments" class="existing-comments"></div>
      <div class="comment-tools">
        <button id="voiceCommentBtn" class="voice-btn" type="button">
          <span class="mic-icon" aria-hidden="true"></span>
          <span id="voiceCommentLabel">Голосом</span>
        </button>
        <span id="voiceStatus" class="voice-status"></span>
      </div>
      <textarea id="commentText" rows="5" placeholder="Комментарий или вопрос по этому полю"></textarea>
      <footer class="modal-actions">
        <button id="commentCancelBtn" type="button">Отмена</button>
        <button id="commentSaveBtn" class="primary" type="button">Сохранить</button>
      </footer>
    </section>
  </div>
  <div id="toast" class="toast" role="status"></div>
  <script src="/viewer.js"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  color-scheme: light;
  --bg: #f4f5f7;
  --surface: #ffffff;
  --surface-soft: #fafafa;
  --ink: #1f2328;
  --muted: #667085;
  --line: #d6dbe3;
  --accent: #1f6feb;
  --accent-soft: #e8f1ff;
  --ok: #1a7f37;
  --warn: #9a6700;
  --danger: #b42318;
  --shadow: 0 14px 34px rgba(31, 35, 40, .08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.48 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
button, input { font: inherit; }
.shell {
  width: min(1360px, calc(100% - 32px));
  margin: 0 auto;
  padding: 24px 0 42px;
}
.topbar {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  padding: 18px 0 16px;
}
h1 { margin: 0; font-size: 29px; line-height: 1.1; letter-spacing: 0; }
#subtitle { margin: 7px 0 0; color: var(--muted); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
button {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  min-height: 34px;
  padding: 0 12px;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 650;
}
button:hover { border-color: #9aa4b2; }
.metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin: 8px 0 14px;
}
.metric {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  text-align: left;
  cursor: pointer;
  min-height: 104px;
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.metric:hover {
  border-color: #9aa4b2;
  box-shadow: 0 8px 20px rgba(31, 35, 40, .08);
  transform: translateY(-1px);
}
.metric.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.metric strong { display: block; font-size: 22px; line-height: 1.1; }
.metric span { display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }
.controls {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 8px;
  padding: 10px;
  position: sticky;
  top: 0;
  z-index: 2;
  box-shadow: var(--shadow);
}
.tabs { display: flex; flex-wrap: wrap; gap: 6px; }
.tabs button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.search-wrap { width: min(360px, 100%); }
.search-wrap input {
  width: 100%;
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0 10px;
  background: #fff;
}
.content {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 14px;
  margin-top: 14px;
  align-items: start;
}
.summary-panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 92px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.summary-panel h2 { margin: 0 0 12px; font-size: 16px; line-height: 1.2; }
.attention-list {
  display: grid;
  gap: 8px;
  overflow-y: auto;
  min-height: 0;
  overscroll-behavior: contain;
  padding-right: 4px;
}
.attention-item {
  display: block;
  width: 100%;
  border-left: 3px solid var(--accent);
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
  padding: 8px 8px 8px 10px;
  background: var(--surface-soft);
  border-radius: 6px;
  color: var(--ink);
  text-align: left;
  min-height: 0;
  font-weight: 400;
  cursor: pointer;
}
.attention-item:hover {
  background: var(--accent-soft);
  border-left-color: var(--accent);
}
.attention-item strong {
  display: -webkit-box;
  overflow: hidden;
  font-size: 13px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.attention-item span { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; }
.report-list { display: grid; gap: 9px; }
.topic-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 13px 14px;
  scroll-margin-top: 76px;
  transition: box-shadow .18s ease, border-color .18s ease, background .18s ease;
}
.topic-card.focused {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow);
  background: #fbfdff;
}
.topic-card.sync { border-left: 4px solid var(--warn); }
.topic-card.question { border-left: 4px solid var(--accent); }
.topic-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
}
.topic-main {
  border-radius: 6px;
  padding: 3px;
  margin: -3px;
}
.topic-title { margin: 0; font-size: 16px; line-height: 1.25; }
.topic-meta { margin-top: 4px; color: var(--muted); font-size: 12px; }
.badges { display: flex; flex-wrap: wrap; gap: 5px; justify-content: flex-end; }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.badge.sync { color: var(--warn); border-color: #e6c76e; background: #fff8df; }
.badge.real_result { color: var(--ok); border-color: #b7dfc0; background: #eefaf1; }
.badge.no_movement { color: var(--muted); background: #f7f8fa; }
.badge.unclear { color: var(--danger); border-color: #f0bbb4; background: #fff1ef; }
.field-block {
  margin: 10px 0 11px;
  border-radius: 6px;
  padding: 2px 0;
}
.field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.field-head label {
  display: block;
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .03em;
}
.result {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
}
.details {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(160px, .65fr);
  gap: 8px;
}
.detail {
  background: var(--surface-soft);
  border: 1px solid #eceff3;
  border-radius: 6px;
  padding: 9px;
}
.detail div { white-space: pre-wrap; overflow-wrap: anywhere; }
.question-block {
  margin-top: 9px;
  border: 1px solid #c8d7ff;
  background: var(--accent-soft);
  border-radius: 6px;
  padding: 9px;
}
.question-block .field-head label { color: #254d9c; }
.comment-target {
  cursor: pointer;
  transition: background .15s ease, box-shadow .15s ease;
}
.comment-target:hover {
  box-shadow: inset 0 0 0 1px rgba(31, 111, 235, .28);
}
.comment-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 26px;
  min-width: 34px;
  padding: 0 8px;
  border-color: #c8d7ff;
  color: #254d9c;
  background: #f8fbff;
  font-size: 12px;
  font-weight: 650;
}
.comment-icon {
  position: relative;
  display: inline-block;
  width: 16px;
  height: 13px;
  border: 1.8px solid currentColor;
  border-radius: 5px;
  background: #f8fbff;
}
.comment-icon::before {
  content: "";
  position: absolute;
  left: 3.5px;
  top: 3.5px;
  width: 7px;
  height: 1.6px;
  border-radius: 2px;
  background: currentColor;
  box-shadow: 0 4px 0 currentColor;
  opacity: .72;
}
.comment-icon::after {
  content: "";
  position: absolute;
  right: 2px;
  bottom: -5px;
  width: 7px;
  height: 7px;
  border-right: 1.8px solid currentColor;
  border-bottom: 1.8px solid currentColor;
  background: #f8fbff;
  transform: rotate(35deg);
}
.comment-count {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  min-width: 18px;
  min-height: 18px;
  margin-left: 6px;
  border-radius: 99px;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
}
.comment-list {
  display: grid;
  gap: 5px;
  margin-top: 8px;
}
.comment-item {
  border: 1px solid #c8d7ff;
  background: #f8fbff;
  border-radius: 6px;
  padding: 7px 8px;
  color: #254d9c;
  font-size: 12px;
}
.comment-item strong {
  display: block;
  color: #254d9c;
  font-size: 11px;
  margin-bottom: 2px;
}
.comment-item span { display: block; color: var(--ink); }
.empty-state {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 28px;
  color: var(--muted);
  text-align: center;
}
.toast {
  position: fixed;
  right: 18px;
  bottom: 18px;
  background: #1f2328;
  color: #fff;
  border-radius: 6px;
  padding: 9px 12px;
  opacity: 0;
  transform: translateY(8px);
  transition: .18s ease;
  pointer-events: none;
}
.toast.visible { opacity: 1; transform: translateY(0); }
.modal.hidden { display: none; }
.modal {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: grid;
  place-items: center;
  padding: 20px;
}
.modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(31, 35, 40, .42);
}
.modal-card {
  position: relative;
  width: min(560px, 100%);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 24px 70px rgba(31, 35, 40, .22);
  padding: 16px;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: start;
  margin-bottom: 12px;
}
.modal-head h2 {
  margin: 0;
  font-size: 18px;
  line-height: 1.2;
}
.modal-head p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.modal-card textarea {
  width: 100%;
  resize: vertical;
  min-height: 118px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
}
.comment-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.voice-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.voice-btn.listening {
  color: #b42318;
  border-color: #f0bbb4;
  background: #fff1ef;
}
.voice-btn:disabled { opacity: .55; cursor: not-allowed; }
.mic-icon {
  position: relative;
  display: inline-block;
  width: 12px;
  height: 16px;
  border: 1.8px solid currentColor;
  border-radius: 7px 7px 6px 6px;
}
.mic-icon::before {
  content: "";
  position: absolute;
  left: 50%;
  bottom: -7px;
  width: 1.8px;
  height: 6px;
  background: currentColor;
  transform: translateX(-50%);
}
.mic-icon::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: -9px;
  width: 12px;
  height: 1.8px;
  border-radius: 2px;
  background: currentColor;
  transform: translateX(-50%);
}
.voice-status {
  color: var(--muted);
  font-size: 12px;
}
.existing-comments {
  display: grid;
  gap: 6px;
  margin-bottom: 10px;
}
.existing-comments:empty { display: none; }
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
button.saving { cursor: wait; opacity: .78; }
.spinner {
  display: inline-block;
  width: 13px;
  height: 13px;
  margin-right: 7px;
  border: 2px solid rgba(255, 255, 255, .55);
  border-top-color: #fff;
  border-radius: 50%;
  vertical-align: -2px;
  animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 980px) {
  .shell { width: min(100% - 20px, 760px); padding-top: 12px; }
  .topbar { display: block; }
  .actions { justify-content: flex-start; margin-top: 12px; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .controls { display: block; position: static; }
  .search-wrap { width: 100%; margin-top: 10px; }
  .content { grid-template-columns: 1fr; }
  .summary-panel { position: static; max-height: 360px; }
  .topic-head, .details { grid-template-columns: 1fr; }
  .badges { justify-content: flex-start; }
}
"""


APP_JS = """
let report = null;
let filter = "all";
let query = "";
let pendingComment = null;
let commentSaveInProgress = false;
let voiceRecognition = null;
let voiceListening = false;
let voiceBaseText = "";
const sheetSyncWatchers = new Set();

const fieldLabels = {
  topic: "Тема",
  result: "Результат",
  milestones: "Вехи",
  ball_side: "Мяч",
  open_question: "Вопрос к Евгению",
};

const filterButtons = () => Array.from(document.querySelectorAll("[data-filter]"));

async function loadReport() {
  setToast("Обновляю отчет...");
  const response = await fetch(isStaticMode() ? "report.json" : "/api/report", { cache: "no-store" });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  report = data;
  if (isStaticMode()) {
    report.feedback = mergeFeedback(report.feedback, loadStaticFeedback());
  }
  render();
  setToast("Отчет обновлен");
}

function render() {
  document.getElementById("subtitle").textContent = `${report.week_label} · ${report.source_sheet} · ${report.metrics.total_active} active-тем`;
  const feedbackCount = report.feedback?.count || 0;
  document.getElementById("copyFeedbackBtn").textContent = feedbackCount ? `Комментарии (${feedbackCount})` : "Комментарии";
  renderMetrics();
  renderFilterState();
  renderAttention();
  renderItems();
}

function renderMetrics() {
  const metrics = report.metrics;
  const items = [
    ["all", "Active", metrics.total_active],
    ["movement", "С движением", metrics.real_result],
    ["no_movement", "Без движения", metrics.no_movement],
    ["unclear", "Unclear", metrics.unclear],
    ["sync", "Sync", metrics.needs_sync],
    ["questions", "Вопросы", metrics.open_questions],
  ];
  document.getElementById("metrics").innerHTML = items.map(([metricFilter, label, value]) => (
    `<button class="metric${filter === metricFilter ? " active" : ""}" data-metric-filter="${metricFilter}" type="button"><strong>${value}</strong><span>${label}</span></button>`
  )).join("");
  for (const metric of document.querySelectorAll("[data-metric-filter]")) {
    metric.onclick = () => setFilter(metric.dataset.metricFilter);
  }
}

function renderAttention() {
  const actionable = report.items.filter(item => item.needs_sync === "yes" || item.open_question);
  const attention = actionable.filter(item => !hasTopicComment(item.topic_id));
  const container = document.getElementById("attentionList");
  if (!attention.length) {
    const message = actionable.length ? "По всем attention-темам уже есть реакция." : "По текущим правилам sync не требуется.";
    container.innerHTML = `<div class="attention-item"><strong>Нет явных блокеров</strong><span>${message}</span></div>`;
    return;
  }
  container.innerHTML = attention.map(item => `
    <button class="attention-item" data-jump-topic="${escapeHtml(item.topic_id)}" type="button">
      <strong>${escapeHtml(item.topic_title)}</strong>
      <span>${escapeHtml(item.open_question || item.sync_reason || "Нужен sync")}</span>
    </button>
  `).join("");
}

function renderItems() {
  const items = visibleItems();
  const container = document.getElementById("reportList");
  if (!items.length) {
    container.innerHTML = `<div class="empty-state">Нет строк под выбранный фильтр.</div>`;
    return;
  }
  container.innerHTML = items.map(renderItem).join("");
}

function renderItem(item) {
  const className = "topic-card" + (item.needs_sync === "yes" ? " sync" : item.open_question ? " question" : "");
  return `
    <article class="${className}" id="${escapeHtml(topicCardId(item.topic_id))}" data-topic-card="${escapeHtml(item.topic_id)}">
      <header class="topic-head">
        <div class="topic-main comment-target" ${commentAttrs(item, "topic")}>
          <h3 class="topic-title">${escapeHtml(item.topic_title)}</h3>
          <div class="topic-meta">${escapeHtml(item.section)} · ${escapeHtml(item.topic_id)}</div>
        </div>
        <div class="badges">
          ${item.needs_sync === "yes" ? `<span class="badge sync">sync</span>` : ""}
          <span class="badge ${escapeHtml(item.movement_type)}">${movementLabel(item.movement_type)}</span>
          ${item.focus === "yes" ? `<span class="badge">focus</span>` : ""}
          ${commentButton(item, "topic")}
        </div>
      </header>
      ${commentsMarkup(item, "topic")}
      <section class="field-block comment-target" ${commentAttrs(item, "result")}>
        <div class="field-head">
          <label>Результат</label>
          ${commentButton(item, "result")}
        </div>
        <p class="result">${escapeHtml(item.result)}</p>
        ${commentsMarkup(item, "result")}
      </section>
      <div class="details">
        <div class="detail comment-target" ${commentAttrs(item, "milestones")}>
          <div class="field-head">
            <label>Вехи</label>
            ${commentButton(item, "milestones")}
          </div>
          <div>${escapeHtml(item.milestones || "Веха не указана")}</div>
          ${commentsMarkup(item, "milestones")}
        </div>
        <div class="detail comment-target" ${commentAttrs(item, "ball_side")}>
          <div class="field-head">
            <label>Мяч</label>
            ${commentButton(item, "ball_side")}
          </div>
          <div>${escapeHtml(item.ball_side || "Не указан")}</div>
          ${commentsMarkup(item, "ball_side")}
        </div>
      </div>
      ${item.open_question ? `
        <div class="question-block comment-target" ${commentAttrs(item, "open_question")}>
          <div class="field-head">
            <label>Вопрос к Евгению</label>
            ${commentButton(item, "open_question")}
          </div>
          <div>${escapeHtml(item.open_question)}</div>
          ${commentsMarkup(item, "open_question")}
        </div>
      ` : ""}
    </article>
  `;
}

function visibleItems() {
  return report.items.filter(item => {
    if (filter === "sync" && item.needs_sync !== "yes") return false;
    if (filter === "questions" && !item.open_question) return false;
    if (filter === "movement" && item.movement_type !== "real_result") return false;
    if (filter === "no_movement" && item.movement_type !== "no_movement") return false;
    if (filter === "unclear" && item.movement_type !== "unclear") return false;
    if (!query) return true;
    const haystack = [item.topic_title, item.result, item.milestones, item.ball_side, item.open_question, item.sync_reason].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function movementLabel(value) {
  return { real_result: "движение", no_movement: "без движения", unclear: "неясно" }[value] || value;
}

function hasTopicComment(topicId) {
  return (report?.feedback?.by_topic?.[topicId] || 0) > 0;
}

function topicCardId(topicId) {
  return `topic-${String(topicId || "").replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function jumpToTopic(topicId) {
  const searchInput = document.getElementById("search");
  if (filter !== "all" || query) {
    filter = "all";
    query = "";
    searchInput.value = "";
    renderMetrics();
    renderFilterState();
    renderItems();
  }
  window.requestAnimationFrame(() => {
    const card = document.getElementById(topicCardId(topicId));
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.add("focused");
    window.clearTimeout(jumpToTopic.timer);
    jumpToTopic.timer = window.setTimeout(() => card.classList.remove("focused"), 1800);
  });
}

function commentAttrs(item, field) {
  return `data-topic-id="${escapeHtml(item.topic_id)}" data-comment-field="${escapeHtml(field)}"`;
}

function commentButton(item, field) {
  const count = commentCount(item.topic_id, field);
  const label = `Комментарий: ${fieldLabels[field] || field}`;
  return `<button class="comment-btn" ${commentAttrs(item, field)} type="button" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"><span class="comment-icon" aria-hidden="true"></span>${count ? `<span class="comment-count">${count}</span>` : ""}</button>`;
}

function commentCount(topicId, field) {
  return report?.feedback?.by_key?.[`${topicId}:${field}`] || 0;
}

function commentsFor(topicId, field) {
  return (report?.feedback?.comments || []).filter(comment => comment.topic_id === topicId && comment.field === field);
}

function commentsMarkup(item, field) {
  const comments = commentsFor(item.topic_id, field);
  if (!comments.length) return "";
  return `<div class="comment-list">${comments.map(commentMarkup).join("")}</div>`;
}

function commentMarkup(comment) {
  const author = comment.author || "Евгений";
  const createdAt = shortDateTime(comment.created_at);
  const status = comment.sheet_sync === "saved" ? " · записано в таблицу" : comment.sheet_sync === "failed" ? " · не записано в таблицу" : comment.sheet_sync === "local" ? " · сохранено в браузере" : "";
  return `<div class="comment-item"><strong>${escapeHtml(author)}${createdAt ? ` · ${escapeHtml(createdAt)}` : ""}${status}</strong><span>${escapeHtml(comment.text)}</span></div>`;
}

function shortDateTime(value) {
  if (!value) return "";
  const match = String(value).match(/(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}:\\d{2})/);
  if (!match) return String(value);
  return `${match[3]}.${match[2]} ${match[4]}`;
}

function openComment(topicId, field) {
  if (commentSaveInProgress) return;
  const item = report.items.find(row => row.topic_id === topicId);
  if (!item) return;
  pendingComment = { item, field };
  setCommentSaving(false);
  stopVoiceComment(true);
  document.getElementById("commentTitle").textContent = `Комментарий: ${fieldLabels[field] || field}`;
  document.getElementById("commentContext").textContent = `${item.topic_title} · ${item.topic_id}`;
  document.getElementById("existingComments").innerHTML = commentsMarkup(item, field);
  document.getElementById("commentText").value = "";
  document.getElementById("commentModal").classList.remove("hidden");
  document.getElementById("commentText").focus();
}

function closeComment() {
  stopVoiceComment(true);
  pendingComment = null;
  setCommentSaving(false);
  document.getElementById("commentModal").classList.add("hidden");
}

async function saveComment() {
  if (!pendingComment || commentSaveInProgress) return;
  const text = document.getElementById("commentText").value.trim();
  if (!text) {
    setToast("Напиши комментарий");
    return;
  }
  const target = pendingComment;
  commentSaveInProgress = true;
  setCommentSaving(true);
  try {
    if (isStaticMode()) {
      const comment = createLocalComment(target, text);
      report.feedback = mergeFeedback(report.feedback, { comments: [comment] });
      saveStaticFeedback(report.feedback);
      closeComment();
      render();
      setToast("Комментарий сохранен в этом браузере");
      return;
    }
    const response = await fetch("/api/comment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic_id: target.item.topic_id,
        topic_title: target.item.topic_title,
        field: target.field,
        text,
        author: "Евгений",
      }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || response.statusText);
    report.feedback = mergeFeedback(report.feedback, data.feedback);
    closeComment();
    render();
    if (data.sheet?.status === "queued") {
      setToast("Комментарий сохранен. Записываю в таблицу...");
      watchSheetSync(data.comment?.id);
    } else if (data.sheet?.status === "saved") {
      setToast(`Комментарий записан в ${data.sheet.sheet_name}`);
    } else if (data.sheet?.status === "failed") {
      setToast("Комментарий сохранен локально, но не записан в таблицу");
    } else {
      setToast("Комментарий сохранен");
    }
  } finally {
    commentSaveInProgress = false;
    setCommentSaving(false);
  }
}

function setCommentSaving(isSaving) {
  const saveBtn = document.getElementById("commentSaveBtn");
  const cancelBtn = document.getElementById("commentCancelBtn");
  const closeBtn = document.getElementById("commentCloseBtn");
  const voiceBtn = document.getElementById("voiceCommentBtn");
  const textarea = document.getElementById("commentText");
  saveBtn.disabled = isSaving;
  cancelBtn.disabled = isSaving;
  closeBtn.disabled = isSaving;
  textarea.disabled = isSaving;
  voiceBtn.disabled = isSaving || !speechRecognitionCtor();
  saveBtn.classList.toggle("saving", isSaving);
  saveBtn.innerHTML = isSaving ? `<span class="spinner" aria-hidden="true"></span>Сохраняю` : "Сохранить";
}

async function watchSheetSync(commentId) {
  if (!commentId || sheetSyncWatchers.has(commentId)) return;
  sheetSyncWatchers.add(commentId);
  try {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await delay(1200);
      const response = await fetch("/api/feedback");
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      report.feedback = data;
      renderAttention();
      renderItems();
      const comment = (data.comments || []).find(item => item.id === commentId);
      if (!comment || comment.sheet_sync === "pending") continue;
      if (comment.sheet_sync === "saved") {
        setToast("Комментарий записан в Weekly Feedback");
      } else if (comment.sheet_sync === "failed") {
        setToast("Комментарий сохранен локально, но не записан в таблицу");
      }
      return;
    }
  } catch (error) {
    setToast(error.message);
  } finally {
    sheetSyncWatchers.delete(commentId);
  }
}

function delay(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function isStaticMode() {
  return Boolean(window.WEEKLY_VIEWER_STATIC);
}

function staticFeedbackKey() {
  return `weekly-viewer-feedback:${report?.source_sheet || "report"}`;
}

function loadStaticFeedback() {
  try {
    const payload = JSON.parse(window.localStorage.getItem(staticFeedbackKey()) || "{}");
    return buildFeedbackSummary({ comments: Array.isArray(payload.comments) ? payload.comments : [] });
  } catch {
    return buildFeedbackSummary({ comments: [] });
  }
}

function saveStaticFeedback(feedback) {
  const payload = { comments: feedback.comments || [] };
  window.localStorage.setItem(staticFeedbackKey(), JSON.stringify(payload));
}

function createLocalComment(target, text) {
  return {
    id: `L-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    created_at: new Date().toISOString(),
    topic_id: target.item.topic_id,
    topic_title: target.item.topic_title,
    field: target.field,
    field_label: fieldLabels[target.field] || target.field,
    text,
    author: "Евгений",
    status: "open",
    sheet_sync: "local",
  };
}

function mergeFeedback(currentFeedback, incomingFeedback) {
  const byId = new Map();
  for (const comment of currentFeedback?.comments || []) {
    if (comment.id) byId.set(comment.id, comment);
  }
  for (const comment of incomingFeedback?.comments || []) {
    if (comment.id) byId.set(comment.id, { ...(byId.get(comment.id) || {}), ...comment });
  }
  return buildFeedbackSummary({
    ...(currentFeedback || {}),
    ...(incomingFeedback || {}),
    comments: Array.from(byId.values()),
  });
}

function buildFeedbackSummary(feedback) {
  const openComments = (feedback.comments || []).filter(comment => (comment.status || "open") === "open");
  const byKey = {};
  const byTopic = {};
  for (const comment of openComments) {
    if (!comment.topic_id || !comment.field) continue;
    const key = `${comment.topic_id}:${comment.field}`;
    byKey[key] = (byKey[key] || 0) + 1;
    byTopic[comment.topic_id] = (byTopic[comment.topic_id] || 0) + 1;
  }
  return {
    ...feedback,
    count: openComments.length,
    comments: openComments,
    by_key: byKey,
    by_topic: byTopic,
  };
}

function speechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition;
}

function updateVoiceAvailability() {
  const voiceBtn = document.getElementById("voiceCommentBtn");
  if (speechRecognitionCtor()) {
    voiceBtn.disabled = false;
    voiceBtn.title = "Надиктовать комментарий";
    return;
  }
  voiceBtn.disabled = true;
  voiceBtn.title = "Голосовой ввод недоступен в этом браузере";
  document.getElementById("voiceStatus").textContent = "Голосовой ввод недоступен";
}

function toggleVoiceComment() {
  if (voiceListening) {
    stopVoiceComment();
    return;
  }
  startVoiceComment();
}

function startVoiceComment() {
  const Recognition = speechRecognitionCtor();
  if (!Recognition) {
    setToast("Голосовой ввод недоступен в этом браузере");
    return;
  }
  const textarea = document.getElementById("commentText");
  voiceBaseText = textarea.value.trim();
  voiceRecognition = new Recognition();
  voiceRecognition.lang = "ru-RU";
  voiceRecognition.interimResults = true;
  voiceRecognition.continuous = true;
  voiceRecognition.onresult = event => {
    let finalText = "";
    let interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0].transcript.trim();
      if (event.results[i].isFinal) {
        finalText += `${transcript} `;
      } else {
        interimText += `${transcript} `;
      }
    }
    if (finalText.trim()) {
      voiceBaseText = [voiceBaseText, finalText.trim()].filter(Boolean).join(" ");
    }
    textarea.value = [voiceBaseText, interimText.trim()].filter(Boolean).join(" ");
  };
  voiceRecognition.onerror = event => {
    setToast(`Голосовой ввод: ${event.error || "ошибка"}`);
    setVoiceListening(false);
  };
  voiceRecognition.onend = () => setVoiceListening(false);
  setVoiceListening(true);
  voiceRecognition.start();
}

function stopVoiceComment(silent = false) {
  if (voiceRecognition && voiceListening) {
    try {
      voiceRecognition.stop();
    } catch (error) {
      if (!silent) setToast(error.message);
    }
  }
  setVoiceListening(false);
}

function setVoiceListening(isListening) {
  voiceListening = isListening;
  const voiceBtn = document.getElementById("voiceCommentBtn");
  voiceBtn.classList.toggle("listening", isListening);
  document.getElementById("voiceCommentLabel").textContent = isListening ? "Стоп" : "Голосом";
  document.getElementById("voiceStatus").textContent = isListening ? "Слушаю..." : "";
}

async function copyText(text, label) {
  await navigator.clipboard.writeText(text);
  setToast(`${label} скопированы`);
}

function questionsText() {
  const rows = report.items.filter(item => item.open_question);
  if (!rows.length) return "Открытых вопросов к Евгению нет.";
  return rows.map(item => `Тема: ${item.topic_title}\\nВопрос: ${item.open_question}`).join("\\n\\n");
}

function syncText() {
  const rows = report.items.filter(item => item.needs_sync === "yes");
  if (!rows.length) return "Sync по текущим правилам не требуется.";
  return rows.map(item => `- ${item.topic_title}: ${item.sync_reason || item.open_question || "Нужен sync"}`).join("\\n");
}

function feedbackText() {
  const comments = report.feedback?.comments || [];
  if (!comments.length) return "Комментариев пока нет.";
  return comments.map(comment => {
    const title = comment.topic_title || comment.topic_id;
    const label = comment.field_label || fieldLabels[comment.field] || comment.field;
    return `Тема: ${title}\\nПоле: ${label}\\nКомментарий: ${comment.text}`;
  }).join("\\n\\n");
}

function setToast(text) {
  const toast = document.getElementById("toast");
  toast.textContent = text;
  toast.classList.add("visible");
  window.clearTimeout(setToast.timer);
  setToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 1700);
}

function setFilter(nextFilter) {
  filter = nextFilter;
  renderMetrics();
  renderFilterState();
  renderItems();
}

function renderFilterState() {
  filterButtons().forEach(item => item.classList.toggle("active", item.dataset.filter === filter));
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch]));
}

document.getElementById("refreshBtn").onclick = () => loadReport().catch(error => setToast(error.message));
document.getElementById("copyQuestionsBtn").onclick = () => copyText(questionsText(), "Вопросы").catch(error => setToast(error.message));
document.getElementById("copySyncBtn").onclick = () => copyText(syncText(), "Sync").catch(error => setToast(error.message));
document.getElementById("copyFeedbackBtn").onclick = () => copyText(feedbackText(), "Комментарии").catch(error => setToast(error.message));
document.getElementById("commentSaveBtn").onclick = () => saveComment().catch(error => setToast(error.message));
document.getElementById("commentCancelBtn").onclick = closeComment;
document.getElementById("commentCloseBtn").onclick = closeComment;
document.getElementById("voiceCommentBtn").onclick = toggleVoiceComment;
document.querySelector("[data-close-comment]").onclick = closeComment;
document.getElementById("search").addEventListener("input", event => {
  query = event.target.value.trim().toLowerCase();
  renderItems();
});
document.addEventListener("click", event => {
  const jumpTarget = event.target.closest("[data-jump-topic]");
  if (jumpTarget) {
    jumpToTopic(jumpTarget.dataset.jumpTopic);
    return;
  }
  const target = event.target.closest("[data-comment-field][data-topic-id]");
  if (!target) return;
  openComment(target.dataset.topicId, target.dataset.commentField);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeComment();
});
for (const button of filterButtons()) {
  button.onclick = () => setFilter(button.dataset.filter);
}
updateVoiceAvailability();
loadReport().catch(error => setToast(error.message));
"""


if __name__ == "__main__":
    raise SystemExit(main())
