const state = {
  view: "chats",
  conversations: [],
  catalog: [],
  selectedId: null,
  selectedSlug: null,
  messages: [],
  metrics: {},
  health: {},
};

const $ = (id) => document.getElementById(id);
const simId = Number(localStorage.simId || (1_000_000_000_000 + Math.floor(Math.random() * 100000)));
localStorage.simId = String(simId);

function moodLabel(mood) {
  return { interesado: "Interesado", dudoso: "Dudoso", frustrado: "Frustrado", caliente: "Lead caliente" }[mood] || "Interesado";
}

function when(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.replace("T", " ").slice(0, 16);
  return date.toLocaleString("es-EC", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" });
}

async function load() {
  const [conversations, metrics, catalog, health] = await Promise.all([
    fetch("/api/conversations").then((r) => r.json()),
    fetch("/api/metrics").then((r) => r.json()),
    fetch("/api/catalog").then((r) => r.json()),
    fetch("/api/health").then((r) => r.json()),
  ]);
  state.conversations = conversations;
  state.metrics = metrics;
  state.catalog = catalog;
  state.health = health;
  if (state.selectedId) {
    const detail = await fetch(`/api/conversations/${state.selectedId}`);
    state.messages = detail.ok ? (await detail.json()).messages : [];
  }
  render();
}

let timer;
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => load().catch(console.error), 80);
}

function render() {
  const m = state.metrics;
  const moods = m.moods || {};
  $("metrics").innerHTML = [
    ["Activas", m.conversations || 0],
    ["Con asesor", m.with_human || 0],
    ["Calientes", moods.caliente || 0],
    ["Frustrados", moods.frustrado || 0],
    ["Alertas", m.flagged || 0],
  ].map(([label, value]) => `<span class="chip">${label} ${value}</span>`).join("");

  const refreshing = m.catalog_refreshing;
  $("catalogMeta").textContent = refreshing
    ? "Leyendo el sitio…"
    : `${m.courses || 0} cursos${m.catalog_updated_at ? ` · ${when(m.catalog_updated_at)}` : ""}`;
  $("refreshCatalog").disabled = Boolean(refreshing);

  const notes = [];
  if (!state.health.llm) notes.push("Falta LLM_API_KEY en .env. Mientras tanto la respuesta sale de la ficha del curso.");
  if (!state.health.telegram) notes.push("Falta TELEGRAM_BOT_TOKEN. Puedes probar con Simular prospecto.");
  if (m.catalog_error) notes.push(m.catalog_error);
  $("banner").hidden = notes.length === 0;
  $("banner").textContent = notes.join(" ");

  $("list").innerHTML = state.view === "chats" ? renderChats() : renderCatalog();
  renderMain();
}

function renderChats() {
  if (!state.conversations.length) return `<p class="muted">Aún no hay conversaciones.</p>`;
  return state.conversations.map((chat) => `
    <button class="card ${chat.mood} ${chat.id === state.selectedId ? "on" : ""}" data-id="${chat.id}">
      <div class="row"><b>${escapeHtml(chat.user_name || "Prospecto")}</b><span class="pill ${chat.mood}">${moodLabel(chat.mood)}</span></div>
      <small>${chat.mode === "human" ? "Asesor" : "IA"} · ${when(chat.updated_at)}</small>
      <div>${escapeHtml(chat.last_message || "")}</div>
      ${chat.flagged ? `<div class="flag">Intento de alterar reglas o precio</div>` : ""}
    </button>`).join("");
}

function renderCatalog() {
  if (!state.catalog.length) return `<p class="muted">El pénsum todavía se está leyendo.</p>`;
  return state.catalog.map((course) => `
    <button class="course ${course.slug === state.selectedSlug ? "on" : ""}" data-slug="${course.slug}">
      <b>${escapeHtml(course.title)}</b>
      <small>${escapeHtml(course.status_label || "")}${course.hours ? ` · ${course.hours} h` : ""}</small>
    </button>`).join("");
}

