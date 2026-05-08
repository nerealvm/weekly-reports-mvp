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

  React.useEffect(() => {
    if (!hasApiKey && hasPublicCsv) {
      setSheetsStatus("loading");
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
      setSheetsStatus("loading");
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
    setSheetsStatus("loading");
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
                  const meta = weekMetaFromSheetName(sheetName);
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

        {/* Sheets status indicator */}
        {sheetsStatus === "ok" && (
          <span className="topbar-status" style={{ fontSize: 10, fontFamily: "var(--mono)", color: "var(--accent-good)", letterSpacing: "0.06em" }}>
            ● Sheets
          </span>
        )}
        {sheetsStatus === "public_csv" && (
          <span className="topbar-status" style={{ fontSize: 10, fontFamily: "var(--mono)", color: "var(--accent-good)", letterSpacing: "0.06em" }} title="Данные читаются напрямую из публичного CSV Google Sheets без API key">
            ● csv
          </span>
        )}
        {sheetsStatus === "static" && (
          <span className="topbar-status" style={{ fontSize: 10, fontFamily: "var(--mono)", color: "var(--ink-4)", letterSpacing: "0.04em" }} title="статические данные">
            static
          </span>
        )}
        {sheetsStatus === "error" && (
          <span className="topbar-status" style={{ fontSize: 10, fontFamily: "var(--mono)", color: "var(--accent-warn)", letterSpacing: "0.04em" }} title={sheetsError}>
            ⚠
          </span>
        )}

        <div className="role-switch" role="tablist" aria-label="Роль">
          <button className="role-btn" aria-pressed={role==="editor"} onClick={()=>setRole("editor")}>
            <span className="dot"/><span className="role-name">Вол</span><span className="role-label-long">одя</span>
          </button>
          <button className="role-btn" aria-pressed={role==="reader"} onClick={()=>setRole("reader")}>
            <span className="dot"/><span className="role-name">Евг</span><span className="role-label-long">ений</span>
          </button>
        </div>

        <div className="topbar-actions">
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

// Helper re-exported so the week picker can use it too
function weekMetaFromSheetName(sheetName) {
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
