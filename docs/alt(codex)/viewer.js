
let report = null;
let filter = "all";
let query = "";
let pendingComment = null;
let commentSaveInProgress = false;
let voiceRecognition = null;
let voiceListening = false;
let voiceBaseText = "";
const sheetSyncWatchers = new Set();

const fieldLabels = {
  topic: "Тема",
  result: "Результат",
  milestones: "Вехи",
  ball_side: "Мяч",
  open_question: "Вопрос к Евгению",
};

const filterButtons = () => Array.from(document.querySelectorAll("[data-filter]"));

async function loadReport() {
  setToast("Обновляю отчет...");
  const response = await fetch(isStaticMode() ? "../report.json" : "/api/report", { cache: "no-store" });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  report = data;
  if (isStaticMode()) {
    report.feedback = mergeFeedback(report.feedback, loadStaticFeedback());
  }
  render();
  setToast("Отчет обновлен");
}

function render() {
  document.getElementById("subtitle").textContent = `${report.week_label} · ${report.source_sheet} · ${report.metrics.total_active} active-тем`;
  const feedbackCount = report.feedback?.count || 0;
  document.getElementById("copyFeedbackBtn").textContent = feedbackCount ? `Комментарии (${feedbackCount})` : "Комментарии";
  renderMetrics();
  renderFilterState();
  renderAttention();
  renderItems();
}

function renderMetrics() {
  const metrics = report.metrics;
  const items = [
    ["all", "Active", metrics.total_active],
    ["movement", "С движением", metrics.real_result],
    ["no_movement", "Без движения", metrics.no_movement],
    ["unclear", "Unclear", metrics.unclear],
    ["sync", "Sync", metrics.needs_sync],
    ["questions", "Вопросы", metrics.open_questions],
  ];
  document.getElementById("metrics").innerHTML = items.map(([metricFilter, label, value]) => (
    `<button class="metric${filter === metricFilter ? " active" : ""}" data-metric-filter="${metricFilter}" type="button"><strong>${value}</strong><span>${label}</span></button>`
  )).join("");
  for (const metric of document.querySelectorAll("[data-metric-filter]")) {
    metric.onclick = () => setFilter(metric.dataset.metricFilter);
  }
}

function renderAttention() {
  const actionable = report.items.filter(item => item.needs_sync === "yes" || item.open_question);
  const attention = actionable.filter(item => !hasTopicComment(item.topic_id));
  const container = document.getElementById("attentionList");
  if (!attention.length) {
    const message = actionable.length ? "По всем attention-темам уже есть реакция." : "По текущим правилам sync не требуется.";
    container.innerHTML = `<div class="attention-item"><strong>Нет явных блокеров</strong><span>${message}</span></div>`;
    return;
  }
  container.innerHTML = attention.map(item => `
    <button class="attention-item" data-jump-topic="${escapeHtml(item.topic_id)}" type="button">
      <strong>${escapeHtml(item.topic_title)}</strong>
      <span>${escapeHtml(item.open_question || item.sync_reason || "Нужен sync")}</span>
    </button>
  `).join("");
}

function renderItems() {
  const items = visibleItems();
  const container = document.getElementById("reportList");
  if (!items.length) {
    container.innerHTML = `<div class="empty-state">Нет строк под выбранный фильтр.</div>`;
    return;
  }
  container.innerHTML = items.map(renderItem).join("");
}

