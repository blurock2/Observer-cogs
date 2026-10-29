const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const guildId = new URLSearchParams(location.search).get("guild_id");
let panel;
let activityData;
let selectedModule = null;
const dirtySettings = new Set();
const MODULE_EMOJIS = Object.freeze({
  afk: "\\u{1F4A4}", autorole: "\\u{1F3AD}", verification: "\\u2705", reminders: "\\u23F0", bot: "\\u{1F916}",
  audit_log: "\\u{1F9FE}", reaction_roles: "\\u{1F501}", message_quoter: "\\u{1F4AC}", spotify: "\\u{1F3B5}",
  acc_link: "\\u{1F517}", utilities: "\\u{1F9F0}", report_msg: "\\u{1F4E3}", server_stats: "\\u{1F4CA}",
  temporary_voice: "\\u{1F50A}", rules: "\\u{1F4DC}", tickets: "\\u{1F3AB}", message_relay: "\\u{1F4E1}",
  moderation: "\\u{1F6E1}\\uFE0F", mentions: "\\u{1F514}", security: "\\u{1F6E1}\\uFE0F", tags: "\\u{1F3F7}\\uFE0F",
  member_commands: "\\u{1F465}", help: "\\u2753", leveling: "\\u{1F4C8}", weather: "\\u{1F324}\\uFE0F",
  auto_responder: "\\u{1F4AC}", setup_ui: "\\u2699\\uFE0F",
});
const moduleEmoji = (module) => MODULE_EMOJIS[module.key] || module.emoji || "";

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function brand() { return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></a>`; }

function renderCards() {
  const query = (document.querySelector("#module-search")?.value || "").trim().toLowerCase();
  const modules = panel.modules.filter((item) => `${item.label} ${item.description}`.toLowerCase().includes(query));
  document.querySelector("#module-grid").innerHTML = modules.map((module) => {
    const enabled = module.settings.find((setting) => setting.key === "enabled");
    const state = enabled ? (enabled.value ? "Enabled" : "Disabled") : "Configure options";
    const count = module.key === "auto_responder" ? `${module.responses?.length || 0} RESPONSES` : `${module.settings.length} SETTINGS`;
    return `<article class="control-module" data-open-module="${module.key}" tabindex="0"><div class="module-card-top"><span class="module-emoji">${esc(moduleEmoji(module))}</span><span class="module-count">${count}</span></div><h2>${esc(module.label)}</h2><p>${esc(module.description)}</p><div class="module-card-actions">${enabled ? `<label class="web-toggle"><input type="checkbox" data-toggle-setting="${module.key}|enabled" ${enabled.value ? "checked" : ""}><span></span><b>${state}</b></label>` : `<span class="module-state">${state}</span>`}<button class="configure-module" data-config-module="${module.key}">Configure <span>&#8594;</span></button></div></article>`;
  }).join("") || `<p class="empty-settings">No features match that search.</p>`;
}

