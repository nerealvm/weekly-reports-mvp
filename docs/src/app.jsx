// App shell — GitHub Pages version, defaults to reader (Евгений)
// Fetches weekly data from Google Sheets automatically on load.

function App() {
  const [role, setRole] = React.useState("reader");
  const [theme, setTheme] = React.useState("warm");

  // Data state
  const [topics, setTopics] = React.useState(window.FALLBACK_TOPICS);
  const [week, setWeek] = React.useState(window.FALLBACK_WEEK);
  const [selectedId, setSelectedId] = React.useState(window.FALLBACK_TOPICS[0]?.id || null);

  // Sheets state
  const [sheetsStatus, setSheetsStatus] = React.useState("idle"); // idle | loading | ok | public_csv | static | error
  const [sheetsError, setSheetsError] = React.useState("");
  const [availableWeeks, setAvailableWeeks] = React.useState([]);
  const [activeSheetName, setActiveSheetName] = React.useState("");
  const [weekPickerOpen, setWeekPickerOpen] = React.useState(false);

  React.useEffect(() => {
    document.body.setAttribute("data-theme", theme);
  }, [theme]);

  const cfg = window.SHEETS_CONFIG;
  const hasApiKey = Boolean(cfg?.apiKey);
  const hasPublicCsv = Boolean(cfg?.publicCsvGid || cfg?.publicCsvSheetName);

  const reloadData = React.useCallback(() => {
    setSheetsStatus("loading");
    if (!hasApiKey && hasPublicCsv) {
      window.fetchPublicCsvWeekData()
        .then(({ TOPICS, WEEK }) => {
          setTopics(TOPICS);
          setWeek(WEEK);
          setSelectedId(TOPICS[0]?.id || null);
          setActiveSheetName(cfg.publicCsvSheetName || WEEK.sheetName || "");
          setSheetsStatus("public_csv");
        })
        .catch(csvErr => {
          setSheetsError(csvErr.message);
          return window.fetchStaticReport()
            .then(({ TOPICS, WEEK }) => {
              setTopics(TOPICS);
              setWeek(WEEK);
              setSelectedId(TOPICS[0]?.id || null);
              setSheetsStatus("static");
            })
            .catch(staticErr => {
              setSheetsError(`${csvErr.message}; ${staticErr.message}`);
              setSheetsStatus("error");
            });
        });
      return;
    }
    if (!hasApiKey) {
      window.fetchStaticReport()
        .then(({ TOPICS, WEEK }) => {
          setTopics(TOPICS);
          setWeek(WEEK);
          setSelectedId(TOPICS[0]?.id || null);
          setSheetsStatus("static");
        })
        .catch(err => {
          setSheetsError(err.message);
          setSheetsStatus("error");
        });
      return;
    }
    window.fetchSheetList()
      .then(sheets => {
        setAvailableWeeks(sheets);
        const latest = sheets[0];
        setActiveSheetName(latest.sheetName);
        return window.fetchWeekData(latest.sheetName);
      })
      .then(({ TOPICS, WEEK }) => {
        setTopics(TOPICS);
        setWeek(WEEK);
        setSelectedId(TOPICS[0]?.id || null);
        setSheetsStatus("ok");
      })
      .catch(err => {
        setSheetsError(err.message);
        setSheetsStatus("error");
      });
  }, []);

  React.useEffect(() => { reloadData(); }, []);

  const switchWeek = (sheetName) => {
    setWeekPickerOpen(false);
    setSheetsStatus("loading");
    window.fetchWeekData(sheetName)
      .then(({ TOPICS, WEEK }) => {
        setTopics(TOPICS);
        setWeek(WEEK);
        setSelectedId(TOPICS[0]?.id || null);
        setActiveSheetName(sheetName);
        setSheetsStatus("ok");
      })
      .catch(err => {
        setSheetsError(err.message);
        setSheetsStatus("error");
      });
  };

  const weekPillLabel = sheetsStatus === "loading"
    ? "загрузка…"
    : `${week.label} · ${week.range}`;

  const [saveStatus, setSaveStatus] = React.useState("idle"); // idle | saving | done | error

  const handleSaveReport = React.useCallback(async () => {
    setSaveStatus("saving");
    try {
      const report = topicsToReportJson(topics, week);
      const json = JSON.stringify(report, null, 2);
      const blob = new Blob([json], { type: "application/json" });
      if (window.showSaveFilePicker) {
        try {
          const handle = await window.showSaveFilePicker({
            suggestedName: "report.json",
            types: [{ description: "JSON", accept: { "application/json": [".json"] } }],
          });
          const writable = await handle.createWritable();
          await writable.write(blob);
          await writable.close();
        } catch (e) {
          if (e.name === "AbortError") { setSaveStatus("idle"); return; }
          throw e;
        }
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = "report.json"; a.click();
        URL.revokeObjectURL(url);
      }
      setSaveStatus("done");
      setTimeout(() => setSaveStatus("idle"), 2200);
    } catch (err) {
      console.error("saveReportJson:", err);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2200);
    }
  }, [topics, week]);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-dot"/>Weekly<sup>v.0.4</sup>
        </div>

        {/* Week pill — clickable if multiple weeks available */}
        <div style={{ position: "relative" }}>
          <button
            className="week-pill"
            style={{
              cursor: availableWeeks.length > 1 ? "pointer" : "default",
              background: sheetsStatus === "loading" ? "var(--line-2)" : undefined,
              display: "flex", alignItems: "center", gap: 6,
            }}
            onClick={() => availableWeeks.length > 1 && setWeekPickerOpen(o => !o)}
            title={availableWeeks.length > 1 ? "Переключить неделю" : undefined}
          >
            {weekPillLabel}
            {availableWeeks.length > 1 && (
              <svg width="9" height="9" viewBox="0 0 12 12" fill="currentColor" style={{ opacity: 0.5 }}>
                <path d="M6 9L1 4h10z"/>
              </svg>
            )}
          </button>

          {weekPickerOpen && availableWeeks.length > 1 && (
            <>
              <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => setWeekPickerOpen(false)}/>
              <div style={{
                position: "absolute", top: "calc(100% + 8px)", left: 0, zIndex: 50,
                background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10,
                boxShadow: "0 8px 32px rgba(20,18,12,0.14)", minWidth: 240, overflow: "hidden",
              }}>
                <div style={{ padding: "8px 12px 6px", borderBottom: "1px solid var(--line-2)" }}>
                  <div className="eyebrow">выбрать неделю</div>
                </div>
                {availableWeeks.map(({ sheetName, date }) => {
                  const meta = weekPickerMeta(sheetName);
                  const isActive = sheetName === activeSheetName;
                  return (
                    <button key={sheetName}
                      onClick={() => switchWeek(sheetName)}
                      style={{
                        display: "flex", flexDirection: "column", gap: 1,
                        width: "100%", padding: "10px 14px", border: 0, borderBottom: "1px solid var(--line-2)",
                        background: isActive ? "var(--paper)" : "var(--surface)",
                        cursor: "pointer", textAlign: "left",
                      }}
                    >
                      <span style={{
                        fontFamily: "var(--mono)", fontSize: 11, fontWeight: isActive ? 600 : 400,
                        color: isActive ? "var(--ink)" : "var(--ink-2)",
                      }}>
                        {meta.label} · {meta.rangeShort || sheetName}
                        {isActive && <span style={{ color: "var(--accent)", marginLeft: 8 }}>← текущая</span>}
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {/* Sheets status + refresh */}
        <span className="topbar-source">
          {sheetsStatus === "ok" && (
            <span className="topbar-status" title="Данные из Google Sheets (API)">● Sheets</span>
          )}
          {sheetsStatus === "public_csv" && (
            <span className="topbar-status" style={{ color: "var(--accent-good)" }} title="Данные из публичного CSV Google Sheets">● csv</span>
          )}
          {sheetsStatus === "static" && (
            <span className="topbar-status" style={{ color: "var(--ink-4)" }} title="Статический снапшот (report.json)">static</span>
          )}
          {sheetsStatus === "error" && (
            <span className="topbar-status" style={{ color: "var(--accent-warn)" }} title={sheetsError}>⚠</span>
          )}
          {sheetsStatus !== "loading" && (
            <button className="reload-btn" onClick={reloadData} title="Обновить данные из Google Sheets">
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M13.5 8A5.5 5.5 0 1 1 10 3.07"/>
                <path d="M10 1v3.5H13.5"/>
              </svg>
            </button>
          )}
        </span>

        <div className="role-switch" role="tablist" aria-label="Роль">
          <button className="role-btn" aria-pressed={role==="editor"} onClick={()=>setRole("editor")}>
            <span className="dot"/><span className="role-name">Вол<span className="role-label-long">одя</span></span>
          </button>
          <button className="role-btn" aria-pressed={role==="reader"} onClick={()=>setRole("reader")}>
            <span className="dot"/><span className="role-name">Евг<span className="role-label-long">ений</span></span>
          </button>
        </div>

        <div className="topbar-actions">
          {role === "editor" && sheetsStatus !== "loading" && (
            <button
              className="save-report-btn"
              onClick={handleSaveReport}
              disabled={saveStatus === "saving"}
              data-status={saveStatus}
              title="Сохранить текущие данные как report.json (статический снапшот)"
            >
              {saveStatus === "done"  ? "✓ Сохранено" :
               saveStatus === "error" ? "⚠ Ошибка"   :
               saveStatus === "saving"? "Сохраняю…"  : "↓ report.json"}
            </button>
          )}
          <div style={{ display: "flex", gap: 4 }}>
            {[["warm","☀"], ["cool","❄"], ["ink","●"]].map(([v, l]) => (
              <button key={v} onClick={() => setTheme(v)}
                style={{
                  border: "1px solid var(--line)", borderRadius: 6,
                  background: theme === v ? "var(--ink)" : "var(--paper)",
                  color: theme === v ? "var(--paper)" : "var(--ink-3)",
                  width: 28, height: 28, fontSize: 12, cursor: "pointer",
                  display: "grid", placeItems: "center",
                }}
                title={v}>{l}</button>
            ))}
          </div>
        </div>
      </header>

      {sheetsStatus === "loading" ? (
        <LoadingView/>
      ) : role === "editor" ? (
        <EditorView topics={topics} setTopics={setTopics} selectedId={selectedId} setSelectedId={setSelectedId}/>
      ) : (
        <ReaderView topics={topics} week={week}/>
      )}
    </div>
  );
}

// Converts current topics+week into report.json format (read by parseStaticReport)
function topicsToReportJson(topics, week) {
  const ballSide = (t) => {
    if (t.ball === "me") return "Володя";
    if (t.ball === "evgeny") return "Евгений";
    return t.ballName || "external";
  };
  const items = topics.map((t, i) => ({
    topic_id: t.id,
    topic_title: t.title,
    section: "",
    focus: "no",
    current_week_facts: t.facts || "",
    result: t.result || "",
    milestones: (t.milestones || []).map(m => [m.date, m.text].filter(Boolean).join(" ")).join("; "),
    milestone_date: t.milestones?.[0]?.date || "",
    ball_side: ballSide(t),
    open_question: t.question || "",
    movement_type: t.movement || "unclear",
    needs_sync: t.sync || "no",
    sync_reason: t.syncReason || "",
    source_links: t.link || "",
    priority: i,
  }));
  return {
    week_label: week.rangeShort || week.label || "",
    source_sheet: week.sheetName || `Weekly MVP ${new Date().toISOString().slice(0, 10)}`,
    metrics: {
      total_active: topics.length,
      real_result: topics.filter(t => t.movement === "real_result").length,
      no_movement: topics.filter(t => t.movement === "no_movement").length,
      unclear: topics.filter(t => t.movement === "unclear").length,
      needs_sync: topics.filter(t => t.sync === "yes").length,
      open_questions: topics.filter(t => t.question).length,
    },
    items,
  };
}

// Week picker pill label — separate name so it doesn't overwrite data.jsx's weekMetaFromSheetName
function weekPickerMeta(sheetName) {
  const m = sheetName.match(/(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return { label: sheetName, rangeShort: "" };
  const [, year, month, day] = m;
  const end = new Date(+year, +month-1, +day);
  const start = new Date(end); start.setDate(start.getDate() - 6);
  const weekNum = getISOWeek(end);
  const rangeShort = `${String(start.getDate()).padStart(2,"0")}.${String(start.getMonth()+1).padStart(2,"0")} – ${String(end.getDate()).padStart(2,"0")}.${String(end.getMonth()+1).padStart(2,"0")}`;
  return { label: `W${weekNum}`, rangeShort };
}

function getISOWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

function LoadingView() {
  return (
    <div style={{
      flex: 1, display: "grid", placeItems: "center",
      color: "var(--ink-3)", fontFamily: "var(--serif)",
      fontStyle: "italic", fontSize: 20, gap: 16,
    }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2"
          strokeLinecap="round" style={{ opacity: 0.4, animation: "spin 1.4s linear infinite" }}>
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
        </svg>
        <span>Загружаю данные из Google Sheets…</span>
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
