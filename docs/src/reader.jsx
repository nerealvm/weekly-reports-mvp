// Reader view — Евгений's role: reads the report, can leave inline comments (stored locally)

const { useState, useRef, useEffect, useMemo } = React;

// ===== comments context =====
const CommentsCtx = React.createContext(null);

function useComments() {
  const c = React.useContext(CommentsCtx);
  if (!c) throw new Error("CommentsCtx missing");
  return c;
}

function CommentsProvider({ children, week }) {
  const [comments, setComments] = useState(() => {
    try { return JSON.parse(localStorage.getItem("weekly-comments") || "[]"); }
    catch { return []; }
  });
  const [active, setActive] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [sheetStatus, setSheetStatus] = useState({}); // { [commentId]: 'sending'|'ok'|'fail' }

  useEffect(() => {
    localStorage.setItem("weekly-comments", JSON.stringify(comments));
  }, [comments]);

  const add = (anchor, label, text) => {
    const comment = {
      id: `C-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`,
      anchor, label, text, ts: new Date().toISOString(),
    };
    setComments(cs => [...cs, comment]);

    const url = window.SHEETS_CONFIG?.appsScriptUrl;
    if (url) {
      setSheetStatus(s => ({ ...s, [comment.id]: 'sending' }));
      fetch(url, {
        method: 'POST',
        mode: 'no-cors',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({
          id: comment.id,
          ts: comment.ts,
          weekLabel: week?.label || '',
          sourceSheet: week?.sheetName || week?.label || '',
          anchor, label, text,
          author: 'Евгений',
          status: 'open',
        }),
      })
        .then(() => setSheetStatus(s => ({ ...s, [comment.id]: 'ok' })))
        .catch(() => setSheetStatus(s => ({ ...s, [comment.id]: 'fail' })));
    }
  };
  const remove = (id) => setComments(cs => cs.filter(c => c.id !== id));
  const resolve = (id) => setComments(cs => cs.map(c => c.id === id ? { ...c, resolved: !c.resolved } : c));

  const byAnchor = useMemo(() => {
    const m = {};
    comments.forEach(c => { (m[c.anchor] ||= []).push(c); });
    return m;
  }, [comments]);

  return (
    <CommentsCtx.Provider value={{ comments, add, remove, resolve, byAnchor, active, setActive, panelOpen, setPanelOpen, sheetStatus }}>
      {children}
    </CommentsCtx.Provider>
  );
}

// ===== Annotatable wrapper =====
function Annotatable({ anchor, label, children, inline = false }) {
  const { byAnchor, setActive } = useComments();
  const ref = useRef(null);
  const list = byAnchor[anchor] || [];
  const open = list.filter(c => !c.resolved).length;

  const handleClick = (e) => {
    e.stopPropagation();
    const rect = ref.current.getBoundingClientRect();
    setActive({ anchor, label, rect: { top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom, width: rect.width } });
  };

  return (
    <span
      ref={ref}
      className={`anno ${inline ? "anno-inline" : "anno-block"} ${open ? "has-comment" : ""}`}
      onClick={handleClick}
      data-anchor={anchor}
    >
      {children}
      {open > 0 && (
        <span className="anno-pin" title={`${open} комм.`} onClick={handleClick}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M4 4h16v12H7l-3 3z"/></svg>
          {open > 1 && <span className="anno-pin-num">{open}</span>}
        </span>
      )}
      <button className="anno-add" onClick={handleClick} aria-label="Добавить комментарий">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
      </button>
    </span>
  );
}

