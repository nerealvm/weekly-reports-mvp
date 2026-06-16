import argparse
import csv
import json
import re
import shutil
import threading
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
from weekly_assistant.services.active_sheet import (
    ACTIVE_GID,
    ACTIVE_SHEET_NAME,
    ACTIVE_SOURCE_FORMAT,
    active_append_row_values,
    active_row_updates,
    build_active_session_rows_from_csv,
    column_letter as active_column_letter,
    ensure_active_week_columns,
    is_active_sheet_csv,
    row_to_normalized_raw,
    week_label_for_date,
)
from weekly_assistant.services.legacy_active_transfer import DEFAULT_TARGET_SHEET, format_legacy_transfer_result, transfer_weekly_to_active
from weekly_assistant.services.singularity_weekly_context import build_singularity_weekly_context
from weekly_assistant.utils.http import JsonHttpClient


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SESSION_ROOT = Path("exports/collector_sessions")
REPORT_BOUNDARY_WEEKDAY = 4  # Friday, using date.weekday(): Monday=0.
FULL_WRITEBACK_HEADERS = list(HEADERS.keys())
STRUCTURAL_WRITEBACK_HEADERS = [
    "section",
    "topic_title",
    "date_created",
    "lifecycle",
    "focus",
    "previous_week_result",
]
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
PROJECT_MODE_PATCHES = {
    "project": {"section": "Проекты", "lifecycle": Lifecycle.ACTIVE.value},
    "task": {"section": "Задачи", "lifecycle": Lifecycle.ACTIVE.value},
    "hold": {"section": "На Паузе", "lifecycle": Lifecycle.PAUSED.value},
}
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
        changed_fields = set(row.get("changed_fields", []))
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
                    changed_fields.update({"next_milestone", "next_milestone_date"})
                elif key in WRITEBACK_HEADERS:
                    changed_fields.add(key)
        row["changed"] = True
        row["changed_fields"] = sorted(changed_fields)
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.save(session)
        return row

    def update_project(self, topic_id: str, patch: dict) -> dict:
        session = self.load()
        row = _find_row(session, topic_id)
        changed_fields = set(row.get("changed_fields", []))

        if patch.get("mode"):
            for key, value in _project_mode_patch(str(patch["mode"])).items():
                if row.get(key, "") != value:
                    row[key] = value
                    changed_fields.add(key)

        mutable_fields = {"section", "topic_title", "date_created", "lifecycle", "focus", "previous_week_result"}
        for key, value in patch.items():
            if key not in mutable_fields:
                continue
            next_value = str(value).strip() if value is not None else ""
            if key == "topic_title" and not next_value:
                raise ValueError("Topic title is required.")
            if key == "lifecycle":
                next_value = _normalize_enum_value(next_value, Lifecycle, Lifecycle.ACTIVE.value)
            if key == "focus":
                next_value = _normalize_enum_value(next_value, YesNo, YesNo.NO.value)
            if row.get(key, "") != next_value:
                row[key] = next_value
                changed_fields.add(key)

        row["changed"] = True
        row["changed_fields"] = sorted(changed_fields)
        row["status"] = "row_updated"
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.save(session)
        return row

    def create_project(self, patch: dict) -> dict:
        session = self.load()
        title = str(patch.get("topic_title", "")).strip()
        if not title:
            raise ValueError("Topic title is required.")
        row_number = _next_local_row_number(session)
        mode_patch = _project_mode_patch(str(patch.get("mode", "project")))
        row = {
            "row_number": row_number,
            "topic_id": _next_topic_id(session),
            "topic_title": title,
            "section": mode_patch["section"],
            "lifecycle": mode_patch["lifecycle"],
            "focus": _normalize_enum_value(str(patch.get("focus", YesNo.NO.value)), YesNo, YesNo.NO.value),
            "date_created": str(patch.get("date_created", "") or datetime.now().strftime("%d.%m")).strip(),
            "previous_week_result": "",
            "raw_fact": "",
            "current_week_facts": "",
            "ai_draft_result": "",
            "final_result": "",
            "next_milestone": "",
            "next_milestone_date": "",
            "ball_side": "",
            "open_question_to_evgeny": "",
            "manual_current_week_facts": False,
            "manual_open_question_to_evgeny": False,
            "milestones": [],
            "milestones_text": "",
            "movement_type": MovementType.UNCLEAR.value,
            "needs_sync": YesNo.NO.value,
            "sync_reason": "",
            "source_links": "",
            "review_status": ReviewStatus.DRAFT.value,
            "status": "new",
            "changed": True,
            "changed_fields": list(FULL_WRITEBACK_HEADERS),
            "is_new": True,
            "hints": [],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        session["rows"].append(row)
        session["raw_rows"].append(_row_to_raw(row, session.get("csv_fieldnames", list(HEADERS.values()))))
        self.save(session)
        return row

    def archive_project(self, topic_id: str) -> dict:
        return self.update_project(topic_id, {"section": "Архив", "lifecycle": Lifecycle.ARCHIVED.value})

    def start_next_week(self, week_start: str = "", week_end: str = "", include_paused: bool = False) -> dict:
        session = self.load()
        next_start, next_end = _resolve_next_week_bounds(session, week_start, week_end)
        changed_count = 0
        for row in session["rows"]:
            lifecycle = row.get("lifecycle", Lifecycle.ACTIVE.value)
            if lifecycle in {Lifecycle.CLOSED.value, Lifecycle.ARCHIVED.value}:
                continue
            if lifecycle != Lifecycle.ACTIVE.value and not include_paused:
                continue

            previous_result = (
                row.get("final_result", "")
                or row.get("ai_draft_result", "")
                or row.get("current_week_facts", "")
                or row.get("previous_week_result", "")
            )
            row["previous_week_result"] = previous_result
            row["raw_fact"] = ""
            row["current_week_facts"] = ""
            row["ai_draft_result"] = ""
            row["final_result"] = ""
            row["movement_type"] = MovementType.UNCLEAR.value
            row["needs_sync"], row["sync_reason"] = _recommend_sync_for_session_row(row)
            row["review_status"] = ReviewStatus.DRAFT.value
            row["status"] = "empty"
            row["manual_current_week_facts"] = False
            row["manual_open_question_to_evgeny"] = False
            row["changed"] = True
            row["changed_fields"] = sorted(
                set(row.get("changed_fields", []))
                | {
                    "previous_week_result",
                    "current_week_facts",
                    "ai_draft_result",
                    "final_result",
                    "movement_type",
                    "needs_sync",
                    "sync_reason",
                    "review_status",
                }
            )
            row["updated_at"] = datetime.now().isoformat(timespec="seconds")
            changed_count += 1

        session.setdefault("metadata", {})["week_start"] = next_start.isoformat()
        session.setdefault("metadata", {})["week_end"] = next_end.isoformat()
        session["metadata"]["started_next_week_at"] = datetime.now().isoformat(timespec="seconds")
        if session["metadata"].get("source_format") == ACTIVE_SOURCE_FORMAT:
            session["metadata"].setdefault("active_sheet", {})["week_label"] = week_label_for_date(next_end)
        self.save(session)
        return {"updated_count": changed_count, "week_start": next_start.isoformat(), "week_end": next_end.isoformat()}

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
                "Сохраняй смысл и лексику пользователя; чисти только устную шероховатость и не усиливай результат."
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
        if _is_active_source(session):
            result = _write_active_session(adapter, session)
            self.save(session)
            return result
        changed_rows = [row for row in session["rows"] if row.get("changed")]
        updated = []
        new_row_ids = {row["topic_id"] for row in changed_rows if row.get("is_new")}
        new_rows = [row for row in changed_rows if row["topic_id"] in new_row_ids]
        if new_rows:
            values = [_sheet_row_values(row, FULL_WRITEBACK_HEADERS) for row in new_rows]
            result = adapter.append_values(_sheet_append_range(session["sheet_name"]), values)
            start_row = _parse_append_start_row(result)
            for offset, row in enumerate(new_rows):
                old_row_number = row["row_number"]
                if start_row:
                    row["row_number"] = start_row + offset
                row["is_new"] = False
                _mark_row_written(row)
                _sync_raw_row(session, row, old_row_number=old_row_number)
                updated.append(
                    {
                        "topic_id": row["topic_id"],
                        "topic_title": row["topic_title"],
                        "range": result.get("updates", {}).get("updatedRange", _sheet_append_range(session["sheet_name"])),
                        "result": result,
                    }
                )
        for row in changed_rows:
            if row["topic_id"] in new_row_ids:
                continue
            row_number = row["row_number"]
            fields = _write_fields_for_row(row)
            values = [_sheet_row_values(row, fields)]
            range_name = _sheet_range(session["sheet_name"], row_number, fields)
            result = adapter.update_values(range_name, values)
            updated.append({"topic_id": row["topic_id"], "topic_title": row["topic_title"], "range": range_name, "result": result})
            _mark_row_written(row)
            _sync_raw_row(session, row)
        self.save(session)
        return {"updated_count": len(updated), "updated": updated}

    def create_week_tab(self) -> dict:
        session = self.load()
        if _is_active_source(session):
            return {
                "sheet_name": session.get("sheet_name", ACTIVE_SHEET_NAME),
                "gid": session.get("gid", ACTIVE_GID),
                "created": False,
                "skipped": True,
                "result": {"message": "Active-source sessions write directly to Активные; weekly MVP tabs are not created."},
            }
        adapter = GoogleSheetsAdapter(self.settings)
        target_title = _weekly_sheet_title_for_session(session)
        source_title = session["sheet_name"]
        duplicate_result = (
            {"skipped": True, "properties": _sheet_properties_by_title(adapter, target_title)}
            if target_title == source_title
            else adapter.duplicate_sheet(source_title, target_title)
        )
        properties = duplicate_result.get("properties", {})
        _write_full_session_to_sheet(adapter, target_title, session)
        session["sheet_name"] = target_title
        if properties.get("sheetId") is not None:
            session["gid"] = str(properties["sheetId"])
        session.setdefault("metadata", {})["google_week_tab_created_at"] = datetime.now().isoformat(timespec="seconds")
        session["metadata"]["google_week_tab"] = target_title
        self.save(session)
        return {
            "sheet_name": target_title,
            "gid": session.get("gid", ""),
            "created": not duplicate_result.get("skipped", False),
            "result": duplicate_result.get("result", {}),
        }

    def export_active(self, week_label: str = "", include_open_questions: bool = False) -> dict:
        session = self.load()
        if _is_active_source(session):
            writeback = self.write_back()
            return {
                "week_label": writeback.get("week_label", _active_session_week_label(self.load())),
                "writeback": writeback,
                "created_week_columns": writeback.get("created_week_columns", []),
                "created_legacy_rows": [],
                "updated_count": writeback.get("updated_count", 0),
                "skipped": [],
                "summary_text": "Active-source session writes directly to Активные; legacy Export Active is skipped.",
            }
        writeback = self.write_back()
        session = self.load()
        label = week_label or _default_legacy_week_label(session)
        result = transfer_weekly_to_active(
            GoogleSheetsAdapter(self.settings),
            source_sheet=session["sheet_name"],
            target_sheet=DEFAULT_TARGET_SHEET,
            week_label=label,
            include_open_questions=include_open_questions,
            ensure_week_columns=True,
            create_missing_legacy_rows=True,
            apply=True,
        )
        for topic_id, _topic_title, legacy_row in result.created_legacy_rows:
            _set_legacy_row_in_session(session, topic_id, legacy_row)
        if result.created_legacy_rows:
            self.save(session)
        summary = format_legacy_transfer_result(result)
        return {
            "week_label": label,
            "writeback": writeback,
            "created_week_columns": list(result.created_week_columns),
            "created_legacy_rows": [
                {"topic_id": topic_id, "topic_title": topic_title, "legacy_row": legacy_row}
                for topic_id, topic_title, legacy_row in result.created_legacy_rows
            ],
            "updated_count": len(result.items),
            "skipped": list(result.skipped),
            "summary_text": summary,
        }

    def _initialize_session(self) -> None:
        if self.config.source_csv:
            shutil.copyfile(self.config.source_csv, self.source_path)
        else:
            download_sheet_csv(self.config.spreadsheet_id, self.config.gid, self.source_path)

        if is_active_sheet_csv(self.source_path):
            week_label = week_label_for_date(self.config.week_end)
            session_rows, raw_rows, active_metadata = build_active_session_rows_from_csv(self.source_path, week_label=week_label)
            hints = self._load_singularity_hints(session_rows)
            for row in session_rows:
                row["hints"] = hints.get(row["topic_title"], [])
            metadata = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "week_start": self.config.week_start.isoformat(),
                "week_end": self.config.week_end.isoformat(),
                "source_csv": str(self.source_path),
                "session_dir": str(self.config.session_dir),
                **active_metadata,
            }
            session = {
                "metadata": metadata,
                "spreadsheet_id": self.config.spreadsheet_id,
                "gid": self.config.gid or ACTIVE_GID,
                "sheet_name": ACTIVE_SHEET_NAME,
                "csv_fieldnames": list(HEADERS.values()),
                "raw_rows": raw_rows,
                "rows": session_rows,
            }
            self.save(session)
            return

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
                    "date_created": row.date_created,
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
                    "changed_fields": [],
                    "is_new": False,
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

    def _load_singularity_hints(self, source_rows: list | None = None) -> dict[str, list[str]]:
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
        if source_rows is None:
            try:
                source_rows = load_weekly_rows(self.source_path) if self.source_path.exists() else []
            except ValueError:
                source_rows = []
        for row in source_rows:
            topic_title = row.topic_title if hasattr(row, "topic_title") else str(row.get("topic_title", ""))
            row_hints = []
            for task in context.completed_this_week:
                if _loosely_matches(topic_title, task.topic):
                    row_hints.append(f"Выполнено {task.done_date}: {task.title}")
            for task in context.open_tasks:
                if _loosely_matches(topic_title, task.topic):
                    start = f" start={task.start_date}" if task.start_date else ""
                    row_hints.append(f"Открытая задача{start}: {task.title}")
            if row_hints:
                hints[topic_title] = row_hints
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
            elif path == "/api/project":
                self._send_json({"row": self.store.update_project(payload["topic_id"], payload.get("patch", {}))})
            elif path == "/api/project-create":
                self._send_json({"row": self.store.create_project(payload.get("patch", {}))})
            elif path == "/api/project-archive":
                self._send_json({"row": self.store.archive_project(payload["topic_id"])})
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
            elif path == "/api/create-week-tab":
                self._send_json(self.store.create_week_tab())
            elif path == "/api/export-active":
                self._send_json(
                    self.store.export_active(
                        week_label=payload.get("week_label", ""),
                        include_open_questions=bool(payload.get("include_open_questions", False)),
                    )
                )
            elif path == "/api/start-next-week":
                self._send_json(
                    self.store.start_next_week(
                        week_start=payload.get("week_start", ""),
                        week_end=payload.get("week_end", ""),
                        include_paused=bool(payload.get("include_paused", False)),
                    )
                )
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


