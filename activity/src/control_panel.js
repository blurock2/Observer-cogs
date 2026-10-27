const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const guildId = new URLSearchParams(location.search).get("guild_id");
let panel;
let selectedModule = null;

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function brand() { return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png" alt=""></span><span>OBSERVER</span></a>`; }

function renderCards() {
  const query = (document.querySelector("#module-search")?.value || "").trim().toLowerCase();
  const modules = panel.modules.filter((item) => `${item.label} ${item.description}`.toLowerCase().includes(query));
  document.querySelector("#module-grid").innerHTML = modules.map((module) => {
    const enabled = module.settings.find((setting) => setting.key === "enabled");
    const state = enabled ? (enabled.value ? "Enabled" : "Disabled") : "Configure options";
    return `<article class="control-module"><div class="module-card-top"><span class="module-emoji">${esc(module.emoji)}</span><span class="module-count">${module.settings.length} SETTINGS</span></div><h2>${esc(module.label)}</h2><p>${esc(module.description)}</p><div class="module-card-actions">${enabled ? `<label class="web-toggle"><input type="checkbox" data-toggle-setting="${module.key}|enabled" ${enabled.value ? "checked" : ""}><span></span><b>${state}</b></label>` : `<span class="module-state">${state}</span>`}<button class="configure-module" data-config-module="${module.key}">Advanced menu <span>→</span></button></div></article>`;
  }).join("") || `<p class="empty-settings">No features match that search.</p>`;
}

function controlFor(module, setting) {
  const id = `setting-${module.key}-${setting.key}`;
  const value = setting.value;
  const save = `<button class="save-setting" data-save-setting="${module.key}|${setting.key}">Save</button>`;
  let field;
  if (setting.kind === "toggle") {
    field = `<label class="web-toggle"><input type="checkbox" data-toggle-setting="${module.key}|${setting.key}" ${value ? "checked" : ""}><span></span><b>${value ? "On" : "Off"}</b></label>`;
  } else if (setting.kind === "channel" || setting.kind === "role") {
    const isChannel = setting.kind === "channel";
    const options = isChannel ? panel.channels : panel.roles;
    const chosen = value == null ? "" : String(value).replace(/\D/g, "");
    const displayOption = (option) => {
      const prefix = isChannel && option.type !== "category" ? "#" : "";
      const kind = isChannel ? option.type.replaceAll("_", " ") : (option.is_default ? "default role" : "role");
      return `${prefix}${option.name} · ${kind}`;
    };
    const selected = options.find((option) => String(option.id) === chosen);
    const optionHtml = options.map((option) => `<option value="${esc(displayOption(option))}" data-id="${esc(option.id)}"></option>`).join("");
    const inputValue = selected ? displayOption(selected) : (chosen ? `Unavailable ${isChannel ? "channel" : "role"} (${chosen})` : "Not set");
    field = `<div class="searchable-picker"><input id="${id}" type="search" list="${id}-options" data-picker-input autocomplete="off" placeholder="Type to search ${isChannel ? "channels" : "roles"}…" value="${esc(inputValue)}"><datalist id="${id}-options"><option value="Not set" data-id=""></option>${optionHtml}</datalist><input type="hidden" data-setting-value="${module.key}|${setting.key}" value="${esc(chosen)}">${save}</div>`;
  } else if (setting.kind === "ticket_panel") {
    const panelSettings = value || {};
    field = `<div class="ticket-panel-fields"><label>Panel title<input data-ticket-field="panel_title" maxlength="256" value="${esc(panelSettings.panel_title || "")}"></label><label>Panel description<textarea data-ticket-field="panel_description" maxlength="2048">${esc(panelSettings.panel_description || "")}</textarea></label><label>Panel footer<textarea data-ticket-field="panel_footer" maxlength="2048">${esc(panelSettings.panel_footer || "")}</textarea></label><label>Ticket reasons <small>One per line; up to 24.</small><textarea data-ticket-field="reason_options">${esc((panelSettings.reason_options || []).join("\n"))}</textarea></label><button class="save-setting" data-save-ticket="${module.key}|${setting.key}">Save ticket panel</button></div>`;
  } else {
    const text = value == null ? "" : String(value);
    const input = setting.kind === "integer"
      ? `<input id="${id}" type="number" min="0" max="1000000" step="1" data-setting-value="${module.key}|${setting.key}" value="${esc(text)}">`
      : (/message|description|filter/i.test(setting.key) || text.length > 120
        ? `<textarea id="${id}" maxlength="4000" data-setting-value="${module.key}|${setting.key}">${esc(text)}</textarea>`
        : `<input id="${id}" type="text" maxlength="4000" data-setting-value="${module.key}|${setting.key}" value="${esc(text)}">`);
    field = `<div class="setting-control">${input}${save}</div>`;
  }
  const warning = /experimental|permanently|ban automatically/i.test(`${setting.label} ${setting.description}`) ? " setting-warning" : "";
  return `<section class="setting-row${warning}"><div class="setting-copy"><label for="${id}">${esc(setting.label)}</label>${setting.description ? `<small>${esc(setting.description)}</small>` : ""}</div><div class="setting-widget">${field}</div></section>`;
}

function renderEditor(key) {
  selectedModule = key;
  const module = panel.modules.find((item) => item.key === key);
  const editor = document.querySelector("#module-editor");
  const enabled = module.settings.find((setting) => setting.key === "enabled");
  editor.hidden = false;
  editor.innerHTML = `<div class="editor-head"><div><div class="eyebrow">ADVANCED SETTINGS</div><h2>${esc(module.emoji)} ${esc(module.label)}</h2><p>${esc(module.description)}</p></div><button id="close-module-editor" class="editor-close" aria-label="Close settings">×</button></div><div class="editor-enable">${enabled ? `<span>${esc(enabled.label)}</span><label class="web-toggle"><input type="checkbox" data-toggle-setting="${module.key}|enabled" ${enabled.value ? "checked" : ""}><span></span><b>${enabled.value ? "Enabled" : "Disabled"}</b></label>` : `<span>This cog has no master switch. Configure its options below.</span>`}</div><div class="settings-list">${module.settings.map((setting) => controlFor(module, setting)).join("") || `<p class="empty-settings">No web settings are defined for this cog yet.</p>`}</div>`;
  editor.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function save(moduleKey, key, value) {
  await api(`/api/control-panel/${encodeURIComponent(guildId)}/setting`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ module: moduleKey, key, value }) });
  const module = panel.modules.find((item) => item.key === moduleKey);
  const setting = module?.settings.find((item) => item.key === key);
  if (setting) setting.value = value;
  renderCards();
  if (selectedModule) renderEditor(selectedModule);
  const notice = document.querySelector("#save-notice");
  notice.textContent = "Saved to Observer";
  notice.classList.add("is-visible");
  setTimeout(() => notice.classList.remove("is-visible"), 2200);
}

