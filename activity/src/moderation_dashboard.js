const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const guildId = new URLSearchParams(location.search).get("guild_id");
let data;
let filter = "open";

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.message || `Request failed (${response.status})`);
  return body;
}

function brand() {
  return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png" alt=""></span><span>OBSERVER</span></a>`;
}

function reportRows() {
  const reports = (data.reports || []).filter((report) => filter === "all" || report.status === "open");
  if (!reports.length) return `<div class="mod-empty"><span>âœ“</span><h3>${filter === "open" ? "All caught up" : "No reports yet"}</h3><p>${filter === "open" ? "There are no open reports in this serverâ€™s configured reports channel." : "New message reports will appear here when members submit them."}</p></div>`;
  return `<div class="mod-report-list">${reports.map((report) => {
    const date = new Date(report.created_at);
    const time = Number.isNaN(date.getTime()) ? "Recently" : date.toLocaleString();
    return `<article class="mod-report"><div class="mod-report-symbol ${report.status === "reviewed" ? "is-reviewed" : ""}">${report.status === "reviewed" ? "âœ“" : "!"}</div><div class="mod-report-body"><div class="mod-report-meta"><strong>Message report</strong><time>${esc(time)}</time></div><p>${esc(report.reason || "No reason provided")}</p><div class="mod-report-target">Reported member <b>${esc(report.target_name)}</b>${report.channel ? ` Â· ${esc(report.channel)}` : ""}</div></div><span class="mod-status ${report.status === "reviewed" ? "is-reviewed" : ""}">${report.status === "reviewed" ? "REVIEWED" : "OPEN"}</span><a class="mod-review-link" href="${esc(report.url)}" target="_blank" rel="noreferrer">Open in Discord â†—</a></article>`;
  }).join("")}</div>`;
}

function render() {
  const guild = data.guild;
  const icon = guild.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(guild.id)}/${encodeURIComponent(guild.icon)}.png?size=96" alt="">` : `<span class="control-server-fallback">${esc(guild.name.slice(0, 1).toUpperCase())}</span>`;
  root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">MODERATOR DASHBOARD</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="mod-dashboard"><a class="back-link" href="/?dashboard=1">â† All servers</a><header class="mod-header">${icon}<div><div class="eyebrow">SERVER OVERVIEW</div><h1>${esc(guild.name)}</h1><p>Reports and moderation activity for your community.</p></div><span class="bot-online-pill"><i></i> ${data.bot_online ? "BOT ONLINE" : "BOT OFFLINE"}</span></header><section class="mod-metrics"><article class="mod-metric metric-attention"><div class="eyebrow">OPEN REPORTS</div><strong>${data.open_reports}</strong><small>${data.reports_configured ? "Need a moderatorâ€™s review" : "Configure a reports channel to begin"}</small></article><article class="mod-metric"><div class="eyebrow">MEMBERS</div><strong>${Number(data.member_count).toLocaleString()}</strong><small>In this Discord server</small></article><article class="mod-metric"><div class="eyebrow">BOT STATUS</div><strong class="online-text">${data.bot_online ? "Online" : "Offline"}</strong><small>${data.bot_online ? "Ready when you are" : "Observer is reconnecting"}</small></article></section><section class="mod-queue"><div class="mod-section-head"><div><div class="eyebrow">MODERATION QUEUE</div><h2>Recent reports</h2><p>${data.reports_configured ? `From ${esc(data.reports_channel || "the configured channel")}` : "Reports are not configured yet."}</p></div><div class="mod-filters"><button data-filter="open" class="${filter === "open" ? "active" : ""}">Open</button><button data-filter="all" class="${filter === "all" ? "active" : ""}">All recent</button></div></div>${!data.reports_configured ? `<div class="mod-empty"><span>i</span><h3>No reports channel configured</h3><p>A server owner can set one up in Observerâ€™s control panel.</p>${guild.is_owner ? `<a class="server-action server-configure" href="/setup?guild_id=${encodeURIComponent(guild.id)}">Configure Observer â†—</a>` : ""}</div>` : reportRows()}<p class="mod-footnote">Reports are loaded from the serverâ€™s configured Discord channel. Use â€œOpen in Discordâ€ to claim or accept a report with Observerâ€™s existing staff controls.</p></section></main></div>`;
  root.querySelector("#signout").addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  root.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { filter = button.dataset.filter; render(); }));
}

async function start() {
  try {
    if (!guildId) throw new Error("Choose a server from your server list first.");
    data = await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`);
    render();
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Could not load the moderator dashboard.</h1><p>${esc(error.message)}</p><a class="back-link" href="/?dashboard=1">Return to servers</a></main>`;
  }
}

start();

