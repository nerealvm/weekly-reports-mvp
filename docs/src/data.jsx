// ─── Google Sheets configuration ────────────────────────────────────────────
// 1. Share the spreadsheet: "Доступ → Все, у кого есть ссылка → Читатель"
// 2. If the spreadsheet is public by link, GitHub Pages reads the current
//    weekly tab through the public gviz CSV endpoint, no API key required.
// 3. Optional: paste an API key to enable automatic week-tab discovery.
// 4. Optional: deploy Apps Script web app to receive comments → see apps_script.js
const SHEETS_CONFIG = {
  spreadsheetId: "14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs",
  apiKey: "",          // ← optional: API-ключ для discovery недельных вкладок
  publicCsvGid: "",
  publicCsvSheetName: "Weekly MVP 2026-05-15",
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
  { id:"T-001", title:"Отчетность ОТЗ и АА",
    facts:"", result:"По АА сдвинул срок подготовки отчетности на 22.05: бухгалтерия закрыта, Ирина в отпуске, ожидаю сбор пакета к новой дате. По ОТЗ жду от Павла материалы 15.05; есть риск задержки, так как 14.05 вечером ответа не было.",
    milestones:[{date:"15.05",text:"15.05 Получить материалы по ОТЗ от Павла; 22.05 Подготовить отчетность по АА; 01.06 Найти и зафиксировать причину расхождения / «дырку»"}],
    ball:"external", ballName:"Павел / бухгалтер", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-002", title:"Банковские займы для ОТЗ",
    facts:"", result:"Подал финальную заявку в Альфу по займам для ОТЗ; подача в МСП Банк пока заблокирована бухгалтерским документом, вопрос требует разбора.",
    milestones:[{date:"15.05",text:"15.05 Подать заявку в МСП Банк; 30.05 Получены займы"}],
    ball:"me", ballName:"", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-003", title:"ТЭЦ",
    facts:"", result:"Возобновили трек по ТЭЦ: команда из Беларуси вернулась, на 19.05 запланирована встреча по обсуждению страны «желтые штаны» и проектированию электростанции под нее.",
    milestones:[{date:"19.05",text:"19.05 Встреча по стране «желтые штаны» и проектированию электростанции; 30.05 Найти и заразить идеей проектировщика; 30.10 Спроектировать «металл»"}],
    ball:"me", ballName:"", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-004", title:"Бизнес-план по коксованию",
    facts:"", result:"Без нового движения за неделю; мяч на Эльвире.",
    milestones:[{date:"30.06",text:"30.06 Получить принципиальный ок на еще один проект"}],
    ball:"external", ballName:"Эльвира", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-005", title:"Геш Групп",
    facts:"", result:"Получили вводные от Дом.РФ по требованиям к проекту; Игорь считает бюджет на переработку концепции под капитальное строительство и дополнительные маркетинговые исследования, после чего нужно принять решение по темпу движения — целиться в июль или идти более размеренно.",
    milestones:[{date:"30.05",text:"30.05 Обновленная концепция объекта для соответствия требованиям льготной программы + необходимый пакет документов; 01.09 Получены первые кредитные средства"}],
    ball:"external", ballName:"Игорь", question:"", movement:"real_result", sync:"yes",
    syncReason:"После бюджетирования нужно определить с Евгением целевой темп проекта: июль этого года или более размеренное движение.", link:"" },
  { id:"T-006", title:"Рыбоводство (РФ)",
    facts:"", result:"Получил от Руслана новую финмодель и план выхода на 5 млрд; после первых правок мяч перешел ко мне — на следующей неделе нужно вернуться с финальной обратной связью и помочь доработать материалы.",
    milestones:[{date:"22.05",text:"22.05 Вернуться к Руслану с финальной обратной связью и помочь доработать модель/план; 30.05 Войти с кем-нибудь в DueDil; 01.10 Выкуп доли"}],
    ball:"me", ballName:"", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-007", title:"Рыбоводство (ОАЭ)",
    facts:"", result:"Без нового движения за неделю.",
    milestones:[{date:"",text:"07.2026 Собрать реалистичную воронку проектов; 31.12 Выкуп доли при приемлемой цене"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-008", title:"Вайб финансы для smb",
    facts:"", result:"Без нового движения за неделю; мяч на Игоре.",
    milestones:[{date:"30.04",text:"30.04 Сбор требований и запуск пилота на одном из проектов"}],
    ball:"external", ballName:"Игорь", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-009", title:"Онлайн вет-карта для дом животных",
    facts:"", result:"Без нового движения за неделю.",
    milestones:[{date:"31.05",text:"31.05 Найден проект и/или лидер"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-010", title:"(Не)ватный блог про успехи России",
    facts:"", result:"Без нового движения за неделю.",
    milestones:[{date:"30.06",text:"30.06 Ударили по рукам с лидером проекта; 01.10 1000 аудитории с нуля или x2 к существующей"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-011", title:"Закрытие фонда Экспонента",
    facts:"", result:"Определили покупателя дебиторки — Дмитрий; процесс перешел к подготовке писем Анной, оценке и запуску дальнейших шагов. Дополнительно получен апдейт от Вики: Бакс работает над предотвращением банкротства, параллельно идут M&A-процессы.",
    milestones:[{date:"15.05",text:"15.05 Собрать предложения по продаже дебиторки; 31.05 Заключение договора уступки права требования между ООО «Бакс Технология» и физическим лицом; 01.12 Окончание"}],
    ball:"external", ballName:"Анна", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-012", title:"Продажа Квадриги",
    facts:"", result:"Продвинул продажу Квадриги по трем направлениям: ожидаю от Анны конкретные шаги по продаже и налогам, от оценщика — финализацию оценки автопарка, по отчетности — организую встречу для голосового разбора 6 концептуальных вопросов по старым и новым цифрам.",
    milestones:[{date:"15.05",text:"15.05 Получить от Анны конкретные шаги по продаже и налогам; 15.05 Получить дособранную оценку автопарка от оценщика; 22.05 Финализация решения по продаже"}],
    ball:"me", ballName:"", question:"", movement:"real_result", sync:"yes",
    syncReason:"Нужна встреча для голосового разбора 6 концептуальных вопросов по отчетности и выхода к финализации решения по продаже.", link:"" },
  { id:"T-013", title:"Биотех. Займы акционеров",
    facts:"", result:"Отработал полученные комментарии по реестру займов акционеров и сверил данные в текущей версии; открыт один точечный вопрос по Кузнецову, дополнительно жду возможную обратную связь.",
    milestones:[{date:"8.05",text:"08.05 Финализированный документ"}],
    ball:"evgeny", ballName:"", question:"", movement:"real_result", sync:"yes",
    syncReason:"Остался точечный вопрос по Кузнецову и ожидается возможная дополнительная обратная связь.", link:"https://t.me/c/3666755521/26/363" },
  { id:"T-014", title:"Биотех. Аудит на статус завода",
    facts:"", result:"Без нового движения за неделю; жду вводные от Евгения.",
    milestones:[{date:"",text:"Получить вводные от Евгения"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-015", title:"Биотех. Аудит экономики и баланса исторический",
    facts:"", result:"Без нового движения за неделю; жду вводные от Евгения.",
    milestones:[{date:"",text:"Получить вводные от Евгения"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-016", title:"Биотех. Корп договор",
    facts:"", result:"Без нового движения за неделю; жду вводные от Евгения.",
    milestones:[{date:"",text:"Получить вводные от Евгения"}],
    ball:"evgeny", ballName:"", question:"Никита попросил конкретизировать контекст и состав работ, в чем будет отличие от Квадриги",
    movement:"no_movement", sync:"yes", syncReason:"", link:"" },
  { id:"T-035", title:"Биотех. Финансовый аудит",
    facts:"", result:"Никита попросил конкретизировать контекст и состав работ, в чем будет отличие от Квадриги",
    milestones:[],
    ball:"evgeny", ballName:"", question:"", movement:"unclear", sync:"no", syncReason:"", link:"" },
  { id:"T-017", title:"Планирование поездки в Китай на выставки",
    facts:"", result:"Тема без нового движения за неделю; жду обратную связь по текущему документу с планом мероприятий и логистики.",
    milestones:[{date:"22.05",text:"22.05 План мероприятий и логистики, утвержденный Евгением"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"https://t.me/c/3666755521/314/315" },
  { id:"T-018", title:"Личная подборка think tank и новостей по интересующей теме",
    facts:"", result:"Без нового движения за неделю.",
    milestones:[{date:"17.04",text:"Подбор источников информации по интересующим темам - 17.04; Сбор и отладка общей системы - 30.04"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-019", title:"Новая зарубежная карта",
    facts:"", result:"Без нового движения за неделю; жду команды на инициацию процесса получения.",
    milestones:[{date:"2.04",text:"02.04 Отдал топ-3 варианта; жду команды на инициацию процесса получения"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-020", title:"Биотех. Мониторинг конкурентов",
    facts:"", result:"Без нового движения за неделю; жду обратную связь.",
    milestones:[{date:"",text:"Жду обратной связи"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-021", title:"Собрать историю налогов в РФ",
    facts:"", result:"Без нового движения за неделю; жду обратную связь.",
    milestones:[{date:"",text:"Жду обратной связи"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-022", title:"Корп. законодательства африканских стран",
    facts:"", result:"Без нового движения за неделю; жду обратную связь по ранее переданному апдейту.",
    milestones:[{date:"20.03",text:"20.03 Отдал апдейт"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-023", title:"Историк для удаленных занятий",
    facts:"", result:"Без нового движения за неделю.",
    milestones:[{date:"",text:"Обсудить тему на синке"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-024", title:"Белорусские номера и симки",
    facts:"", result:"Без нового движения за неделю; общий трек остается закрытым, возвращаться к теме только под конкретный запрос на машину.",
    milestones:[{date:"8.04",text:"08.04 Проверка реализуемости и целесообразности"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-034", title:"Карта отрасли (graphCRM)",
    facts:"", result:"Провел первичный разбор me.sh (личная CRM) и пришел к выводу, что текущий вариант не закрывает существенную часть требований; собрал предварительный список open source CRM для доработки и вынес следующий шаг в отдельную веху — сформировать предварительную концепцию технической реализации.",
    milestones:[{date:"20.06",text:"20.06 Предварительная концепция технической реализации сформирована"}],
    ball:"me", ballName:"", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
];

const FALLBACK_WEEK = {
  label: "W20",
  range: "9 — 15 мая 2026",
  rangeShort: "09.05 – 15.05",
  to: "Евгению",
  from: "Володя",
  prevWeek: "W19 · 2 — 8 мая",
};

window.SHEETS_CONFIG = SHEETS_CONFIG;
window.fetchSheetList = fetchSheetList;
window.fetchWeekData = fetchWeekData;
window.fetchPublicCsvWeekData = fetchPublicCsvWeekData;
window.fetchStaticReport = fetchStaticReport;
window.FALLBACK_TOPICS = FALLBACK_TOPICS;
window.FALLBACK_WEEK = FALLBACK_WEEK;
