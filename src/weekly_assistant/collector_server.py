import argparse
import csv
import json
import re
import shutil
import threading
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from weekly_assistant.adapters.csv_adapter import HEADERS, load_weekly_rows, write_weekly_rows
from weekly_assistant.adapters.google_csv_export import download_sheet_csv
from weekly_assistant.config.settings import Settings, load_settings
from weekly_assistant.domain.enums import Lifecycle, MovementType, ReviewStatus, YesNo
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter
from weekly_assistant.integrations.openai_responses import OpenAIResponsesAdapter
from weekly_assistant.integrations.singularity import SingularityAdapter
from weekly_assistant.services.exports import build_open_questions_export, build_summary, build_sync_export, format_summary
from weekly_assistant.services.singularity_weekly_context import build_singularity_weekly_context
from weekly_assistant.utils.http import JsonHttpClient


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SESSION_ROOT = Path("exports/collector_sessions")
WRITEBACK_HEADERS = [
    "current_week_facts",
    "ai_draft_result",
    "final_result",
    "next_milestone",
    "next_milestone_date",
    "ball_side",
    "open_question_to_evgeny",
    "movement_type",
    "needs_sync",
    "sync_reason",
    "source_links",
    "review_status",
]
CHATGPT_IMPORT_FIELDS = {
    "raw_fact",
    "current_week_facts",
    "ai_draft_result",
    "final_result",
    "next_milestone",
    "next_milestone_date",
    "ball_side",
    "open_question_to_evgeny",
    "movement_type",
    "needs_sync",
    "sync_reason",
    "source_links",
    "review_status",
}


@dataclass(frozen=True)
class CollectorConfig:
    spreadsheet_id: str
    gid: str
    sheet_name: str
    week_start: date
    week_end: date
    session_dir: Path
    source_csv: Path | None
    refresh: bool