function controlFor(module, setting) {
  const id = `setting-${module.key}-${setting.key}`;
  const value = setting.value;
  const save = `<button class="save-setting" data-save-setting="${module.key}|${setting.key}">Save this setting</button>`;
  let field;
  if (setting.kind === "toggle") {
    field = `<label class="web-toggle"><input type="checkbox" data-toggle-setting="${module.key}|${setting.key}" ${value ? "checked" : ""}><span></span><b>${value ? "On" : "Off"}</b></label><small class="setting-timing">Changes apply immediately</small>`;
  } else if (setting.kind === "channel" || setting.kind === "role") {
    const isChannel = setting.kind === "channel";
    const options = isChannel ? panel.channels : panel.roles;
    const chosen = value == null ? "" : String(value).replace(/\D/g, "");
    const displayOption = (option) => {
      const prefix = isChannel && option.type !== "category" ? "#" : "";
      const kind = isChannel ? option.type.replaceAll("_", " ") : (option.is_default ? "default role" : "role");
      return `${prefix}${option.name} &#183; ${kind}`;
    };
    const selected = options.find((option) => String(option.id) === chosen);
    const optionHtml = options.map((option) => `<option value="${esc(displayOption(option))}" data-id="${esc(option.id)}"></option>`).join("");
    const inputValue = selected ? displayOption(selected) : (chosen ? `Unavailable ${isChannel ? "channel" : "role"} (${chosen})` : "Not set");
    field = `<div class="searchable-picker"><input id="${id}" type="search" list="${id}-options" data-picker-input autocomplete="off" placeholder="Type to search ${isChannel ? "channels" : "roles"}&#8230;" value="${esc(inputValue)}"><datalist id="${id}-options"><option value="Not set" data-id=""></option>${optionHtml}</datalist><input type="hidden" data-setting-value="${module.key}|${setting.key}" value="${esc(chosen)}">${save}</div>`;
  } else if (setting.kind === "ticket_panel") {
    const panelSettings = value || {};
    field = `<div class="ticket-panel-fields"><label>Panel title<input data-ticket-field="panel_title" maxlength="256" value="${esc(panelSettings.panel_title || "")}"></label><label>Panel description<textarea data-ticket-field="panel_description" maxlength="2048">${esc(panelSettings.panel_description || "")}</textarea></label><label>Panel footer<textarea data-ticket-field="panel_footer" maxlength="2048">${esc(panelSettings.panel_footer || "")}</textarea></label><label>Ticket reasons <small>One per line; up to 24.</small><textarea data-ticket-field="reason_options">${esc((panelSettings.reason_options || []).join("\n"))}</textarea></label><div class="ticket-panel-actions"><button class="save-setting" data-save-ticket="${module.key}|${setting.key}">Save panel settings</button><button class="save-setting" data-send-ticket-panel="${module.key}|${setting.key}">Save &amp; send panel to channel</button></div></div>`;
  } else {
    const text = value == null ? "" : String(value);
    const input = setting.kind === "integer"
      ? `<input id="${id}" type="number" min="0" max="1000000" step="1" data-setting-value="${module.key}|${setting.key}" value="${esc(text)}">`
      : (/message|description|filter/i.test(setting.key) || text.length > 120
        ? `<textarea id="${id}" maxlength="4000" data-setting-value="${module.key}|${setting.key}">${esc(text)}</textarea>`
        : `<input id="${id}" type="text" maxlength="4000" data-setting-value="${module.key}|${setting.key}" value="${esc(text)}">`);
    field = `<div class="setting-control">${input}${save}</div><small class="setting-timing">Save this setting to apply your changes</small>`;
  }
  const warning = /experimental|permanently|ban automatically/i.test(`${setting.label} ${setting.description}`) ? " setting-warning" : "";
  return `<section class="setting-row${warning}"><div class="setting-copy"><label for="${id}">${esc(setting.label)}</label>${setting.description ? `<small>${esc(setting.description)}</small>` : ""}</div><div class="setting-widget">${field}</div></section>`;
}

function autoResponseEditor(module) {
  const responses = module.responses || [];
  const rows = responses.map((response) => `<article class="auto-response-row"><div><strong>${esc(response.responder_name)}</strong><small>Triggers: ${esc((response.triggers || "").split("\n").join(", "))} &#183; ${esc(response.style)} &#183; ${Number(response.cooldown_seconds)}s cooldown</small></div><div><button class="save-setting" type="button" data-edit-response="${esc(response.id)}">Edit</button><button class="save-setting response-delete" type="button" data-delete-response="${esc(response.id)}">Delete</button></div></article>`).join("");
  return `<div class="auto-responder-editor"><div class="auto-responder-list-head"><div><h3>Saved responses</h3><p>${responses.length}/30 responses. A response sends when two or more distinct triggers or variations match one message.</p></div><button class="save-setting" type="button" data-new-response>New response</button></div><div class="auto-response-list">${rows || `<p class="empty-settings">No auto responses configured yet.</p>`}</div><form id="auto-response-form" class="auto-response-form"><input type="hidden" name="id"><h3 id="auto-response-form-title">Create auto response</h3><label>Responder name<input name="responder_name" maxlength="50" required placeholder="e.g. Greeting"></label><label>Triggers <small>1&#8211;5 words, one per line</small><textarea name="triggers" rows="3" required placeholder="hi&#10;hello"></textarea></label><label>Trigger variations <small>Up to 20 words, one per line</small><textarea name="sub_triggers" rows="3" placeholder="hiya&#10;hey"></textarea></label><label>Response text<textarea name="reply" rows="4" maxlength="1800" required placeholder="Write the response&#8230;"></textarea></label><div class="auto-response-form-grid"><label>Style<select name="style"><option value="text">Normal text</option><option value="embed">Embed</option><option value="card">Card V2</option></select></label><label>Cooldown in seconds<input name="cooldown_seconds" type="number" min="0" max="3600" step="1" value="0" required></label></div><div class="auto-response-form-actions"><button class="save-setting" type="submit">Save response</button><button class="save-setting" type="button" data-cancel-response hidden>Cancel edit</button></div></form></div>`;
}

