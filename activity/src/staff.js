import { DiscordSDK } from "@discord/embedded-app-sdk";

const root = document.querySelector("#app");
const clientId = import.meta.env.VITE_DISCORD_CLIENT_ID;
let accessToken;
let selectedGuildId;
let currentTab = "overview";

function frame(title, subtitle, body) {
  root.innerHTML = `<div class="shell">
    <aside class="rail"><div class="brand"><span class="brand-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></div>
      <div class="rail-label">STAFF SPACE</div>
      <nav>${["overview", "reports", "cases", "bot status"].map((label) =>
        `<button class="nav-item ${currentTab === label ? "active" : ""}" data-tab="${label}"><span class="nav-dot"></span>${label}</button>`
      ).join("")}</nav>
      <div class="rail-bottom"><span class="online-dot"></span> Observer connected</div>
    </aside>
    <section class="content"><header><div><div class="eyebrow">MODERATION CONSOLE</div><h1>${title}</h1><p>${subtitle}</p></div><div class="guild-chip">${selectedGuildId ? `SERVER · ${selectedGuildId}` : "STAFF VIEW"}</div></header>
      ${body}</section>
  </div>`;
  root.querySelectorAll("[data-tab]").forEach((el) => el.addEventListener("click", () => loadTab(el.dataset.tab)));
}

function card(label, value, hint, tone = "") {
  return `<article class="metric ${tone}"><div class="metric-top"><span>${label}</span><i></i></div><strong>${value}</strong><small>${hint}</small></article>`;
}

async function api(path) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${accessToken}` } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || "Observer could not load this view.");
  return data;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function loadTab(tab) {
  currentTab = tab;
  frame("Loading…", "Fetching the latest staff information.", `<div class="loading-card">Loading this server’s moderation data</div>`);
  try {
    const data = await api(`/api/activity/${tab.replace(" ", "-")}?guild_id=${encodeURIComponent(selectedGuildId)}`);
    if (tab === "overview") {
      const reports = data.reports || [];
      frame("Staff overview", "A clear view of what needs attention.", `
        <div class="metrics">${card("OPEN REPORTS", data.open_reports, "Awaiting staff review", "accent")}${card("OPEN CASES", data.open_cases, "Across moderation records")}${card("BOT LATENCY", `${data.latency_ms} ms`, `Uptime ${esc(data.uptime)}`, "green")}</div>
        <div class="section-heading"><div><span class="eyebrow">WORK QUEUE</span><h2>Reports to review</h2></div><button class="text-button" data-tab="reports">View all <span>↗</span></button></div>
        ${reportList(reports.slice(0, 5))}`);
    } else if (tab === "reports") {
      frame("Reports", "Open reports recorded in this server.", `<div class="section-heading"><div><span class="eyebrow">WORK QUEUE</span><h2>${data.open_reports} open</h2></div></div>${reportList(data.reports || [])}`);
    } else if (tab === "cases") {
      const rows = data.cases || [];
      frame("Cases", "Recently recorded moderation cases.", `<div class="section-heading"><div><span class="eyebrow">CASE LOG</span><h2>${data.total_cases} recorded</h2></div></div>
        <div class="table-wrap"><table><thead><tr><th>CASE</th><th>MEMBER</th><th>TYPE</th><th>STATUS</th><th>REASON</th></tr></thead><tbody>${rows.map((c) => `<tr><td class="case-id">#${c.case_number}</td><td>${esc(c.target_name || c.target_id)}</td><td>${esc(c.case_type)}</td><td><span class="status-pill">${esc(c.status)}</span></td><td class="reason">${esc(c.reason || "No reason recorded")}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No cases recorded yet.</td></tr>`}</tbody></table></div>`);
    } else {
      frame("Bot status", "Live health information for Observer.", `<div class="status-hero"><span class="pulse"></span><div><span class="eyebrow">CURRENT STATUS</span><h2>Online and responding</h2><p>Connected to ${data.server_count} server${data.server_count === 1 ? "" : "s"}</p></div></div><div class="metrics">${card("GATEWAY LATENCY", `${data.latency_ms} ms`, "Discord connection")}${card("UPTIME", esc(data.uptime), "Since current process start")}${card("OPEN REPORTS", data.open_reports, "In this server", "accent")}</div>${data.errors ? `<div class="section-heading"><div><span class="eyebrow">OWNER ONLY</span><h2>Recent runtime errors</h2></div></div><div class="error-list">${data.errors.map((e) => `<div class="error-row"><code>${esc(e.id)}</code><span>${esc(e.source)} · ${esc(e.type)} · ${esc(e.location)}</span><time>${esc(e.time)}</time></div>`).join("") || `<p class="empty">No runtime errors recorded since startup.</p>`}</div>` : ""}`);
    }
  } catch (error) {
    frame("Couldn’t load this view", "Observer could not fetch the requested staff data.", `<div class="error-card">${esc(error.message)}<button id="retry">Try again</button></div>`);
    root.querySelector("#retry")?.addEventListener("click", () => loadTab(tab));
  }
}

function reportList(reports) {
  if (!reports.length) return `<div class="empty-card"><div class="empty-icon">✓</div><h3>All caught up</h3><p>There are no open reports for this server.</p></div>`;
  return `<div class="report-list">${reports.map((r) => `<article class="report-row"><div class="report-icon">!</div><div class="report-main"><div class="report-top"><strong>Report #${r.case_number}</strong><span>${esc(r.created_at)}</span></div><p>${esc(r.reason || "No reason provided")}</p><div class="report-target">Reported member <b>${esc(r.target_name || r.target_id)}</b></div></div><span class="status-pill open">OPEN</span></article>`).join("")}</div>`;
}

async function start() {
  if (!clientId) throw new Error("Set VITE_DISCORD_CLIENT_ID in activity/.env before starting the Activity.");
  const sdk = new DiscordSDK(clientId);
  await sdk.ready();
  const { code } = await sdk.commands.authorize({ client_id: clientId, response_type: "code", state: "observer", prompt: "none", scope: ["identify"] });
  const tokenResponse = await fetch("/api/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
  const tokenData = await tokenResponse.json().catch(() => ({}));
  if (!tokenResponse.ok) throw new Error(tokenData.error || tokenData.message || "Discord authorization failed.");
  accessToken = tokenData.access_token;
  const member = await sdk.commands.authenticate({ access_token: accessToken });
  if (!member) throw new Error("Discord could not authenticate this Activity session.");
  selectedGuildId = sdk.guildId;
  if (!selectedGuildId) throw new Error("Open Observer Staff from a server channel to view its moderation data.");
  await loadTab("overview");
}

start().catch((error) => {
  root.innerHTML = `<main class="fatal"><div class="fatal-mark"><img src="/observer.png?v=3" alt="Observer logo"></div><div class="eyebrow">OBSERVER STAFF ACTIVITY</div><h1>We couldn’t connect</h1><p>${esc(error.message)}</p><small>Launch Observer from a server’s App Launcher and try again.</small></main>`;
});