def _is_active_source(session: dict) -> bool:
    metadata = session.get("metadata", {})
    return metadata.get("source_format") == ACTIVE_SOURCE_FORMAT or (
        session.get("sheet_name") == ACTIVE_SHEET_NAME and bool(metadata.get("active_sheet"))
    )


def _write_active_session(adapter: GoogleSheetsAdapter, session: dict) -> dict:
    target_sheet = session.get("metadata", {}).get("active_sheet", {}).get("sheet_name") or session.get("sheet_name") or ACTIVE_SHEET_NAME
    week_label = _active_session_week_label(session)
    columns, created_week_columns = ensure_active_week_columns(adapter, target_sheet=target_sheet, week_label=week_label)
    session.setdefault("metadata", {}).setdefault("active_sheet", {})["sheet_name"] = target_sheet
    session["metadata"]["active_sheet"]["week_label"] = week_label
    session["metadata"]["active_sheet"]["columns"] = columns.to_dict()
    session["metadata"]["active_sheet"]["created_week_columns"] = list(created_week_columns)

    updated = []
    changed_rows = [row for row in session["rows"] if row.get("changed") or row.get("is_new")]
    new_rows = [row for row in changed_rows if row.get("is_new")]
    if new_rows:
        values = [active_append_row_values(row, columns) for row in new_rows]
        append_width = max((len(value) for value in values), default=columns.question_col)
        append_range = f"'{target_sheet}'!A:{active_column_letter(append_width)}"
        result = adapter.append_values(append_range, values)
        start_row = _parse_append_start_row(result)
        for offset, row in enumerate(new_rows):
            old_row_number = row["row_number"]
            if start_row:
                row["row_number"] = start_row + offset
            row["is_new"] = False
            _mark_row_written(row)
            _sync_raw_row(session, row, old_row_number=old_row_number)
            updated.append(
                {
                    "topic_id": row["topic_id"],
                    "topic_title": row["topic_title"],
                    "range": result.get("updates", {}).get("updatedRange", append_range),
                    "fields": ["new_row"],
                    "result": result,
                }
            )

    new_row_ids = {row["topic_id"] for row in new_rows}
    for row in session["rows"]:
        if row.get("topic_id") in new_row_ids:
            continue
        if row.get("lifecycle") in {Lifecycle.CLOSED.value, Lifecycle.ARCHIVED.value} and not row.get("changed"):
            continue
        updates = active_row_updates(row, columns)
        if not updates:
            continue
        cell_results = []
        for col_index, value, field in updates:
            range_name = f"'{target_sheet}'!{active_column_letter(col_index)}{row['row_number']}"
            result = adapter.update_values(range_name, [[value]])
            cell_results.append({"range": range_name, "field": field, "result": result})
        if row.get("changed"):
            _mark_row_written(row)
            _sync_raw_row(session, row)
        updated.append(
            {
                "topic_id": row["topic_id"],
                "topic_title": row["topic_title"],
                "range": ", ".join(item["range"] for item in cell_results),
                "fields": [item["field"] for item in cell_results],
                "result": {"cells": cell_results},
            }
        )

    return {
        "updated_count": len(updated),
        "updated": updated,
        "target_sheet": target_sheet,
        "week_label": week_label,
        "created_week_columns": list(created_week_columns),
    }


def _active_session_week_label(session: dict) -> str:
    metadata = session.get("metadata", {})
    label = metadata.get("active_sheet", {}).get("week_label")
    if label:
        return str(label)
    week_end = _parse_iso_date(metadata.get("week_end", ""))
    return week_label_for_date(week_end or date.today())


def _next_topic_id(session: dict) -> str:
    max_number = 0
    for row in session.get("rows", []):
        match = re.match(r"^T-(\d+)$", str(row.get("topic_id", "")).strip())
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"T-{max_number + 1:03d}"


def _next_local_row_number(session: dict) -> int:
    numbers = []
    numbers.extend(int(row.get("_row_number", 1)) for row in session.get("raw_rows", []) if str(row.get("_row_number", "")).isdigit())
    numbers.extend(int(row.get("row_number", 1)) for row in session.get("rows", []) if str(row.get("row_number", "")).isdigit())
    return (max(numbers) if numbers else 1) + 1


def _project_mode_patch(value: str) -> dict[str, str]:
    normalized = (value or "project").strip().casefold()
    aliases = {
        "проект": "project",
        "projects": "project",
        "project": "project",
        "задача": "task",
        "задачи": "task",
        "tasks": "task",
        "task": "task",
        "холд": "hold",
        "на холде": "hold",
        "пауза": "hold",
        "paused": "hold",
        "hold": "hold",
    }
    return dict(PROJECT_MODE_PATCHES[aliases.get(normalized, normalized if normalized in PROJECT_MODE_PATCHES else "project")])


def _resolve_next_week_bounds(session: dict, week_start: str, week_end: str) -> tuple[date, date]:
    if week_start:
        start = date.fromisoformat(week_start)
    else:
        start = _next_friday_report_window(session)[0]
    if week_end:
        end = date.fromisoformat(week_end)
    else:
        end = start + timedelta(days=7)
    if end <= start:
        raise ValueError("Week end must be greater than week start.")
    return start, end


def _next_friday_report_window(session: dict) -> tuple[date, date]:
    metadata = session.get("metadata", {})
    current_start = _parse_iso_date(metadata.get("week_start", "")) or date.today()
    current_end = _parse_iso_date(metadata.get("week_end", ""))
    if current_end and current_end > current_start:
        if current_end.weekday() == REPORT_BOUNDARY_WEEKDAY:
            next_start = current_end
        else:
            next_start = _friday_on_or_after(current_end)
    else:
        next_start = _friday_after(current_start)
    return next_start, next_start + timedelta(days=7)


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _friday_on_or_after(value: date) -> date:
    return value + timedelta(days=(REPORT_BOUNDARY_WEEKDAY - value.weekday()) % 7)