function renderEditor(key, animate = true, force = false) {
  if (selectedModule === key && !force) return;
  if (selectedModule && selectedModule !== key && [...dirtySettings].some((item) => item.startsWith(`${selectedModule}|`))) {
    if (!confirm("You have unsaved settings. Discard them and open another feature?")) return;
    for (const item of [...dirtySettings]) if (item.startsWith(`${selectedModule}|`)) dirtySettings.delete(item);
  }
  selectedModule = key;
  const module = panel.modules.find((item) => item.key === key);
  const editor = document.querySelector("#module-editor");
  const enabled = module.settings.find((setting) => setting.key === "enabled");
  editor.hidden = false;
  if (animate) editor.classList.remove("is-open");
  if (module.key === "auto_responder") {
    editor.innerHTML = `<div class="module-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="module-editor-title"><div class="editor-head"><div><div class="eyebrow">CONFIGURE FEATURE</div><h2 id="module-editor-title">${esc(moduleEmoji(module))} ${esc(module.label)}</h2><p>${esc(module.description)}</p></div><button id="close-module-editor" class="editor-close" aria-label="Close settings">&#215;</button></div>${autoResponseEditor(module)}</div>`;
    document.body.classList.add("modal-open");
    if (animate) requestAnimationFrame(() => editor.classList.add("is-open"));
    return;
  }
  editor.innerHTML = `<div class="module-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="module-editor-title"><div class="editor-head"><div><div class="eyebrow">CONFIGURE FEATURE</div><h2 id="module-editor-title">${esc(moduleEmoji(module))} ${esc(module.label)}</h2><p>${esc(module.description)}</p></div><button id="close-module-editor" class="editor-close" aria-label="Close settings">&#215;</button></div>${module.key === "bot" ? `<a class="embed-tool-link" href="/embeds?guild_id=${encodeURIComponent(guildId)}">Open custom embed sender <span>&#8599;</span></a>` : ""}<div class="editor-enable">${enabled ? `<span>${esc(enabled.label)}</span><label class="web-toggle"><input type="checkbox" data-toggle-setting="${module.key}|enabled" ${enabled.value ? "checked" : ""}><span></span><b>${enabled.value ? "Enabled" : "Disabled"}</b></label>` : `<span>This feature has no master switch. Configure its options below.</span>`}</div><div class="pending-save-bar"><span>Unsaved edits stay here until you save them.</span><button class="save-setting" data-save-all disabled>Save changes</button></div><div class="settings-list">${module.settings.map((setting) => controlFor(module, setting)).join("") || `<p class="empty-settings">No web settings are defined for this feature yet.</p>`}</div></div>`;
  document.body.classList.add("modal-open");
  if (animate) requestAnimationFrame(() => editor.classList.add("is-open"));
}

function closeEditor() {
  const editor = document.querySelector("#module-editor");
  if (!editor || editor.hidden) return;
  if ([...dirtySettings].some((key) => key.startsWith(`${selectedModule}|`)) && !confirm("You have unsaved settings. Close and discard these changes?")) return;
  for (const key of [...dirtySettings]) if (key.startsWith(`${selectedModule}|`)) dirtySettings.delete(key);
  editor.classList.remove("is-open");
  document.body.classList.remove("modal-open");
  selectedModule = null;
  setTimeout(() => { if (!editor.classList.contains("is-open")) editor.hidden = true; }, 240);
}