class CollectorStore:
    def __init__(self, settings: Settings, config: CollectorConfig):
        self.settings = settings
        self.config = config
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_path = self.config.session_dir / "session.json"
        self.source_path = self.config.session_dir / "source.csv"
        self.candidate_path = self.config.session_dir / "candidate.csv"
        self.lock = threading.Lock()
        if self.config.refresh or not self.session_path.exists():
            self._initialize_session()

    def load(self) -> dict:
        with self.lock:
            return json.loads(self.session_path.read_text(encoding="utf-8"))

    def save(self, session: dict) -> None:
        with self.lock:
            self.session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_row(self, topic_id: str, patch: dict) -> dict:
        session = self.load()
        row = _find_row(session, topic_id)
        mutable_fields = {
            "status",
            "raw_fact",
            "current_week_facts",
            "ai_draft_result",
            "final_result",
            "next_milestone",
            "next_milestone_date",
            "ball_side",
            "open_question_to_evgeny",
            "movement_type",
            "needs_sync",
            "sync_reason",
            "source_links",
            "review_status",
            "milestones_text",
        }
        for key, value in patch.items():
            if key in mutable_fields:
                next_value = str(value) if value is not None else ""
                if key == "current_week_facts" and next_value != row.get(key, ""):
                    row["manual_current_week_facts"] = True
                if key == "open_question_to_evgeny" and next_value != row.get(key, ""):
                    row["manual_open_question_to_evgeny"] = True
                row[key] = next_value
                if key == "milestones_text":
                    _apply_milestones(row, _parse_milestones_text(next_value))
        row["changed"] = True
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.save(session)
        return row

    def apply_status(self, topic_id: str, status: str) -> dict:
        row = _find_row(self.load(), topic_id)
        patch: dict[str, str] = {"status": status}
        if status == "not_touched":
            patch.update(
                {
                    "final_result": "Без нового движения за неделю.",
                    "movement_type": MovementType.NO_MOVEMENT.value,
                    "needs_sync": YesNo.NO.value,
                    "sync_reason": "Движения по теме на этой неделе не было.",
                    "review_status": ReviewStatus.REVIEWED.value,
                }
            )
        elif status == "question_only":
            patch.update(
                {
                    "movement_type": MovementType.NO_MOVEMENT.value,
                    "needs_sync": YesNo.YES.value if row.get("open_question_to_evgeny") else YesNo.NO.value,
                    "sync_reason": "Есть вопрос к Евгению." if row.get("open_question_to_evgeny") else "",
                }
            )
        elif status == "process_only":
            patch.update({"movement_type": MovementType.NO_MOVEMENT.value, "needs_sync": YesNo.NO.value})
        elif status == "result":
            patch.update({"movement_type": MovementType.REAL_RESULT.value})
        return self.update_row(topic_id, patch)

    def draft_row(self, topic_id: str) -> dict:
        session = self.load()
        row = _find_row(session, topic_id)
        adapter = OpenAIResponsesAdapter(self.settings)
        prompt = _build_row_prompt(row)
        result = adapter.draft_weekly_row(
            prompt,
            instructions=(
                "Ты готовишь weekly-строку на русском. Не выдумывай факты, даты, решения и артефакты. "
                "Используй только новую фактуру этой недели из сырого ввода пользователя и подсказок Singularity. "
                "Не используй результат прошлой недели и перенесенные поля как факт новой недели. "
                "Если есть только follow-up или процесс без предъявляемого результата, честно ставь no_movement. "
                "Вопрос к Евгению возвращай только если пользователь явно обозначил его как вопрос к Евгению. "
                "Пиши кратко, через результат, пригодно для отчета Евгению."
            ),
        )
        open_question = _draft_open_question(row, result)
        patch = {
            "ai_draft_result": result.get("result_text", ""),
            "final_result": result.get("result_text", ""),
            "movement_type": result.get("movement_type", MovementType.UNCLEAR.value),
            "next_milestone": result.get("next_milestone", row.get("next_milestone", "")),
            "next_milestone_date": result.get("next_milestone_date", row.get("next_milestone_date", "")),
            "ball_side": result.get("ball_side", row.get("ball_side", "")),
            "open_question_to_evgeny": open_question,
            "needs_sync": result.get("needs_sync", row.get("needs_sync", YesNo.NO.value)),
            "sync_reason": result.get("sync_reason", row.get("sync_reason", "")),
            "review_status": ReviewStatus.DRAFT.value,
        }
        if open_question:
            patch["needs_sync"] = YesNo.YES.value
            patch["sync_reason"] = patch["sync_reason"] or "Есть явно заданный вопрос к Евгению."
        elif _sync_reason_depends_on_question(patch["sync_reason"]):
            patch["needs_sync"] = YesNo.NO.value
            patch["sync_reason"] = ""
        if patch["movement_type"] == MovementType.REAL_RESULT.value:
            patch["status"] = "result"
        elif row.get("status") in {"empty", ""}:
            patch["status"] = "drafted"
        return self.update_row(topic_id, patch)

    def bulk_suggest(self, text: str) -> dict:
        if not self.settings.openai_api_key or not self.settings.openai_model:
            raise RuntimeError("OpenAI is not configured.")
        session = self.load()
        active_rows = [row for row in session["rows"] if row["lifecycle"] == Lifecycle.ACTIVE.value]
        topics = "\n".join(f"- {row['topic_id']}: {row['topic_title']}" for row in active_rows)
        prompt = (
            "Разложи сырой weekly dump по темам. Не придумывай факты. "
            "Если фрагмент не сопоставляется уверенно, положи его в unmatched.\n\n"
            f"Темы:\n{topics}\n\nСырой dump:\n{text}"
        )
        payload = {
            "model": self.settings.openai_model,
            "instructions": "Return JSON only according to schema. Preserve raw facts; do not polish.",
            "input": prompt,
            "text": {"format": {"type": "json_schema", "name": "bulk_weekly_mapping", "schema": BULK_SCHEMA, "strict": True}},
        }
        response = JsonHttpClient().request(
            "POST",
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
            payload=payload,
        )
        output_text = response.get("output_text") or _extract_output_text(response)
        result = json.loads(output_text)
        topic_ids = {row["topic_id"] for row in active_rows}
        result["items"] = [item for item in result.get("items", []) if item.get("topic_id") in topic_ids]
        return result

    def import_chatgpt_json(self, text: str) -> dict:
        session = self.load()
        result = _apply_chatgpt_import(session, text)
        self.save(session)
        return result

    def chatgpt_context(self) -> dict:
        return _build_chatgpt_context(self.load())

    def export(self) -> dict:
        session = self.load()
        rows = _apply_session_to_csv_rows(session)
        _write_dict_csv(self.candidate_path, rows, session["csv_fieldnames"])
        weekly_rows = load_weekly_rows(self.candidate_path)
        questions_path = self.config.session_dir / "questions.txt"
        sync_path = self.config.session_dir / "sync.txt"
        summary_path = self.config.session_dir / "summary.txt"
        milestones_path = self.config.session_dir / "milestones.txt"
        chatgpt_context_path = self.config.session_dir / "chatgpt_context.json"
        questions_path.write_text(build_open_questions_export(weekly_rows) + "\n", encoding="utf-8")
        sync_path.write_text(build_sync_export(weekly_rows) + "\n", encoding="utf-8")
        summary_path.write_text(format_summary(build_summary(weekly_rows)) + "\n", encoding="utf-8")
        milestones_path.write_text(_build_milestones_export(session) + "\n", encoding="utf-8")
        chatgpt_context_path.write_text(json.dumps(_build_chatgpt_context(session), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "candidate_csv": str(self.candidate_path),
            "questions": str(questions_path),
            "sync": str(sync_path),
            "summary": str(summary_path),
            "milestones": str(milestones_path),
            "chatgpt_context": str(chatgpt_context_path),
            "summary_text": summary_path.read_text(encoding="utf-8"),
        }

    def write_back(self) -> dict:
        session = self.load()
        adapter = GoogleSheetsAdapter(self.settings)
        changed_rows = [row for row in session["rows"] if row.get("changed")]
        updated = []
        for row in changed_rows:
            row_number = row["row_number"]
            values = [[_writeback_value(row, field) for field in WRITEBACK_HEADERS]]
            range_name = f"'{session['sheet_name']}'!H{row_number}:S{row_number}"
            result = adapter.update_values(range_name, values)
            updated.append({"topic_id": row["topic_id"], "topic_title": row["topic_title"], "range": range_name, "result": result})
        return {"updated_count": len(updated), "updated": updated}

    def _initialize_session(self) -> None:
        if self.config.source_csv:
            shutil.copyfile(self.config.source_csv, self.source_path)
        else:
            download_sheet_csv(self.config.spreadsheet_id, self.config.gid, self.source_path)

        raw_rows, fieldnames = _read_raw_csv(self.source_path)
        weekly_rows = {row.topic_id: row for row in load_weekly_rows(self.source_path)}
        hints = self._load_singularity_hints()
        session_rows = []
        for raw in raw_rows:
            topic_title = (raw.get(HEADERS["topic_title"]) or "").strip()
            topic_id = (raw.get(HEADERS["topic_id"]) or "").strip()
            if not topic_title or not topic_id:
                continue
            row = weekly_rows.get(topic_id)
            if not row:
                continue
            status = "empty"
            if row.final_result or row.current_week_facts:
                status = "has_facts"
            session_rows.append(
                {
                    "row_number": raw["_row_number"],
                    "topic_id": row.topic_id,
                    "topic_title": row.topic_title,
                    "section": row.section,
                    "lifecycle": row.lifecycle.value,
                    "focus": row.focus.value,
                    "previous_week_result": row.previous_week_result,
                    "raw_fact": "",
                    "current_week_facts": row.current_week_facts,
                    "ai_draft_result": row.ai_draft_result,
                    "final_result": row.final_result,
                    "next_milestone": row.next_milestone,
                    "next_milestone_date": row.next_milestone_date,
                    "ball_side": row.ball_side,
                    "open_question_to_evgeny": row.open_question_to_evgeny,
                    "manual_current_week_facts": False,
                    "manual_open_question_to_evgeny": False,
                    "milestones": _parse_milestones_from_row(row.next_milestone, row.next_milestone_date),
                    "milestones_text": _format_milestones_text(_parse_milestones_from_row(row.next_milestone, row.next_milestone_date)),
                    "movement_type": row.movement_type.value,
                    "needs_sync": row.needs_sync.value,
                    "sync_reason": row.sync_reason,
                    "source_links": row.source_links,
                    "review_status": row.review_status.value,
                    "status": status,
                    "changed": False,
                    "hints": hints.get(row.topic_title, []),
                }
            )

        session = {
            "metadata": {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "week_start": self.config.week_start.isoformat(),
                "week_end": self.config.week_end.isoformat(),
                "source_csv": str(self.source_path),
                "session_dir": str(self.config.session_dir),
            },
            "spreadsheet_id": self.config.spreadsheet_id,
            "gid": self.config.gid,
            "sheet_name": self.config.sheet_name,
            "csv_fieldnames": fieldnames,
            "raw_rows": raw_rows,
            "rows": session_rows,
        }
        self.save(session)

    def _load_singularity_hints(self) -> dict[str, list[str]]:
        if not self.settings.singularity_api_token:
            return {}
        try:
            context = build_singularity_weekly_context(
                SingularityAdapter(self.settings),
                config_path=self.settings.singularity_projects_config,
                week_start=self.config.week_start,
                week_end=self.config.week_end,
            )
        except Exception:
            return {}
        hints: dict[str, list[str]] = {}
        source_rows = load_weekly_rows(self.source_path) if self.source_path.exists() else []
        for row in source_rows:
            row_hints = []
            for task in context.completed_this_week:
                if _loosely_matches(row.topic_title, task.topic):
                    row_hints.append(f"Выполнено {task.done_date}: {task.title}")
            for task in context.open_tasks:
                if _loosely_matches(row.topic_title, task.topic):
                    start = f" start={task.start_date}" if task.start_date else ""
                    row_hints.append(f"Открытая задача{start}: {task.title}")
            if row_hints:
                hints[row.topic_title] = row_hints
        return hints


class CollectorHandler(BaseHTTPRequestHandler):
    store: CollectorStore

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_text(INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/app.css":
            self._send_text(APP_CSS, "text/css; charset=utf-8")
        elif path == "/app.js":
            self._send_text(APP_JS, "application/javascript; charset=utf-8")
        elif path == "/api/session":
            self._send_json(self.store.load())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/row":
                self._send_json({"row": self.store.update_row(payload["topic_id"], payload.get("patch", {}))})
            elif path == "/api/status":
                self._send_json({"row": self.store.apply_status(payload["topic_id"], payload["status"])})
            elif path == "/api/draft":
                self._send_json({"row": self.store.draft_row(payload["topic_id"])})
            elif path == "/api/bulk-suggest":
                self._send_json(self.store.bulk_suggest(payload.get("text", "")))
            elif path == "/api/chatgpt-import":
                self._send_json(self.store.import_chatgpt_json(payload.get("text", "")))
            elif path == "/api/chatgpt-context":
                self._send_json(self.store.chatgpt_context())
            elif path == "/api/export":
                self._send_json(self.store.export())
            elif path == "/api/write-back":
                self._send_json(self.store.write_back())
            else:
                self.send_error(404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def log_message(self, format: str, *args) -> None:
        print(f"[collector] {self.address_string()} - {format % args}")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekly-collector")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--spreadsheet-id", default="")
    parser.add_argument("--gid", default="")
    parser.add_argument("--sheet-name", default="")
    parser.add_argument("--week-start", required=True)
    parser.add_argument("--week-end", required=True)
    parser.add_argument("--session-dir", default="")
    parser.add_argument("--source-csv", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    session_dir = Path(args.session_dir) if args.session_dir else SESSION_ROOT / f"{args.week_start}_{args.week_end}"
    config = CollectorConfig(
        spreadsheet_id=args.spreadsheet_id or settings.google_sheets_spreadsheet_id,
        gid=args.gid or settings.google_sheets_weekly_gid,
        sheet_name=args.sheet_name or settings.google_sheets_weekly_sheet_name,
        week_start=date.fromisoformat(args.week_start),
        week_end=date.fromisoformat(args.week_end),
        session_dir=session_dir,
        source_csv=Path(args.source_csv) if args.source_csv else None,
        refresh=args.refresh,
    )
    store = CollectorStore(settings, config)
    handler = type("ConfiguredCollectorHandler", (CollectorHandler,), {"store": store})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Weekly collector running: {url}")
    print(f"Session dir: {session_dir}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping weekly collector.")
    return 0


def _find_row(session: dict, topic_id: str) -> dict:
    for row in session["rows"]:
        if row["topic_id"] == topic_id:
            return row
    raise KeyError(f"Unknown topic_id: {topic_id}")


def _build_row_prompt(row: dict) -> str:
    hints = "\n".join(f"- {hint}" for hint in row.get("hints", [])) or "-"
    current_evidence = _current_week_evidence(row)
    manual_question = row.get("open_question_to_evgeny", "").strip() if row.get("manual_open_question_to_evgeny") else ""
    return f"""
Тема: {row.get('topic_title', '')}

Новая фактура этой недели:
{current_evidence}

Подсказки из Singularity:
{hints}

Явно заданный пользователем вопрос к Евгению:
{manual_question or '-'}

Текущая ближайшая веха: {row.get('next_milestone', '')}
Дата вехи: {row.get('next_milestone_date', '')}
Все вехи:
{row.get('milestones_text', '') or '-'}
Мяч: {row.get('ball_side', '')}

Правила:
- Не используй прошлую неделю как факт новой недели.
- Если в новой фактуре нет предъявляемого результата, верни честный no_movement.
- open_question_to_evgeny заполняй только из явно заданного пользователем вопроса.
""".strip()


def _current_week_evidence(row: dict) -> str:
    parts = []
    raw_fact = row.get("raw_fact", "").strip()
    if raw_fact:
        parts.append(raw_fact)
    if row.get("manual_current_week_facts"):
        current_week_facts = row.get("current_week_facts", "").strip()
        if current_week_facts:
            parts.append(current_week_facts)
    return "\n".join(parts) or "-"


def _draft_open_question(row: dict, result: dict) -> str:
    manual_question = row.get("open_question_to_evgeny", "").strip() if row.get("manual_open_question_to_evgeny") else ""
    if manual_question:
        return manual_question
    if not _has_explicit_question_signal(row):
        return ""
    return (result.get("open_question_to_evgeny") or "").strip()


def _has_explicit_question_signal(row: dict) -> bool:
    text = _current_week_evidence(row).casefold()
    return bool(
        re.search(
            r"(вопрос(?:ы)?\s+к\s+евгени[юя]|спросить\s+евгени[яю]|уточнить\s+у\s+евгени[яю]|"
            r"для\s+евгени[яю]\s*:|евгени[йюя].*\?)",
            text,
        )
    )


def _sync_reason_depends_on_question(value: str) -> bool:
    return bool(re.search(r"вопрос|евгени", (value or "").casefold()))


def _apply_chatgpt_import(session: dict, text: str) -> dict:
    payload = _load_chatgpt_payload(text)
    items = _extract_chatgpt_items(payload)
    imported = []
    unmatched = []
    for item in items:
        row = _match_import_row(session, item)
        if not row:
            unmatched.append(
                {
                    "topic_id": str(item.get("topic_id", "")),
                    "topic_title": str(item.get("topic_title", "")),
                }
            )
            continue
        patch = _normalize_import_item(item)
        row.update(patch)
        row["manual_current_week_facts"] = bool(row.get("current_week_facts"))
        row["manual_open_question_to_evgeny"] = bool(row.get("open_question_to_evgeny"))
        row["changed"] = True
        row["status"] = "chatgpt_imported"
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        imported.append({"topic_id": row["topic_id"], "topic_title": row["topic_title"]})
    return {"imported_count": len(imported), "imported": imported, "unmatched": unmatched}


def _build_chatgpt_context(session: dict) -> dict:
    active_rows = [row for row in session["rows"] if row.get("lifecycle") == Lifecycle.ACTIVE.value]
    active_rows.sort(key=lambda row: (row.get("focus") != YesNo.YES.value, row.get("section", ""), row.get("topic_title", "")))
    return {
        "task": "weekly_collect_for_evgeny",
        "week_start": session.get("metadata", {}).get("week_start", ""),
        "week_end": session.get("metadata", {}).get("week_end", ""),
        "instructions": [
            "Иди по темам строго по одной.",
            "Задавай один вопрос: что по этой теме на этой неделе?",
            "Прошлая неделя, старые вехи и старые вопросы — только контекст для уточнения, не факт новой недели.",
            "Если old_open_question_to_evgeny не пустой, отдельно спроси, оставлять ли этот вопрос актуальным на эту неделю.",
            "Подтвержденный старый вопрос верни в open_question_to_evgeny; снятый или неподтвержденный старый вопрос верни пустой строкой.",
            "Вопрос к Евгению заполняй только если пользователь явно подтвердил, что это вопрос к Евгению.",
            "Вех может быть несколько; верни milestones[] в порядке ближайшая первая.",
            "После всех тем верни только JSON по output_schema.",
        ],
        "output_schema": {
            "items": [
                {
                    "topic_id": "T-001",
                    "topic_title": "Название темы",
                    "current_week_facts": "Сырые факты этой недели",
                    "final_result": "Готовая формулировка через результат",
                    "milestones": [{"date": "10.05", "text": "Ближайшая веха"}],
                    "ball_side": "me | evgeny | external: кто именно",
                    "open_question_to_evgeny": "",
                    "movement_type": "real_result | no_movement | unclear",
                    "needs_sync": "yes | no",
                    "sync_reason": "",
                    "source_links": "",
                }
            ],
            "unmatched_notes": ["Факты, которые не удалось привязать к теме"],
        },
        "topics": [
            {
                "topic_id": row.get("topic_id", ""),
                "topic_title": row.get("topic_title", ""),
                "section": row.get("section", ""),
                "focus": row.get("focus", YesNo.NO.value),
                "previous_week_result": row.get("previous_week_result", ""),
                "current_milestones": row.get("milestones_text") or row.get("next_milestone", ""),
                "current_milestone_date": row.get("next_milestone_date", ""),
                "ball_side": row.get("ball_side", ""),
                "old_open_question_to_evgeny": row.get("open_question_to_evgeny", ""),
                "singularity_hints": row.get("hints", []),
            }
            for row in active_rows
        ],
    }


def _load_chatgpt_payload(text: str) -> dict:
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Import JSON is empty.")
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


def _extract_chatgpt_items(payload: dict) -> list[dict]:
    if isinstance(payload, list):
        return payload
    for key in ("items", "rows", "weekly_rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise ValueError("Import JSON must contain items, rows, weekly_rows, or be a list.")


def _match_import_row(session: dict, item: dict) -> dict | None:
    topic_id = str(item.get("topic_id", "")).strip()
    if topic_id:
        for row in session["rows"]:
            if row["topic_id"] == topic_id:
                return row
    topic_title = str(item.get("topic_title", "") or item.get("topic", "")).strip()
    if topic_title:
        matches = [row for row in session["rows"] if _loosely_matches(row["topic_title"], topic_title)]
        if len(matches) == 1:
            return matches[0]
    return None


def _normalize_import_item(item: dict) -> dict:
    patch = {}
    for key in CHATGPT_IMPORT_FIELDS:
        if key in item and item[key] is not None:
            patch[key] = str(item[key]).strip()
    if not patch.get("current_week_facts") and item.get("facts") is not None:
        patch["current_week_facts"] = str(item["facts"]).strip()
    if not patch.get("current_week_facts") and item.get("raw_fact") is not None:
        patch["current_week_facts"] = str(item["raw_fact"]).strip()
    if not patch.get("final_result") and item.get("result_text") is not None:
        patch["final_result"] = str(item["result_text"]).strip()
    if not patch.get("open_question_to_evgeny") and item.get("open_question") is not None:
        patch["open_question_to_evgeny"] = str(item["open_question"]).strip()
    patch.setdefault("current_week_facts", "")
    patch.setdefault("final_result", "")
    patch.setdefault("open_question_to_evgeny", "")
    patch["movement_type"] = _normalize_enum_value(patch.get("movement_type", ""), MovementType, MovementType.UNCLEAR.value)
    patch["needs_sync"] = _normalize_enum_value(patch.get("needs_sync", ""), YesNo, YesNo.NO.value)
    if patch.get("review_status"):
        patch["review_status"] = _normalize_enum_value(patch["review_status"], ReviewStatus, ReviewStatus.DRAFT.value)
    else:
        patch["review_status"] = ReviewStatus.REVIEWED.value
    milestones = _normalize_milestones(item.get("milestones", []))
    if not milestones and item.get("milestones_text"):
        milestones = _parse_milestones_text(str(item["milestones_text"]))
    if not milestones:
        milestones = _parse_milestones_from_row(patch.get("next_milestone", ""), patch.get("next_milestone_date", ""))
    _apply_milestones(patch, milestones)
    return patch


def _normalize_enum_value(value: str, enum_type, default: str) -> str:
    try:
        return enum_type((value or default).strip()).value
    except ValueError:
        return default


def _normalize_milestones(value) -> list[dict]:
    milestones = []
    if not isinstance(value, list):
        return milestones
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                milestones.append({"text": text, "date": ""})
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or item.get("milestone", "") or item.get("title", "")).strip()
        date_value = str(item.get("date", "") or item.get("target_date", "") or "").strip()
        if text or date_value:
            milestones.append({"text": text, "date": date_value})
    return milestones


def _parse_milestones_from_row(next_milestone: str, next_milestone_date: str) -> list[dict]:
    milestone = (next_milestone or "").strip()
    if not milestone:
        return []
    if ";" in milestone:
        return _parse_milestones_text(milestone.replace(";", "\n"))
    return [{"text": milestone, "date": (next_milestone_date or "").strip()}]


def _parse_milestones_text(value: str) -> list[dict]:
    milestones = []
    for line in (value or "").replace(";", "\n").splitlines():
        raw = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if not raw:
            continue
        match = re.match(r"^(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{4}-\d{2}-\d{2}|TBD)\s+(.+)$", raw, flags=re.IGNORECASE)
        if match:
            milestones.append({"date": match.group(1).strip(), "text": match.group(2).strip()})
        else:
            milestones.append({"date": "", "text": raw})
    return milestones


def _apply_milestones(row: dict, milestones: list[dict]) -> None:
    normalized = _normalize_milestones(milestones)
    row["milestones"] = normalized
    row["milestones_text"] = _format_milestones_text(normalized)
    if normalized:
        row["next_milestone"] = _format_milestones_for_excel(normalized)
        row["next_milestone_date"] = normalized[0].get("date", "")


def _format_milestones_text(milestones: list[dict]) -> str:
    return "\n".join(_format_milestone(milestone) for milestone in milestones if _format_milestone(milestone))


def _format_milestones_for_excel(milestones: list[dict]) -> str:
    return "; ".join(_format_milestone(milestone) for milestone in milestones if _format_milestone(milestone))


def _format_milestone(milestone: dict) -> str:
    text = str(milestone.get("text", "")).strip()
    date_value = str(milestone.get("date", "")).strip()
    if text and date_value:
        return f"{date_value} {text}"
    return text or date_value


def _build_milestones_export(session: dict) -> str:
    blocks = []
    for row in session["rows"]:
        if row.get("lifecycle") != Lifecycle.ACTIVE.value:
            continue
        milestones_text = (row.get("milestones_text") or "").strip()
        if milestones_text:
            blocks.append(f"[{row['topic_id']}] {row['topic_title']}\n{milestones_text}")
    return "\n\n".join(blocks) if blocks else "Вехи не заполнены."


def _read_raw_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = []
        for index, row in enumerate(reader, start=2):
            row["_row_number"] = index
            rows.append(row)
        return rows, list(reader.fieldnames or [])


def _write_dict_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _apply_session_to_csv_rows(session: dict) -> list[dict]:
    by_row_number = {row["row_number"]: row for row in session["rows"]}
    rows = []
    for raw in session["raw_rows"]:
        row = dict(raw)
        update = by_row_number.get(raw["_row_number"])
        if update:
            for field in WRITEBACK_HEADERS:
                row[HEADERS[field]] = _writeback_value(update, field)
        row.pop("_row_number", None)
        rows.append(row)
    return rows


def _writeback_value(row: dict, field: str) -> str:
    value = row.get(field, "")
    if field == "movement_type" and value not in {item.value for item in MovementType}:
        return MovementType.UNCLEAR.value
    if field == "needs_sync" and value not in {item.value for item in YesNo}:
        return YesNo.NO.value
    if field == "review_status" and value not in {item.value for item in ReviewStatus}:
        return ReviewStatus.DRAFT.value
    return value


def _extract_output_text(response: dict) -> str:
    if "output_text" in response:
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and "text" in content:
                return content["text"]
    raise RuntimeError("OpenAI response did not contain output text.")


def _loose_key(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", value.casefold())


def _loosely_matches(topic_title: str, hint_topic: str) -> bool:
    left = _loose_key(topic_title)
    right = _loose_key(hint_topic)
    return bool(left and right and (left in right or right in left))


BULK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "unmatched"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic_id", "raw_fact", "confidence"],
                "properties": {
                    "topic_id": {"type": "string"},
                    "raw_fact": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
            },
        },
        "unmatched": {"type": "array", "items": {"type": "string"}},
    },
}


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Weekly Collector</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>Weekly Collector</h1>
        <div id="period" class="muted"></div>
      </div>
      <div class="toolbar">
        <button id="exportBtn">Export</button>
        <button id="writeBtn">Write Sheet</button>
      </div>
      <div id="stats" class="stats"></div>
      <div class="bulk">
        <label for="bulkText">Сырой dump</label>
        <textarea id="bulkText" rows="7" placeholder="Кидай хаотичный апдейт по всем темам. Инструмент разложит по строкам."></textarea>
        <button id="bulkBtn">Разложить по темам</button>
        <div id="bulkResult" class="muted"></div>
      </div>
      <div class="import-box">
        <label for="chatgptImport">ChatGPT JSON</label>
        <button id="chatgptContextBtn">Copy ChatGPT context</button>
        <textarea id="chatgptImport" rows="7" placeholder="Вставь сюда финальный JSON из ChatGPT Project. Поля: items[], topic_id, final_result, milestones[], ball_side, open_question_to_evgeny."></textarea>
        <button id="chatgptImportBtn">Import ChatGPT JSON</button>
        <div id="chatgptImportResult" class="muted"></div>
      </div>
      <nav id="topicList" class="topic-list"></nav>
    </aside>
    <section class="workspace">
      <header class="workspace-header">
        <div>
          <div id="topicMeta" class="muted"></div>
          <h2 id="topicTitle">Загрузка...</h2>
        </div>
        <div class="status-row">
          <button data-status="result">Результат</button>
          <button data-status="process_only">Процесс</button>
          <button data-status="question_only">Вопрос</button>
          <button data-status="not_touched">Не трогал</button>
        </div>
      </header>
      <section class="grid">
        <div class="main-form">
          <label>Сырой ввод</label>
          <textarea id="rawFact" rows="5" placeholder="Пиши как есть. Красоту делает AI draft."></textarea>
          <div class="form-actions">
            <button id="saveBtn">Save</button>
            <button id="draftBtn">AI draft</button>
            <button id="nextBtn">Next empty</button>
          </div>
          <label>Итоговая формулировка</label>
          <textarea id="finalResult" rows="4"></textarea>
          <label>Факты этой недели</label>
          <textarea id="facts" rows="4"></textarea>
          <div class="two-col">
            <div>
              <label>Дата вехи</label>
              <input id="milestoneDate">
            </div>
            <div>
              <label>Мяч</label>
              <input id="ballSide">
            </div>
          </div>
          <label>Ближайшая веха</label>
          <textarea id="milestone" rows="3"></textarea>
          <label>Все вехи</label>
          <textarea id="milestonesText" rows="4" placeholder="Одна веха на строку: дата текст. В Excel будет собрана в одну строку через ;"></textarea>
          <label>Вопрос к Евгению</label>
          <textarea id="question" rows="3"></textarea>
          <div class="two-col">
            <div>
              <label>Movement</label>
              <select id="movement">
                <option value="unclear">unclear</option>
                <option value="real_result">real_result</option>
                <option value="no_movement">no_movement</option>
              </select>
            </div>
            <div>
              <label>Sync</label>
              <select id="sync">
                <option value="no">no</option>
                <option value="yes">yes</option>
              </select>
            </div>
          </div>
          <label>Причина sync</label>
          <input id="syncReason">
        </div>
        <aside class="context-panel">
          <h3>Контекст</h3>
          <div class="context-block">
            <h4>Прошлая неделя</h4>
            <pre id="previousResult"></pre>
          </div>
          <div class="context-block">
            <h4>Singularity</h4>
            <ul id="hints"></ul>
          </div>
          <div class="context-block">
            <h4>AI draft</h4>
            <pre id="aiDraft"></pre>
          </div>
          <div id="message" class="message"></div>
        </aside>
      </section>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f8;
  --panel: #ffffff;
  --ink: #1f2328;
  --muted: #69707a;
  --line: #d9dee5;
  --accent: #1f6feb;
  --accent-soft: #e8f1ff;
  --warn: #9a6700;
  --ok: #1a7f37;
  --radius: 8px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}