def _friday_after(value: date) -> date:
    days_until_friday = (REPORT_BOUNDARY_WEEKDAY - value.weekday()) % 7
    return value + timedelta(days=days_until_friday or 7)


def _recommend_sync_for_session_row(row: dict) -> tuple[str, str]:
    if row.get("lifecycle") != Lifecycle.ACTIVE.value:
        return YesNo.NO.value, ""
    reasons = []
    if (row.get("open_question_to_evgeny") or "").strip():
        reasons.append("есть вопрос к Евгению")
    if "Евгений" in (row.get("ball_side") or ""):
        reasons.append("мяч у Евгения")
    if row.get("focus") == YesNo.YES.value and row.get("movement_type") == MovementType.UNCLEAR.value:
        reasons.append("focus-тема с неясным движением")
    if row.get("focus") == YesNo.YES.value and not (row.get("next_milestone_date") or "").strip():
        reasons.append("focus-тема без даты вехи")
    return (YesNo.YES.value, "; ".join(reasons)) if reasons else (YesNo.NO.value, "")


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
        if not patch.get("ball_side") and row.get("ball_side"):
            patch.pop("ball_side", None)
        row.update(patch)
        row["manual_current_week_facts"] = bool(row.get("current_week_facts"))
        row["manual_open_question_to_evgeny"] = bool(row.get("open_question_to_evgeny"))
        row["changed"] = True
        row["changed_fields"] = sorted(set(row.get("changed_fields", [])) | set(patch.keys()))
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
            "Начинай прямо с T-001 и иди по темам строго по одной в порядке topic_id.",
            "Задавай один вопрос: что по этой теме на этой неделе? Какая следующая веха: когда докатимся и куда?",
            "Сохраняй фактуру почти дословно; разрешена только легкая чистка устной речи.",
            "Не добавляй факты, даты, решения, артефакты, риски и выводы, которых пользователь явно не сказал.",
            "final_result соответствует колонке 'Куда мы докатились на этой'.",
            "milestones[] соответствует колонке 'Когда докатимся и куда'.",
            "ball_side соответствует колонке 'На чьей стороне мяч'. Меняй его только если пользователь явно сказал, у кого мяч.",
            "open_question_to_evgeny соответствует колонке 'Открытые вопросы'.",
            "movement_type, needs_sync и sync_reason — служебные поля JSON; в Google Sheet они пишутся в скрытые колонки.",
            "Статус предыдущей недели не заполняй сам: collector копирует его из предыдущей недельной колонки.",
            "Прошлая неделя, старые вехи и старые вопросы — только контекст для уточнения, не факт новой недели.",
            "Если old_open_question_to_evgeny не пустой, отдельно спроси, оставлять ли этот вопрос актуальным на эту неделю.",
            "Подтвержденный старый вопрос верни в open_question_to_evgeny; снятый или неподтвержденный старый вопрос верни пустой строкой.",
            "Вопрос к Евгению заполняй только если пользователь явно подтвердил, что это вопрос к Евгению.",
            "Если пользователь не назвал новую веху, верни пустой milestones[]; старую веху автоматически не копируй.",
            "После всех тем создай файл weekly_import_<week_end>.json с JSON по output_schema; не печатай большой JSON в чат.",
            "Если создание файла недоступно, верни один компактный JSON-блок без markdown.",
        ],
        "output_file": f"weekly_import_{session.get('metadata', {}).get('week_end', '')}.json",
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
    parsed = _parse_milestones_text(milestone)
    if parsed and (parsed[0].get("date") or not (next_milestone_date or "").strip()):
        return parsed
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
    else:
        row["next_milestone"] = ""
        row["next_milestone_date"] = ""


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
            for field in FULL_WRITEBACK_HEADERS:
                row[HEADERS[field]] = _writeback_value(update, field)
        row.pop("_row_number", None)
        rows.append(row)
    return rows


def _write_fields_for_row(row: dict) -> list[str]:
    changed_fields = set(row.get("changed_fields", []))
    if not changed_fields:
        return WRITEBACK_HEADERS
    if changed_fields & set(STRUCTURAL_WRITEBACK_HEADERS):
        return FULL_WRITEBACK_HEADERS
    if changed_fields & set(WRITEBACK_HEADERS):
        return WRITEBACK_HEADERS
    return WRITEBACK_HEADERS


def _sheet_row_values(row: dict, fields: list[str]) -> list[str]:
    return [_writeback_value(row, field) for field in fields]


def _sheet_range(sheet_name: str, row_number: int, fields: list[str]) -> str:
    keys = list(HEADERS.keys())
    start_column = _column_letter(keys.index(fields[0]) + 1)
    end_column = _column_letter(keys.index(fields[-1]) + 1)
    return f"'{sheet_name}'!{start_column}{row_number}:{end_column}{row_number}"


def _sheet_append_range(sheet_name: str) -> str:
    last_column = _column_letter(len(FULL_WRITEBACK_HEADERS))
    return f"'{sheet_name}'!A:{last_column}"


def _weekly_sheet_title_for_session(session: dict) -> str:
    metadata = session.get("metadata", {})
    end = _parse_iso_date(metadata.get("week_end", ""))
    if not end:
        end = _friday_on_or_after(date.today())
    return f"Weekly MVP {end.isoformat()}"


def _default_legacy_week_label(session: dict) -> str:
    sheet_date = _date_from_weekly_sheet_title(session.get("sheet_name", ""))
    label_date = sheet_date or _parse_iso_date(session.get("metadata", {}).get("week_end", "")) or date.today()
    return f"{label_date.day}.{label_date.month:02d}"


def _date_from_weekly_sheet_title(value: str) -> date | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value or "")
    return _parse_iso_date(match.group(1)) if match else None


def _sheet_properties_by_title(adapter: GoogleSheetsAdapter, title: str) -> dict:
    for properties in adapter.sheet_properties():
        if properties.get("title") == title:
            return properties
    raise ValueError(f"Sheet not found: {title}")


def _write_full_session_to_sheet(adapter: GoogleSheetsAdapter, sheet_name: str, session: dict) -> dict:
    rows = [_row_values_for_fieldnames(row, session, session.get("csv_fieldnames", list(HEADERS.values()))) for row in session["rows"]]
    if not rows:
        return {"updatedRows": 0}
    end_column = _column_letter(len(session.get("csv_fieldnames", list(HEADERS.values()))))
    range_name = f"'{sheet_name}'!A2:{end_column}{len(rows) + 1}"
    return adapter.update_values(range_name, rows)


def _row_values_for_fieldnames(row: dict, session: dict, fieldnames: list[str]) -> list[str]:
    raw = _find_raw_row(session, row) or {}
    values = []
    for fieldname in fieldnames:
        key = _field_key_for_header(fieldname)
        values.append(_writeback_value(row, key) if key else raw.get(fieldname, ""))
    return values


def _set_legacy_row_in_session(session: dict, topic_id: str, legacy_row: int) -> None:
    for raw in session.get("raw_rows", []):
        if raw.get(HEADERS["topic_id"]) == topic_id:
            raw["Legacy row"] = str(legacy_row)
            return


def _field_key_for_header(header: str) -> str | None:
    for key, value in HEADERS.items():
        if value == header:
            return key
    return None


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _parse_append_start_row(result: dict) -> int | None:
    updated_range = str(result.get("updates", {}).get("updatedRange", ""))
    match = re.search(r"![A-Z]+(\d+):", updated_range)
    return int(match.group(1)) if match else None


def _mark_row_written(row: dict) -> None:
    row["changed"] = False
    row["changed_fields"] = []
    row["written_at"] = datetime.now().isoformat(timespec="seconds")


def _sync_raw_row(session: dict, row: dict, old_row_number: int | None = None) -> None:
    raw = _find_raw_row(session, row, old_row_number=old_row_number)
    if raw is None:
        raw = _row_to_raw(row, session.get("csv_fieldnames", list(HEADERS.values())))
        session.setdefault("raw_rows", []).append(raw)
    raw["_row_number"] = row["row_number"]
    for field in FULL_WRITEBACK_HEADERS:
        raw[HEADERS[field]] = _writeback_value(row, field)


def _find_raw_row(session: dict, row: dict, old_row_number: int | None = None) -> dict | None:
    topic_id_header = HEADERS["topic_id"]
    for raw in session.get("raw_rows", []):
        if raw.get(topic_id_header) == row.get("topic_id"):
            return raw
    if old_row_number is not None:
        for raw in session.get("raw_rows", []):
            if raw.get("_row_number") == old_row_number:
                return raw
    for raw in session.get("raw_rows", []):
        if raw.get("_row_number") == row.get("row_number"):
            return raw
    return None


def _row_to_raw(row: dict, fieldnames: list[str]) -> dict:
    raw = {fieldname: "" for fieldname in fieldnames}
    raw["_row_number"] = row["row_number"]
    for field in FULL_WRITEBACK_HEADERS:
        raw[HEADERS[field]] = _writeback_value(row, field)
    return raw


def _writeback_value(row: dict, field: str) -> str:
    value = row.get(field, "")
    if field == "lifecycle" and value not in {item.value for item in Lifecycle}:
        return Lifecycle.ACTIVE.value
    if field == "focus" and value not in {item.value for item in YesNo}:
        return YesNo.NO.value
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