function renderMain() {
  if (state.view === "catalog") {
    const course = state.catalog.find((item) => item.slug === state.selectedSlug);
    $("head").className = "head";
    $("head").textContent = course ? course.title : "Elige un curso del pénsum";
    $("thread").innerHTML = course ? courseSheet(course) : "";
    $("draft").disabled = true;
    $("sendBtn").disabled = true;
    $("resumeBtn").disabled = true;
    $("side").innerHTML = "";
    return;
  }
  const chat = state.conversations.find((item) => item.id === state.selectedId);
  const ready = Boolean(chat);
  $("draft").disabled = !ready;
  $("sendBtn").disabled = !ready;
  $("resumeBtn").disabled = !ready || chat.mode === "ai";
  if (!chat) {
    $("head").className = "head empty";
    $("head").textContent = "Elige una conversación";
    $("thread").innerHTML = "";
    $("side").innerHTML = "";
    return;
  }
  $("head").className = "head";
  $("head").innerHTML = `<b>${escapeHtml(chat.user_name)}</b> <span class="pill ${chat.mood}">${moodLabel(chat.mood)}</span> <small>${chat.mode === "human" ? "IA en pausa" : "IA activa"}</small>`;
  $("thread").innerHTML = state.messages.map((message) => `
    <div class="bubble ${message.role}">
      <span>${message.role === "operator" ? "Asesor" : message.role === "assistant" ? "IA" : "Prospecto"} · ${when(message.created_at)}</span>
      ${escapeHtml(message.content)}
    </div>`).join("");
  $("thread").scrollTop = $("thread").scrollHeight;
  const profile = chat.profile || {};
  const courses = chat.courses || [];
  $("side").innerHTML = `
    <article><h3>Resumen</h3><p>${escapeHtml(chat.summary || "Todavía no hay resumen.")}</p></article>
    <article><h3>Perfil</h3>
      <div class="fact">Perfil: ${escapeHtml(profile.perfil || "desconocido")}</div>
      <div class="fact">Interés: ${escapeHtml(profile.interes || "—")}</div>
      <div class="fact">Objetivo: ${escapeHtml(profile.objetivo || "—")}</div>
    </article>
    <article><h3>Cursos en juego</h3>
      ${courses.length ? courses.map((course) => `<div class="fact"><b>${escapeHtml(course.title)}</b><br><small>${escapeHtml(course.status_label || "")}</small></div>`).join("") : `<p class="muted">Aún no se recomienda uno.</p>`}
    </article>
    ${chat.handover_reason ? `<article><h3>Pausa</h3><p>${escapeHtml(chat.handover_reason)}</p></article>` : ""}
  `;
}

function courseSheet(course) {
  const prices = course.prices || {};
  const money = (value) => (value == null ? "no publicado" : `$${Number(value)}`);
  return `
    <article class="fact"><b>${escapeHtml(course.status_label || "")}</b> ${course.hours ? `· ${course.hours} h` : ""} ${course.modality ? `· ${escapeHtml(course.modality)}` : ""}</article>
    <p>${escapeHtml(course.description || "")}</p>
    <p>Estudiante UTA ${money(prices.estudiante_uta)} · personal UTA ${money(prices.uta)} · público ${money(prices.publico)}</p>
    <p>${escapeHtml(course.schedule || "")}</p>
    <p><a href="${course.url}" target="_blank" rel="noreferrer">Ficha oficial</a></p>
    <pre>${escapeHtml(course.contents || "")}</pre>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

document.querySelectorAll(".switch button").forEach((button) => {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".switch button").forEach((item) => item.classList.toggle("on", item === button));
    render();
  });
});

$("list").addEventListener("click", (event) => {
  const chat = event.target.closest("[data-id]");
  const course = event.target.closest("[data-slug]");
  if (chat) {
    state.selectedId = Number(chat.dataset.id);
    load().catch(console.error);
  }
  if (course) {
    state.selectedSlug = course.dataset.slug;
    render();
  }
});

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("draft").value.trim();
  if (!text || !state.selectedId) return;
  $("sendBtn").disabled = true;
  await fetch(`/api/conversations/${state.selectedId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  $("draft").value = "";
  await load();
});

$("resumeBtn").addEventListener("click", async () => {
  if (!state.selectedId) return;
  await fetch(`/api/conversations/${state.selectedId}/mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "ai" }),
  });
  await load();
});

$("sim").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("simText").value.trim();
  if (!text) return;
  const response = await fetch("/api/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telegram_chat_id: simId, name: $("simName").value.trim() || "Prospecto de prueba", text }),
  });
  const conversation = await response.json();
  state.selectedId = conversation.id;
  state.view = "chats";
  document.querySelectorAll(".switch button").forEach((item) => item.classList.toggle("on", item.dataset.view === "chats"));
  $("simText").value = "";
  await load();
});

$("refreshCatalog").addEventListener("click", async () => {
  $("refreshCatalog").disabled = true;
  await fetch("/api/catalog/refresh", { method: "POST" });
  await load();
});

const source = new EventSource("/api/events");
source.onopen = () => { $("live").textContent = "en vivo"; $("live").classList.remove("off"); };
source.onerror = () => { $("live").textContent = "reconectando"; $("live").classList.add("off"); };
source.onmessage = schedule;

load().catch(console.error);