.app { display: grid; grid-template-columns: 360px minmax(0, 1fr); min-height: 100vh; }
.sidebar {
  border-right: 1px solid var(--line);
  background: var(--panel);
  padding: 18px;
  overflow: auto;
  max-height: 100vh;
}
.brand h1 { margin: 0 0 2px; font-size: 21px; line-height: 1.15; }
.muted { color: var(--muted); font-size: 12px; }
.toolbar { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 16px 0; }
button, select, input, textarea {
  font: inherit;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
button {
  min-height: 34px;
  padding: 0 12px;
  cursor: pointer;
  font-weight: 600;
}
button:hover { border-color: #9aa4b2; }
button.primary, #draftBtn { background: var(--accent); color: #fff; border-color: var(--accent); }
textarea, input, select {
  width: 100%;
  padding: 9px 10px;
  resize: vertical;
}
label { display: block; margin: 14px 0 6px; font-weight: 650; font-size: 12px; color: #343941; }
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  margin-bottom: 16px;
}
.stat { padding: 8px; border: 1px solid var(--line); border-radius: 6px; background: #fafbfc; }
.stat strong { display: block; font-size: 16px; }
.bulk, .import-box { border-top: 1px solid var(--line); padding: 14px 0; margin-bottom: 12px; }
.import-box { border-bottom: 1px solid var(--line); }
.bulk button, .import-box button { width: 100%; margin-top: 8px; }
.topic-list { display: grid; gap: 6px; }
.topic-item {
  width: 100%;
  text-align: left;
  min-height: 42px;
  padding: 8px;
  border-radius: 6px;
  background: #fff;
}
.topic-item.active { border-color: var(--accent); background: var(--accent-soft); }
.topic-item .title { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topic-item .badge { display: inline-block; margin-top: 4px; font-size: 11px; color: var(--muted); }
.topic-item.changed .badge { color: var(--accent); }
.topic-item.done .badge { color: var(--ok); }
.workspace { padding: 22px; overflow: auto; max-height: 100vh; }
.workspace-header {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
  margin-bottom: 16px;
}
.workspace-header h2 { margin: 2px 0 0; font-size: 26px; line-height: 1.18; }
.status-row { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 18px; align-items: start; }
.main-form, .context-panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px;
}
.form-actions { display: flex; gap: 8px; margin: 10px 0 4px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.context-panel h3 { margin: 0 0 10px; }
.context-block { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 12px; }
.context-block h4 { margin: 0 0 8px; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
pre {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  color: #343941;
  font-family: inherit;
}
#hints { padding-left: 18px; margin: 0; }
#hints li { margin-bottom: 7px; }
.message { margin-top: 14px; color: var(--muted); }
.message.error { color: #b42318; }
@media (max-width: 980px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { max-height: none; border-right: 0; border-bottom: 1px solid var(--line); }
  .grid { grid-template-columns: 1fr; }
  .workspace-header { display: block; }
  .status-row { justify-content: flex-start; margin-top: 12px; }
}
"""


APP_JS = """
let session = null;
let selectedId = null;
let dirty = false;

const fields = {
  rawFact: "raw_fact",
  facts: "current_week_facts",
  finalResult: "final_result",
  milestone: "next_milestone",
  milestoneDate: "next_milestone_date",
  milestonesText: "milestones_text",
  ballSide: "ball_side",
  question: "open_question_to_evgeny",
  movement: "movement_type",
  sync: "needs_sync",
  syncReason: "sync_reason"
};

async function api(path, body) {
  const options = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

async function loadSession() {
  session = await api("/api/session");
  selectedId = selectedId || firstActiveRow()?.topic_id;
  render();
}

function activeRows() {
  return session.rows.filter(row => row.lifecycle === "active");
}

function firstActiveRow() {
  return activeRows()[0] || session.rows[0];
}

function selectedRow() {
  return session.rows.find(row => row.topic_id === selectedId) || firstActiveRow();
}

function render() {
  const meta = session.metadata;
  document.getElementById("period").textContent = `${meta.week_start} - ${meta.week_end}`;
  renderStats();
  renderTopics();
  renderSelected();
}

function renderStats() {
  const rows = activeRows();
  const changed = rows.filter(row => row.changed).length;
  const final = rows.filter(row => row.final_result).length;
  const raw = rows.filter(row => row.raw_fact && !row.final_result).length;
  const empty = rows.length - final - raw;
  document.getElementById("stats").innerHTML = [
    ["Active", rows.length],
    ["Changed", changed],
    ["Final", final],
    ["Empty", empty]
  ].map(([label, value]) => `<div class="stat"><strong>${value}</strong>${label}</div>`).join("");
}

function renderTopics() {
  const list = document.getElementById("topicList");
  list.innerHTML = "";
  for (const row of activeRows()) {
    const button = document.createElement("button");
    button.className = "topic-item" + (row.topic_id === selectedId ? " active" : "") + (row.changed ? " changed" : "") + (row.final_result ? " done" : "");
    button.innerHTML = `<span class="title">${escapeHtml(row.topic_title)}</span><span class="badge">${escapeHtml(row.status || "empty")}${row.hints.length ? " · hints" : ""}</span>`;
    button.onclick = () => { saveCurrentIfDirty(); selectedId = row.topic_id; dirty = false; renderSelected(); renderTopics(); };
    list.appendChild(button);
  }
}

function renderSelected() {
  const row = selectedRow();
  if (!row) return;
  document.getElementById("topicMeta").textContent = `${row.topic_id} · row ${row.row_number} · ${row.section}`;
  document.getElementById("topicTitle").textContent = row.topic_title;
  for (const [id, key] of Object.entries(fields)) {
    document.getElementById(id).value = row[key] || "";
  }
  document.getElementById("previousResult").textContent = row.previous_week_result || "Нет прошлого результата.";
  document.getElementById("aiDraft").textContent = row.ai_draft_result || "Пока нет AI draft.";
  const hints = document.getElementById("hints");
  hints.innerHTML = row.hints.length ? row.hints.map(hint => `<li>${escapeHtml(hint)}</li>`).join("") : "<li>Нет подсказок.</li>";
}

function collectPatch() {
  const patch = {};
  for (const [id, key] of Object.entries(fields)) patch[key] = document.getElementById(id).value;
  return patch;
}

async function saveCurrentIfDirty() {
  if (!dirty || !selectedId) return;
  await saveCurrent();
}

async function saveCurrent() {
  const data = await api("/api/row", { topic_id: selectedId, patch: collectPatch() });
  replaceRow(data.row);
  dirty = false;
  message("Saved");
  render();
}

function replaceRow(updated) {
  const index = session.rows.findIndex(row => row.topic_id === updated.topic_id);
  if (index >= 0) session.rows[index] = updated;
}

async function setStatus(status) {
  await saveCurrentIfDirty();
  const data = await api("/api/status", { topic_id: selectedId, status });
  replaceRow(data.row);
  dirty = false;
  message(`Status: ${status}`);
  render();
}

async function draftCurrent() {
  await saveCurrentIfDirty();
  message("AI draft...");
  const data = await api("/api/draft", { topic_id: selectedId });
  replaceRow(data.row);
  dirty = false;
  message("Draft ready");
  render();
}

async function bulkSuggest() {
  const text = document.getElementById("bulkText").value.trim();
  if (!text) return;
  message("Разбираю dump...");
  const data = await api("/api/bulk-suggest", { text });
  let applied = 0;
  for (const item of data.items || []) {
    const row = session.rows.find(candidate => candidate.topic_id === item.topic_id);
    if (!row) continue;
    row.raw_fact = row.raw_fact ? `${row.raw_fact}\\n${item.raw_fact}` : item.raw_fact;
    row.status = `suggested:${item.confidence}`;
    row.changed = true;
    await api("/api/row", { topic_id: row.topic_id, patch: { raw_fact: row.raw_fact, status: row.status } });
    applied += 1;
  }
  document.getElementById("bulkResult").textContent = `Разложено: ${applied}; unmatched: ${(data.unmatched || []).length}`;
  await loadSession();
  message("Bulk suggestions applied");
}

async function importChatgpt() {
  await saveCurrentIfDirty();
  const text = document.getElementById("chatgptImport").value.trim();
  if (!text) return;
  message("Импортирую ChatGPT JSON...");
  const data = await api("/api/chatgpt-import", { text });
  document.getElementById("chatgptImportResult").textContent = `Импортировано: ${data.imported_count}; unmatched: ${(data.unmatched || []).length}`;
  if (data.imported && data.imported.length) selectedId = data.imported[0].topic_id;
  await loadSession();
  dirty = false;
  message(`ChatGPT import: ${data.imported_count}`);
}

async function copyChatgptContext() {
  await saveCurrentIfDirty();
  const data = await api("/api/chatgpt-context", {});
  const text = JSON.stringify(data, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    document.getElementById("chatgptImportResult").textContent = "Контекст скопирован. Вставь его в ChatGPT Project.";
  } catch (error) {
    document.getElementById("chatgptImport").value = text;
    document.getElementById("chatgptImportResult").textContent = "Clipboard недоступен. Контекст положил в поле ниже.";
  }
  message("ChatGPT context ready");
}

function nextEmpty() {
  const rows = activeRows();
  const start = rows.findIndex(row => row.topic_id === selectedId);
  const ordered = rows.slice(start + 1).concat(rows.slice(0, start + 1));
  const next = ordered.find(row => !row.final_result && !row.raw_fact) || ordered.find(row => !row.final_result);
  if (next) {
    selectedId = next.topic_id;
    dirty = false;
    render();
  }
}

async function exportSession() {
  await saveCurrentIfDirty();
  const data = await api("/api/export", {});
  message(`Exported: ${data.candidate_csv}`);
  alert(`Exported\\n${data.summary_text}`);
}

async function writeBack() {
  await saveCurrentIfDirty();
  if (!confirm("Записать измененные строки в Google Sheet?")) return;
  const data = await api("/api/write-back", {});
  message(`Updated rows: ${data.updated_count}`);
  alert(`Updated rows: ${data.updated_count}`);
}

function message(text, isError = false) {
  const el = document.getElementById("message");
  el.textContent = text;
  el.className = "message" + (isError ? " error" : "");
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch]));
}

document.addEventListener("input", event => {
  if (Object.keys(fields).includes(event.target.id)) dirty = true;
});
document.getElementById("saveBtn").onclick = () => saveCurrent().catch(error => message(error.message, true));
document.getElementById("draftBtn").onclick = () => draftCurrent().catch(error => message(error.message, true));
document.getElementById("nextBtn").onclick = () => nextEmpty();
document.getElementById("bulkBtn").onclick = () => bulkSuggest().catch(error => message(error.message, true));
document.getElementById("chatgptContextBtn").onclick = () => copyChatgptContext().catch(error => message(error.message, true));
document.getElementById("chatgptImportBtn").onclick = () => importChatgpt().catch(error => message(error.message, true));
document.getElementById("exportBtn").onclick = () => exportSession().catch(error => message(error.message, true));
document.getElementById("writeBtn").onclick = () => writeBack().catch(error => message(error.message, true));
for (const button of document.querySelectorAll("[data-status]")) {
  button.onclick = () => setStatus(button.dataset.status).catch(error => message(error.message, true));
}
loadSession().catch(error => message(error.message, true));
"""


if __name__ == "__main__":
    raise SystemExit(main())
