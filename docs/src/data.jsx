// ─── Google Sheets configuration ────────────────────────────────────────────
// 1. Share the spreadsheet: "Доступ → Все, у кого есть ссылка → Читатель"
// 2. If the spreadsheet is public by link, GitHub Pages reads the current
//    weekly tab through the public gviz CSV endpoint, no API key required.
// 3. Optional: paste an API key to enable automatic week-tab discovery.
// 4. Optional: deploy Apps Script web app to receive comments → see apps_script.js
const SHEETS_CONFIG = {
  spreadsheetId: "14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs",
  apiKey: "",          // ← optional: API-ключ для discovery недельных вкладок
  publicCsvGid: "20260508",
  publicCsvSheetName: "Weekly MVP 2026-05-08",
  appsScriptUrl: "https://script.google.com/macros/s/AKfycbxqO_xtAfwH3jmstuS-uEl8696HMEeEp_KmTMSegmy5sw4hUgoMo2ra3Yje0ZisuSS6/exec",   // ← URL задеплоенного Apps Script (для записи комментариев)
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

async function fetchPublicCsvWeekData(sheetName = SHEETS_CONFIG.publicCsvSheetName) {
  const url = new URL(`https://docs.google.com/spreadsheets/d/${SHEETS_CONFIG.spreadsheetId}/gviz/tq`);
  url.searchParams.set("tqx", "out:csv");
  if (SHEETS_CONFIG.publicCsvGid) {
    url.searchParams.set("gid", SHEETS_CONFIG.publicCsvGid);
  } else if (sheetName) {
    url.searchParams.set("sheet", sheetName);
  } else {
    throw new Error("Не настроен publicCsvGid или publicCsvSheetName");
  }
  url.searchParams.set("_", String(Date.now()));

  const res = await fetch(url.toString(), { cache: "no-store", credentials: "omit" });
  if (!res.ok) throw new Error(`Public CSV error ${res.status}`);
  const text = await res.text();
  if (!text.trim() || text.trimStart().startsWith("<")) {
    throw new Error("Public CSV endpoint вернул не CSV. Проверь доступ «Все, у кого есть ссылка → Читатель».");
  }
  const values = parseCsv(text);
  if (values.length < 2) throw new Error(`Пустая публичная CSV-вкладка: ${sheetName}`);
  return parseSheetValues(values, sheetName || SHEETS_CONFIG.publicCsvSheetName || "Weekly");
}

async function fetchStaticReport() {
  const res = await fetch("report.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`Static report error ${res.status}`);
  const report = await res.json();
  return parseStaticReport(report);
}

// ─── Parser ──────────────────────────────────────────────────────────────────
function parseSheetValues(values, sheetName) {
  const headers = values[0].map(h => (h || "").trim());
  const idx = {};
  for (const [key, header] of Object.entries(COL)) {
    const i = headers.indexOf(header);
    if (i >= 0) idx[key] = i;
  }

  const get = (row, key) => String(row[idx[key]] ?? "").trim();

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

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (ch === "\r") {
      if (text[i + 1] !== "\n") {
        row.push(field);
        rows.push(row);
        row = [];
        field = "";
      }
    } else {
      field += ch;
    }
  }

  row.push(field);
  rows.push(row);
  return rows.filter(items => items.some(item => String(item || "").trim()));
}

function parseBall(raw) {
  const v = String(raw || "").trim().toLowerCase();
  if (!v || v === "me" || v === "я" || v.includes("волод")) return { ball: "me" };
  if (v === "evgeny" || v.includes("евгени")) return { ball: "evgeny" };
  return { ball: "external", name: String(raw || "").trim() };
}

function parseStaticReport(report) {
  const TOPICS = (report.items || []).map(item => {
    const ball = parseBall(item.ball_side);
    return {
      id: item.topic_id,
      title: item.topic_title,
      facts: item.current_week_facts || item.result || "",
      result: item.result || "Без нового движения за неделю.",
      milestones: item.milestones
        ? [{ date: item.milestone_date || "", text: item.milestones }]
        : [],
      ball: ball.ball,
      ballName: ball.name,
      question: item.open_question || "",
      movement: item.movement_type || "unclear",
      sync: item.needs_sync === "yes" ? "yes" : "no",
      syncReason: item.sync_reason || "",
      link: item.source_links || "",
    };
  });
  return {
    TOPICS,
    WEEK: weekMetaFromSheetName(report.source_sheet || report.week_label || "Weekly"),
  };
}

function weekMetaFromSheetName(sheetName) {
  const m = sheetName.match(/(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return { label: sheetName, range: sheetName, rangeShort: "", to: "Евгению", from: "Володя", prevWeek: "", sheetName };
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
    sheetName,
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
window.fetchPublicCsvWeekData = fetchPublicCsvWeekData;
window.fetchStaticReport = fetchStaticReport;
window.FALLBACK_TOPICS = FALLBACK_TOPICS;
window.FALLBACK_WEEK = FALLBACK_WEEK;
