const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { message: text }; }
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function safeLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : "";
  } catch { return ""; }
}

function dateLabel(value, options = {}) {
  const date = new Date(Number(value) * 1000);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString(undefined, options);
}

function renderItems(bookmarks, reminders) {
  const bookmarkCards = bookmarks.map((item) => {
    const url = safeLink(item.message_url);
    const attachments = String(item.attachment_urls || "").split(/\r?\n/).filter(Boolean);
    return `<article class="diary-entry"><div class="diary-entry-top"><span class="diary-entry-kind">BOOKMARK</span><time>${esc(dateLabel(item.bookmarked_at, { dateStyle: "medium", timeStyle: "short" }))}</time></div><p>${esc(item.content || "Saved message with no text.")}</p>${attachments.length ? `<div class="diary-attachments">${attachments.map((attachment, index) => { const link = safeLink(attachment); return link ? `<a href="${esc(link)}" target="_blank" rel="noreferrer">Attachment ${index + 1} ↗</a>` : ""; }).join("")}</div>` : ""}${url ? `<a class="diary-open-link" href="${esc(url)}" target="_blank" rel="noreferrer">Open saved message in Discord ↗</a>` : ""}</article>`;
  }).join("");
  const reminderCards = reminders.map((item) => {
    const url = safeLink(item.source_url);
    return `<article class="diary-entry diary-reminder"><div class="diary-entry-top"><span class="diary-entry-kind">REMINDER</span><time>${Number(item.next_send_at) <= Date.now() / 1000 ? "Due now" : `Due ${esc(dateLabel(item.next_send_at, { dateStyle: "medium", timeStyle: "short" }))}`}</time></div><p>${esc(item.content || "Reminder")}</p>${url ? `<a class="diary-open-link" href="${esc(url)}" target="_blank" rel="noreferrer">Open referenced message ↗</a>` : ""}</article>`;
  }).join("");
  // Preserve the two sections so upcoming reminders and saved messages are easy to scan.
  return `<section class="diary-section"><div class="diary-section-head"><div class="eyebrow">YOUR SCHEDULE</div><h2>Reminders <span>${reminders.length}</span></h2><p>Upcoming personal reminders from Observer.</p></div><div class="diary-entry-list">${reminderCards || `<div class="diary-empty">No active reminders. Set one in Discord and it will appear here.</div>`}</div></section><section class="diary-section"><div class="diary-section-head"><div class="eyebrow">SAVED FOR LATER</div><h2>Bookmarks <span>${bookmarks.length}</span></h2><p>Messages you saved with <code>/bookmark</code>.</p></div><div class="diary-entry-list">${bookmarkCards || `<div class="diary-empty">No bookmarks yet. Save a message in Discord to start your diary.</div>`}</div></section>`;
}

async function start() {
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png?v=3" alt="Observer logo"><p>Opening your diary…</p></main>`;
  try {
    const [session, diary] = await Promise.all([api("/api/session"), api("/api/diary")]);
    if (!session.signed_in) { location.assign("/auth/login"); return; }
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar"><a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></a><div class="topnav"><a href="/?dashboard=1">Your servers</a><span class="signed-in-label">YOUR DIARY</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="diary-page"><a class="back-link" href="/?dashboard=1">← Back to your servers</a><div class="eyebrow"><span class="live-mark"></span> PERSONAL SPACE</div><h1>Your diary.</h1><p class="diary-lede">Your private bookmarks and reminders in one place. Only you can see these entries.</p>${renderItems(diary.bookmarks || [], diary.reminders || [])}</main></div>`;
    root.querySelector("#signout")?.addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>We couldn’t open your diary.</h1><p>${esc(error.message)}</p><a class="button button-primary" href="/auth/login">Sign in with Discord</a><a class="back-link" href="/?dashboard=1">Back to your servers</a></main>`;
  }
}

void start();
