// ─── Google Sheets configuration ────────────────────────────────────────────
// 1. Share the spreadsheet: "Доступ → Все, у кого есть ссылка → Читатель"
// 2. Create an API key in Google Cloud → APIs & Services → Credentials
//    (restrict to Sheets API + your GitHub Pages domain)
// 3. Paste below and push.
const SHEETS_CONFIG = {
  spreadsheetId: "14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs",
  apiKey: "",  // ← вставь сюда свой API-ключ
  sheetPattern: /^Weekly MVP \d{4}-\d{2}-\d{2}$/,
};

// ─── Sheet column headers (from csv_adapter.py HEADERS) ─────────────────────
const COL = {
  section:       "Секция",
  topicId:       "Topic ID",
  topicTitle:    "Тема",
  lifecycle:     "Lifecycle",
  facts:         "Факты этой недели",
  result:        "Итоговая формулировка",
  milestone:     "Ближайшая веха",
  milestoneDate: "Дата вехи",
  ball:          "На чьей стороне мяч",
  question:      "Открытый вопрос к Евгению",
  movement:      "Movement type",
  sync:          "Нужен sync",
  syncReason:    "Причина sync",
  links:         "Source / links",
};

// ─── Sheets API helpers ──────────────────────────────────────────────────────
async function sheetsGet(path, params = {}) {
  const url = new URL(`https://sheets.googleapis.com/v4/spreadsheets/${SHEETS_CONFIG.spreadsheetId}${path}`);
  url.searchParams.set("key", SHEETS_CONFIG.apiKey);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url.toString());
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.error?.message || `Sheets API error ${res.status}`);
  }
  return res.json();
}

// Returns [{sheetName, date}] sorted newest-first
async function fetchSheetList() {
  const data = await sheetsGet("", { fields: "sheets.properties(title)" });
  const sheets = (data.sheets || [])
    .map(s => s.properties?.title || "")
    .filter(t => SHEETS_CONFIG.sheetPattern.test(t))
    .map(t => {
      const m = t.match(/(\d{4})-(\d{2})-(\d{2})$/);
      return { sheetName: t, date: m ? new Date(+m[1], +m[2]-1, +m[3]) : new Date(0) };
    })
    .sort((a, b) => b.date - a.date);
  if (!sheets.length) throw new Error("Не найдено вкладок «Weekly MVP YYYY-MM-DD»");
  return sheets;
}

// Fetches and parses one sheet into { TOPICS, WEEK }
async function fetchWeekData(sheetName) {
  const range = encodeURIComponent(`'${sheetName}'!A:T`);
  const data = await sheetsGet(`/values/${range}`);
  const values = data.values || [];
  if (values.length < 2) throw new Error(`Пустая вкладка: ${sheetName}`);
  return parseSheetValues(values, sheetName);
}

// ─── Parser ──────────────────────────────────────────────────────────────────
function parseSheetValues(values, sheetName) {
  const headers = values[0].map(h => (h || "").trim());
  const idx = {};
  for (const [key, header] of Object.entries(COL)) {
    const i = headers.indexOf(header);
    if (i >= 0) idx[key] = i;
  }

  const get = (row, key) => (row[idx[key]] || "").trim();

  const TOPICS = values.slice(1)
    .filter(row => {
      const title = get(row, "topicTitle");
      const lifecycle = get(row, "lifecycle").toLowerCase();
      return title && lifecycle === "active";
    })
    .map((row, i) => {
      const ball = parseBall(get(row, "ball"));
      const milestoneDate = get(row, "milestoneDate");
      const milestoneText = get(row, "milestone");
      return {
        id: get(row, "topicId") || `T-${String(i+1).padStart(3,"0")}`,
        title: get(row, "topicTitle"),
        facts: get(row, "facts"),
        result: get(row, "result"),
        milestones: milestoneDate || milestoneText
          ? [{ date: milestoneDate, text: milestoneText }]
          : [],
        ball: ball.ball,
        ballName: ball.name,
        question: get(row, "question"),
        movement: get(row, "movement") || "unclear",
        sync: get(row, "sync") === "yes" ? "yes" : "no",
        syncReason: get(row, "syncReason"),
        link: get(row, "links"),
      };
    });

  const WEEK = weekMetaFromSheetName(sheetName);
  return { TOPICS, WEEK };
}

function parseBall(raw) {
  const v = (raw || "").trim().toLowerCase();
  if (!v || v === "me" || v === "я") return { ball: "me" };
  if (v === "evgeny" || v.includes("евгени")) return { ball: "evgeny" };
  return { ball: "external", name: raw.trim() };
}