function activityPanel() {
  if (activityData?.error) {
    return `<aside class="control-activity-panel"><div class="activity-panel-head"><div class="eyebrow">SERVER ACTIVITY</div><h2>Recent reports &amp; actions</h2></div><p class="activity-panel-error">${esc(activityData.error)}</p><a class="activity-panel-link" href="/moderate?guild_id=${encodeURIComponent(guildId)}">Open moderation page &#8599;</a></aside>`;
  }
  const reports = activityData?.reports || [];
  const actions = activityData?.recent_actions || [];
  const reportRows = reports.slice(0, 4).map((report) => {
    const time = new Date(report.created_at);
    return `<a class="activity-entry" href="${esc(report.url)}" target="_blank" rel="noreferrer"><span class="activity-entry-top"><b>Report &#183; ${esc(report.target_name)}</b><i class="activity-status ${report.status === "closed" ? "is-closed" : report.status === "claimed" ? "is-claimed" : ""}">${report.status.toUpperCase()}</i></span><span class="activity-entry-summary">${esc(report.reason || "No reason provided")}</span><small>${Number.isNaN(time.getTime()) ? "Recent" : esc(time.toLocaleString())}</small></a>`;
  }).join("");
  const actionRows = actions.slice(0, 5).map((action) => {
    const time = new Date(action.created_at);
    return `<div class="activity-entry activity-action-entry"><span class="activity-entry-top"><b>${esc(action.case_type)} &#183; ${esc(action.target_name)}</b><i class="activity-case-number">#${esc(action.case_number)}</i></span><span class="activity-entry-summary">${esc(action.reason)}</span><small>${esc(action.moderator_name)} &#183; ${Number.isNaN(time.getTime()) ? "Recent" : esc(time.toLocaleString())}</small></div>`;
  }).join("");
  return `<aside class="control-activity-panel"><div class="activity-panel-head"><div><div class="eyebrow">SERVER ACTIVITY</div><h2>Recent reports &amp; actions</h2></div><a class="activity-panel-link" href="/moderate?guild_id=${encodeURIComponent(guildId)}">View moderation page &#8599;</a></div><section class="activity-panel-section"><h3>Reports</h3>${reportRows || `<p class="activity-panel-empty">${activityData?.reports_configured ? "No reports found in the configured channel." : "No reports channel configured."}</p>`}</section><section class="activity-panel-section"><h3>Moderation actions</h3>${actionRows || `<p class="activity-panel-empty">No moderation actions recorded yet.</p>`}</section></aside>`;
}

async function save(moduleKey, key, value) {
  await api(`/api/control-panel/${encodeURIComponent(guildId)}/setting`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ module: moduleKey, key, value }) });
  const module = panel.modules.find((item) => item.key === moduleKey);
  const setting = module?.settings.find((item) => item.key === key);
  if (moduleKey === "moderation" && /^punishment_role_\d+$/.test(key)) {
    try {
      // The API returns configured role slots plus the next empty slot, so
      // saving a role reveals the next "add another" selector immediately.
      panel = await api(`/api/control-panel/${encodeURIComponent(guildId)}`);
    } catch (error) {
      if (setting) setting.value = value;
      console.warn("Could not refresh punishment role slots", error);
    }
  } else if (setting) {
    setting.value = value;
  }
  dirtySettings.delete(`${moduleKey}|${key}`);
  renderCards();
  document.querySelectorAll("[data-save-all]").forEach((button) => { button.disabled = ![...dirtySettings].some((item) => item.startsWith(`${selectedModule}|`)); });
  const notice = document.querySelector("#save-notice");
  notice.textContent = "Saved to Observer";
  notice.classList.add("is-visible");
  setTimeout(() => notice.classList.remove("is-visible"), 2200);
}