# Claude Design handoff implementation for the vanilla collector frontend.
INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Weekly Collector</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="collector-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">W</div>
        <div class="brand-copy">
          <h1>Weekly Collector</h1>
          <div id="sourceLabel" class="brand-subtitle">Активные · проверка недели</div>
        </div>
        <div id="period" class="period-pill"></div>
      </div>
      <section class="progress-box" aria-label="Прогресс проверки">
        <div class="progress-head"><span>Проверено</span><strong id="progressSummary">0 / 0 тем</strong></div>
        <div class="progress-track"><div id="progressBar" class="progress-bar"></div></div>
        <div id="progressMeta" class="progress-meta">0 ждут · 0 без данных</div>
      </section>
      <nav class="top-actions" aria-label="Действия сессии">
        <button id="chatgptContextBtn" class="btn btn-ghost" title="Скопировать JSON-контекст для ChatGPT в буфер">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v7A1.5 1.5 0 0 0 3.5 12H5"/></svg>
          <span>Контекст для GPT</span>
        </button>
        <button id="exportFileBtn" class="btn btn-ghost" title="Скачать JSON-контекст файлом">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v7"/><path d="M5 7l3 3 3-3"/><path d="M3 13h10"/></svg>
          <span>Экспорт в файл</span>
        </button>
        <button id="openImportBtn" class="btn btn-primary" title="Импортировать результат интервью из ChatGPT (.json)">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9"/><path d="M9 5l3 3-3 3"/></svg>
          <span>Импорт результата</span>
        </button>
        <input id="importFileInput" type="file" accept=".json,application/json" hidden>
        <div class="final-actions">
          <button id="finalMenuBtn" class="btn btn-warn" aria-expanded="false">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 11V3"/><path d="M5 6l3-3 3 3"/><path d="M3 13h10"/></svg>
            <span>Write Active</span>
            <svg class="caret" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg>
          </button>
          <div id="finalMenu" class="final-popover" hidden>
            <div id="readyNote" class="ready-note">Проверь поля перед записью.</div>
            <button id="writeBtn" class="btn btn-warn-solid">Записать в «Активные»</button>
            <button id="nextWeekBtn" class="btn btn-subtle full">Start next week</button>
          </div>
        </div>
      </nav>
    </header>

    <section class="main-layout">
      <aside class="panel queue-panel" id="queuePanel">
        <button id="expandQueueBtn" class="queue-expand" title="Развернуть очередь" aria-label="Развернуть очередь">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>
        </button>
        <div class="queue-head">
          <div class="section-title">Очередь тем</div>
          <div class="queue-head-actions">
          <details class="mini-form" id="newProjectPanel">
            <summary><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 4v8M4 8h8"/></svg>тема</summary>
            <div class="mini-form-body">
              <label for="newProjectTitle">Новая строка</label>
              <input id="newProjectTitle" placeholder="Название проекта или задачи">
              <select id="newProjectMode">
                <option value="project">Проект</option>
                <option value="task">Задача</option>
                <option value="hold">На холде</option>
              </select>
              <button id="newProjectBtn" class="btn btn-subtle full">Добавить строку</button>
              <div id="newProjectResult" class="muted"></div>
            </div>
          </details>
          <button id="collapseQueueBtn" class="icon-only" title="Свернуть очередь" aria-label="Свернуть очередь">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 4l-4 4 4 4"/></svg>
          </button>
          </div>
        </div>
        <div class="search-wrap">
          <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4"/><path d="M13 13l-3-3"/></svg>
          <input id="topicSearch" placeholder="Поиск темы...">
        </div>
        <div id="stats" class="stats"></div>

        <nav id="topicList" class="topic-list" aria-label="Темы"></nav>
      </aside>

      <section class="panel editor-panel">
        <header class="editor-head">
          <div class="topic-line">
            <span id="topicMeta" class="topic-meta"></span>
            <div class="topic-line-right">
              <div class="topic-nav">
                <button id="prevTopicBtn" class="icon-only" title="Предыдущая тема">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 4l-4 4 4 4"/></svg>
                </button>
                <span id="topicPosition" class="topic-position">0 / 0</span>
                <button id="nextTopicBtn" class="icon-only" title="Следующая тема">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>
                </button>
              </div>
              <button class="btn review-next btn-ok" data-review-next title="Пометить проверенной и перейти к следующей">
                <span class="rn-label">Проверено и дальше</span>
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9"/><path d="M9 5l3 3-3 3"/></svg>
              </button>
            </div>
          </div>
          <div class="topic-title-row">
            <h2 id="topicTitle">Загрузка...</h2>
            <span id="reviewBadge" class="review-badge">Нет данных</span>
          </div>
          <div class="status-row" aria-label="Быстрый статус">
            <span>статус:</span>
            <button data-status="result">Результат</button>
            <button data-status="process_only">Процесс</button>
            <button data-status="question_only">Вопрос</button>
            <button data-status="not_touched">Не трогал</button>
          </div>
        </header>

        <div class="editor-scroll">
          <div id="emptyState" class="empty-state" hidden>
            <div class="empty-icon"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9"/><path d="M9 5l3 3-3 3"/></svg></div>
            <div><strong>Нет данных по теме</strong><p>Импортируй результат интервью из ChatGPT или заполни weekly-поля вручную.</p></div>
            <button id="emptyImportBtn" class="btn btn-primary">Импорт результата</button>
          </div>

          <section class="sheet-fields">
            <div class="divider-title"><span>Уйдет в «Активные»</span><i></i></div>
            <label for="finalResult">Итоговая формулировка</label>
            <textarea id="finalResult" class="textarea-hero" rows="4" placeholder="Одно-два предложения: что по итогу недели."></textarea>
            <div class="label-row"><label for="milestonesText">Все вехи</label><span>когда докатимся и куда</span></div>
            <textarea id="milestonesText" rows="4" placeholder="Одна веха на строку: дата текст. В Excel будет собрана в одну строку через ;"></textarea>
            <div class="two-col">
              <div>
                <label for="ballSide">Мяч</label>
                <input id="ballSide" placeholder="На чьей стороне ход">
              </div>
              <div>
                <label for="question">Вопрос к Евгению</label>
                <input id="question" placeholder="Что нужно решить">
              </div>
            </div>
          </section>

          <details class="collapse-box">
            <summary>Исходные данные <span>для сверки</span></summary>
            <div class="collapse-body">
              <label for="rawFact">Сырой ввод</label>
              <textarea id="rawFact" rows="5" placeholder="Как наговорено, без формулировок."></textarea>
              <label for="facts">Факты этой недели</label>
              <textarea id="facts" rows="3" placeholder="Опорные факты, цифры, источники."></textarea>
            </div>
          </details>

          <details class="collapse-box service-box">
            <summary>Служебная проверка <span>hidden columns</span></summary>
            <div class="collapse-body">
              <div class="two-col">
                <div>
                  <label for="movement">Movement</label>
                  <select id="movement">
                    <option value="unclear">unclear</option>
                    <option value="real_result">real_result</option>
                    <option value="no_movement">no_movement</option>
                  </select>
                </div>
                <div>
                  <label for="sync">Sync</label>
                  <select id="sync">
                    <option value="no">no</option>
                    <option value="yes">yes</option>
                  </select>
                </div>
              </div>
              <label for="syncReason">Причина sync</label>
              <input id="syncReason">
              <label for="reviewStatus">Review status</label>
              <select id="reviewStatus">
                <option value="draft">draft</option>
                <option value="reviewed">reviewed</option>
                <option value="final">final</option>
              </select>
              <div class="two-col">
                <div>
                  <label for="milestoneDate">Дата вехи</label>
                  <input id="milestoneDate">
                </div>
                <div>
                  <label for="milestone">Ближайшая веха</label>
                  <textarea id="milestone" rows="2"></textarea>
                </div>
              </div>
            </div>
          </details>
        </div>

        <footer class="editor-footer">
          <span id="saveState" class="save-state"><i></i>Сохранено</span>
          <div class="footer-actions">
            <button class="btn review-next btn-ok" data-review-next title="Пометить проверенной и перейти к следующей">
              <span class="rn-label">Проверено и дальше</span>
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9"/><path d="M9 5l3 3-3 3"/></svg>
            </button>
          </div>
        </footer>
      </section>

      <aside class="panel context-panel">
        <div class="context-scroll">
          <section class="context-block">
            <div class="context-title">Прошлая неделя</div>
            <pre id="previousResult"></pre>
          </section>
          <section class="context-block">
            <div class="context-head"><div class="context-title">Вехи · контекст</div><span>read-only</span></div>
            <div id="contextMilestones" class="context-milestones"></div>
          </section>
          <section class="context-block">
            <div class="context-title">AI draft</div>
            <pre id="aiDraft"></pre>
          </section>
          <section class="context-block">
            <div class="context-title">Singularity</div>
            <ul id="hints"></ul>
          </section>
          <details class="collapse-box project-editor">
            <summary>Настройки строки</summary>
            <div class="collapse-body">
              <label for="projectTitle">Название</label>
              <input id="projectTitle">
              <div class="two-col">
                <div>
                  <label for="projectMode">Тип</label>
                  <select id="projectMode">
                    <option value="project">Проект</option>
                    <option value="task">Задача</option>
                    <option value="hold">На холде</option>
                  </select>
                </div>
                <div>
                  <label for="projectFocus">Focus</label>
                  <select id="projectFocus">
                    <option value="no">no</option>
                    <option value="yes">yes</option>
                  </select>
                </div>
              </div>
              <label for="projectDateCreated">Дата постановки</label>
              <input id="projectDateCreated">
              <div class="form-actions">
                <button id="saveProjectBtn" class="btn btn-subtle">Save row</button>
                <button id="archiveProjectBtn" class="btn btn-danger">Archive</button>
              </div>
            </div>
          </details>
          <div id="message" class="message"></div>
        </div>
      </aside>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  color-scheme: light;
  --bg: #f4f4f1;
  --panel: #fff;
  --ink: #25241f;
  --ink-2: #4a4944;
  --muted: #6f6e68;
  --muted-2: #9c9b93;
  --muted-3: #b4b3ab;
  --line: #e7e7e3;
  --line-2: #eeeeea;
  --accent: #3a5bd0;
  --accent-soft: #eef1fc;
  --accent-border: #c8d2f6;
  --warn: #9a6912;
  --warn-bg: #fdf7e8;
  --warn-border: #e3cd92;
  --ok: #2e8b57;
  --ok-bg: #e8f3ec;
  --danger: #b42318;
  --danger-bg: #fff1f0;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