function renderItem(item) {
  const className = "topic-card" + (item.needs_sync === "yes" ? " sync" : item.open_question ? " question" : "");
  return `
    <article class="${className}" id="${escapeHtml(topicCardId(item.topic_id))}" data-topic-card="${escapeHtml(item.topic_id)}">
      <header class="topic-head">
        <div class="topic-main comment-target" ${commentAttrs(item, "topic")}>
          <h3 class="topic-title">${escapeHtml(item.topic_title)}</h3>
          <div class="topic-meta">${escapeHtml(item.section)} · ${escapeHtml(item.topic_id)}</div>
        </div>
        <div class="badges">
          ${item.needs_sync === "yes" ? `<span class="badge sync">sync</span>` : ""}
          <span class="badge ${escapeHtml(item.movement_type)}">${movementLabel(item.movement_type)}</span>
          ${item.focus === "yes" ? `<span class="badge">focus</span>` : ""}
          ${commentButton(item, "topic")}
        </div>
      </header>
      ${commentsMarkup(item, "topic")}
      <section class="field-block comment-target" ${commentAttrs(item, "result")}>
        <div class="field-head">
          <label>Результат</label>
          ${commentButton(item, "result")}
        </div>
        <p class="result">${escapeHtml(item.result)}</p>
        ${commentsMarkup(item, "result")}
      </section>
      <div class="details">
        <div class="detail comment-target" ${commentAttrs(item, "milestones")}>
          <div class="field-head">
            <label>Вехи</label>
            ${commentButton(item, "milestones")}
          </div>
          <div>${escapeHtml(item.milestones || "Веха не указана")}</div>
          ${commentsMarkup(item, "milestones")}
        </div>
        <div class="detail comment-target" ${commentAttrs(item, "ball_side")}>
          <div class="field-head">
            <label>Мяч</label>
            ${commentButton(item, "ball_side")}
          </div>
          <div>${escapeHtml(item.ball_side || "Не указан")}</div>
          ${commentsMarkup(item, "ball_side")}
        </div>
      </div>
      ${item.open_question ? `
        <div class="question-block comment-target" ${commentAttrs(item, "open_question")}>
          <div class="field-head">
            <label>Вопрос к Евгению</label>
            ${commentButton(item, "open_question")}
          </div>
          <div>${escapeHtml(item.open_question)}</div>
          ${commentsMarkup(item, "open_question")}
        </div>
      ` : ""}
    </article>
  `;
}

