PYTHONPATH := src
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
SAMPLE := tests/fixtures/weekly_mvp_sample.csv
WEEK_START ?= 2026-05-01
WEEK_END ?= 2026-05-08
COLLECTOR_PORT ?= 8765
VIEWER_PORT ?= 8770
COLLECTOR_GID ?= 0
COLLECTOR_SHEET_NAME ?= Активные
COLLECTOR_REFRESH ?=
TRANSFER_APPLY ?=
TRANSFER_WEEK_LABEL ?=
TRANSFER_ARGS ?=

.PHONY: test status inspect sample-flow live-full-test singularity-context collector viewer transfer-active pages-export

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests

status:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli integration-status

inspect:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli inspect --csv $(SAMPLE)

sample-flow:
	mkdir -p /tmp/weekly-assistant
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli refresh --csv $(SAMPLE) --out /tmp/weekly-assistant/refreshed.csv
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli create-week --csv /tmp/weekly-assistant/refreshed.csv --out /tmp/weekly-assistant/next_week.csv
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli draft --csv /tmp/weekly-assistant/next_week.csv --out /tmp/weekly-assistant/drafted.csv
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli export-questions --csv /tmp/weekly-assistant/drafted.csv --out /tmp/weekly-assistant/questions.txt
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli export-sync --csv /tmp/weekly-assistant/drafted.csv --out /tmp/weekly-assistant/sync.txt

live-full-test:
	mkdir -p /tmp/weekly-live-test
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli download-sheet-csv --spreadsheet-id 14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs --gid 20260508 --out /tmp/weekly-live-test/source.csv
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli launch-readiness --csv /tmp/weekly-live-test/source.csv
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli full-test --csv /tmp/weekly-live-test/source.csv --out-dir /tmp/weekly-live-test

singularity-context:
	mkdir -p /tmp/weekly-live-test
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli singularity-weekly-context --week-start $(WEEK_START) --week-end $(WEEK_END) --out /tmp/weekly-live-test/singularity_context.md

collector:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.collector_server --week-start $(WEEK_START) --week-end $(WEEK_END) --port $(COLLECTOR_PORT) --gid $(COLLECTOR_GID) --sheet-name "$(COLLECTOR_SHEET_NAME)" $(COLLECTOR_REFRESH)

viewer:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.viewer_server --port $(VIEWER_PORT)

pages-export:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.pages_export --out-dir docs

transfer-active:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m weekly_assistant.cli transfer-to-active --week-label "$(TRANSFER_WEEK_LABEL)" $(TRANSFER_ARGS) $(TRANSFER_APPLY)