function weekMetaFromSheetName(sheetName) {
  const m = sheetName.match(/(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return { label: sheetName, range: sheetName, rangeShort: "", to: "Евгению", from: "Володя", prevWeek: "" };
  const [, year, month, day] = m;
  const end = new Date(+year, +month-1, +day);
  const start = new Date(end); start.setDate(start.getDate() - 6);
  const MONTHS_RU = ["янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"];
  const range = `${start.getDate()} — ${end.getDate()} ${MONTHS_RU[end.getMonth()]} ${end.getFullYear()}`;
  const rangeShort = `${String(start.getDate()).padStart(2,"0")}.${String(start.getMonth()+1).padStart(2,"0")} – ${String(end.getDate()).padStart(2,"0")}.${String(end.getMonth()+1).padStart(2,"0")}`;
  const weekNum = getISOWeek(end);
  const prevEnd = new Date(start); prevEnd.setDate(prevEnd.getDate() - 1);
  const prevStart = new Date(prevEnd); prevStart.setDate(prevStart.getDate() - 6);
  const prevRange = `${prevStart.getDate()} — ${prevEnd.getDate()} ${MONTHS_RU[prevEnd.getMonth()]}`;
  return {
    label: `W${weekNum}`,
    range,
    rangeShort,
    to: "Евгению",
    from: "Володя",
    prevWeek: `W${weekNum-1} · ${prevRange}`,
  };
}

function getISOWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

// ─── Fallback static data (last generated week) ──────────────────────────────
const FALLBACK_TOPICS = [
  { id: "T-001", title: "Отчётность ОТЗ и АА",
    facts: "Познакомился с новым бухгалтером ОТЗ; выглядит более клиентоориентированной, но управленческий бардак в проекте сохраняется. Майскую отчётность по ОТЗ держу на ежедневном контроле. По АА пробую завести процесс через нового бухгалтера на фоне ухода Ирины в отпуск. При этом сохраняется риск, что бухгалтерия по АА не закроется на этой неделе и отчёт не соберётся в срок.",
    result: "Взял на ежедневный контроль сбор майской отчётности по ОТЗ и начал перестраивать контур по АА через нового бухгалтера; ключевой риск недели — возможный срыв сроков по АА из-за незакрытой бухгалтерии.",
    milestones: [{date:"15.05", text:"Отчёт за май"},{date:"01.06", text:"Найти и зафиксировать причину расхождения"}],
    ball:"external", ballName:"Павел / бухгалтер",
    question:"", movement:"real_result", sync:"yes",
    syncReason:"По АА есть риск, что бухгалтерия не закроется вовремя и отчёт не соберётся в срок." },
  { id: "T-004", title: "Бизнес-план по коксованию",
    facts: "Провели встречу вчетвером с Эльвирой, Иван Ивановичем и Сергеем.",
    result: "Пересобрали фокус по рынкам и каналам продаж для проекта по коксованию; жду от Эльвиры план по ресурсам для аутрича.",
    milestones:[{date:"30.06", text:'Получить принципиальный «ок» на ещё один проект'}],
    ball:"external", ballName:"Эльвира",
    question:"Нужно обсудить, возвращаемся ли к переговорам с российским лидом, которому ранее отказали.",
    movement:"real_result", sync:"yes",
    syncReason:"Нужно решение по возврату к российскому лиду." },
  { id: "T-011", title: "Закрытие фонда «Экспонента»",
    facts: "Дорабатывали объявление о продаже дебиторки. До 15.05 собираются предложения.",
    result:"Доработал объявление по продаже дебиторки и вывел процесс в стадию сбора предложений до 15.05.",
    milestones:[{date:"15.05", text:"Собрать предложения"},{date:"31.05", text:"Заключение договора уступки"}],
    ball:"evgeny",
    question:"Кого выбираем покупателем дебиторки: Стекольщиков или вариант через Анну?",
    movement:"real_result", sync:"yes",
    syncReason:"Нужен выбор покупателя, без этого тема не двигается к сделке." },
  { id: "T-013", title: "Биотех. Займы акционеров",
    facts: "В Excel проставлены даты договоров. Результат отправлен, ожидается обратная связь.",
    result:"Финализировал ключевые доработки по реестру займов акционеров; текущая версия передана на ревью.",
    milestones:[{date:"08.05", text:"Финализированный документ"}],
    ball:"evgeny", question:"Что ещё критично допилить в текущей версии?",
    movement:"real_result", sync:"yes",
    syncReason:"Нужна обратная связь по финальной версии перед закрытием." },
  { id: "T-003", title: "ТЭЦ",
    facts:"Без нового движения за неделю.", result:"Без нового движения за неделю.",
    milestones:[{date:"30.05", text:"Найти проектировщика"}],
    ball:"me", question:"", movement:"no_movement", sync:"no" },
  { id: "T-006", title: "Рыбоводство (РФ)",
    facts: "Веху сдвигаем на 13–14.05.",
    result: "Существенного продвижения не было; сдвинута ближайшая веха.",
    milestones:[{date:"13.05", text:"Планы и модель от Руслана"}],
    ball:"me", question:"", movement:"unclear", sync:"no" },
];

const FALLBACK_WEEK = {
  label: "W18",
  range: "1 — 7 мая 2026",
  rangeShort: "01.05 – 07.05",
  to: "Евгению",
  from: "Володя",
  prevWeek: "W17 · 24 — 30 апреля",
};

window.SHEETS_CONFIG = SHEETS_CONFIG;
window.fetchSheetList = fetchSheetList;
window.fetchWeekData = fetchWeekData;
window.FALLBACK_TOPICS = FALLBACK_TOPICS;
window.FALLBACK_WEEK = FALLBACK_WEEK;
