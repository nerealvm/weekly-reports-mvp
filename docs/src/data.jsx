// ─── Google Sheets configuration ────────────────────────────────────────────
// 1. Share the spreadsheet: "Доступ → Все, у кого есть ссылка → Читатель"
// 2. If the spreadsheet is public by link, GitHub Pages reads the current
//    `Активные` through the public gviz CSV endpoint, no API key required.
// 3. Optional: paste an API key to read the same sheet through Sheets API.
// 4. Optional: deploy Apps Script web app to receive comments → see apps_script.js
const SHEETS_CONFIG = {
  spreadsheetId: "14vjMSr2YaGRcD9Ud1zrDvULhEIE6o5kmZkKfdxarSFs",
  apiKey: "AIzaSyDIJh8TWdiGSR0R9RfLZk50nEsVXGw219o",
  publicCsvGid: "0",
  publicCsvSheetName: "Активные",
  appsScriptUrl: "https://script.google.com/macros/s/AKfycbxqO_xtAfwH3jmstuS-uEl8696HMEeEp_KmTMSegmy5sw4hUgoMo2ra3Yje0ZisuSS6/exec",   // ← URL задеплоенного Apps Script (для записи комментариев)
  activeSheetName: "Активные",
  sheetPattern: /^Активные$/,
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
      return { sheetName: t, date: new Date() };
    })
    .sort((a, b) => b.date - a.date);
  if (!sheets.length) throw new Error("Не найдена вкладка «Активные»");
  return sheets;
}

// Fetches and parses one sheet into { TOPICS, WEEK }
async function fetchWeekData(sheetName) {
  const range = encodeURIComponent(`'${sheetName}'!A:CR`);
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
  if (isActiveSheetValues(values)) return parseActiveSheetValues(values, sheetName);
  return parseNormalizedSheetValues(values, sheetName);
}