function render() {
  const guild = panel.guild;
  root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">CONTROL PANEL</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="control-panel"><a class="back-link" href="/?dashboard=1">&#8592; All servers</a><header class="control-header">${guild.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(guild.id)}/${encodeURIComponent(guild.icon)}.png?size=96" alt="">` : `<span class="control-server-fallback">${esc(guild.name.slice(0, 1).toUpperCase())}</span>`}<div><div class="eyebrow">OBSERVER CONTROL PANEL</div><h1>${esc(guild.name)}</h1><p>Browse and configure features for this server.</p></div></header><div class="control-content-layout"><section class="control-feature-area"><a class="custom-embed-launch-card" href="/embeds?guild_id=${encodeURIComponent(guild.id)}"><span class="custom-embed-launch-mark" aria-hidden="true">&#9993;</span><span class="custom-embed-launch-copy"><span class="eyebrow">SERVER TOOLS</span><strong>Custom message sender</strong><small>Create an embed (a rich Discord message with a title, text, and image) or a clean card (a simple layout with text and optional buttons).</small></span><span class="custom-embed-launch-arrow" aria-hidden="true">&#8599;</span></a><section class="getting-started"><div><div class="eyebrow">FIRST TIME HERE?</div><h2>Get started</h2><p>Follow these steps to set up Observer for your server.</p></div><ol><li><button data-setup-module="report_msg" data-setup-setting="staff_role">Select a staff role</button></li><li><button data-setup-module="report_msg" data-setup-setting="report_channel">Choose a reports channel</button></li><li><button data-setup-module="moderation">Review and enable the protections you want</button></li><li><button data-setup-module="report_msg" data-setup-setting="report_channel">Check permissions for the reports channel</button><small>Observer needs permission to view and send messages in that channel.</small></li></ol></section><div class="control-toolbar"><div><h2>Features</h2><p>${panel.modules.length} features and feature groups</p></div><input id="module-search" type="search" placeholder="Search features&#8230;" autocomplete="off"></div><div id="module-grid" class="module-grid"></div><section id="module-editor" class="module-editor" hidden></section><div id="save-notice" class="save-notice" role="status"></div></section>${activityPanel()}</div></main></div>`;
  renderCards();
  document.querySelector("#module-search").addEventListener("input", renderCards);
  document.querySelector("#signout").addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  root.addEventListener("click", async (event) => {
    const card = event.target.closest("[data-open-module]");
    if (card && !event.target.closest("button, input, label, a, textarea, select")) { renderEditor(card.dataset.openModule); return; }
    const setup = event.target.closest("[data-setup-module]");
    if (setup) {
      renderEditor(setup.dataset.setupModule);
      const setting = setup.dataset.setupSetting;
      if (setting) setTimeout(() => document.querySelector(`[data-setting-value="${setup.dataset.setupModule}|${setting}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 30);
      return;
    }
    const config = event.target.closest("[data-config-module]");
    if (config) { renderEditor(config.dataset.configModule); return; }
    if (event.target.closest("[data-new-response]")) {
      const form = document.querySelector("#auto-response-form");
      form.reset();
      form.elements.namedItem("id").value = "";
      document.querySelector("#auto-response-form-title").textContent = "Create auto response";
      document.querySelector("[data-cancel-response]").hidden = true;
      form.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const editResponse = event.target.closest("[data-edit-response]");
    if (editResponse) {
      const response = panel.modules.find((item) => item.key === "auto_responder")?.responses?.find((item) => String(item.id) === editResponse.dataset.editResponse);
      if (!response) return;
      const form = document.querySelector("#auto-response-form");
      form.elements.namedItem("id").value = response.id;
      form.elements.namedItem("responder_name").value = response.responder_name;
      form.elements.namedItem("triggers").value = response.triggers;
      form.elements.namedItem("sub_triggers").value = response.sub_triggers;
      form.elements.namedItem("reply").value = response.reply;
      form.elements.namedItem("style").value = response.style;
      form.elements.namedItem("cooldown_seconds").value = response.cooldown_seconds;
      document.querySelector("#auto-response-form-title").textContent = `Edit ${response.responder_name}`;
      document.querySelector("[data-cancel-response]").hidden = false;
      form.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (event.target.closest("[data-cancel-response]")) {
      const form = document.querySelector("#auto-response-form");
      form.reset();
      form.elements.namedItem("id").value = "";
      document.querySelector("#auto-response-form-title").textContent = "Create auto response";
      document.querySelector("[data-cancel-response]").hidden = true;
      return;
    }
    const deleteResponse = event.target.closest("[data-delete-response]");
    if (deleteResponse) {
      if (!confirm("Delete this auto response?")) return;
      try {
        const result = await api(`/api/control-panel/${encodeURIComponent(guildId)}/auto-responses/${encodeURIComponent(deleteResponse.dataset.deleteResponse)}`, { method: "DELETE" });
        panel.modules.find((item) => item.key === "auto_responder").responses = result.responses;
        renderCards();
        renderEditor("auto_responder", false, true);
      } catch (error) { alert(error.message); }
      return;
    }
    if (event.target.closest("#close-module-editor") || event.target.id === "module-editor") { closeEditor(); return; }
    if (event.target.closest("[data-save-all]")) {
      const pending = [...dirtySettings].filter((item) => item.startsWith(`${selectedModule}|`));
      for (const item of pending) {
        const [moduleKey, key] = item.split("|");
        const setting = panel.modules.find((entry) => entry.key === moduleKey)?.settings.find((entry) => entry.key === key);
        if (setting?.kind === "ticket_panel") {
          try { await save(moduleKey, key, readTicketPanelFields()); } catch (error) { alert(error.message); break; }
          continue;
        }
        const input = document.querySelector(`[data-setting-value="${moduleKey}|${key}"]`);
        const pickerSave = document.querySelector(`[data-save-setting="${moduleKey}|${key}"]`);
        if (!setting || !input) continue;
        if (pickerSave?.disabled) { alert("Choose a valid role or channel before saving."); break; }
        const value = setting.kind === "integer" ? Number(input.value) : input.value;
        try { await save(moduleKey, key, value); } catch (error) { alert(error.message); break; }
      }
      return;
    }
    const saveButton = event.target.closest("[data-save-setting]");
    if (saveButton) {
      const [moduleKey, key] = saveButton.dataset.saveSetting.split("|");
      const input = document.querySelector(`[data-setting-value="${moduleKey}|${key}"]`);
      const setting = panel.modules.find((item) => item.key === moduleKey).settings.find((item) => item.key === key);
      const value = setting.kind === "integer" ? Number(input.value) : input.value;
      saveButton.disabled = true;
      try { await save(moduleKey, key, value); } catch (error) { alert(error.message); saveButton.disabled = false; }
      return;
    }
    const ticket = event.target.closest("[data-save-ticket]");
    if (ticket) {
      const [moduleKey, key] = ticket.dataset.saveTicket.split("|");
      const value = readTicketPanelFields();
      try { await save(moduleKey, key, value); } catch (error) { alert(error.message); }
      return;
    }
    const sendTicketPanel = event.target.closest("[data-send-ticket-panel]");
    if (sendTicketPanel) {
      const [moduleKey, key] = sendTicketPanel.dataset.sendTicketPanel.split("|");
      sendTicketPanel.disabled = true;
      try {
        await save(moduleKey, key, readTicketPanelFields());
        const result = await api(`/api/control-panel/${encodeURIComponent(guildId)}/ticket-panel`, { method: "POST" });
        const notice = document.querySelector("#save-notice");
        notice.textContent = `Ticket panel sent to #${result.channel_name}`;
        notice.classList.add("is-visible");
        setTimeout(() => notice.classList.remove("is-visible"), 2600);
      } catch (error) {
        alert(error.message);
      } finally {
        const currentButton = document.querySelector("[data-send-ticket-panel]");
        if (currentButton) currentButton.disabled = false;
      }
    }
  });
  root.addEventListener("submit", async (event) => {
    const form = event.target.closest("#auto-response-form");
    if (!form) return;
    event.preventDefault();
    const lines = (value) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    const payload = {
      responder_name: form.elements.namedItem("responder_name").value.trim(),
      triggers: lines(form.elements.namedItem("triggers").value),
      sub_triggers: lines(form.elements.namedItem("sub_triggers").value),
      reply: form.elements.namedItem("reply").value.trim(),
      style: form.elements.namedItem("style").value,
      cooldown_seconds: Number(form.elements.namedItem("cooldown_seconds").value),
    };
    const id = form.elements.namedItem("id").value;
    if (id) payload.id = Number(id);
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const result = await api(`/api/control-panel/${encodeURIComponent(guildId)}/auto-responses`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      panel.modules.find((item) => item.key === "auto_responder").responses = result.responses;
      renderCards();
      renderEditor("auto_responder", false, true);
      const current = document.querySelector("#auto-response-form");
      current.reset();
      current.elements.namedItem("id").value = "";
      document.querySelector("#auto-response-form-title").textContent = "Create auto response";
      document.querySelector("[data-cancel-response]").hidden = true;
    } catch (error) { alert(error.message); }
    finally { const currentButton = document.querySelector('#auto-response-form [type="submit"]'); if (currentButton) currentButton.disabled = false; }
  });
  root.addEventListener("change", async (event) => {
    const toggle = event.target.closest("[data-toggle-setting]");
    if (toggle) {
      const [moduleKey, key] = toggle.dataset.toggleSetting.split("|");
      toggle.disabled = true;
      try { await save(moduleKey, key, toggle.checked); } catch (error) { toggle.checked = !toggle.checked; alert(error.message); toggle.disabled = false; }
      const label = toggle.closest(".web-toggle")?.querySelector("b");
      if (label) label.textContent = key === "enabled" ? (toggle.checked ? "Enabled" : "Disabled") : (toggle.checked ? "On" : "Off");
      return;
    }
    const picker = event.target.closest("[data-picker-input]");
    if (picker) {
      syncPicker(picker);
      const saveButton = picker.closest(".searchable-picker")?.querySelector("[data-save-setting]");
      if (saveButton) { const [moduleKey, key] = saveButton.dataset.saveSetting.split("|"); dirtySettings.add(`${moduleKey}|${key}`); document.querySelectorAll("[data-save-all]").forEach((button) => { button.disabled = false; }); }
    }
  });
  root.addEventListener("input", (event) => {
    const settingField = event.target.closest("[data-setting-value]");
    if (settingField) {
      const [moduleKey, key] = settingField.dataset.settingValue.split("|");
      dirtySettings.add(`${moduleKey}|${key}`);
      document.querySelectorAll("[data-save-all]").forEach((button) => { button.disabled = false; });
    }
    const ticketField = event.target.closest("[data-ticket-field]");
    if (ticketField) {
      const ticket = ticketField.closest(".ticket-panel-fields")?.querySelector("[data-save-ticket]")?.dataset.saveTicket;
      if (ticket) { dirtySettings.add(ticket); document.querySelectorAll("[data-save-all]").forEach((button) => { button.disabled = false; }); }
    }
    const picker = event.target.closest("[data-picker-input]");
    if (picker) {
      syncPicker(picker);
      const saveButton = picker.closest(".searchable-picker")?.querySelector("[data-save-setting]");
      if (saveButton) { const [moduleKey, key] = saveButton.dataset.saveSetting.split("|"); dirtySettings.add(`${moduleKey}|${key}`); document.querySelectorAll("[data-save-all]").forEach((button) => { button.disabled = false; }); }
    }
  });
  root.addEventListener("keydown", (event) => {
    const card = event.target.closest("[data-open-module]");
    if (card && event.target === card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      renderEditor(card.dataset.openModule);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeEditor();
  });
}

function readTicketPanelFields() {
  return {
    panel_title: document.querySelector('[data-ticket-field="panel_title"]').value,
    panel_description: document.querySelector('[data-ticket-field="panel_description"]').value,
    panel_footer: document.querySelector('[data-ticket-field="panel_footer"]').value,
    reason_options: document.querySelector('[data-ticket-field="reason_options"]').value.split("\n").map((item) => item.trim()).filter(Boolean),
  };
}

function syncPicker(input) {
  const wrapper = input.closest(".searchable-picker");
  const options = document.getElementById(input.getAttribute("list"));
  const match = [...(options?.options || [])].find((option) => option.value === input.value);
  const hidden = wrapper.querySelector("[data-setting-value]");
  const saveButton = wrapper.querySelector(".save-setting");
  if (!match) { saveButton.disabled = true; return; }
  hidden.value = match.dataset.id || "";
  saveButton.disabled = false;
}

async function start() {
  try {
    if (!guildId) { location.replace("/"); return; }
    const results = await Promise.allSettled([
      api(`/api/control-panel/${encodeURIComponent(guildId)}`),
      api(`/api/mod-dashboard/${encodeURIComponent(guildId)}`),
    ]);
    if (results[0].status === "rejected") throw results[0].reason;
    panel = results[0].value;
    activityData = results[1].status === "fulfilled" ? results[1].value : { error: results[1].reason.message };
    render();
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Could not load the control panel.</h1><p>${esc(error.message)}</p><a class="back-link" href="/">Return to servers</a></main>`;
  }
}

start();