// ===== popover =====
function CommentPopover() {
  const { active, setActive, add, byAnchor, remove, resolve, sheetStatus } = useComments();
  const [text, setText] = useState("");
  const taRef = useRef(null);
  const hasSheets = Boolean(window.SHEETS_CONFIG?.appsScriptUrl);

  useEffect(() => {
    if (active) { setText(""); setTimeout(() => taRef.current?.focus(), 50); }
  }, [active]);

  if (!active) return null;
  const list = byAnchor[active.anchor] || [];

  const submit = () => {
    if (!text.trim()) return;
    add(active.anchor, active.label, text.trim());
    setText("");
  };

  const isMobile = window.innerWidth < 760;
  const top = isMobile ? undefined : Math.min(active.rect.bottom + 8, window.innerHeight - 340);
  const left = isMobile ? undefined : Math.min(Math.max(active.rect.left, 16), window.innerWidth - 376);

  return (
    <>
      <div className="anno-backdrop" onClick={() => setActive(null)}/>
      <div className={`anno-pop${isMobile ? " anno-pop-mobile" : ""}`} style={isMobile ? {} : { top, left }} onClick={e => e.stopPropagation()}>
        <div className="anno-pop-head">
          <div>
            <div className="eyebrow">комментарий к</div>
            <div style={{ fontSize: 13, fontWeight: 500, marginTop: 2 }}>{active.label}</div>
          </div>
          <button className="anno-close" onClick={() => setActive(null)}>×</button>
        </div>
        {list.length > 0 && (
          <div className="anno-list">
            {list.map(c => (
              <div key={c.id} className={`anno-item ${c.resolved ? "resolved" : ""}`}>
                <div className="anno-meta">
                  <span className="anno-author">Е</span>
                  <span style={{ fontSize: 11, color: "var(--ink-3)" }}>Евгений</span>
                  <span style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--mono)", marginLeft: "auto" }}>
                    {new Date(c.ts).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>
                <div className="anno-text">{c.text}</div>
                <div className="anno-actions">
                  <button onClick={() => resolve(c.id)}>{c.resolved ? "↺ открыть" : "✓ решено"}</button>
                  <button onClick={() => remove(c.id)}>удалить</button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="anno-form">
          <textarea ref={taRef} value={text} onChange={e => setText(e.target.value)}
            onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit(); }}
            placeholder="Задать вопрос или оставить комментарий…" rows={3}/>
          <div className="anno-form-foot">
            {hasSheets
              ? <span className="sheets-tag" title="Комментарий запишется в Google Sheets «Weekly Feedback»">→ Sheets</span>
              : <span style={{ fontSize: 11, color: "var(--ink-3)" }}>локально</span>
            }
            <span style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--mono)", marginLeft: "auto" }}>⌘+Enter</span>
            <button className="btn btn-primary" onClick={submit}>отправить</button>
          </div>
        </div>
      </div>
    </>
  );
}

// ===== comments panel =====
function CommentsPanel() {
  const { comments, panelOpen, setPanelOpen, resolve, remove } = useComments();
  if (!panelOpen) return null;

  const copyAll = () => {
    const open = comments.filter(c => !c.resolved);
    if (!open.length) return;
    const text = open.map(c => `[${c.label}] ${c.text}`).join("\n\n");
    navigator.clipboard?.writeText(text);
  };

  return (
    <aside className="anno-panel">
      <div className="anno-panel-head">
        <div>
          <h4>Все комментарии</h4>
          <button className="sheets-mini-link" style={{ background: "none", border: "none", cursor: "pointer", padding: 0, textAlign: "left" }}
            onClick={copyAll}>скопировать все открытые ↗</button>
        </div>
        <button className="anno-close" onClick={() => setPanelOpen(false)}>×</button>
      </div>
      {comments.length === 0 ? (
        <div style={{ padding: 24, color: "var(--ink-3)", fontSize: 12, fontStyle: "italic" }}>
          Кликните по любому полю в отчёте, чтобы оставить комментарий или вопрос.
        </div>
      ) : (
        <div className="anno-panel-list">
          {comments.map(c => (
            <div key={c.id} className={`anno-panel-item ${c.resolved ? "resolved" : ""}`}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <span className="eyebrow" style={{ color: c.resolved ? "var(--ink-4)" : "var(--accent)" }}>
                  {c.resolved ? "решено" : "открыт"}
                </span>
                <span style={{ fontSize: 11, color: "var(--ink-3)" }}>· {c.label}</span>
              </div>
              <div style={{ fontSize: 13, color: c.resolved ? "var(--ink-3)" : "var(--ink)", lineHeight: 1.45, marginBottom: 6 }}>{c.text}</div>
              <div style={{ display: "flex", gap: 6, fontSize: 11 }}>
                <button className="anno-mini" onClick={() => resolve(c.id)}>{c.resolved ? "↺ открыть" : "✓ решить"}</button>
                <button className="anno-mini" onClick={() => remove(c.id)}>удалить</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

// ===== comments banner =====
function CommentsBanner() {
  const { comments, setPanelOpen } = useComments();
  const open = comments.filter(c => !c.resolved).length;
  const resolved = comments.filter(c => c.resolved).length;
  const hasSheets = Boolean(window.SHEETS_CONFIG?.appsScriptUrl);

  const copyAll = () => {
    const list = comments.filter(c => !c.resolved);
    if (!list.length) return;
    const text = list.map(c => `[${c.label}]\n${c.text}`).join("\n\n---\n\n");
    navigator.clipboard?.writeText(text);
  };

  return (
    <div className="sheets-banner">
      <div className="sb-icon" aria-hidden="true">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </div>
      <div className="sb-text">
        <div className="sb-title">
          Комментарии и вопросы — кликни по любому блоку
          {hasSheets
            ? <span className="sb-live"><span className="sb-live-dot"/>Sheets</span>
            : <span className="sb-live" style={{ background: "color-mix(in oklab, var(--accent) 12%, transparent)", color: "var(--accent)" }}>
                <span className="sb-live-dot" style={{ background: "var(--accent)", animation: "none" }}/>локально
              </span>
          }
        </div>
        <div className="sb-sub">
          {hasSheets
            ? "Кликни по любому блоку — теме, вехе, вопросу — и оставь комментарий или вопрос Володе. Каждый комментарий сразу записывается в лист «Weekly Feedback» в Google Sheets."
            : "Кликни по любому блоку — теме, вехе, вопросу — и оставь комментарий или вопрос Володе. Комментарии сохраняются в этом браузере. Скопируй все открытые одной кнопкой."
          }
        </div>
        <div className="sb-stats-row">
          <span className="sb-stat"><b>{open}</b> открыто</span>
          <span className="sb-stat sb-stat-q"><b>{resolved}</b> решено</span>
          <button className="sb-link-btn" onClick={() => setPanelOpen(true)}>все · в боковой панели →</button>
          <button className="sb-link" onClick={copyAll} style={{ cursor: "pointer" }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            скопировать открытые
          </button>
        </div>
      </div>
    </div>
  );
}

// ===== main reader =====
function ReaderView({ topics, week }) {
  const real = topics.filter(t => t.movement === "real_result");
  const unclear = topics.filter(t => t.movement === "unclear");
  const stale = topics.filter(t => t.movement === "no_movement");
  const syncs = topics.filter(t => t.sync === "yes");
  const questions = topics.filter(t => t.question);

  const milestonesAll = topics.flatMap(t =>
    t.milestones.map(m => ({ ...m, topic: t.title, id: t.id, ball: t.ball }))
  );
  const upcoming = milestonesAll.filter(m => /^\d{2}\.\d{2}/.test(m.date)).slice(0, 6);

  const sectionRefs = {
    sync: useRef(null),
    questions: useRef(null),
    upcoming: useRef(null),
    real: useRef(null),
    unclear: useRef(null),
    stale: useRef(null),
  };
  const [focus, setFocus] = useState(null);

  const goTo = (key) => {
    setFocus(key);
    const ref = sectionRefs[key];
    if (ref?.current) {
      const reader = document.querySelector('.reader');
      const usesWindow = !reader || reader.scrollHeight <= reader.clientHeight + 1;
      const rectTop = ref.current.getBoundingClientRect().top;
      if (usesWindow) {
        const top = rectTop + (window.scrollY || window.pageYOffset || 0) - 24;
        window.scrollTo({ top, behavior: 'smooth' });
      } else {
        const top = rectTop + reader.scrollTop - 24;
        reader.scrollTo({ top, behavior: 'smooth' });
      }
      window.setTimeout(() => setFocus(null), 1800);
    }
  };

  const scrollToTopic = (id) => {
    const el = document.getElementById(`topic-${id}`);
    if (!el) return;
    const reader = document.querySelector('.reader');
    const usesWindow = !reader || reader.scrollHeight <= reader.clientHeight + 1;
    if (usesWindow) {
      window.scrollTo({ top: el.getBoundingClientRect().top + (window.scrollY || 0) - 24, behavior: 'smooth' });
    } else {
      reader.scrollTo({ top: el.getBoundingClientRect().top + reader.scrollTop - 24, behavior: 'smooth' });
    }
  };

  return (
    <CommentsProvider week={week}>
      <main className="reader" data-focus={focus || ""}>
        <div className="reader-inner">
          <CommentsHeader/>

          <header className="r-masthead">
            <div className="r-mast-l">
              <div className="eyebrow">Weekly · бизнес-контур</div>
              <h1>The Weekly<span className="week-no">{week.label} / {week.range}</span></h1>
            </div>
            <div className="r-mast-r">
              <div className="from"><span style={{color:"var(--ink-4)"}}>от</span> Володи · <span style={{color:"var(--ink-4)"}}>для</span> Евгения</div>
              <div>отправлено · 7 мая, четверг, 22:14</div>
              <div style={{color:"var(--ink-4)"}}>прошлая неделя — {week.prevWeek}</div>
            </div>
          </header>

          <CommentsBanner/>

          <section style={{ marginBottom: 56 }}>
            <div className="eyebrow" style={{ marginBottom: 14 }}>TL;DR · что было важного</div>
            <Annotatable anchor="tldr" label="TL;DR недели" inline>
              <p className="serif-h" style={{ fontSize: 24, fontStyle: "italic", lineHeight: 1.35, margin: 0, color: "var(--ink)", maxWidth: 820, textWrap: "pretty" }}>
                {real.length > 0 ? (
                  <>
                    {"Реальный результат — "}
                    {real.slice(0, 5).map((t, i, arr) => (
                      <React.Fragment key={t.id}>
                        {i > 0 && (i === arr.length - 1 ? " и " : ", ")}
                        <button onClick={() => scrollToTopic(t.id)} style={{
                          background: "none", border: "none", padding: 0, cursor: "pointer",
                          font: "inherit", color: "inherit",
                          textDecoration: "underline",
                          textDecorationColor: "var(--accent-good)",
                          textDecorationThickness: 2,
                          textUnderlineOffset: 4,
                        }}>{t.title}</button>
                      </React.Fragment>
                    ))}
                    {real.length > 5 ? ` и ещё ${real.length - 5}` : ""}.
                    {syncs.length > 0 && <>{" "}{syncs.length} {syncs.length === 1 ? "тема" : "темы"} на sync — детали ниже.</>}
                  </>
                ) : (
                  `Новых результатов за эту неделю нет.${syncs.length > 0 ? ` ${syncs.length} тем на sync — детали ниже.` : ""}`
                )}
              </p>
            </Annotatable>
          </section>

          <div className="stats-strip" role="navigation" aria-label="Сводка — клик уводит к разделу">
            <button type="button" className="stat good" onClick={()=>goTo('real')} aria-pressed={focus==='real'}>
              <div className="num">{real.length}<span className="of">/{topics.length}</span></div>
              <div className="lbl">с реальным результатом</div>
              <span className="stat-go">перейти к разделу ↓</span>
            </button>
            <button type="button" className="stat accent" onClick={()=>goTo('sync')} aria-pressed={focus==='sync'}>
              <div className="num">{syncs.length}</div>
              <div className="lbl">кандидаты на sync</div>
              <span className="stat-go">к списку для синка ↓</span>
            </button>
            <button type="button" className="stat warn" onClick={()=>goTo('questions')} aria-pressed={focus==='questions'}>
              <div className="num">{questions.length}</div>
              <div className="lbl">вопросов к тебе</div>
              <span className="stat-go">показать вопросы ↓</span>
            </button>
            <button type="button" className="stat" onClick={()=>goTo('stale')} aria-pressed={focus==='stale'}>
              <div className="num" style={{color:"var(--ink-3)"}}>{stale.length}</div>
              <div className="lbl">без движения · перенесены</div>
              <span className="stat-go">показать перенесённые ↓</span>
            </button>
          </div>

          <div className="sync-rec" ref={sectionRefs.sync} data-focused={focus==='sync'}>
            <div className="eyebrow" style={{ marginBottom: 8, color: "var(--accent)" }}>Рекомендую синхронизироваться</div>
            <h2>На синке стоит закрыть {syncs.length} тем — есть решения, которые лучше взять одной пачкой</h2>
            <div className="sub">Темы отсортированы по «дороговизне» отсутствия решения для движения вперёд</div>
            <div className="sync-list">
              {syncs.map((t, i) => (
                <div className="sync-item" key={t.id}>
                  <div className="num">{String(i+1).padStart(2,"0")}</div>
                  <div>
                    <div className="si-title">
                      {t.title}
                      <span className="id-mono">{t.id}</span>
                      <BallChip ball={t.ball} name={t.ballName}/>
                    </div>
                    <Annotatable anchor={`sync-reason-${t.id}`} label={`Sync · ${t.title}`}>
                      <div className="si-reason">{t.syncReason}</div>
                    </Annotatable>
                    {t.question && (
                      <Annotatable anchor={`sync-q-${t.id}`} label={`Вопрос · ${t.title}`}>
                        <div className="si-q"><span style={{ fontFamily: "var(--mono)", fontSize: 9, fontStyle: "normal", letterSpacing: ".1em", textTransform: "uppercase", color: "var(--accent)", display: "block", marginBottom: 2 }}>вопрос</span>{t.question}</div>
                      </Annotatable>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {questions.length > 0 && (
            <>
              <div className="section-title" ref={sectionRefs.questions} data-focused={focus==='questions'}>
                <h3>Открытые вопросы к тебе</h3>
                <span className="count">{questions.length}</span>
                <div className="rule"/>
                <span className="section-hint">клик по вопросу → комментарий</span>
              </div>
              <div className="questions-grid">
                {questions.map(t => (
                  <div key={t.id} style={{ padding: "16px 0", borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className="id-mono">{t.id}</span>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{t.title}</span>
                    </div>
                    <Annotatable anchor={`q-${t.id}`} label={`Вопрос · ${t.title}`}>
                      <p style={{ margin: 0, fontFamily: "var(--serif)", fontStyle: "italic", fontSize: 14.5, lineHeight: 1.45, color: "var(--ink)" }}>{t.question}</p>
                    </Annotatable>
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="section-title" ref={sectionRefs.upcoming}>
            <h3>Ближайшие вехи</h3>
            <span className="count">{upcoming.length}</span>
            <div className="rule"/>
          </div>
          <div style={{ border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden", background: "var(--paper)" }}>
            {upcoming.map((m, i) => (
              <Annotatable key={i} anchor={`ms-${m.id}-${i}`} label={`Веха ${m.date} · ${m.topic}`}>
                <div className="ms-table-row" style={{ borderTop: i ? "1px solid var(--line-2)" : "0" }}>
                  <span className="id-mono msr-date">{m.date}</span>
                  <span className="msr-text">{m.text}</span>
                  <span className="msr-topic">{m.topic}</span>
                  <BallChip ball={m.ball}/>
                </div>
              </Annotatable>
            ))}
          </div>

          <div className="section-title" ref={sectionRefs.real} data-focused={focus==='real'}>
            <h3>Реальное движение</h3>
            <span className="count">{real.length}</span>
            <div className="rule"/>
          </div>
          {real.map(t => <TopicArticle key={t.id} t={t}/>)}

          {unclear.length > 0 && (
            <>
              <div className="section-title">
                <h3>Неясно</h3>
                <span className="count">{unclear.length}</span>
                <div className="rule"/>
              </div>
              {unclear.map(t => <TopicArticle key={t.id} t={t}/>)}
            </>
          )}

          <div className="section-title" ref={sectionRefs.stale} data-focused={focus==='stale'}>
            <h3>Без движения · перенесены</h3>
            <span className="count">{stale.length}</span>
            <div className="rule"/>
          </div>
          <div style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--paper)" }}>
            {stale.map((t,i) => (
              <Annotatable key={t.id} anchor={`stale-${t.id}`} label={t.title}>
                <div className="stale-table-row" style={{ borderTop: i ? "1px solid var(--line-2)" : "0" }}>
                  <span className="id-mono">{t.id}</span>
                  <span className="str-title">{t.title}</span>
                  <span className="str-date">{t.milestones[0]?.date || "—"}</span>
                  <BallChip ball={t.ball} name={t.ballName}/>
                </div>
              </Annotatable>
            ))}
          </div>

          <div className="archive-note">
            <span>Всего активных тем: {topics.length} · фокус недели задаёт Володя · архив скрыт</span>
            <span style={{ fontFamily: "var(--mono)" }}>сгенерировано 7.05.26 · v.0.4</span>
          </div>
        </div>
        <CommentPopover/>
        <CommentsPanel/>
      </main>
    </CommentsProvider>
  );
}

// floating header for comments tools
function CommentsHeader() {
  const { comments, setPanelOpen, panelOpen } = useComments();
  const open = comments.filter(c => !c.resolved).length;
  return (
    <div className="anno-fab" onClick={() => setPanelOpen(!panelOpen)}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      <span>Комментарии</span>
      {open > 0 && <span className="anno-fab-num">{open}</span>}
    </div>
  );
}

function TopicArticle({ t }) {
  const isStaleMS = (d) => {
    if (!/^\d{2}\.\d{2}/.test(d)) return false;
    const [dd, mm] = d.split(".");
    const today = new Date(2026, 4, 7);
    const dt = new Date(2026, parseInt(mm)-1, parseInt(dd));
    return dt < today;
  };
  return (
    <article className="topic-art" id={`topic-${t.id}`}>
      <div className="ta-meta">
        <span className="id-mono">{t.id}</span>
        <MovementTag m={t.movement}/>
        {t.sync === "yes" && <span className="tag tag-sync"><span className="tdot"/>sync</span>}
        <BallChip ball={t.ball} name={t.ballName}/>
        {t.link && <a href={t.link} className="id-mono" style={{ color: "var(--accent)" }}>↗ источник</a>}
      </div>
      <div className="ta-body">
        <Annotatable anchor={`title-${t.id}`} label={t.title} inline>
          <h4>{t.title}</h4>
        </Annotatable>
        <Annotatable anchor={`result-${t.id}`} label={`Результат · ${t.title}`}>
          <p className="result">{t.result}</p>
        </Annotatable>
        {t.question && (
          <Annotatable anchor={`q-${t.id}`} label={`Вопрос · ${t.title}`}>
            <div className="question">
              <span className="qlbl">вопрос к тебе</span>
              {t.question}
            </div>
          </Annotatable>
        )}
      </div>
      <div className="ms-timeline-wrap">
        <div className="eyebrow" style={{ marginBottom: 8 }}>вехи</div>
        {t.milestones.length === 0 ? (
          <div className="ms-empty">не заданы</div>
        ) : (
          <div className="ms-timeline">
            {t.milestones.map((m,i) => (
              <Annotatable key={i} anchor={`ms-${t.id}-${i}`} label={`Веха ${m.date} · ${t.title}`}>
                <div className="ms-tl-row">
                  <span className={`ms-tl-date ${isStaleMS(m.date)?"stale":""}`}>{m.date}</span>
                  <span className="ms-tl-text">{m.text}</span>
                </div>
              </Annotatable>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

window.ReaderView = ReaderView;
window.TopicArticle = TopicArticle;