function parseNormalizedSheetValues(values, sheetName) {
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

function parseActiveSheetValues(values, sheetName) {
  const first = (values[0] || []).map(cleanCell);
  const second = (values[1] || []).map(cleanCell);
  const topicCol = findHeaderIndex(first, "тема");
  const dateCol = findHeaderIndex(first, "дата постановки");
  const currentColumns = weekColumns(first, second, "куда мы докатились");
  const previousColumns = weekColumns(first, second, "статус предыдущей недели");
  const milestoneColumns = weekColumns(first, second, "когда докатимся");
  const ballCol = findHeaderIndex(first, "на чьей стороне мяч");
  const questionCol = findHeaderIndex(first, "открытые вопросы");
  const currentWeek = currentColumns[currentColumns.length - 1];
  const weekLabel = currentWeek?.label || "";
  const previousCol = findWeekIndex(previousColumns, weekLabel) ?? previousCurrentIndex(currentColumns, weekLabel);
  const milestoneCol = findWeekIndex(milestoneColumns, weekLabel) ?? milestoneColumns[milestoneColumns.length - 1]?.index;

  let section = "";
  let count = 0;
  const TOPICS = values.slice(2).flatMap(row => {
    const sectionCell = cleanCell(row[0]);
    const title = cleanCell(row[topicCol]);
    if (!title) {
      if (sectionCell) section = sectionCell;
      return [];
    }
    count += 1;
    const result = cleanCell(row[currentWeek?.index]);
    const milestoneText = cleanCell(row[milestoneCol]);
    const question = cleanCell(row[questionCol]);
    const ball = parseBall(cleanCell(row[ballCol]));
    return [{
      id: `T-${String(count).padStart(3, "0")}`,
      title,
      section,
      dateCreated: cleanCell(row[dateCol]),
      facts: cleanCell(row[previousCol]),
      result: result || "Без нового движения за неделю.",
      milestones: parseMilestones(milestoneText),
      ball: ball.ball,
      ballName: ball.name,
      question,
      movement: movementFromResult(result),
      sync: question ? "yes" : "no",
      syncReason: question ? "Есть открытый вопрос." : "",
      link: "",
    }];
  });

  return { TOPICS, WEEK: weekMetaFromActiveLabel(weekLabel, sheetName) };
}

function isActiveSheetValues(values) {
  const first = (values[0] || []).map(cleanCell).join(" ").toLowerCase();
  return first.includes("статус предыдущей недели")
    && first.includes("куда мы докатились")
    && first.includes("когда докатимся");
}

function weekColumns(first, second, groupQuery) {
  const groupStart = findHeaderIndex(first, groupQuery);
  const nextGroupStart = nextHeaderIndex(first, groupStart + 1) ?? Math.max(first.length, second.length);
  const result = [];
  for (let index = groupStart + 1; index < nextGroupStart; index += 1) {
    const label = cleanCell(second[index]);
    if (label) result.push({ label, index });
  }
  return result;
}

function findWeekIndex(columns, weekLabel) {
  return columns.find(column => column.label === weekLabel)?.index;
}

function previousCurrentIndex(columns, weekLabel) {
  const position = columns.findIndex(column => column.label === weekLabel);
  if (position > 0) return columns[position - 1].index;
  if (position === 0) return undefined;
  return columns[columns.length - 1]?.index;
}

function findHeaderIndex(row, query) {
  const normalizedQuery = normalize(query);
  const index = row.findIndex(value => normalize(value).includes(normalizedQuery));
  if (index < 0) throw new Error(`Не найдена колонка: ${query}`);
  return index;
}

function nextHeaderIndex(row, startIndex) {
  for (let index = startIndex; index < row.length; index += 1) {
    if (cleanCell(row[index])) return index;
  }
  return undefined;
}

function parseMilestones(value) {
  return String(value || "")
    .replaceAll(";", "\n")
    .split(/\n+/)
    .map(item => item.trim())
    .filter(Boolean)
    .map(item => {
      const match = item.match(/^(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|TBD)\s+(.+)$/i);
      return match ? { date: match[1], text: match[2] } : { date: "", text: item };
    });
}

function movementFromResult(value) {
  const normalized = normalize(value);
  if (!normalized) return "unclear";
  if (normalized.includes("без нового движения") || normalized.includes("без движения")) return "no_movement";
  return "real_result";
}

function cleanCell(value) {
  return String(value ?? "").trim();
}

function normalize(value) {
  return cleanCell(value).toLowerCase().replace(/\s+/g, " ");
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

function weekMetaFromActiveLabel(weekLabel, sheetName) {
  const match = String(weekLabel || "").match(/^(\d{1,2})[.](\d{1,2})$/);
  if (!match) return { label: weekLabel || sheetName, range: weekLabel || sheetName, rangeShort: "", to: "Евгению", from: "Володя", prevWeek: "", sheetName };
  const year = new Date().getFullYear();
  const end = new Date(year, Number(match[2]) - 1, Number(match[1]));
  const start = new Date(end); start.setDate(start.getDate() - 6);
  const MONTHS_RU = ["янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"];
  const range = `${start.getDate()} — ${end.getDate()} ${MONTHS_RU[end.getMonth()]} ${end.getFullYear()}`;
  const rangeShort = `${String(start.getDate()).padStart(2,"0")}.${String(start.getMonth()+1).padStart(2,"0")} – ${String(end.getDate()).padStart(2,"0")}.${String(end.getMonth()+1).padStart(2,"0")}`;
  const weekNum = getISOWeek(end);
  return {
    label: `W${weekNum}`,
    range,
    rangeShort,
    to: "Евгению",
    from: "Володя",
    prevWeek: "",
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
    facts:"Без нового движения.", result:"Тема оставлена без изменений; отчётность и поиск причины расхождения остаются в работе по старому контуру.",
    milestones:[{date:"1.06",text:"01.06 Найти и зафиксировать причину расхождения / «дырку»"}],
    ball:"external", ballName:"Павел / бухгалтер", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-002", title:"Банковские займы для ОТЗ",
    facts:"От Альфы пришёл ответ: без залога при текущей кредитной нагрузке финансирование не дадут. Нужно ждать закрытия хотя бы одного кредита.", result:"Беззалоговый сценарий сейчас не проходит по банкам; основной путь — дождаться снижения кредитной нагрузки и/или обсуждать залоговый вариант.",
    milestones:[{date:"15.07",text:"15.07 Повторно вернуться к получению займа / собрать рабочий банковский сценарий"}],
    ball:"external", ballName:"бухгалтер", question:"Если всё-таки нужно вытащить деньги, стоит ли вернуться к рассмотрению варианта с залогом?", movement:"real_result", sync:"yes",
    syncReason:"Нужно решение по залоговому сценарию, если деньги нужны раньше или с большей вероятностью.", link:"" },
  { id:"T-003", title:"ТЭЦ",
    facts:"Беларусь разочаровала: технологов внутри нет. Владимир посчитал модель по предложению команды, которую нашёл Дмитрий; экономика не сходится. Расчёт передан Дмитрию.", result:"По ТЭЦ проверили белорусский и альтернативный контуры: у Беларуси нет нужных технологов, а экономика по найденной Дмитрием команде пока не сходится.",
    milestones:[{date:"30.05",text:"30.05 Найти и заразить идеей проектировщика; 30.10 Спроектировать «металл»"}],
    ball:"external", ballName:"Дмитрий", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-004", title:"Бизнес-план по коксованию",
    facts:"Есть вопрос от Ивана Ивановича, который он хочет обсудить с Евгением: русский клиент, которому очень нужны технологии.", result:"Тема возвращается к коммерциализации технологии через потенциального русского клиента; параллельно ожидается дорожная карта от Эльвиры.",
    milestones:[{date:"",text:"Иван Иванович обсуждает с Евгением русского клиента; Получить от Эльвиры дорожную карту"}],
    ball:"external", ballName:"Иван Иванович / Эльвира", question:"Обсудить с Иваном Ивановичем тему русского клиента, которому нужны технологии.", movement:"unclear", sync:"yes",
    syncReason:"Нужно обсудить потенциального клиента и направление продажи технологии.", link:"" },
  { id:"T-005", title:"Геш Групп",
    facts:"Посчитаны дополнительные вложения для попытки зайти в кредитование через Дом.РФ: ориентир 3–4 млн руб.", result:"По Геш Групп нужно управленческое решение: инвестировать 3–4 млн руб. в подготовку к Дом.РФ сейчас или идти спокойнее.",
    milestones:[{date:"30.05",text:"30.05 Обновленная концепция объекта для соответствия требованиям льготной программы; 01.09 Получены первые кредитные средства"}],
    ball:"evgeny", ballName:"", question:"Идти сейчас в Дом.РФ и тратить 3–4 млн или двигаться спокойнее и возвращаться к кредитованию в следующем году?", movement:"real_result", sync:"yes",
    syncReason:"Игорь не может принять решение сам, нужен выбор сценария от Евгения.", link:"" },
  { id:"T-006", title:"Рыбоводство (РФ)",
    facts:"Руслан совместно с Владимиром подготовил план на 3 года. Трек двигается.", result:"По рыбоводству РФ подготовлен трёхлетний план; следующий шаг — получить от Руслана обратную связь после доработки оценки рынка.",
    milestones:[{date:"",text:"Получить обратную связь от Руслана; 30.05 Войти с кем-нибудь в DueDil; 01.10 Выкуп доли"}],
    ball:"external", ballName:"Руслан", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-007", title:"Рыбоводство (ОАЭ)",
    facts:"Без нового движения.", result:"Тема оставлена без изменений.",
    milestones:[{date:"7.2026",text:"07.2026 Собрать реалистичную воронку проектов; 31.12 Выкуп доли при приемлемой цене"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-008", title:"Вайб финансы для smb",
    facts:"Без нового движения.", result:"Тема оставлена без изменений; мяч остаётся на Игоре.",
    milestones:[{date:"30.04",text:"30.04 Сбор требований и запуск пилота на одном из проектов"}],
    ball:"external", ballName:"Игорь", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-009", title:"Онлайн вет-карта для дом животных",
    facts:"Без нового движения.", result:"Тема оставлена без изменений.",
    milestones:[{date:"31.05",text:"31.05 Найден проект и/или лидер"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-010", title:"(Не)ватный блог про успехи России",
    facts:"Без нового движения.", result:"Тема оставлена без изменений.",
    milestones:[{date:"30.06",text:"30.06 Ударить по рукам с лидером проекта; 01.10 1000 аудитории с нуля или x2 к существующей"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-011", title:"Закрытие фонда Экспонента",
    facts:"Процесс идет. Ждём документы от Алексея и заказ оценки от Дмитрия.", result:"Трек по продаже дебиторки движется к запуску оценки: ждём документы от Алексея и заказ оценки от Дмитрия.",
    milestones:[{date:"31.05",text:"31.05 Заключение договора уступки права требования; 01.12 Окончание"}],
    ball:"external", ballName:"Алексей / Дмитрий", question:"", movement:"unclear", sync:"no", syncReason:"", link:"" },
  { id:"T-012", title:"Продажа Квадриги",
    facts:"Три трека. 1) КДП: Нехина обещала прислать версии. 2) Оценка: оценщик запущен. 3) Судебные дела: переданы Богдану.", result:"Продажа Квадриги продвигается по трём основным трекам: КДП, оценка автопарка и передача судебных дел юристам.",
    milestones:[{date:"29.05",text:"29.05 Подписать КДП; Получить от Нехиной версии по КДП; Добиться подготовки документов для оценщика"}],
    ball:"external", ballName:"Нехина / Андрей / Богдан", question:"Может понадобиться содействие, чтобы Андрей подготовил документы для оценщика.", movement:"real_result", sync:"yes",
    syncReason:"Может понадобиться точечное содействие Евгения по Андрею и документам для оценщика.", link:"" },
  { id:"T-013", title:"Биотех. Займы акционеров",
    facts:"Вопрос по Кузнецову решился. По займам в АА суммы совпали. Остался один финальный вопрос.", result:"Основные расхождения по реестру займов сняты; открыт один финальный вопрос.",
    milestones:[{date:"",text:"Получить обратную связь по последнему открытому вопросу"}],
    ball:"external", ballName:"Ирина", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-014", title:"Биотех. Аудит на статус завода",
    facts:"Без нового движения; ожидаются вводные от Евгения.", result:"Тема не продвинулась, мяч остаётся на Евгении по вводным.",
    milestones:[{date:"",text:"Получить вводные от Евгения"}],
    ball:"evgeny", ballName:"", question:"Дай вводные плз", movement:"no_movement", sync:"yes",
    syncReason:"Нужны вводные от Евгения, чтобы двигать задачу дальше.", link:"" },
  { id:"T-015", title:"Биотех. Аудит экономики и баланса исторический",
    facts:"Без нового движения; ожидаются вводные от Евгения.", result:"Тема не продвинулась, мяч остаётся на Евгении по вводным.",
    milestones:[{date:"",text:"Получить вводные от Евгения"}],
    ball:"evgeny", ballName:"", question:"Дай вводные плз", movement:"no_movement", sync:"yes",
    syncReason:"Нужны вводные от Евгения, чтобы двигать задачу дальше.", link:"" },
  { id:"T-016", title:"Биотех. Корп договор",
    facts:"По корпдоговору нужен Zoom. Владимир ждёт от Евгения время.", result:"Работа по корпдоговору упёрлась в необходимость короткого Zoom.",
    milestones:[{date:"",text:"Провести Zoom по правкам в корпдоговоре"}],
    ball:"evgeny", ballName:"", question:"Когда провести Zoom и что именно править / куда двигать документ?", movement:"unclear", sync:"yes",
    syncReason:"Нужна обратная связь Евгения по времени Zoom и направлению правок.", link:"" },
  { id:"T-035", title:"Биотех. Финансовый аудит",
    facts:"Создана группа с Игорем. Непонятно, что именно должен делать Никита.", result:"Контекст и состав работ для Никиты пока не очевидны: сначала нужно определить, есть ли объект аудита.",
    milestones:[{date:"",text:"Уточнить роль Никиты и состав работ по финансовому аудиту Биотеха"}],
    ball:"external", ballName:"Игорь", question:"Что именно нужно делать Никите с учетом того, что текущей версии отчетности, вероятно, нет?", movement:"unclear", sync:"yes",
    syncReason:"Нужно определить постановку задачи и роль Никиты.", link:"" },
  { id:"T-017", title:"Планирование поездки в Китай на выставки",
    facts:"Нужна обратная связь от Евгения по текущим вариантам.", result:"План поездки упёрся в обратную связь Евгения.",
    milestones:[{date:"22.05",text:"22.05 План мероприятий и логистики, утвержденный Евгением"}],
    ball:"evgeny", ballName:"", question:"Какие варианты поездки в Китай считать приоритетными и что прорабатывать глубже?", movement:"no_movement", sync:"yes",
    syncReason:"Нужна обратная связь по текущим вариантам поездки.", link:"" },
  { id:"T-018", title:"Личная подборка think tank и новостей",
    facts:"Движения нет. Нужен синк по ТЗ.", result:"Тема не двигается, потому что не уточнено ТЗ и формат постановки.",
    milestones:[{date:"",text:"Уточнить ТЗ и постановку задачи"}],
    ball:"evgeny", ballName:"", question:"Нужен синк по ТЗ и постановке задачи.", movement:"no_movement", sync:"yes",
    syncReason:"Надо уточнить ТЗ и формат постановки.", link:"" },
  { id:"T-019", title:"Новая зарубежная карта",
    facts:"Без нового движения.", result:"Тема оставлена без изменений, команда на инициацию не поступала.",
    milestones:[{date:"2.04",text:"02.04 Отдал топ-3 варианта; Жду команды на инициацию процесса получения"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-020", title:"Биотех. Мониторинг конкурентов",
    facts:"Нового движения нет. Открыт вопрос: можно ли убирать тему в архив.", result:"Тема без движения; требуется решение Евгения — закрывать/архивировать или продолжать.",
    milestones:[],
    ball:"evgeny", ballName:"", question:"Можно ли убирать тему в архив или есть ещё что обсудить?", movement:"no_movement", sync:"yes",
    syncReason:"Нужно решение Евгения по архивированию темы.", link:"" },
  { id:"T-021", title:"Собрать историю налогов в РФ",
    facts:"Без нового движения.", result:"Тема оставлена без изменений.",
    milestones:[{date:"",text:"Жду обратной связи"}],
    ball:"evgeny", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-022", title:"Корп. законодательства африканских стран",
    facts:"Нового движения нет. Открыт вопрос: архивируем тему или оставляем в работе.", result:"Тема без движения; требуется решение Евгения, архивировать ли её.",
    milestones:[{date:"20.03",text:"20.03 Отдал апдейт"}],
    ball:"evgeny", ballName:"", question:"Архивируем тему или ещё нужна в работе?", movement:"no_movement", sync:"yes",
    syncReason:"Нужно решение Евгения по архивированию темы.", link:"" },
  { id:"T-023", title:"Историк для удаленных занятий",
    facts:"Без нового движения.", result:"Тема оставлена без изменений.",
    milestones:[{date:"",text:"Обсудить тему на синке"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-024", title:"Белорусские номера и симки",
    facts:"Белорусские номера/симки больше не нужны: сделали французскую симку.", result:"Тему можно архивировать: задача закрыта через французскую симку.",
    milestones:[],
    ball:"me", ballName:"", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
  { id:"T-034", title:"Карта отрасли (graphCRM)",
    facts:"Без нового движения.", result:"Тема оставлена без изменений.",
    milestones:[{date:"20.06",text:"20.06 Предварительная концепция технической реализации сформирована"}],
    ball:"me", ballName:"", question:"", movement:"no_movement", sync:"no", syncReason:"", link:"" },
  { id:"T-036", title:"Флиппинг. Лендинг и презентация",
    facts:"", result:"Нашли маркетолога, в понедельник обещала прислать первую версию лендинга/презентации.",
    milestones:[{date:"",text:"Получить первую версию от маркетолога и протестировать на компьютерном трафике."}],
    ball:"external", ballName:"маркетолог", question:"", movement:"real_result", sync:"no", syncReason:"", link:"" },
];

const FALLBACK_WEEK = {
  label: "W22",
  range: "23 — 29 мая 2026",
  rangeShort: "23.05 – 29.05",
  to: "Евгению",
  from: "Володя",
  prevWeek: "W21 · 16 — 22 мая",
};


window.SHEETS_CONFIG = SHEETS_CONFIG;
window.fetchSheetList = fetchSheetList;
window.fetchWeekData = fetchWeekData;
window.fetchPublicCsvWeekData = fetchPublicCsvWeekData;
window.fetchStaticReport = fetchStaticReport;
window.FALLBACK_TOPICS = FALLBACK_TOPICS;
window.FALLBACK_WEEK = FALLBACK_WEEK;
