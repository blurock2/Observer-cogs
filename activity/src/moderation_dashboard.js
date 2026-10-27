const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const guildId = new URLSearchParams(location.search).get("guild_id");
let data;
let filter = "open";
let actionInFlight = 0;

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || body.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function brand() {
  return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png" alt=""></span><span>OBSERVER</span></a>`;
}

function reportRows() {
  const reports = (data.reports || []).filter((report) => filter === "all" || report.status === "open");
  if (!reports.length) return `<div class="mod-empty"><span>✓</span><h3>${filter === "open" ? "All caught up" : "No unclaimed reports to display"}</h3><p>${filter === "open" ? "There are no unclaimed reports in this server’s configured reports channel." : "Claimed reports are hidden here while staff handles them. You can still manage them in Discord."}</p></div>`;
  return `<div class="mod-report-list">${reports.map((report) => {
    const date = new Date(report.created_at);
    const time = Number.isNaN(date.getTime()) ? "Recently" : date.toLocaleString();
    const actionButton = (action, label, className = "") => `<button class="mod-action-button ${className}" data-report-action="${action}" data-report-id="${esc(report.id)}">${label}</button>`;
    const isClaimed = report.claimed ?? Boolean(report.claimed_by);
    const isClaimant = isClaimed && String(data.viewer_id) === String(report.claimed_by || "");
    const claimControl = isClaimed
      ? actionButton("unclaim", "↩ Unclaim Report", "secondary")
      : actionButton("claim", "Claim", "secondary");
    const claimantActions = isClaimant
      ? `${actionButton("warn", "Warn", "warn")}${actionButton("timeout", "Timeout", "timeout")}${actionButton("delete", "Delete", "danger")}${!report.accepted ? actionButton("close", "Close Report", "secondary") : `<span class="mod-action-complete">Closed</span>`}${!report.escalated_by_name ? actionButton("escalate", "Escalate", "escalate") : `<span class="mod-action-complete">Escalated</span>`}`
      : `<span class="mod-action-complete">${isClaimed ? `Actions reserved for ${esc(report.claimed_by_name || "a moderator")}` : "Claim to take report actions"}</span>`;
    const reportActions = `${claimControl}${claimantActions}`;
    const claimNote = report.claimed_by_name ? `<small class="mod-claim-note">Claimed by ${esc(report.claimed_by_name)}</small>` : "";
    const linkedActionText = (report.linked_actions || []).map((action) => `Case #${action.case_number} ${action.case_type} by ${action.moderator_name}`).join(" · ");
    const resolution = report.status === "closed"
      ? `<div class="mod-report-resolution">${claimNote}${report.accepted_by_name ? `<small>Closed by ${esc(report.accepted_by_name)}</small>` : ""}${report.escalated_by_name ? `<small>Escalated by ${esc(report.escalated_by_name)}</small>` : ""}<small>${linkedActionText ? `Linked moderation action: ${esc(linkedActionText)}` : "No linked moderation action recorded."}</small></div>`
      : claimNote;
    return `<article class="mod-report"><div class="mod-report-symbol ${report.status === "closed" ? "is-reviewed" : ""}">${report.status === "closed" ? "✓" : "!"}</div><div class="mod-report-body"><div class="mod-report-meta"><strong>Message report</strong><time>${esc(time)}</time></div><p>${esc(report.reason || "No reason provided")}</p><div class="mod-report-target">Reported member <b>${esc(report.target_name)}</b>${report.channel ? ` · ${esc(report.channel)}` : ""}</div>${resolution}</div><div class="mod-report-controls"><span class="mod-status ${report.status === "closed" ? "is-reviewed" : ""}">${report.status === "closed" ? "CLOSED" : "OPEN"}</span><div class="mod-report-actions">${reportActions}</div><a class="mod-review-link" href="${esc(report.url)}" target="_blank" rel="noreferrer">Open in Discord ↗</a></div></article>`;
  }).join("")}</div>`;
}

function openActionDialog(report, action) {
  const titles = { warn: "Warn member", timeout: "Timeout member", delete: "Delete reported message" };
  const deleteWarning = action === "delete"
    ? `<label class="mod-action-confirm"><input type="checkbox" name="confirm" required><span>I understand this permanently deletes the original Discord message.</span></label>`
    : "";
  const duration = action === "timeout"
    ? `<label class="mod-action-field">Timeout duration in minutes<input name="duration_minutes" type="number" min="1" max="40320" value="60" required></label>`
    : "";
  root.insertAdjacentHTML("beforeend", `<div class="mod-action-modal" id="mod-action-modal"><form class="mod-action-dialog"><button class="mod-action-modal-close" type="button" aria-label="Close">×</button><div class="eyebrow">REPORT ACTION</div><h2>${titles[action]}</h2><p>Reported member: <strong>${esc(report.target_name)}</strong></p>${duration}<label class="mod-action-field">Reason<textarea name="reason" maxlength="1000">${esc(report.reason || "")}</textarea></label>${deleteWarning}<div class="mod-action-modal-buttons"><button class="mod-action-cancel" type="button">Cancel</button><button class="mod-action-submit ${action === "delete" ? "is-danger" : ""}" type="submit">${action === "delete" ? "Delete message" : `Confirm ${action}`}</button></div></form></div>`);
  const overlay = root.querySelector("#mod-action-modal");
  const form = overlay.querySelector("form");
  requestAnimationFrame(() => overlay.classList.add("is-open"));
  const close = () => { overlay.classList.remove("is-open"); setTimeout(() => overlay.remove(), 220); };
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });
  overlay.querySelector(".mod-action-modal-close").addEventListener("click", close);
  overlay.querySelector(".mod-action-cancel").addEventListener("click", close);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector(".mod-action-submit");
    const body = { reason: form.elements.reason.value.trim() };
    if (action === "timeout") body.duration_minutes = Number(form.elements.duration_minutes.value);
    if (action === "delete") body.confirm = form.elements.confirm.checked;
    submit.disabled = true;
    actionInFlight += 1;
    submit.textContent = action === "delete" ? "Deleting…" : "Applying…";
    try {
      await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}/reports/${encodeURIComponent(report.id)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      data = await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`);
      overlay.remove();
      render();
    } catch (error) {
      if (error.status === 409) {
        try { data = await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`); render(); }
        catch { alert(error.message); }
        return;
      }
      alert(error.message);
      submit.disabled = false;
      submit.textContent = action === "delete" ? "Delete message" : `Confirm ${action}`;
    } finally {
      actionInFlight = Math.max(0, actionInFlight - 1);
    }
  });
}

function actionHistory() {
  const actions = data.recent_actions || [];
  if (!actions.length) return `<div class="empty-card"><div class="empty-icon">✓</div><h3>No moderation actions yet</h3><p>Actions recorded by Observer will appear here.</p></div>`;
  return `<div class="mod-action-history">${actions.map((action) => {
    const date = new Date(action.created_at);
    const time = Number.isNaN(date.getTime()) ? "Recently" : date.toLocaleString();
    return `<article class="mod-history-row"><div class="mod-history-icon">${esc(action.case_type.slice(0, 1).toUpperCase())}</div><div class="mod-history-copy"><div class="mod-history-title"><strong>${esc(action.case_type)} · ${esc(action.target_name)}</strong><span>CASE #${esc(action.case_number)}</span></div><p>${esc(action.reason)}</p><small>${esc(action.moderator_name)} · ${esc(time)}</small></div><a class="mod-history-profile" href="https://discord.com/users/${encodeURIComponent(action.target_id)}" target="_blank" rel="noreferrer">Member ↗</a></article>`;
  }).join("")}</div>`;
}

function render() {
  const guild = data.guild;
  const icon = guild.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(guild.id)}/${encodeURIComponent(guild.icon)}.png?size=96" alt="">` : `<span class="control-server-fallback">${esc(guild.name.slice(0, 1).toUpperCase())}</span>`;
  root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">MODERATOR DASHBOARD</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="mod-dashboard"><a class="back-link" href="/?dashboard=1">← All servers</a><header class="mod-header">${icon}<div><div class="eyebrow">SERVER OVERVIEW</div><h1>${esc(guild.name)}</h1><p>Reports and moderation activity for your community.</p></div></header><section class="mod-metrics"><article class="mod-metric metric-attention"><div class="eyebrow">OPEN REPORTS</div><strong>${data.open_reports}</strong><small>${data.reports_configured ? "Need a moderator’s review" : "Configure a reports channel to begin"}</small></article><article class="mod-metric metric-closed"><div class="eyebrow">CLOSED / CLAIMED</div><strong>${data.closed_reports || 0}</strong><small>Claimed or accepted reports</small></article><article class="mod-metric"><div class="eyebrow">MEMBERS</div><strong>${Number(data.member_count).toLocaleString()}</strong><small>In this Discord server</small></article><article class="mod-metric"><div class="eyebrow">BOT STATUS</div><strong class="online-text">${data.bot_online ? "Online" : "Offline"}</strong><small>${data.bot_online ? "Ready when you are" : "Observer is reconnecting"}</small></article></section><section class="mod-queue"><div class="mod-section-head"><div><div class="eyebrow">MODERATION QUEUE</div><h2>Recent reports</h2><p>${data.reports_configured ? `From ${esc(data.reports_channel || "the configured channel")}` : "Reports are not configured yet."}</p></div><div class="mod-filters"><button data-filter="open" class="${filter === "open" ? "active" : ""}">Open</button><button data-filter="all" class="${filter === "all" ? "active" : ""}">All recent</button></div></div>${!data.reports_configured ? `<div class="mod-empty"><span>i</span><h3>No reports channel configured</h3><p>A server owner can set one up in Observer’s control panel.</p>${guild.is_owner ? `<a class="server-action server-configure" href="/setup?guild_id=${encodeURIComponent(guild.id)}">Configure Observer ↗</a>` : ""}</div>` : reportRows()}<p class="mod-footnote">Claim and accept reports here, or open one in Discord for the original report controls.</p></section><section class="mod-queue"><div class="mod-section-head"><div><div class="eyebrow">CASE LOG</div><h2>Moderation action history</h2><p>Recent warnings, timeouts, kicks, bans, and other recorded cases.</p></div></div>${actionHistory()}</section></main></div>`;
  root.querySelector("#signout").addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  root.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { filter = button.dataset.filter; render(); }));
  root.querySelectorAll("[data-report-action]").forEach((button) => button.addEventListener("click", async () => {
    const action = button.dataset.reportAction;
    const reportId = button.dataset.reportId;
    const report = (data.reports || []).find((item) => String(item.id) === String(reportId));
    if (["warn", "timeout", "delete"].includes(action)) { openActionDialog(report, action); return; }
    button.disabled = true;
    actionInFlight += 1;
    button.textContent = action === "close" ? "Closing…" : action === "claim" ? "Claiming…" : action === "escalate" ? "Escalating…" : "Updating…";
    try {
      await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}/reports/${encodeURIComponent(reportId)}/${action}`, { method: "POST" });
      data = await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`);
      render();
    } catch (error) {
      if (error.status === 409) {
        try { data = await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`); render(); }
        catch { alert(error.message); }
        return;
      }
      alert(error.message);
      button.disabled = false;
      button.textContent = action === "close" ? "Close Report" : action === "escalate" ? "Escalate" : action[0].toUpperCase() + action.slice(1);
    } finally {
      actionInFlight = Math.max(0, actionInFlight - 1);
    }
  }));
}

async function refreshDashboard() {
  if (document.hidden || actionInFlight || root.querySelector("#mod-action-modal")) return;
  try {
    const latest = await api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`);
    if (JSON.stringify(latest) !== JSON.stringify(data)) {
      data = latest;
      render();
    }
  } catch (error) {
    // Keep the current dashboard visible during brief network or Discord API
    // hiccups; the next poll will retry.
  }
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
window.setInterval(refreshDashboard, 5000);
