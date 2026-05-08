import argparse
import json
from pathlib import Path

from weekly_assistant.config.settings import load_settings
from weekly_assistant.viewer_server import APP_CSS, APP_JS, INDEX_HTML, WeeklyViewerStore, _feedback_summary, _week_label_from_sheet_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekly-pages-export")
    parser.add_argument("--out-dir", default="docs")
    parser.add_argument("--sheet-name", default="")
    parser.add_argument("--week-label", default="")
    args = parser.parse_args(argv)

    settings = load_settings()
    sheet_name = args.sheet_name or settings.google_sheets_weekly_sheet_name
    week_label = args.week_label or _week_label_from_sheet_name(sheet_name)
    store = WeeklyViewerStore(settings, sheet_name=sheet_name, week_label=week_label)
    report = store.report()
    report["feedback"] = _feedback_summary([], sheet={"status": "static", "sheet_name": "local browser"})

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(_static_index_html(), encoding="utf-8")
    (out_dir / "viewer.css").write_text(APP_CSS, encoding="utf-8")
    (out_dir / "viewer.js").write_text(APP_JS, encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"GitHub Pages snapshot written to {out_dir}")
    print(f"Rows: {report['metrics']['total_active']}")
    return 0


def _static_index_html() -> str:
    html = INDEX_HTML.replace('href="/viewer.css"', 'href="viewer.css"')
    html = html.replace('<script src="/viewer.js"></script>', '<script>window.WEEKLY_VIEWER_STATIC = true;</script>\n  <script src="viewer.js"></script>')
    return html


if __name__ == "__main__":
    raise SystemExit(main())