function render() {
  const guild = panel.guild;
  root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">CONTROL PANEL</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="control-panel"><a class="back-link" href="/?dashboard=1">← All servers</a><header class="control-header">${guild.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(guild.id)}/${encodeURIComponent(guild.icon)}.png?size=96" alt="">` : `<span class="control-server-fallback">${esc(guild.name.slice(0, 1).toUpperCase())}</span>`}<div><div class="eyebrow">OBSERVER CONTROL PANEL</div><h1>${esc(guild.name)}</h1><p>Browse each cog, see what it does, and configure server options.</p></div><span class="bot-online-pill"><i></i> BOT CONNECTED</span></header><div class="control-toolbar"><div><h2>Features</h2><p>${panel.modules.length} cogs and feature groups</p></div><input id="module-search" type="search" placeholder="Search cogs and features…" autocomplete="off"></div><div id="module-grid" class="module-grid"></div><section id="module-editor" class="module-editor" hidden></section><div id="save-notice" class="save-notice" role="status"></div></main></div>`;
  renderCards();
  document.querySelector("#module-search").addEventListener("input", renderCards);
  document.querySelector("#signout").addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  root.addEventListener("click", async (event) => {
    const config = event.target.closest("[data-config-module]");
    if (config) { renderEditor(config.dataset.configModule); return; }
    if (event.target.closest("#close-module-editor")) { document.querySelector("#module-editor").hidden = true; selectedModule = null; return; }
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
      const value = { panel_title: document.querySelector('[data-ticket-field="panel_title"]').value, panel_description: document.querySelector('[data-ticket-field="panel_description"]').value, panel_footer: document.querySelector('[data-ticket-field="panel_footer"]').value, reason_options: document.querySelector('[data-ticket-field="reason_options"]').value.split("\n").map((item) => item.trim()).filter(Boolean) };
      try { await save(moduleKey, key, value); } catch (error) { alert(error.message); }
    }
  });
  root.addEventListener("change", async (event) => {
    const toggle = event.target.closest("[data-toggle-setting]");
    if (toggle) {
      const [moduleKey, key] = toggle.dataset.toggleSetting.split("|");
      toggle.disabled = true;
      try { await save(moduleKey, key, toggle.checked); } catch (error) { toggle.checked = !toggle.checked; alert(error.message); toggle.disabled = false; }
      return;
    }
    const picker = event.target.closest("[data-picker-input]");
    if (picker) syncPicker(picker);
  });
  root.addEventListener("input", (event) => {
    const picker = event.target.closest("[data-picker-input]");
    if (picker) syncPicker(picker);
  });
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
    panel = await api(`/api/control-panel/${encodeURIComponent(guildId)}`);
    render();
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Could not load the control panel.</h1><p>${esc(error.message)}</p><a class="back-link" href="/">Return to servers</a></main>`;
  }
}

start();