function visibleItems() {
  return report.items.filter(item => {
    if (filter === "sync" && item.needs_sync !== "yes") return false;
    if (filter === "questions" && !item.open_question) return false;
    if (filter === "movement" && item.movement_type !== "real_result") return false;
    if (filter === "no_movement" && item.movement_type !== "no_movement") return false;
    if (filter === "unclear" && item.movement_type !== "unclear") return false;
    if (!query) return true;
    const haystack = [item.topic_title, item.result, item.milestones, item.ball_side, item.open_question, item.sync_reason].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function movementLabel(value) {
  return { real_result: "движение", no_movement: "без движения", unclear: "неясно" }[value] || value;
}

function hasTopicComment(topicId) {
  return (report?.feedback?.by_topic?.[topicId] || 0) > 0;
}

function topicCardId(topicId) {
  return `topic-${String(topicId || "").replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function jumpToTopic(topicId) {
  const searchInput = document.getElementById("search");
  if (filter !== "all" || query) {
    filter = "all";
    query = "";
    searchInput.value = "";
    renderMetrics();
    renderFilterState();
    renderItems();
  }
  window.requestAnimationFrame(() => {
    const card = document.getElementById(topicCardId(topicId));
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.add("focused");
    window.clearTimeout(jumpToTopic.timer);
    jumpToTopic.timer = window.setTimeout(() => card.classList.remove("focused"), 1800);
  });
}

function commentAttrs(item, field) {
  return `data-topic-id="${escapeHtml(item.topic_id)}" data-comment-field="${escapeHtml(field)}"`;
}

function commentButton(item, field) {
  const count = commentCount(item.topic_id, field);
  const label = `Комментарий: ${fieldLabels[field] || field}`;
  return `<button class="comment-btn" ${commentAttrs(item, field)} type="button" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"><span class="comment-icon" aria-hidden="true"></span>${count ? `<span class="comment-count">${count}</span>` : ""}</button>`;
}

function commentCount(topicId, field) {
  return report?.feedback?.by_key?.[`${topicId}:${field}`] || 0;
}

function commentsFor(topicId, field) {
  return (report?.feedback?.comments || []).filter(comment => comment.topic_id === topicId && comment.field === field);
}

function commentsMarkup(item, field) {
  const comments = commentsFor(item.topic_id, field);
  if (!comments.length) return "";
  return `<div class="comment-list">${comments.map(commentMarkup).join("")}</div>`;
}

function commentMarkup(comment) {
  const author = comment.author || "Евгений";
  const createdAt = shortDateTime(comment.created_at);
  const status = comment.sheet_sync === "saved" ? " · записано в таблицу" : comment.sheet_sync === "failed" ? " · не записано в таблицу" : comment.sheet_sync === "local" ? " · сохранено в браузере" : "";
  return `<div class="comment-item"><strong>${escapeHtml(author)}${createdAt ? ` · ${escapeHtml(createdAt)}` : ""}${status}</strong><span>${escapeHtml(comment.text)}</span></div>`;
}

function shortDateTime(value) {
  if (!value) return "";
  const match = String(value).match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/);
  if (!match) return String(value);
  return `${match[3]}.${match[2]} ${match[4]}`;
}

function openComment(topicId, field) {
  if (commentSaveInProgress) return;
  const item = report.items.find(row => row.topic_id === topicId);
  if (!item) return;
  pendingComment = { item, field };
  setCommentSaving(false);
  stopVoiceComment(true);
  document.getElementById("commentTitle").textContent = `Комментарий: ${fieldLabels[field] || field}`;
  document.getElementById("commentContext").textContent = `${item.topic_title} · ${item.topic_id}`;
  document.getElementById("existingComments").innerHTML = commentsMarkup(item, field);
  document.getElementById("commentText").value = "";
  document.getElementById("commentModal").classList.remove("hidden");
  document.getElementById("commentText").focus();
}

function closeComment() {
  stopVoiceComment(true);
  pendingComment = null;
  setCommentSaving(false);
  document.getElementById("commentModal").classList.add("hidden");
}

async function saveComment() {
  if (!pendingComment || commentSaveInProgress) return;
  const text = document.getElementById("commentText").value.trim();
  if (!text) {
    setToast("Напиши комментарий");
    return;
  }
  const target = pendingComment;
  commentSaveInProgress = true;
  setCommentSaving(true);
  try {
    if (isStaticMode()) {
      const comment = createLocalComment(target, text);
      report.feedback = mergeFeedback(report.feedback, { comments: [comment] });
      saveStaticFeedback(report.feedback);
      closeComment();
      render();
      setToast("Комментарий сохранен в этом браузере");
      return;
    }
    const response = await fetch("/api/comment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic_id: target.item.topic_id,
        topic_title: target.item.topic_title,
        field: target.field,
        text,
        author: "Евгений",
      }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || response.statusText);
    report.feedback = mergeFeedback(report.feedback, data.feedback);
    closeComment();
    render();
    if (data.sheet?.status === "queued") {
      setToast("Комментарий сохранен. Записываю в таблицу...");
      watchSheetSync(data.comment?.id);
    } else if (data.sheet?.status === "saved") {
      setToast(`Комментарий записан в ${data.sheet.sheet_name}`);
    } else if (data.sheet?.status === "failed") {
      setToast("Комментарий сохранен локально, но не записан в таблицу");
    } else {
      setToast("Комментарий сохранен");
    }
  } finally {
    commentSaveInProgress = false;
    setCommentSaving(false);
  }
}

function setCommentSaving(isSaving) {
  const saveBtn = document.getElementById("commentSaveBtn");
  const cancelBtn = document.getElementById("commentCancelBtn");
  const closeBtn = document.getElementById("commentCloseBtn");
  const voiceBtn = document.getElementById("voiceCommentBtn");
  const textarea = document.getElementById("commentText");
  saveBtn.disabled = isSaving;
  cancelBtn.disabled = isSaving;
  closeBtn.disabled = isSaving;
  textarea.disabled = isSaving;
  voiceBtn.disabled = isSaving || !speechRecognitionCtor();
  saveBtn.classList.toggle("saving", isSaving);
  saveBtn.innerHTML = isSaving ? `<span class="spinner" aria-hidden="true"></span>Сохраняю` : "Сохранить";
}

async function watchSheetSync(commentId) {
  if (!commentId || sheetSyncWatchers.has(commentId)) return;
  sheetSyncWatchers.add(commentId);
  try {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await delay(1200);
      const response = await fetch("/api/feedback");
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      report.feedback = data;
      renderAttention();
      renderItems();
      const comment = (data.comments || []).find(item => item.id === commentId);
      if (!comment || comment.sheet_sync === "pending") continue;
      if (comment.sheet_sync === "saved") {
        setToast("Комментарий записан в Weekly Feedback");
      } else if (comment.sheet_sync === "failed") {
        setToast("Комментарий сохранен локально, но не записан в таблицу");
      }
      return;
    }
  } catch (error) {
    setToast(error.message);
  } finally {
    sheetSyncWatchers.delete(commentId);
  }
}