button, input, textarea, select { font: inherit; color: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .45; }
svg { width: 15px; height: 15px; display: block; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
button svg { pointer-events: none; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: #dcdcd6; border-radius: 8px; border: 3px solid transparent; background-clip: padding-box; }
::placeholder { color: var(--muted-3); }
.collector-shell { min-height: 100vh; display: flex; flex-direction: column; }
.topbar {
  height: 58px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  position: sticky;
  top: 0;
  z-index: 20;
}
.brand { display: flex; align-items: center; gap: 11px; min-width: 0; flex-shrink: 0; }
.brand-mark { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: var(--accent); color: #fff; font-weight: 750; font-size: 15px; }
.brand-copy { display: flex; flex-direction: column; line-height: 1.15; }
.brand h1 { margin: 0; font-size: 14px; font-weight: 680; letter-spacing: -.2px; }
.brand-subtitle { margin-top: 2px; color: var(--muted-2); font-size: 11px; }
.period-pill { display: inline-flex; align-items: center; gap: 7px; min-height: 28px; margin-left: 6px; padding: 5px 10px; border: 1px solid var(--line); border-radius: 7px; color: #56554f; font-size: 12px; font-weight: 560; white-space: nowrap; }
.period-pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--ok); }
.progress-box { flex: 0 1 220px; min-width: 150px; }
.progress-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 5px; }
.progress-head span { color: var(--muted-2); font-size: 11px; font-weight: 650; letter-spacing: .4px; text-transform: uppercase; }
.progress-head strong { color: var(--muted); font-size: 12px; font-weight: 500; font-variant-numeric: tabular-nums; }
.progress-head b { color: var(--ink); }
.progress-track { height: 6px; border-radius: 6px; background: #ecece7; overflow: hidden; }
.progress-bar { width: 0; height: 100%; border-radius: 6px; background: var(--accent); transition: width .25s ease, background .25s ease; }
.progress-meta { margin-top: 5px; color: var(--muted-2); font-size: 11px; font-variant-numeric: tabular-nums; }
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; min-width: 0; }
.btn { min-height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 0 13px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: #3a3934; font-size: 12.5px; font-weight: 600; white-space: nowrap; transition: background .12s, border-color .12s, filter .12s; }
.btn:hover { background: #f4f4f1; border-color: #dcdcd6; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn-primary:hover, .btn-warn-solid:hover, .btn-ok:hover { filter: brightness(.96); }
.btn-subtle { min-height: 32px; color: #56554f; }
.btn-warn { border-color: var(--warn-border); background: var(--warn-bg); color: var(--warn); }
.btn-warn:hover { background: #f8efd8; border-color: #d6bd77; }
.btn-warn .caret { width: 13px; height: 13px; transition: transform .15s; }
.btn-warn[aria-expanded="true"] .caret { transform: rotate(180deg); }
.btn-warn-solid { width: 100%; border-color: #b07a1e; background: #b07a1e; color: #fff; }
.btn-ok { border-color: var(--ok); background: var(--ok); color: #fff; }
.btn-ok.reviewed { border-color: #bfe0cd; background: var(--ok-bg); color: var(--ok); }
.btn-danger { border-color: #f1bbb7; background: var(--danger-bg); color: var(--danger); }
.full { width: 100%; }
.final-actions { position: relative; }
.final-popover { position: absolute; top: calc(100% + 8px); right: 0; width: 310px; padding: 13px; border: 1px solid var(--line); border-radius: 10px; background: #fff; box-shadow: 0 8px 28px rgba(40, 38, 30, .14); z-index: 30; }
.ready-note { margin-bottom: 11px; padding: 8px 10px; border-radius: 7px; background: #fbf3e1; color: #8a6314; font-size: 12px; line-height: 1.5; }
.ready-note.ready { background: var(--ok-bg); color: #2e7a4e; }
.final-popover .btn + .btn { margin-top: 7px; }
.main-layout { flex: 1; min-height: 0; display: flex; gap: 16px; padding: 16px; overflow: hidden; }
.panel { min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }
.queue-panel { width: 300px; flex: none; overflow: hidden; }
.editor-panel { flex: 1; min-width: 0; overflow: hidden; }
.context-panel { width: 336px; flex: none; overflow: hidden; }
.queue-head { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 14px 14px 10px; border-bottom: 1px solid var(--line-2); }
.queue-head-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
.queue-expand { width: 100%; min-height: 46px; display: none; align-items: center; justify-content: center; border: 0; border-bottom: 1px solid var(--line-2); background: transparent; color: var(--muted-2); }
.queue-expand:hover { background: #f4f4f1; color: #56554f; }
.queue-panel.collapsed { width: 56px; }
.queue-panel.collapsed .queue-head,
.queue-panel.collapsed .search-wrap,
.queue-panel.collapsed .stats,
.queue-panel.collapsed .utility-panel { display: none; }
.queue-panel.collapsed .queue-expand { display: flex; }
.queue-panel.collapsed .topic-list { align-items: center; gap: 5px; padding: 6px; }
.topic-rail-item { width: 42px; height: 42px; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; border: 1px solid transparent; border-radius: 9px; background: transparent; color: inherit; transition: background .12s, border-color .12s; }
.topic-rail-item:hover { background: #f4f4f1; }
.topic-rail-item.active { border-color: var(--accent-border); background: var(--accent-soft); }
.topic-rail-item .topic-dot { width: 8px; height: 8px; }
.topic-rail-item.done .topic-dot { border-color: var(--accent); background: var(--accent); }
.topic-rail-item.reviewed .topic-dot { border-color: var(--ok); background: var(--ok); }
.topic-rail-item code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; font-weight: 650; color: #a3a29b; }
.topic-rail-item.active code { color: var(--accent); }
.section-title, .context-title { color: var(--muted-2); font-size: 11px; font-weight: 680; letter-spacing: .6px; text-transform: uppercase; }
.mini-form { position: relative; }
.mini-form summary, .utility-panel summary, .collapse-box summary { cursor: pointer; list-style: none; }
.mini-form summary::-webkit-details-marker, .utility-panel summary::-webkit-details-marker, .collapse-box summary::-webkit-details-marker { display: none; }
.mini-form summary { display: inline-flex; align-items: center; gap: 4px; padding: 4px 6px; border-radius: 6px; color: var(--accent); font-size: 12px; font-weight: 600; }
.mini-form summary:hover { background: var(--accent-soft); }
.mini-form-body { position: absolute; right: 0; top: calc(100% + 8px); width: 260px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: #fff; box-shadow: 0 8px 28px rgba(40, 38, 30, .12); z-index: 15; }
.mini-form-body select, .mini-form-body .btn { margin-top: 8px; }
.search-wrap { position: relative; flex-shrink: 0; margin: 10px 14px 8px; }
.search-wrap svg { position: absolute; left: 10px; top: 50%; width: 14px; height: 14px; transform: translateY(-50%); color: var(--muted-3); pointer-events: none; }
.search-wrap input { padding-left: 31px; }
input, textarea, select { width: 100%; border: 1px solid #e3e3dd; border-radius: 9px; background: #fff; outline: none; transition: border-color .12s, box-shadow .12s, background .12s; }
input, select { min-height: 36px; padding: 8px 11px; }
textarea { padding: 11px 13px; resize: vertical; line-height: 1.55; }
input:focus, textarea:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
label { display: block; margin: 0 0 7px; color: #56554f; font-size: 11px; font-weight: 680; letter-spacing: .3px; }
.muted { margin-top: 8px; color: var(--muted-2); font-size: 11.5px; }
.stats { flex-shrink: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; padding: 0 14px 10px; border-bottom: 1px solid var(--line-2); }
.stat { min-width: 0; padding: 7px 8px; border: 1px solid var(--line-2); border-radius: 8px; background: #fafaf8; color: var(--muted-2); font-size: 10.5px; line-height: 1.2; }
.stat strong { display: block; margin-bottom: 3px; color: var(--ink); font-size: 16px; font-weight: 680; font-variant-numeric: tabular-nums; }
.utility-panel { flex-shrink: 0; border-bottom: 1px solid var(--line-2); }
.utility-panel summary, .collapse-box summary { min-height: 40px; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 14px; color: var(--muted); font-size: 12.5px; font-weight: 600; }
.utility-panel summary:hover, .collapse-box summary:hover { background: #fafaf8; }
.utility-panel summary::after, .collapse-box summary::after { content: ""; width: 7px; height: 7px; border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor; transform: rotate(45deg); transition: transform .15s; }
.utility-panel[open] summary::after, .collapse-box[open] summary::after { transform: rotate(225deg); }
.utility-body { display: flex; flex-direction: column; gap: 8px; padding: 0 14px 14px; }
.topic-list { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 3px; padding: 8px; }
.topic-item { width: 100%; min-height: 56px; display: flex; align-items: center; gap: 11px; padding: 9px 10px; border: 1px solid transparent; border-radius: 9px; background: #fff; color: inherit; text-align: left; transition: background .12s, border-color .12s; }
.topic-item:hover { background: #f6f6f3; }
.topic-item.active { border-color: var(--accent-border); background: var(--accent-soft); }
.topic-dot { width: 9px; height: 9px; border-radius: 50%; border: 2px solid #d2d1ca; flex-shrink: 0; }
.topic-item.done .topic-dot { border-color: var(--accent); background: var(--accent); }
.topic-item.reviewed .topic-dot { border-color: var(--ok); background: var(--ok); }
.topic-copy { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; }
.topic-title { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: var(--ink); font-size: 13px; font-weight: 580; }
.topic-item.empty .topic-title { color: var(--muted-2); }
.topic-meta-small { display: flex; align-items: center; gap: 6px; min-width: 0; color: #a3a29b; font-size: 11px; }
.topic-meta-small code { color: var(--muted-2); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 650; }
.topic-item.active .topic-meta-small code { color: var(--accent); }
.topic-flags { display: flex; align-items: center; gap: 5px; flex-shrink: 0; }
.flag { display: inline-flex; align-items: center; justify-content: center; color: var(--muted-2); font-size: 12px; font-weight: 750; }
.flag.question { color: #b07a1e; }
.flag.changed { width: 6px; height: 6px; border-radius: 50%; background: #b07a1e; }
.flag.imported { color: var(--accent); }
.editor-head { flex-shrink: 0; padding: 18px 26px 15px; border-bottom: 1px solid var(--line-2); }
.topic-line { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 9px; }
.topic-meta { min-width: 0; color: var(--muted-2); font-size: 11.5px; }
.topic-meta code { padding: 2px 7px; border-radius: 5px; background: var(--accent-soft); color: var(--accent); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 650; letter-spacing: .3px; }
.topic-nav { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.topic-line-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.review-next { gap: 7px; }
.icon-only { width: 30px; height: 30px; display: grid; place-items: center; border: 0; border-radius: 7px; background: transparent; color: #56554f; }
.icon-only:hover { background: #f4f4f1; }
.topic-position { min-width: 42px; text-align: center; color: var(--muted-2); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.topic-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.topic-title-row h2 { margin: 0; min-width: 0; color: var(--ink); font-size: 21px; font-weight: 680; letter-spacing: -.4px; line-height: 1.2; }
.review-badge { flex-shrink: 0; display: inline-flex; align-items: center; padding: 4px 11px; border-radius: 20px; background: #f2f2ee; color: var(--muted-2); font-size: 11px; font-weight: 650; }
.review-badge.reviewed { background: var(--ok-bg); color: var(--ok); }
.review-badge.pending { background: #fbf3e1; color: #b07a1e; }
.status-row { margin-top: 14px; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.status-row > span { margin-right: 2px; color: #a3a29b; font-size: 11px; }
.status-row button { min-height: 31px; display: inline-flex; align-items: center; gap: 6px; padding: 6px 11px; border: 1px solid var(--line); border-radius: 7px; background: #fff; color: #8a8980; font-size: 12px; font-weight: 650; }
.status-row button::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; opacity: .55; }
.status-row button.active { color: var(--accent); border-color: var(--accent-border); background: var(--accent-soft); }
.status-row button[data-status="result"].active { color: var(--ok); border-color: #bfe0cd; background: var(--ok-bg); }
.status-row button[data-status="question_only"].active { color: #b07a1e; border-color: #ecd9ab; background: #fbf3e1; }
.status-row button[data-status="not_touched"].active { color: var(--muted-2); border-color: #e2e2dc; background: #f2f2ee; }
.editor-scroll, .context-scroll { flex: 1; min-height: 0; overflow-y: auto; }
.editor-scroll { display: flex; flex-direction: column; gap: 20px; padding: 20px 26px 24px; }
.empty-state { align-items: center; gap: 14px; padding: 18px; border: 1px dashed #ddddd6; border-radius: 10px; background: #fafaf8; }
.empty-state:not([hidden]) { display: flex; }
.empty-icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 9px; background: var(--accent-soft); color: var(--accent); flex-shrink: 0; }
.empty-state strong { display: block; margin-bottom: 2px; font-size: 13.5px; font-weight: 650; }
.empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
.sheet-fields { display: flex; flex-direction: column; gap: 18px; }
.divider-title { display: flex; align-items: center; gap: 9px; }
.divider-title span { color: var(--accent); font-size: 11px; font-weight: 700; letter-spacing: .5px; text-transform: uppercase; }
.divider-title i { height: 1px; flex: 1; background: var(--accent-border); }
.textarea-hero { min-height: 96px; border-color: var(--accent-border); font-size: 14px; }
.label-row { margin-bottom: -11px; display: flex; align-items: baseline; gap: 9px; }
.label-row label { margin: 0; }
.label-row span { color: #a3a29b; font-size: 11px; }
.two-col { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
.collapse-box { border: 1px solid var(--line-2); border-radius: 9px; overflow: hidden; }
.collapse-box summary span { color: #a3a29b; font-weight: 400; }
.collapse-body { padding: 0 13px 13px; }
.collapse-body label { margin-top: 12px; }
.collapse-body label:first-child { margin-top: 0; }
.service-box { background: #fcfcfb; }
.editor-footer { min-height: 58px; flex-shrink: 0; display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 6px; row-gap: 8px; padding: 11px 16px; border-top: 1px solid var(--line-2); background: #fcfcfb; }
.editor-footer .btn { padding: 0 11px; }
.editor-footer .save-state { flex: 1 1 auto; min-width: 0; }
.footer-actions .btn-ghost { padding: 0 9px; }
.save-state { display: inline-flex; align-items: center; gap: 6px; color: var(--ok); font-size: 11.5px; }
.save-state i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.save-state.dirty { color: #b07a1e; }
.footer-actions { display: flex; flex-wrap: nowrap; align-items: center; gap: 6px; }
.context-scroll { display: flex; flex-direction: column; gap: 16px; padding: 16px; }
.context-block pre { margin: 9px 0 0; padding: 12px 13px; border: 1px solid var(--line-2); border-radius: 9px; background: #fafaf8; color: var(--ink-2); font: inherit; font-size: 12.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.context-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 9px; }
.context-head span { color: var(--muted-3); font-size: 10.5px; }
.context-milestones { display: flex; flex-direction: column; gap: 6px; }
.milestone-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 11px; border: 1px solid var(--line-2); border-radius: 8px; }
.milestone-row span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); font-size: 12.5px; }
.milestone-row .date { display: inline-flex; align-items: center; gap: 6px; color: #8a8980; font-size: 11px; font-weight: 650; font-variant-numeric: tabular-nums; white-space: nowrap; }
.milestone-row .date::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
#hints { margin: 9px 0 0; padding-left: 18px; color: var(--ink-2); font-size: 12.5px; }
#hints li { margin-bottom: 7px; }
.form-actions { display: flex; gap: 8px; margin-top: 12px; }
.message { min-height: 20px; color: var(--muted-2); font-size: 12px; }
.message.error { color: var(--danger); }
@media (max-width: 1180px) {
  .progress-box { display: none; }
  .top-actions .btn-ghost span { display: none; }
  .queue-panel { width: 280px; }
  .context-panel { width: 320px; }
}
@media (max-width: 980px) {
  .topbar { height: auto; min-height: 58px; flex-wrap: wrap; align-items: flex-start; padding: 10px 12px; }
  .brand { flex: 1 1 100%; }
  .period-pill { margin-left: auto; }
  .top-actions { width: 100%; margin-left: 0; overflow-x: auto; padding-bottom: 2px; }
  .main-layout { min-height: auto; flex-direction: column; overflow: visible; padding: 12px; }
  .panel, .queue-panel, .editor-panel, .context-panel { width: 100%; min-height: auto; overflow: visible; }
  #collapseQueueBtn { display: none; }
  .topic-line-right .review-next { display: none; }
  .topic-list { max-height: 420px; overflow-y: auto; }
  .editor-scroll, .context-scroll { overflow: visible; max-height: none; }
  .editor-footer { position: sticky; bottom: 0; z-index: 5; flex-wrap: wrap; box-shadow: 0 -6px 16px rgba(40, 38, 30, .06); }
  .footer-actions { width: 100%; margin-left: 0; flex-wrap: wrap; }
  .footer-actions .btn { flex: 1 1 150px; }
}
@media (max-width: 620px) {
  .period-pill { width: 100%; margin: 8px 0 0; }
  .top-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .top-actions .btn, .final-actions, .final-actions .btn { width: 100%; }
  .final-popover { right: auto; left: 0; width: min(310px, calc(100vw - 24px)); }
  .stats, .two-col { grid-template-columns: 1fr; }
  .editor-head, .editor-scroll, .editor-footer { padding-left: 14px; padding-right: 14px; }
  .topic-title-row { display: block; }
  .review-badge { margin-top: 10px; }
  .empty-state { align-items: flex-start; flex-direction: column; }
}
"""


APP_JS = """
let session = null;
let selectedId = null;
let dirty = false;
let projectDirty = false;
let topicQuery = "";
let queueCollapsed = (() => { try { return localStorage.getItem("wc_queue_collapsed") === "1"; } catch (error) { return false; } })();

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
  syncReason: "sync_reason",
  reviewStatus: "review_status"
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
  selectedId = selectedId || firstVisibleRow()?.topic_id;
  render();
}

function activeRows() {
  return session.rows.filter(row => row.lifecycle === "active");
}

function visibleRows() {
  return session.rows.filter(row => !["archived", "closed"].includes(row.lifecycle));
}

function filteredRows() {
  const q = topicQuery.trim().toLowerCase();
  const rows = visibleRows();
  if (!q) return rows;
  return rows.filter(row => [row.topic_id, row.topic_title, row.section].some(value => String(value || "").toLowerCase().includes(q)));
}

function firstVisibleRow() {
  return visibleRows()[0] || session.rows[0];
}

function selectedRow() {
  const row = session.rows.find(row => row.topic_id === selectedId);
  if (row && !["archived", "closed"].includes(row.lifecycle)) return row;
  return firstVisibleRow();
}

function projectMode(row) {
  if (row.lifecycle === "paused") return "hold";
  const section = String(row.section || "").toLowerCase();
  if (section.includes("зада")) return "task";
  return "project";
}

function projectModeLabel(row) {
  const labels = { project: "Проект", task: "Задача", hold: "На холде" };
  return labels[projectMode(row)] || row.section || row.lifecycle;
}

function isFilled(row) {
  return Boolean(String(row.final_result || "").trim());
}

function hasEvidence(row) {
  return Boolean([row.raw_fact, row.current_week_facts, row.final_result, row.milestones_text, row.next_milestone, row.open_question_to_evgeny].some(value => String(value || "").trim()));
}

function isReviewed(row) {
  return isFilled(row) && ["reviewed", "final"].includes(row.review_status);
}

function rowStatusKey(row) {
  if (["result", "process_only", "question_only", "not_touched"].includes(row.status)) return row.status;
  if (row.open_question_to_evgeny) return "question_only";
  if (row.movement_type === "real_result") return "result";
  if (row.movement_type === "no_movement") return "process_only";
  return "";
}

function render() {
  const meta = session.metadata || {};
  const activeSheet = meta.active_sheet || {};
  const source = activeSheet.sheet_name || session.sheet_name || "Активные";
  const weekLabel = activeSheet.week_label ? ` · ${activeSheet.week_label}` : "";
  document.getElementById("period").textContent = `${meta.week_start || ""} - ${meta.week_end || ""}${weekLabel}`;
  document.getElementById("sourceLabel").textContent = `${source} · проверка недели`;
  renderStats();
  applyQueueCollapsed();
  renderTopics();
  renderSelected();
  renderFinalMenu();
}

function renderStats() {
  const rows = activeRows();
  const filled = rows.filter(isFilled).length;
  const reviewed = rows.filter(isReviewed).length;
  const pending = Math.max(filled - reviewed, 0);
  const empty = rows.filter(row => !hasEvidence(row)).length;
  const changed = session.rows.filter(row => row.changed).length;
  const questions = rows.filter(row => String(row.open_question_to_evgeny || "").trim()).length;
  const pct = filled ? Math.round((reviewed / filled) * 100) : 0;
  document.getElementById("progressSummary").innerHTML = `<b>${reviewed}</b> / ${filled} тем`;
  document.getElementById("progressBar").style.width = `${pct}%`;
  document.getElementById("progressBar").style.background = filled && reviewed === filled ? "var(--ok)" : "var(--accent)";
  document.getElementById("progressMeta").textContent = `${pending} ждут · ${empty} без данных`;
  document.getElementById("stats").innerHTML = [
    ["Active", rows.length],
    ["Filled", filled],
    ["Changed", changed],
    ["Вопросы", questions],
  ].map(([label, value]) => `<div class="stat"><strong>${value}</strong>${label}</div>`).join("");
}

function renderFinalMenu() {
  const rows = activeRows();
  const filled = rows.filter(isFilled).length;
  const reviewed = rows.filter(isReviewed).length;
  const pending = Math.max(filled - reviewed, 0);
  const note = document.getElementById("readyNote");
  note.className = "ready-note" + (filled > 0 && pending === 0 ? " ready" : "");
  note.textContent = filled > 0 && pending === 0
    ? "Все заполненные темы проверены — можно записывать."
    : `${pending} тем еще не проверено. Запись доступна, но лучше пройти очередь.`;
  document.getElementById("writeBtn").textContent = `Записать ${filled} тем в «Активные»`;
}

function queueIsCollapsed() {
  return queueCollapsed && window.innerWidth >= 980;
}

function applyQueueCollapsed() {
  const panel = document.getElementById("queuePanel");
  if (panel) panel.classList.toggle("collapsed", queueIsCollapsed());
}

function setQueueCollapsed(value) {
  queueCollapsed = value;
  try { localStorage.setItem("wc_queue_collapsed", value ? "1" : "0"); } catch (error) {}
  applyQueueCollapsed();
  renderTopics();
}

function renderTopics() {
  const list = document.getElementById("topicList");
  list.innerHTML = "";
  const rows = filteredRows();
  const rail = queueIsCollapsed();
  for (const row of rows) {
    const filled = isFilled(row);
    const reviewed = isReviewed(row);
    const button = document.createElement("button");
    if (rail) {
      button.className = ["topic-rail-item", row.topic_id === selectedId ? "active" : "", filled ? "done" : "empty", reviewed ? "reviewed" : ""].filter(Boolean).join(" ");
      button.title = `${row.topic_id} · ${row.topic_title}`;
      button.innerHTML = `<span class="topic-dot"></span><code>${escapeHtml(String(row.topic_id).replace(/^T-?/, ""))}</code>`;
    } else {
      button.className = ["topic-item", row.topic_id === selectedId ? "active" : "", filled ? "done" : "empty", reviewed ? "reviewed" : "", row.changed ? "changed" : ""].filter(Boolean).join(" ");
      button.innerHTML = `
      <span class="topic-dot"></span>
      <span class="topic-copy">
        <span class="topic-title">${escapeHtml(row.topic_title)}</span>
        <span class="topic-meta-small"><code>${escapeHtml(row.topic_id)}</code><span>·</span><span>${escapeHtml(projectModeLabel(row))}</span><span>·</span><span>${escapeHtml(topicStateLabel(row))}</span></span>
      </span>
      <span class="topic-flags">
        ${row.status === "chatgpt_imported" ? '<span class="flag imported" title="импортировано">↧</span>' : ""}
        ${row.open_question_to_evgeny ? '<span class="flag question" title="есть вопрос">?</span>' : ""}
        ${row.changed ? '<span class="flag changed" title="изменено"></span>' : ""}
        ${reviewed ? '<span class="flag" title="проверено">✓</span>' : ""}
      </span>`;
    }
    button.onclick = () => (async () => {
      await saveEverythingIfDirty();
      selectedId = row.topic_id;
      dirty = false;
      projectDirty = false;
      render();
    })().catch(error => message(error.message, true));
    list.appendChild(button);
  }
  if (!rows.length) list.innerHTML = rail ? "" : '<div class="muted">Ничего не найдено.</div>';
}

function topicStateLabel(row) {
  if (isReviewed(row)) return "проверено";
  if (row.status === "chatgpt_imported") return "импорт";
  if (isFilled(row)) return "ждет проверки";
  if (hasEvidence(row)) return "есть факты";
  return "пусто";
}

function renderSelected() {
  const row = selectedRow();
  if (!row) return;
  selectedId = row.topic_id;
  const rows = visibleRows();
  const index = rows.findIndex(candidate => candidate.topic_id === row.topic_id);
  document.getElementById("topicPosition").textContent = `${index + 1} / ${rows.length}`;
  document.getElementById("topicMeta").innerHTML = `<code>${escapeHtml(row.topic_id)}</code> ${escapeHtml(row.section || "")} · row ${escapeHtml(row.row_number || "")}`;
  document.getElementById("topicTitle").textContent = row.topic_title || "Без названия";
  document.getElementById("projectTitle").value = row.topic_title || "";
  document.getElementById("projectMode").value = projectMode(row);
  document.getElementById("projectFocus").value = row.focus || "no";
  document.getElementById("projectDateCreated").value = row.date_created || "";
  for (const [id, key] of Object.entries(fields)) {
    const el = document.getElementById(id);
    if (el) el.value = row[key] || "";
  }
  if (!document.getElementById("reviewStatus").value) document.getElementById("reviewStatus").value = "draft";
  document.getElementById("previousResult").textContent = row.previous_week_result || "Нет прошлого результата.";
  document.getElementById("aiDraft").textContent = row.ai_draft_result || "Пока нет AI draft.";
  renderMilestoneContext(row);
  renderReview(row);
  renderStatusButtons(row);
  renderNavigationButtons(index, rows.length);
  renderPrimaryAction(row, index, rows.length);
  document.getElementById("emptyState").hidden = hasEvidence(row);
  const hints = document.getElementById("hints");
  hints.innerHTML = (row.hints || []).length ? row.hints.map(hint => `<li>${escapeHtml(hint)}</li>`).join("") : "<li>Нет подсказок.</li>";
  updateDirtyState();
}

function renderMilestoneContext(row) {
  const box = document.getElementById("contextMilestones");
  const milestones = [];
  if (Array.isArray(row.milestones)) {
    for (const item of row.milestones) {
      const text = item.text || "";
      const date = item.date || "";
      if (text || date) milestones.push({ text: text || row.next_milestone || "Веха", date: date || row.next_milestone_date || "без даты" });
    }
  }
  if (!milestones.length && (row.milestones_text || row.next_milestone || row.next_milestone_date)) {
    const lines = String(row.milestones_text || row.next_milestone || "").split("\\n").map(line => line.trim()).filter(Boolean);
    for (const line of lines.length ? lines : [row.next_milestone || "Веха"]) milestones.push({ text: line, date: row.next_milestone_date || "без даты" });
  }
  box.innerHTML = milestones.length
    ? milestones.map(item => `<div class="milestone-row"><span>${escapeHtml(item.text)}</span><span class="date">${escapeHtml(item.date)}</span></div>`).join("")
    : '<div class="muted">Нет текущих вех.</div>';
}

function renderReview(row) {
  const badge = document.getElementById("reviewBadge");
  badge.className = "review-badge";
  if (!isFilled(row)) {
    badge.textContent = "Нет данных";
  } else if (isReviewed(row)) {
    badge.textContent = "✓ Проверено";
    badge.classList.add("reviewed");
  } else {
    badge.textContent = "Ждет проверки";
    badge.classList.add("pending");
  }
}

function renderPrimaryAction(row, index, length) {
  const hasNext = index >= 0 && index < length - 1;
  const willReview = isFilled(row) && !isReviewed(row);
  const label = willReview ? "Проверено и дальше" : "Следующая тема";
  for (const btn of document.querySelectorAll("[data-review-next]")) {
    btn.className = "btn review-next " + (willReview ? "btn-ok" : "btn-primary");
    const span = btn.querySelector(".rn-label");
    if (span) span.textContent = label;
    btn.disabled = !willReview && !hasNext;
  }
}

function renderStatusButtons(row) {
  const key = rowStatusKey(row);
  for (const button of document.querySelectorAll("[data-status]")) button.classList.toggle("active", button.dataset.status === key);
}

function renderNavigationButtons(index, length) {
  document.getElementById("prevTopicBtn").disabled = index <= 0;
  document.getElementById("nextTopicBtn").disabled = index < 0 || index >= length - 1;
}

function collectPatch() {
  const patch = {};
  for (const [id, key] of Object.entries(fields)) {
    const el = document.getElementById(id);
    if (el) patch[key] = el.value;
  }
  return patch;
}

async function saveCurrentIfDirty() {
  if (!dirty || !selectedId) return;
  await saveCurrent();
}

async function saveEverythingIfDirty() {
  await saveCurrentIfDirty();
  if (projectDirty && selectedId) await saveProject();
}

async function saveCurrent() {
  const data = await api("/api/row", { topic_id: selectedId, patch: collectPatch() });
  replaceRow(data.row);
  dirty = false;
  message("Saved");
  render();
}

function collectProjectPatch() {
  return {
    topic_title: document.getElementById("projectTitle").value,
    mode: document.getElementById("projectMode").value,
    focus: document.getElementById("projectFocus").value,
    date_created: document.getElementById("projectDateCreated").value
  };
}

async function saveProject() {
  const data = await api("/api/project", { topic_id: selectedId, patch: collectProjectPatch() });
  replaceRow(data.row);
  projectDirty = false;
  message("Row saved");
  render();
}

async function createProject() {
  await saveEverythingIfDirty();
  const title = document.getElementById("newProjectTitle").value.trim();
  if (!title) return;
  const data = await api("/api/project-create", { patch: { topic_title: title, mode: document.getElementById("newProjectMode").value } });
  session.rows.push(data.row);
  selectedId = data.row.topic_id;
  document.getElementById("newProjectTitle").value = "";
  document.getElementById("newProjectResult").textContent = `Добавлено: ${data.row.topic_id}. Нажми Write Active, чтобы записать в Google Sheet.`;
  dirty = false;
  projectDirty = false;
  message("New row added");
  render();
}

async function archiveProject() {
  await saveCurrentIfDirty();
  const row = selectedRow();
  const activeSource = session.metadata?.source_format === "active_legacy";
  const actionText = activeSource ? "Скрыть в этой локальной сессии" : "Перенести в архив";
  if (!row || !confirm(`${actionText}: ${row.topic_title}?`)) return;
  const data = await api("/api/project-archive", { topic_id: selectedId });
  replaceRow(data.row);
  selectedId = firstVisibleRow()?.topic_id;
  dirty = false;
  projectDirty = false;
  message(activeSource ? "Hidden locally. Перенос в Архив сделай вручную в Google Sheet." : "Moved to archive. Нажми Write Active, чтобы записать в Google Sheet.");
  render();
}

function replaceRow(updated) {
  const index = session.rows.findIndex(row => row.topic_id === updated.topic_id);
  if (index >= 0) session.rows[index] = updated;
}

async function setStatus(status) {
  await saveEverythingIfDirty();
  const data = await api("/api/status", { topic_id: selectedId, status });
  replaceRow(data.row);
  dirty = false;
  message(`Status: ${status}`);
  render();
}

async function reviewAndNext() {
  await saveEverythingIfDirty();
  const row = selectedRow();
  if (row && isFilled(row) && !isReviewed(row)) {
    const data = await api("/api/row", { topic_id: selectedId, patch: { review_status: "reviewed" } });
    replaceRow(data.row);
    message("Отмечено проверенной");
  }
  const rows = visibleRows();
  const index = rows.findIndex(candidate => candidate.topic_id === selectedId);
  const next = rows[index + 1];
  if (next) { selectedId = next.topic_id; dirty = false; projectDirty = false; }
  render();
}

function contextFileName() {
  const meta = (session && session.metadata) ? session.metadata : {};
  const tag = String(meta.week_end || meta.week_start || "context").replace(/[^0-9A-Za-z._-]/g, "");
  return `weekly_context_${tag}.json`;
}

function downloadJson(text, filename) {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyChatgptContext() {
  await saveEverythingIfDirty();
  const data = await api("/api/chatgpt-context", {});
  const text = JSON.stringify(data, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    message("Контекст для GPT скопирован в буфер.");
  } catch (error) {
    downloadJson(text, contextFileName());
    message("Буфер недоступен — контекст скачан файлом.");
  }
}

async function exportContextFile() {
  await saveEverythingIfDirty();
  const data = await api("/api/chatgpt-context", {});
  downloadJson(JSON.stringify(data, null, 2), contextFileName());
  message(`Контекст сохранён: ${contextFileName()}`);
}

function triggerImportFile() {
  const input = document.getElementById("importFileInput");
  input.value = "";
  input.click();
}

async function importFromFile(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  await saveEverythingIfDirty();
  message(`Импортирую ${file.name}...`);
  const text = (await file.text()).trim();
  const data = await api("/api/chatgpt-import", { text });
  if (data.imported && data.imported.length) selectedId = data.imported[0].topic_id;
  await loadSession();
  dirty = false;
  const unmatched = (data.unmatched || []).length;
  message(`Импортировано: ${data.imported_count}${unmatched ? `; не сматчилось: ${unmatched}` : ""}`);
}

async function moveTopic(delta) {
  await saveEverythingIfDirty();
  const rows = visibleRows();
  const index = rows.findIndex(row => row.topic_id === selectedId);
  const next = rows[index + delta];
  if (!next) return;
  selectedId = next.topic_id;
  dirty = false;
  projectDirty = false;
  render();
}

async function startNextWeek() {
  await saveEverythingIfDirty();
  closeFinalMenu();
  const { weekStart, weekEnd } = nextReportWindow(session.metadata || {});
  if (!confirm(`Начать новую неделю ${weekStart} - ${weekEnd}? Итоги активных строк уйдут в прошлую неделю, weekly-поля очистятся.`)) return;
  const data = await api("/api/start-next-week", { week_start: weekStart, week_end: weekEnd });
  await loadSession();
  message(`Started week ${data.week_start} - ${data.week_end}; rows changed: ${data.updated_count}`);
}

function nextReportWindow(metadata) {
  const currentStart = parseIsoDate(metadata.week_start) || utcDateFromParts(new Date().getUTCFullYear(), new Date().getUTCMonth() + 1, new Date().getUTCDate());
  const currentEnd = parseIsoDate(metadata.week_end);
  let nextStart;
  if (currentEnd && currentEnd > currentStart) nextStart = currentEnd.getUTCDay() === 5 ? currentEnd : fridayOnOrAfter(currentEnd);
  else nextStart = fridayAfter(currentStart);
  return { weekStart: formatIsoDate(nextStart), weekEnd: formatIsoDate(addDays(nextStart, 7)) };
}

function parseIsoDate(value) {
  if (!/^\\d{4}-\\d{2}-\\d{2}$/.test(String(value || ""))) return null;
  const [year, month, day] = value.split("-").map(Number);
  return utcDateFromParts(year, month, day);
}

function utcDateFromParts(year, month, day) {
  return new Date(Date.UTC(year, month - 1, day));
}

function fridayOnOrAfter(date) {
  return addDays(date, (5 - date.getUTCDay() + 7) % 7);
}

function fridayAfter(date) {
  return addDays(date, ((5 - date.getUTCDay() + 7) % 7) || 7);
}

function addDays(date, days) {
  const next = new Date(date.getTime());
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

function formatIsoDate(date) {
  return date.toISOString().slice(0, 10);
}

async function writeBack() {
  await saveEverythingIfDirty();
  closeFinalMenu();
  const target = session.metadata?.source_format === "active_legacy" ? "в Активные" : "в Google Sheet";
  if (!confirm(`Записать изменения ${target}?`)) return;
  const data = await api("/api/write-back", {});
  const created = (data.created_week_columns || []).length ? `\\nСозданы колонки: ${data.created_week_columns.join(", ")}` : "";
  message(`Updated rows: ${data.updated_count}`);
  alert(`Updated rows: ${data.updated_count}${created}`);
}

async function createWeekTab() {
  await saveEverythingIfDirty();
  if (!confirm("Создать/обновить MVP-вкладку для текущего периода в Google Sheet?")) return;
  const data = await api("/api/create-week-tab", {});
  await loadSession();
  message(`${data.created ? "Created" : "Updated"} tab: ${data.sheet_name}`);
  alert(`${data.created ? "Created" : "Updated"}\\n${data.sheet_name}`);
}

async function exportActive() {
  await saveEverythingIfDirty();
  if (!confirm("Создать недельные колонки в Активные, добавить новые темы без Legacy row и перенести итоги?")) return;
  const data = await api("/api/export-active", {});
  await loadSession();
  message(`Active exported: ${data.week_label}; rows updated: ${data.updated_count}`);
  alert(data.summary_text || `Active exported: ${data.week_label}`);
}

function toggleFinalMenu() {
  const menu = document.getElementById("finalMenu");
  const button = document.getElementById("finalMenuBtn");
  const nextHidden = !menu.hidden;
  menu.hidden = nextHidden;
  button.setAttribute("aria-expanded", String(!nextHidden));
}

function closeFinalMenu() {
  document.getElementById("finalMenu").hidden = true;
  document.getElementById("finalMenuBtn").setAttribute("aria-expanded", "false");
}

function markWeeklyDirty(sourceId) {
  dirty = true;
  const reviewStatus = document.getElementById("reviewStatus");
  if (sourceId !== "reviewStatus" && reviewStatus && reviewStatus.value !== "draft") reviewStatus.value = "draft";
  updateDirtyState();
  scheduleAutoSave();
}

let autoSaveTimer = null;
function scheduleAutoSave() {
  clearTimeout(autoSaveTimer);
  autoSaveTimer = setTimeout(() => autoSave().catch(error => message(error.message, true)), 1200);
}

async function autoSave() {
  if (!selectedId) return;
  if (dirty) {
    const data = await api("/api/row", { topic_id: selectedId, patch: collectPatch() });
    replaceRow(data.row);
    dirty = false;
  }
  if (projectDirty) {
    const data = await api("/api/project", { topic_id: selectedId, patch: collectProjectPatch() });
    replaceRow(data.row);
    projectDirty = false;
  }
  updateDirtyState();
  renderStats();
  renderTopics();
}

function updateDirtyState() {
  const el = document.getElementById("saveState");
  el.className = "save-state" + (dirty || projectDirty ? " dirty" : "");
  el.lastChild.textContent = dirty || projectDirty ? "Есть несохраненные правки" : "Сохранено";
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
  if (event.target.id === "topicSearch") {
    topicQuery = event.target.value;
    renderTopics();
    return;
  }
  if (Object.keys(fields).includes(event.target.id)) markWeeklyDirty(event.target.id);
  if (["projectTitle", "projectMode", "projectFocus", "projectDateCreated"].includes(event.target.id)) {
    projectDirty = true;
    updateDirtyState();
    scheduleAutoSave();
  }
});
document.addEventListener("change", event => {
  if (Object.keys(fields).includes(event.target.id)) markWeeklyDirty(event.target.id);
  if (["projectTitle", "projectMode", "projectFocus", "projectDateCreated"].includes(event.target.id)) {
    projectDirty = true;
    updateDirtyState();
    scheduleAutoSave();
  }
});
document.addEventListener("click", event => {
  const menu = document.getElementById("finalMenu");
  const finalActions = document.querySelector(".final-actions");
  if (!menu.hidden && !finalActions.contains(event.target)) closeFinalMenu();
});
document.getElementById("saveProjectBtn").onclick = () => saveProject().catch(error => message(error.message, true));
document.getElementById("newProjectBtn").onclick = () => createProject().catch(error => message(error.message, true));
document.getElementById("archiveProjectBtn").onclick = () => archiveProject().catch(error => message(error.message, true));
document.getElementById("nextTopicBtn").onclick = () => moveTopic(1).catch(error => message(error.message, true));
document.getElementById("prevTopicBtn").onclick = () => moveTopic(-1).catch(error => message(error.message, true));
for (const button of document.querySelectorAll("[data-review-next]")) {
  button.onclick = () => reviewAndNext().catch(error => message(error.message, true));
}
document.getElementById("chatgptContextBtn").onclick = () => copyChatgptContext().catch(error => message(error.message, true));
document.getElementById("exportFileBtn").onclick = () => exportContextFile().catch(error => message(error.message, true));
document.getElementById("openImportBtn").onclick = () => triggerImportFile();
document.getElementById("emptyImportBtn").onclick = () => triggerImportFile();
document.getElementById("importFileInput").onchange = event => importFromFile(event).catch(error => message(error.message, true));
document.getElementById("finalMenuBtn").onclick = event => { event.stopPropagation(); toggleFinalMenu(); };
document.getElementById("finalMenu").onclick = event => event.stopPropagation();
document.getElementById("writeBtn").onclick = () => writeBack().catch(error => message(error.message, true));
const weekTabBtn = document.getElementById("weekTabBtn");
if (weekTabBtn) weekTabBtn.onclick = () => createWeekTab().catch(error => message(error.message, true));
const activeExportBtn = document.getElementById("activeExportBtn");
if (activeExportBtn) activeExportBtn.onclick = () => exportActive().catch(error => message(error.message, true));
document.getElementById("nextWeekBtn").onclick = () => startNextWeek().catch(error => message(error.message, true));
for (const button of document.querySelectorAll("[data-status]")) {
  button.onclick = () => setStatus(button.dataset.status).catch(error => message(error.message, true));
}
document.getElementById("collapseQueueBtn").onclick = () => setQueueCollapsed(true);
document.getElementById("expandQueueBtn").onclick = () => setQueueCollapsed(false);
let queueResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(queueResizeTimer);
  queueResizeTimer = setTimeout(() => { applyQueueCollapsed(); if (session) renderTopics(); }, 120);
});
loadSession().catch(error => message(error.message, true));
"""


if __name__ == "__main__":
    raise SystemExit(main())
