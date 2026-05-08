// Editor view — Володя's role: fills out the report
const { useState, useMemo } = React;

function Icon({ name, size = 14 }) {
  const stroke = "currentColor";
  const sw = 1.5;
  const paths = {
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    sparkle: <><path d="M12 3l1.6 4.6L18 9l-4.4 1.4L12 15l-1.6-4.6L6 9l4.4-1.4z"/><path d="M19 15l.7 1.8L21.5 17.5 19.7 18l-.7 1.5-.7-1.5L16.5 17.5 18.3 16.8z"/></>,
    trash: <><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    check: <><path d="M5 12l4 4L19 7"/></>,
    link: <><path d="M9 15l6-6"/><path d="M11 6l1-1a4 4 0 0 1 6 6l-1 1"/><path d="M13 18l-1 1a4 4 0 0 1-6-6l1-1"/></>,
    download: <><path d="M12 4v12M6 11l6 6 6-6M5 20h14"/></>,
    eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function BallChip({ ball, name }) {
  const initials = ball === "me" ? "В" : ball === "evgeny" ? "Е" : (name ? name.charAt(0) : "·");
  const labels = { me: "Володя", evgeny: "Евгений", external: name || "Внешняя сторона" };
  return (
    <span className={`ball ball-${ball}`}>
      <span className="ball-av">{initials}</span>
      <span>{labels[ball]}</span>
    </span>
  );
}

function MovementTag({ m }) {
  const map = {
    real_result: { cls: "tag-real", label: "результат" },
    no_movement: { cls: "tag-no", label: "без движения" },
    unclear: { cls: "tag-unclear", label: "неясно" },
  };
  const c = map[m] || map.no_movement;
  return <span className={`tag ${c.cls}`}><span className="tdot"/>{c.label}</span>;
}

window.Icon = Icon;
window.BallChip = BallChip;
window.MovementTag = MovementTag;

function EditorView({ topics, setTopics, selectedId, setSelectedId }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("active");
  const [showSuggest, setShowSuggest] = useState({});

  const filtered = useMemo(() => {
    let t = topics;
    if (filter === "movement") t = t.filter(x => x.movement === "real_result");
    if (filter === "sync") t = t.filter(x => x.sync === "yes");
    if (filter === "questions") t = t.filter(x => x.question);
    if (filter === "stale") t = t.filter(x => x.movement === "no_movement");
    if (query) t = t.filter(x => (x.title + x.id).toLowerCase().includes(query.toLowerCase()));
    return t;
  }, [topics, filter, query]);

  const groups = useMemo(() => {
    const buckets = { real_result: [], unclear: [], no_movement: [] };
    filtered.forEach(t => buckets[t.movement]?.push(t));
    return buckets;
  }, [filtered]);

  const sel = topics.find(t => t.id === selectedId);

  const update = (patch) => {
    setTopics(topics.map(t => t.id === selectedId ? { ...t, ...patch } : t));
  };

  const counts = {
    active: topics.length,
    movement: topics.filter(t => t.movement === "real_result").length,
    sync: topics.filter(t => t.sync === "yes").length,
    questions: topics.filter(t => t.question).length,
    stale: topics.filter(t => t.movement === "no_movement").length,
  };

  return (
    <div className="editor">
      <aside className="ed-sidebar">
        <div className="ed-sidebar-head">
          <div style={{ position: "relative" }}>
            <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--ink-3)" }}>
              <Icon name="search" size={13}/>
            </span>
            <input className="ed-search" style={{ paddingLeft: 30 }} placeholder="Поиск по темам…"
              value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          <div className="filter-chips">
            <button className="chip" aria-pressed={filter==="active"} onClick={()=>setFilter("active")}>Все<span className="num">{counts.active}</span></button>
            <button className="chip" aria-pressed={filter==="movement"} onClick={()=>setFilter("movement")}>С движением<span className="num">{counts.movement}</span></button>
            <button className="chip" aria-pressed={filter==="sync"} onClick={()=>setFilter("sync")}>На sync<span className="num">{counts.sync}</span></button>
            <button className="chip" aria-pressed={filter==="questions"} onClick={()=>setFilter("questions")}>Вопросы<span className="num">{counts.questions}</span></button>
            <button className="chip" aria-pressed={filter==="stale"} onClick={()=>setFilter("stale")}>Без движ.<span className="num">{counts.stale}</span></button>
          </div>
        </div>
        <div className="ed-list">
          {["real_result","unclear","no_movement"].map(g => groups[g].length > 0 && (
            <div key={g}>
              <div className="ed-section-h">
                <span>{g === "real_result" ? "Реальный результат" : g === "unclear" ? "Неясно" : "Без движения"}</span>
                <span className="count">{groups[g].length}</span>
              </div>
              {groups[g].map(t => (
                <div key={t.id} className={`topic-row ${selectedId===t.id?"active":""}`} onClick={()=>setSelectedId(t.id)}>
                  <div className="tr-top">
                    <span className="tr-id">{t.id}</span>
                    {t.sync === "yes" && <span className="tag tag-sync"><span className="tdot"/>sync</span>}
                    {t.question && <span style={{ color: "var(--accent)", fontSize: 11, fontWeight: 500 }}>?</span>}
                  </div>
                  <div className="tr-title">{t.title}</div>
                  <div className="tr-meta">
                    <BallChip ball={t.ball} name={t.ballName}/>
                    {t.milestones[0] && (<><span className="dotsep">·</span><span style={{ fontFamily: "var(--mono)" }}>{t.milestones[0].date}</span></>)}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </aside>

      <main className="ed-center">
        {sel ? (
          <div className="ed-center-inner">
            <div className="eh-eyebrow">
              <span className="id-mono">{sel.id}</span>
              <MovementTag m={sel.movement}/>
              {sel.sync === "yes" && <span className="tag tag-sync"><span className="tdot"/>требует sync</span>}
            </div>
            <h1 className="eh-title">{sel.title}</h1>
            <div className="eh-subtitle">Перенос с прошлой недели · обновите факты, чтобы переоформить строку</div>

            <div className="eh-toolbar">
              <button className="btn btn-primary"><Icon name="sparkle"/> Предложить строку</button>
              <button className="btn"><Icon name="eye"/> Объясни почему</button>
              <button className="btn-ghost btn"><Icon name="link"/> Источники</button>
              <span style={{ marginLeft: "auto", color: "var(--ink-3)", fontSize: 11, fontFamily: "var(--mono)" }}>
                сохр. 12:42
              </span>
            </div>

            <div className="field">
              <div className="field-label">
                <span>Факты недели</span>
                <span className="hint">что произошло — память, созвоны, telegram</span>
              </div>
              <textarea className="field-textarea lg" defaultValue={sel.facts} key={sel.id+"f"}/>
            </div>

            <div className="field">
              <div className="field-label">
                <span>Финальный результат строки</span>
                <span className="hint">через результат, не через процесс</span>
              </div>
              <textarea className="field-textarea" defaultValue={sel.result} key={sel.id+"r"}/>
              {sel.movement === "real_result" && (
                <div className="field-suggest">
                  <span className="sg-tag">draft</span>
                  <span>Можно сжать: «<i>Финализировал реестр займов и передал на ревью; жду обратную связь по последним правкам.</i>»</span>
                  <span className="sg-actions">
                    <button className="sg-mini">отклонить</button>
                    <button className="sg-mini primary">принять</button>
                  </span>
                </div>
              )}
            </div>

            <div className="field">
              <div className="field-label"><span>Ближайшие вехи</span><span className="hint">только подтверждённые даты</span></div>
              <div className="milestones">
                {sel.milestones.map((m, i) => (
                  <div key={i} className="ms-row">
                    <input className="field-input ms-date" defaultValue={m.date}/>
                    <input className="field-input" defaultValue={m.text}/>
                    <button className="ms-del"><Icon name="trash" size={14}/></button>
                  </div>
                ))}
                <button className="ms-add"><Icon name="plus" size={12}/> добавить веху</button>
              </div>
            </div>

            <div className="row-pair">
              <div className="field" style={{ marginBottom: 0 }}>
                <div className="field-label"><span>На чьей стороне мяч</span></div>
                <div className="ball-picker">
                  {["me","evgeny","external"].map(b => (
                    <button key={b} className="bp-btn" aria-pressed={sel.ball===b} onClick={()=>update({ball:b})}>
                      <span className={`ball ball-${b}`}><span className="ball-av">{b==="me"?"В":b==="evgeny"?"Е":"·"}</span></span>
                      {b==="me"?"Володя":b==="evgeny"?"Евгений":"Внешн."}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field" style={{ marginBottom: 0 }}>
                <div className="field-label"><span>Тип движения</span></div>
                <div className="ball-picker">
                  {[["real_result","результат"],["unclear","неясно"],["no_movement","без движ."]].map(([v,l])=>(
                    <button key={v} className="bp-btn" aria-pressed={sel.movement===v} onClick={()=>update({movement:v})}>
                      {l}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="field" style={{ marginTop: 26 }}>
              <div className="field-label">
                <span>Открытый вопрос Евгению</span>
                <span className="hint">только то, что должен увидеть он</span>
              </div>
              <textarea className="field-textarea" defaultValue={sel.question} key={sel.id+"q"} placeholder="Если вопроса нет — оставьте пустым"/>
            </div>

            <div className="toggle-row">
              <button className="tg" aria-pressed={sel.sync==="yes"} onClick={()=>update({sync: sel.sync==="yes"?"no":"yes"})}/>
              <div style={{ flex: 1 }}>
                <div className="label">Нужен sync с Евгением</div>
                <div className="sub">{sel.syncReason || "Опишите коротко, зачем"}</div>
              </div>
            </div>

            {sel.sync === "yes" && (
              <div className="field" style={{ marginTop: 16 }}>
                <div className="field-label"><span>Причина для sync</span></div>
                <textarea className="field-textarea" defaultValue={sel.syncReason} key={sel.id+"sr"}/>
              </div>
            )}

            <div style={{ marginTop: 40, padding: "14px 16px", border: "1px solid var(--line)", borderRadius: 12, background: "var(--paper)", display: "flex", alignItems: "center", gap: 12 }}>
              <Icon name="link" size={14}/>
              <span style={{ fontSize: 12, color: "var(--ink-3)" }}>Источники / ссылки</span>
              {sel.link ? (
                <a href={sel.link} className="id-mono" style={{ color: "var(--accent)", marginLeft: 8 }}>{sel.link.replace(/^https?:\/\//,"")}</a>
              ) : (
                <span style={{ color: "var(--ink-4)", fontSize: 12, marginLeft: 8 }}>не приложено</span>
              )}
              <button className="btn btn-ghost" style={{ marginLeft: "auto" }}><Icon name="plus" size={12}/> добавить</button>
            </div>
          </div>
        ) : (
          <div className="empty-detail">выберите тему слева</div>
        )}
      </main>

      <aside className="ed-inspector">
        {sel ? (
          <>
            <div className="insp-h">Контекст темы</div>

            <div className="insp-card">
              <h4>Прошлая неделя · W17</h4>
              <p>{sel.movement === "no_movement"
                ? "Также «без движения». Тема переносится 2-ю неделю подряд."
                : "Состояние перенесено с прошлой недели — обновите факты, если есть новое."}</p>
            </div>

            <div className="insp-card">
              <h4>Качество строки</h4>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8, fontSize: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--accent-good)" }}>
                  <Icon name="check" size={12}/> результат, а не процесс
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--accent-good)" }}>
                  <Icon name="check" size={12}/> понятна без расшифровки
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--accent-good)" }}>
                  <Icon name="check" size={12}/> однозначный мяч
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: sel.milestones.length ? "var(--accent-good)" : "var(--accent-warn)" }}>
                  <Icon name={sel.milestones.length ? "check" : "plus"} size={12}/> {sel.milestones.length ? "веха задана" : "веха пустая"}
                </div>
              </div>
            </div>

            <div className="insp-h" style={{ marginTop: 24 }}>Зачем нужен sync</div>
            <div className="movement-pick">
              {[
                ["q","Вопрос Евгению на focus-теме"],
                ["risk","Появился риск или блокер"],
                ["decision","Нужно решение, без него стоп"],
                ["pack","Накопилось > 2 решений — лучше пачкой"],
              ].map(([v,l]) => (
                <label key={v} className={sel.sync==="yes" && v==="decision" ? "checked" : ""}>
                  <input type="checkbox" defaultChecked={sel.sync==="yes" && v==="decision"}/>
                  <span>
                    <span className="mp-title">{l.split(" ")[0]} {l.split(" ").slice(1,3).join(" ")}</span>
                    <span className="mp-sub">{l}</span>
                  </span>
                </label>
              ))}
            </div>
          </>
        ) : (
          <div style={{ color: "var(--ink-4)", fontSize: 12 }}>—</div>
        )}
      </aside>
    </div>
  );
}

window.EditorView = EditorView;