function delay(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function isStaticMode() {
  return Boolean(window.WEEKLY_VIEWER_STATIC);
}

function staticFeedbackKey() {
  return `weekly-viewer-feedback:${report?.source_sheet || "report"}`;
}

function loadStaticFeedback() {
  try {
    const payload = JSON.parse(window.localStorage.getItem(staticFeedbackKey()) || "{}");
    return buildFeedbackSummary({ comments: Array.isArray(payload.comments) ? payload.comments : [] });
  } catch {
    return buildFeedbackSummary({ comments: [] });
  }
}

function saveStaticFeedback(feedback) {
  const payload = { comments: feedback.comments || [] };
  window.localStorage.setItem(staticFeedbackKey(), JSON.stringify(payload));
}

function createLocalComment(target, text) {
  return {
    id: `L-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    created_at: new Date().toISOString(),
    topic_id: target.item.topic_id,
    topic_title: target.item.topic_title,
    field: target.field,
    field_label: fieldLabels[target.field] || target.field,
    text,
    author: "Евгений",
    status: "open",
    sheet_sync: "local",
  };
}

function mergeFeedback(currentFeedback, incomingFeedback) {
  const byId = new Map();
  for (const comment of currentFeedback?.comments || []) {
    if (comment.id) byId.set(comment.id, comment);
  }
  for (const comment of incomingFeedback?.comments || []) {
    if (comment.id) byId.set(comment.id, { ...(byId.get(comment.id) || {}), ...comment });
  }
  return buildFeedbackSummary({
    ...(currentFeedback || {}),
    ...(incomingFeedback || {}),
    comments: Array.from(byId.values()),
  });
}

function buildFeedbackSummary(feedback) {
  const openComments = (feedback.comments || []).filter(comment => (comment.status || "open") === "open");
  const byKey = {};
  const byTopic = {};
  for (const comment of openComments) {
    if (!comment.topic_id || !comment.field) continue;
    const key = `${comment.topic_id}:${comment.field}`;
    byKey[key] = (byKey[key] || 0) + 1;
    byTopic[comment.topic_id] = (byTopic[comment.topic_id] || 0) + 1;
  }
  return {
    ...feedback,
    count: openComments.length,
    comments: openComments,
    by_key: byKey,
    by_topic: byTopic,
  };
}

function speechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition;
}

function updateVoiceAvailability() {
  const voiceBtn = document.getElementById("voiceCommentBtn");
  if (speechRecognitionCtor()) {
    voiceBtn.disabled = false;
    voiceBtn.title = "Надиктовать комментарий";
    return;
  }
  voiceBtn.disabled = true;
  voiceBtn.title = "Голосовой ввод недоступен в этом браузере";
  document.getElementById("voiceStatus").textContent = "Голосовой ввод недоступен";
}

function toggleVoiceComment() {
  if (voiceListening) {
    stopVoiceComment();
    return;
  }
  startVoiceComment();
}

function startVoiceComment() {
  const Recognition = speechRecognitionCtor();
  if (!Recognition) {
    setToast("Голосовой ввод недоступен в этом браузере");
    return;
  }
  const textarea = document.getElementById("commentText");
  voiceBaseText = textarea.value.trim();
  voiceRecognition = new Recognition();
  voiceRecognition.lang = "ru-RU";
  voiceRecognition.interimResults = true;
  voiceRecognition.continuous = true;
  voiceRecognition.onresult = event => {
    let finalText = "";
    let interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0].transcript.trim();
      if (event.results[i].isFinal) {
        finalText += `${transcript} `;
      } else {
        interimText += `${transcript} `;
      }
    }
    if (finalText.trim()) {
      voiceBaseText = [voiceBaseText, finalText.trim()].filter(Boolean).join(" ");
    }
    textarea.value = [voiceBaseText, interimText.trim()].filter(Boolean).join(" ");
  };
  voiceRecognition.onerror = event => {
    setToast(`Голосовой ввод: ${event.error || "ошибка"}`);
    setVoiceListening(false);
  };
  voiceRecognition.onend = () => setVoiceListening(false);
  setVoiceListening(true);
  voiceRecognition.start();
}

function stopVoiceComment(silent = false) {
  if (voiceRecognition && voiceListening) {
    try {
      voiceRecognition.stop();
    } catch (error) {
      if (!silent) setToast(error.message);
    }
  }
  setVoiceListening(false);
}

function setVoiceListening(isListening) {
  voiceListening = isListening;
  const voiceBtn = document.getElementById("voiceCommentBtn");
  voiceBtn.classList.toggle("listening", isListening);
  document.getElementById("voiceCommentLabel").textContent = isListening ? "Стоп" : "Голосом";
  document.getElementById("voiceStatus").textContent = isListening ? "Слушаю..." : "";
}

async function copyText(text, label) {
  await navigator.clipboard.writeText(text);
  setToast(`${label} скопированы`);
}

function questionsText() {
  const rows = report.items.filter(item => item.open_question);
  if (!rows.length) return "Открытых вопросов к Евгению нет.";
  return rows.map(item => `Тема: ${item.topic_title}\nВопрос: ${item.open_question}`).join("\n\n");
}

function syncText() {
  const rows = report.items.filter(item => item.needs_sync === "yes");
  if (!rows.length) return "Sync по текущим правилам не требуется.";
  return rows.map(item => `- ${item.topic_title}: ${item.sync_reason || item.open_question || "Нужен sync"}`).join("\n");
}

function feedbackText() {
  const comments = report.feedback?.comments || [];
  if (!comments.length) return "Комментариев пока нет.";
  return comments.map(comment => {
    const title = comment.topic_title || comment.topic_id;
    const label = comment.field_label || fieldLabels[comment.field] || comment.field;
    return `Тема: ${title}\nПоле: ${label}\nКомментарий: ${comment.text}`;
  }).join("\n\n");
}

function setToast(text) {
  const toast = document.getElementById("toast");
  toast.textContent = text;
  toast.classList.add("visible");
  window.clearTimeout(setToast.timer);
  setToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 1700);
}

function setFilter(nextFilter) {
  filter = nextFilter;
  renderMetrics();
  renderFilterState();
  renderItems();
}

function renderFilterState() {
  filterButtons().forEach(item => item.classList.toggle("active", item.dataset.filter === filter));
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function updateBackTopButton() {
  const button = document.getElementById("backTopBtn");
  if (!button) return;
  button.classList.toggle("visible", window.scrollY > 360);
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch]));
}

document.getElementById("refreshBtn").onclick = () => loadReport().catch(error => setToast(error.message));
document.getElementById("copyQuestionsBtn").onclick = () => copyText(questionsText(), "Вопросы").catch(error => setToast(error.message));
document.getElementById("copySyncBtn").onclick = () => copyText(syncText(), "Sync").catch(error => setToast(error.message));
document.getElementById("copyFeedbackBtn").onclick = () => copyText(feedbackText(), "Комментарии").catch(error => setToast(error.message));
document.getElementById("commentSaveBtn").onclick = () => saveComment().catch(error => setToast(error.message));
document.getElementById("commentCancelBtn").onclick = closeComment;
document.getElementById("commentCloseBtn").onclick = closeComment;
document.getElementById("voiceCommentBtn").onclick = toggleVoiceComment;
document.getElementById("backTopBtn").onclick = scrollToTop;
document.querySelector("[data-close-comment]").onclick = closeComment;
document.getElementById("search").addEventListener("input", event => {
  query = event.target.value.trim().toLowerCase();
  renderItems();
});
window.addEventListener("scroll", updateBackTopButton, { passive: true });
window.addEventListener("resize", updateBackTopButton);
document.addEventListener("click", event => {
  const jumpTarget = event.target.closest("[data-jump-topic]");
  if (jumpTarget) {
    jumpToTopic(jumpTarget.dataset.jumpTopic);
    return;
  }
  const target = event.target.closest("[data-comment-field][data-topic-id]");
  if (!target) return;
  openComment(target.dataset.topicId, target.dataset.commentField);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeComment();
});
for (const button of filterButtons()) {
  button.onclick = () => setFilter(button.dataset.filter);
}
updateVoiceAvailability();
updateBackTopButton();
loadReport().catch(error => setToast(error.message));
