import "./style.css";
import christmasNativityAsset from "./assets/christmas-nativity.svg";

const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const loginUrl = "/auth/login";

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function discordIcon() {
  return `<svg aria-hidden="true" viewBox="0 0 24 24"><path fill="currentColor" d="M19.7 5.3a18.3 18.3 0 0 0-4.5-1.4l-.6 1.2a16.8 16.8 0 0 0-5.2 0l-.6-1.2a18.3 18.3 0 0 0-4.5 1.4C1.5 9.5.7 13.6 1.1 17.7a18.5 18.5 0 0 0 5.5 2.8l1.2-2a11.8 11.8 0 0 1-1.9-.9l.5-.4a13.1 13.1 0 0 0 11.2 0l.5.4a11.8 11.8 0 0 1-1.9.9l1.2 2a18.5 18.5 0 0 0 5.5-2.8c.5-4.7-.8-8.8-3.2-12.4ZM8.7 14.8c-1.1 0-2-1-2-2.2s.9-2.2 2-2.2 2 1 2 2.2-.9 2.2-2 2.2Zm6.6 0c-1.1 0-2-1-2-2.2s.9-2.2 2-2.2 2 1 2 2.2-.9 2.2-2 2.2Z"/></svg>`;
}

function brand() { return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></a>`; }
function sharedFooter() {
  return `<footer class="site-footer shared-site-footer"><div class="footer-brand">${brand()}<small>&#169; ${new Date().getFullYear()} Blurock2 / Fedja Podobnik.</small></div><div class="shared-footer-main"><nav class="footer-links" aria-label="Footer links"><a href="https://github.com/blurock2/Observer-cogs" target="_blank" rel="noreferrer">Source code</a><a href="https://my-website.rockus26.workers.dev/#projects" target="_blank" rel="noreferrer">Creator portfolio</a><a href="https://discord.gg/tccYb2XSxR" target="_blank" rel="noreferrer">Support server</a><a href="/privacy">Privacy Policy</a><a href="/terms">Terms of Service</a></nav><div class="footer-meta"><span class="footer-disclaimer">Not affiliated with Discord Inc.</span><a class="footer-top" href="#top" onclick="window.scrollTo({top:0,behavior:'smooth'});return false">BACK TO TOP &#8593;</a></div></div></footer>`;
}
function ensureSiteFooter() {
  ensureThemeControl();
  if (!root.firstElementChild || root.querySelector(".shared-site-footer")) return;
  const oldFooter = root.querySelector(".site-footer, .site-policy-footer");
  if (oldFooter) oldFooter.outerHTML = sharedFooter();
  else root.insertAdjacentHTML("beforeend", sharedFooter());
}
new MutationObserver(ensureSiteFooter).observe(root, { childList: true });

async function refreshLiveStats() {
  const section = root.querySelector(".live-stats-section");
  if (!section) return;
  try {
    const stats = await api("/observer-stats.json");
    const count = (value) => Number(value || 0).toLocaleString();
    section.querySelector("#live-stat-status").textContent = stats.status || "Unavailable";
    section.querySelector("#live-stat-servers").textContent = count(stats.servers);
    section.querySelector("#live-stat-users").textContent = count(stats.users);
    section.querySelector("#live-stat-commands").textContent = count(stats.commands_run);
  } catch {
    // Keep the last known values visible if a refresh is temporarily unavailable.
  }
}
setInterval(refreshLiveStats, 60_000);

const liveStatusClickTimes = new WeakMap();

const liveThemeUnlockedKey = "observer-live-theme-unlocked";
const liveThemeChoiceKey = "observer-live-theme";
const liveThemeOwnerKey = "observer-live-theme-owner";
const liveThemeChristmasUnlockedKey = "observer-orthodox-christmas-unlocked";
const liveThemeAllUnlockedKey = "observer-all-themes-unlocked";
let themeMenuTimer = 0;
let themeAccountReady = false;
let themeAccountSignedIn = false;
let themeAccountUserId = "";
let accountAllThemesUnlocked = false;

function readLiveTheme() {
  let unlocked = false;
  let christmasUnlocked = false;
  let allThemesUnlocked = accountAllThemesUnlocked;
  let savedTheme = "observer";
  try {
    allThemesUnlocked = allThemesUnlocked || localStorage.getItem(liveThemeAllUnlockedKey) === "true";
    christmasUnlocked = localStorage.getItem(liveThemeChristmasUnlockedKey) === "true";
    unlocked = localStorage.getItem(liveThemeUnlockedKey) === "true" || christmasUnlocked || allThemesUnlocked;
    const stored = localStorage.getItem(liveThemeChoiceKey);
    savedTheme = stored === "christmas" && (christmasUnlocked || allThemesUnlocked) ? "christmas" : ["error", "red-alert"].includes(stored) ? "error" : stored && allThemesUnlocked ? stored : "observer";
  } catch { /* Theme preference is optional when browser storage is unavailable. */ }
  return { unlocked, christmasUnlocked: christmasUnlocked || allThemesUnlocked, allThemesUnlocked, theme: savedTheme };
}

function isOrthodoxChristmasDate() {
  const now = new Date();
  return now.getMonth() === 0 && now.getDate() === 7;
}

function closeThemeMenu() {
  window.clearTimeout(themeMenuTimer);
  const control = root.querySelector("#site-theme-control");
  const toggle = root.querySelector("#site-theme-toggle");
  const menu = root.querySelector("#site-theme-menu");
  control?.classList.remove("is-open");
  toggle?.setAttribute("aria-expanded", "false");
  if (menu) {
    menu.setAttribute("aria-hidden", "true");
    menu.inert = true;
  }
}

function openThemeMenu() {
  const control = root.querySelector("#site-theme-control");
  const toggle = root.querySelector("#site-theme-toggle");
  const menu = root.querySelector("#site-theme-menu");
  if (!control || !toggle || !menu) return;
  control.classList.add("is-open");
  toggle.setAttribute("aria-expanded", "true");
  menu.setAttribute("aria-hidden", "false");
  menu.inert = false;
  window.clearTimeout(themeMenuTimer);
  themeMenuTimer = window.setTimeout(closeThemeMenu, 10_000);
}

function applyLiveTheme(theme, persist = true) {
  const { unlocked, christmasUnlocked, allThemesUnlocked } = readLiveTheme();
  const selected = theme === "observer" ? "observer"
    : theme === "christmas" && christmasUnlocked ? "christmas"
      : ["error", "red-alert"].includes(theme) && unlocked ? "error"
        : typeof theme === "string" && allThemesUnlocked ? theme : "observer";
  document.documentElement.dataset.observerTheme = selected;
  const siteShell = root.querySelector(".site-shell");
  if (siteShell) siteShell.dataset.theme = selected === "error" ? "red-alert" : selected;
  root.querySelectorAll("[data-site-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.siteThemeChoice === selected));
  });
  try { localStorage.setItem(liveThemeChoiceKey, selected); } catch { /* Keep the selection for this page view. */ }
  if (persist) saveAccountThemePreference(selected, readLiveTheme().unlocked);
}

async function saveAccountThemePreference(theme, unlocked) {
  if (!themeAccountReady || !themeAccountSignedIn || !unlocked) return;
  try {
    if (!themeAccountUserId) return;
    localStorage.setItem(liveThemeOwnerKey, themeAccountUserId);
    await api("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme, theme_unlocked: true, christmas_unlocked: readLiveTheme().christmasUnlocked }),
    });
  } catch { /* The local choice remains available if account sync is temporarily offline. */ }
}

async function syncAccountThemePreference() {
  if (isOrthodoxChristmasDate()) unlockOrthodoxChristmasTheme();
  document.documentElement.dataset.observerTheme = readLiveTheme().theme;
  try {
    const session = await api("/api/session");
    if (!session.signed_in || !session.user?.id) {
      accountAllThemesUnlocked = false;
      try { localStorage.removeItem(liveThemeAllUnlockedKey); } catch { /* Owner access is account scoped. */ }
      themeAccountReady = true;
      ensureThemeControl();
      return;
    }
    const userId = String(session.user.id);
    const preferences = await api("/api/preferences");
    const allThemesUnlocked = preferences.all_themes_unlocked === true;
    accountAllThemesUnlocked = allThemesUnlocked;
    if (allThemesUnlocked) localStorage.setItem(liveThemeAllUnlockedKey, "true");
    else localStorage.removeItem(liveThemeAllUnlockedKey);
    const local = readLiveTheme();
    themeAccountUserId = userId;
    themeAccountSignedIn = true;
    themeAccountReady = true;
    const localOwner = localStorage.getItem(liveThemeOwnerKey);
    if (preferences.exists) {
      const localBelongsToAccount = !localOwner || localOwner === userId;
      const christmasUnlocked = allThemesUnlocked || isOrthodoxChristmasDate() || preferences.christmas_unlocked || (localBelongsToAccount && local.christmasUnlocked);
      const selectedTheme = isOrthodoxChristmasDate() ? "christmas" : preferences.theme === "christmas" && christmasUnlocked ? "christmas" : preferences.theme === "error" ? "error" : preferences.theme && allThemesUnlocked ? preferences.theme : "observer";
      localStorage.setItem(liveThemeOwnerKey, userId);
      localStorage.setItem(liveThemeChoiceKey, selectedTheme);
      if ((preferences.theme_unlocked || christmasUnlocked) && !allThemesUnlocked) localStorage.setItem(liveThemeUnlockedKey, "true");
      else localStorage.removeItem(liveThemeUnlockedKey);
      if (christmasUnlocked && !allThemesUnlocked) localStorage.setItem(liveThemeChristmasUnlockedKey, "true");
      else localStorage.removeItem(liveThemeChristmasUnlockedKey);
      applyLiveTheme(selectedTheme, false);
      ensureThemeControl();
      if (allThemesUnlocked || isOrthodoxChristmasDate() || (localBelongsToAccount && local.christmasUnlocked && !preferences.christmas_unlocked)) {
        await saveAccountThemePreference(selectedTheme, true);
      }
    } else if (!localOwner || localOwner === userId) {
      if (local.unlocked) {
        localStorage.setItem(liveThemeOwnerKey, userId);
        await saveAccountThemePreference(local.theme, true);
      }
      applyLiveTheme(local.theme, false);
      ensureThemeControl();
    } else {
      localStorage.setItem(liveThemeOwnerKey, userId);
      if (allThemesUnlocked || isOrthodoxChristmasDate()) {
        localStorage.setItem(liveThemeChoiceKey, "christmas");
        if (isOrthodoxChristmasDate()) {
          localStorage.setItem(liveThemeChristmasUnlockedKey, "true");
          localStorage.setItem(liveThemeUnlockedKey, "true");
          applyLiveTheme("christmas", false);
          await saveAccountThemePreference("christmas", true);
        } else {
          localStorage.setItem(liveThemeChoiceKey, "observer");
          localStorage.setItem(liveThemeOwnerKey, userId);
          applyLiveTheme("observer", false);
          await saveAccountThemePreference("observer", true);
        }
      } else {
        localStorage.setItem(liveThemeChoiceKey, "observer");
        localStorage.removeItem(liveThemeUnlockedKey);
        localStorage.removeItem(liveThemeChristmasUnlockedKey);
        applyLiveTheme("observer", false);
      }
      ensureThemeControl();
    }
  } catch {
    accountAllThemesUnlocked = false;
    try { localStorage.removeItem(liveThemeAllUnlockedKey); } catch { /* Account-only access requires a live session. */ }
    themeAccountReady = true;
    ensureThemeControl();
  }
}

function ensureThemeControl({ open = false } = {}) {
  const { unlocked, christmasUnlocked, theme } = readLiveTheme();
  document.documentElement.dataset.observerTheme = theme;
  let control = root.querySelector("#site-theme-control");
  if (!unlocked) {
    control?.remove();
    return;
  }
  if (!control) {
    root.insertAdjacentHTML("beforeend", `<aside class="site-theme-control" id="site-theme-control"><button type="button" class="site-theme-toggle" id="site-theme-toggle" aria-expanded="false" aria-controls="site-theme-menu">Themes <span aria-hidden="true">✦</span></button><div class="site-theme-menu" id="site-theme-menu" role="group" aria-label="Choose website theme" aria-hidden="true" inert><div class="site-theme-menu-heading"><div class="eyebrow">EASTER EGG UNLOCKED</div><strong>Choose a theme</strong></div><button type="button" class="site-theme-option" data-site-theme-choice="observer" aria-pressed="false"><span>Observer theme</span><small>Black &amp; cyan</small></button><button type="button" class="site-theme-option" data-site-theme-choice="error" aria-pressed="false"><span>Error theme</span><small>Dark red, red &amp; black</small></button></div></aside>`);
    if (christmasUnlocked) {
      root.querySelector("#site-theme-menu")?.insertAdjacentHTML("beforeend", `<button type="button" class="site-theme-option" data-site-theme-choice="christmas" aria-pressed="false"><span>Orthodox Christmas</span><small>Evergreen, cranberry &amp; gold</small></button>`);
    }
    control = root.querySelector("#site-theme-control");
    const toggle = root.querySelector("#site-theme-toggle");
    toggle?.addEventListener("click", () => control.classList.contains("is-open") ? closeThemeMenu() : openThemeMenu());
    control?.querySelectorAll("[data-site-theme-choice]").forEach((button) => button.addEventListener("click", () => {
      applyLiveTheme(button.dataset.siteThemeChoice);
      closeThemeMenu();
    }));
    control?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { closeThemeMenu(); root.querySelector("#site-theme-toggle")?.focus(); }
    });
  }
  applyLiveTheme(theme, false);
  if (open) openThemeMenu();
}

function unlockLiveThemes() {
  if (readLiveTheme().unlocked) return;
  try { localStorage.setItem(liveThemeUnlockedKey, "true"); } catch { /* Unlock for this page view even without storage. */ }
  ensureThemeControl({ open: true });
  saveAccountThemePreference(readLiveTheme().theme, true);
}

function unlockOrthodoxChristmasTheme() {
  try {
    localStorage.setItem(liveThemeUnlockedKey, "true");
    localStorage.setItem(liveThemeChristmasUnlockedKey, "true");
    localStorage.setItem(liveThemeChoiceKey, "christmas");
  } catch { /* The seasonal unlock still applies for this page view. */ }
  applyLiveTheme("christmas", false);
  ensureThemeControl({ open: true });
  saveAccountThemePreference("christmas", true);
}

function activateWebsiteBreak() {
  const site = root.querySelector(".site-shell");
  if (!site) return;
  site.querySelector(".website-break-backend")?.remove();
  site.querySelector(".website-break-reset")?.remove();
  site.querySelectorAll(".easter-egg-fallen").forEach((card) => card.classList.remove("easter-egg-fallen"));

  const backend = document.createElement("div");
  backend.className = "website-break-backend";
  backend.setAttribute("aria-hidden", "true");
  backend.innerHTML = `<svg class="break-circuit-art" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice"><g><path d="M0 120h190v110h140v-70h190v170h170V90h140v180h180v-90h190M0 570h150V430h150v120h190V420h180v180h170V450h140v120h220M460 0v110m370 360v160m-210-70v220m330-510v-100m-650 300v-95"/><path d="M190 230v80h80m240 20v80h-90m340-150v100h90m350-130v100h-80M300 550v100h-120m570-50v100h100m310-130v80h100"/></g><g class="break-circuit-nodes"><circle cx="190" cy="230" r="5"/><circle cx="520" cy="330" r="5"/><circle cx="860" cy="90" r="5"/><circle cx="1010" cy="180" r="5"/><circle cx="300" cy="550" r="5"/><circle cx="670" cy="600" r="5"/><circle cx="990" cy="570" r="5"/></g></svg><div class="break-binary-field"></div><div class="break-terminal">OBSERVER // FRONTEND DETACHED // BACKEND SIGNAL VISIBLE</div>`;
  const binary = backend.querySelector(".break-binary-field");
  for (let index = 0; index < 28; index += 1) {
    const row = document.createElement("span");
    row.className = "break-binary-row";
    row.textContent = Array.from({ length: 52 }, () => Math.random() < 0.5 ? "0" : "1").join("");
    row.style.setProperty("--row-delay", `${Math.random() * -12}s`);
    row.style.setProperty("--row-speed", `${9 + Math.random() * 12}s`);
    binary.append(row);
  }

  const reset = document.createElement("button");
  reset.className = "website-break-reset";
  reset.type = "button";
  reset.textContent = "Reset website";
  reset.setAttribute("aria-label", "Reset the website after the simulated break");
  site.classList.add("is-website-broken");
  site.prepend(backend, reset);

  const candidates = [...site.querySelectorAll(".feature-card, .workflow-steps > article, .project-engineering-item, .support-card, .review-card")];
  for (let index = candidates.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(Math.random() * (index + 1));
    [candidates[index], candidates[swap]] = [candidates[swap], candidates[index]];
  }
  const maxDrops = Math.max(1, Math.min(5, candidates.length - 1));
  const dropCount = 1 + Math.floor(Math.random() * maxDrops);
  candidates.slice(0, dropCount).forEach((card, index) => {
    card.style.setProperty("--break-rotation", `${Math.round(Math.random() * 24 - 12)}deg`);
    card.style.setProperty("--break-delay", `${index * 110 + Math.random() * 180}ms`);
    card.classList.add("easter-egg-fallen");
  });

  const sparkAt = (x, y) => {
    const burst = document.createElement("span");
    burst.className = "website-break-spark-burst";
    burst.style.left = `${x}px`;
    burst.style.top = `${y}px`;
    for (let index = 0; index < 18; index += 1) {
      const spark = document.createElement("i");
      const angle = Math.random() * Math.PI * 2;
      const distance = 18 + Math.random() * 82;
      spark.style.setProperty("--spark-x", `${Math.cos(angle) * distance}px`);
      spark.style.setProperty("--spark-y", `${Math.sin(angle) * distance}px`);
      spark.style.setProperty("--spark-turn", `${Math.round(Math.random() * 240 - 120)}deg`);
      spark.style.setProperty("--spark-delay", `${Math.random() * 120}ms`);
      burst.append(spark);
    }
    document.body.append(burst);
    window.setTimeout(() => burst.remove(), 950);
  };
  const clickSparks = (event) => {
    if (event.target.closest(".website-break-reset, .site-theme-control")) return;
    sparkAt(event.clientX, event.clientY);
  };
  const resetWebsite = () => {
    site.removeEventListener("click", clickSparks, true);
    site.classList.remove("is-website-broken");
    site.querySelector(".website-break-backend")?.remove();
    reset.remove();
    site.querySelectorAll(".easter-egg-fallen").forEach((card) => {
      card.classList.remove("easter-egg-fallen");
      card.style.removeProperty("--break-rotation");
      card.style.removeProperty("--break-delay");
    });
  };
  reset.addEventListener("click", resetWebsite, { once: true });
  site.addEventListener("click", clickSparks, true);
}

function playLiveStatusEasterEgg(card) {
  if (card.dataset.glitching) return;
  const section = card.closest(".live-stats-section");
  if (!section) return;
  card.dataset.glitching = "true";
  card.classList.add("is-glitching");

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let effect = null;
  let reducedNote = null;
  if (reduceMotion) {
    reducedNote = document.createElement("span");
    reducedNote.className = "live-status-reduced-note";
    reducedNote.textContent = "SIMULATED ALERTS: ALL SYSTEMS OK";
    reducedNote.setAttribute("aria-hidden", "true");
    card.append(reducedNote);
  } else {
    effect = document.createElement("div");
    effect.className = "live-status-chaos";
    effect.setAttribute("aria-hidden", "true");
    const logMessages = [
      "ERROR: status daemon exceeded its fun quota",
      "WARN: spark service started unexpectedly",
      "TRACE: observer.exe is still very much online",
      "ERROR: seriousness.dll not found",
      "NOTICE: no actual errors detected",
      "WARN: vibes are outside normal parameters",
      "TRACE: spark.exe initialized successfully",
      "STATUS: just kidding - all systems are online",
    ];
    for (let index = 0; index < 16; index += 1) {
      const log = document.createElement("span");
      const code = Math.floor(10_000_000 + Math.random() * 90_000_000);
      log.className = `live-status-rain-log live-status-rain-log-${index % 3}`;
      log.textContent = `OBS-${code}  ${logMessages[index % logMessages.length]}`;
      log.style.left = `${Math.random() * 92}%`;
      log.style.setProperty("--rain-delay", `${Math.random() * 1.1}s`);
      log.style.setProperty("--rain-duration", `${2.2 + Math.random() * 1.1}s`);
      log.style.setProperty("--rain-drift", `${Math.round(Math.random() * 80 - 40)}px`);
      effect.append(log);
    }
    const sparkColors = ["#ff334f", "#f0442e", "#d94b16", "#ff6b35"];
    for (let index = 0; index < 48; index += 1) {
      const spark = document.createElement("span");
      const angle = Math.random() * Math.PI * 2;
      const distance = 75 + Math.random() * 205;
      spark.className = "live-status-spark";
      // Start across the face of the Online button, then burst away from it.
      spark.style.left = `${18 + Math.random() * 64}%`;
      spark.style.top = `${28 + Math.random() * 44}%`;
      spark.style.setProperty("--spark-color", sparkColors[index % sparkColors.length]);
      spark.style.setProperty("--spark-x", `${Math.round(Math.cos(angle) * distance)}px`);
      spark.style.setProperty("--spark-y", `${Math.round(Math.sin(angle) * distance)}px`);
      spark.style.setProperty("--spark-rotation", `${Math.round(angle * 180 / Math.PI)}deg`);
      spark.style.setProperty("--spark-delay", `${Math.random() * 0.2}s`);
      effect.append(spark);
    }
    card.append(effect);
  }

  window.setTimeout(() => {
    card.classList.remove("is-glitching");
    delete card.dataset.glitching;
    effect?.remove();
    reducedNote?.remove();
    unlockLiveThemes();
    activateWebsiteBreak();
  }, 4700);
}

function trackLiveStatusClick(card) {
  if (card.dataset.glitching) return;
  const now = performance.now();
  const recent = (liveStatusClickTimes.get(card) || []).filter((time) => now - time <= 2000);
  recent.push(now);
  liveStatusClickTimes.set(card, recent);
  if (recent.length >= 5) {
    liveStatusClickTimes.set(card, []);
    playLiveStatusEasterEgg(card);
  }
}

async function loadLanding() {
  const [session, publicInfo, liveStatsData] = await Promise.all([
    api("/api/session"),
    api("/api/public-info"),
    api("/observer-stats.json").catch(() => ({ status: "Unavailable", servers: 0, users: 0, commands_run: 0 })),
  ]);
  const liveStats = liveStatsData || { status: "Unavailable", servers: 0, users: 0, commands_run: 0 };
  const formatCount = (value) => Number(value || 0).toLocaleString();
  const profile = session.signed_in ? `<button class="nav-profile" id="open-dashboard">${session.user.avatar ? `<img src="https://cdn.discordapp.com/avatars/${encodeURIComponent(session.user.id)}/${encodeURIComponent(session.user.avatar)}.png?size=64" alt="">` : ""}<span>Manage servers</span></button>` : `<a class="nav-login" href="${loginUrl}">Sign in <span>&#8599;</span></a>`;
  const supportOptions = [
    { period: "ONE-TIME", themeClass: "one-time", name: "One-Time Supporter", price: "Any amount", roles: ["Supporter <3"], description: "A single contribution helps with Observer hosting, upkeep, and future improvements.", button: "Donate once on Ko-fi", benefits: ["A lasting thank-you for supporting Observer"] },
    { period: "TIER 1", themeClass: "tier-1", name: "Tier 1 - Cookie", price: "&euro;1.99 per month", roles: ["Monthly Supporter <3", "Tier 1 - Cookie :3"], description: "Your first monthly supporter tier with a special role and a place in the supporter community.", button: "Join Tier 1 on Ko-fi", url: "https://ko-fi.com/summary/a3ac6044-6b02-495c-849e-87d3a7861057", benefits: ["Custom supporters channel", "Added to the supporter wall"] },
    { period: "TIER 2", themeClass: "tier-2", name: "Tier 2 - Cutie", price: "&euro;4.99 per month", roles: ["Monthly Supporter <3", "Tier 2 - Cutie <3"], description: "Everything in Tier 1, plus access to selected supporter-only Observer commands.", button: "Join Tier 2 on Ko-fi", url: "https://ko-fi.com/summary/95a1d75d-18f9-4d07-9bff-da1e07e7563b", benefits: ["Custom supporters channel", "Added to the supporter wall", "Selected supporter-only bot commands"] },
    { period: "TIER 3", themeClass: "tier-3", name: "Tier 3 - Goated", price: "&euro;7.99 per month", roles: ["Monthly Supporter <3", "Tier 3 - Goated"], description: "The highest tier, with priority help and direct-message support for Observer questions.", button: "Join Tier 3 on Ko-fi", url: "https://ko-fi.com/summary/95423b6d-fbe5-47be-93e6-29f7117f62ca", benefits: ["Everything in the lower tiers", "Priority support and help", "Direct-message support for Observer questions"] },
  ];
  const supportCards = supportOptions.map((option, index) => `<article class="support-card support-card-${option.themeClass}"><div class="support-card-top"><span class="support-period">${esc(option.period)}</span><span class="support-symbol">${index === 0 ? "+" : index}</span></div><h3>${esc(option.name)}</h3><div class="support-price">${option.price}</div><p>${esc(option.description)}</p><div class="support-role-chips">${option.roles.map((role) => `<span>${esc(role)}</span>`).join("")}</div><ul class="support-benefits">${option.benefits.map((benefit) => `<li>${esc(benefit)}</li>`).join("")}</ul><a class="button button-quiet support-button" href="${esc(option.url || publicInfo.kofi_url)}" target="_blank" rel="noreferrer">${esc(option.button)} <span>&gt;</span></a></article>`).join("");
  root.innerHTML = `<div class="site-shell">
    <nav class="topbar">${brand()}<div class="topnav"><a href="#features">Features</a><a href="#updates">Updates</a><a href="#support">Support</a><a href="#about">About</a>${profile}</div></nav>
    <main>
      <section class="hero">
        <div class="hero-copy"><div class="eyebrow"><span class="live-mark"></span> YOUR SERVER, IN CLEAR VIEW</div>
          <h1>Keep your community<br><span>in good hands.</span></h1>
          <p class="hero-lede">Observer brings moderation, security, and useful server tools together in one thoughtful Discord bot.</p>
          <div class="hero-actions">${session.signed_in ? `<button type="button" class="button button-primary" id="open-dashboard-hero">Manage your servers</button>` : `<a class="button button-primary" href="${loginUrl}">${discordIcon()} Continue with Discord</a>`}<a class="button button-quiet" href="#features">Explore Observer <span>↓</span></a></div>
          <div class="hero-note"><span class="note-check">✓</span> Discord sign-in · servers you own or moderate</div>
        </div>
        <div class="hero-art" aria-label="Preview of Observer moderation dashboard"><div class="christmas-nativity" aria-hidden="true"><img src="${christmasNativityAsset}" alt=""></div>
          <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div>
          <div class="preview-card"><div class="preview-head"><div class="preview-brand"><span class="mini-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></div><span class="preview-live"><i></i> ALL SYSTEMS READY</span></div>
            <div class="preview-title"><div><span class="eyebrow">SERVER OVERVIEW</span><h2>Good evening, team.</h2></div><span class="server-pill"><span class="server-glyph">&#9672;</span> Your community</span></div>
            <div class="preview-stats"><div><span>OPEN REPORTS</span><strong>03</strong><small class="stat-warm">Needs a look</small></div><div><span>MEMBERS SAFE</span><strong>1,284</strong><small>Across your server</small></div><div><span>BOT STATUS</span><strong class="status-green">Online</strong><small>Ready when you are</small></div></div>
            <div class="preview-activity"><div class="activity-head"><span>RECENT ACTIVITY</span><span>View all &#8599;</span></div><div class="activity-row"><span class="activity-icon activity-amber">!</span><div><b>Member report received</b><small>Spam in #general · 2m ago</small></div><span class="activity-tag">REVIEW</span></div><div class="activity-row"><span class="activity-icon activity-green">✓</span><div><b>Security scan complete</b><small>No threats found · 8m ago</small></div><span class="activity-tag tag-done">CLEAR</span></div></div>
            <div class="preview-glow"></div>
          </div><div class="floating-chip chip-top"><span class="chip-dot"></span> Security active</div><div class="floating-chip chip-bottom"><span class="chip-spark">✳</span> Built for your community</div>
        </div>
        <div class="hero-foot"><span>MODERATION · SECURITY · COMMUNITY</span><span>SCROLL TO DISCOVER <b>↓</b></span></div>
      </section>
      <section class="project-facts" aria-label="Observer project details"><span>BUILT WITH</span><b>Python</b><i></i><b>discord.py</b><i></i><b>SQLite</b><i></i><b>Modular cogs</b><span class="facts-status">IN ACTIVE DEVELOPMENT</span></section>
      <section class="live-stats-section" aria-labelledby="live-stats-heading"><div class="live-stats-head"><div class="eyebrow">03 / LIVE STATS</div><h2 id="live-stats-heading">Current numbers.</h2></div><div class="live-stats-grid"><button type="button" class="live-stat live-stat-clickable" id="live-stat-card" aria-label="Observer bot status. Rapid clicking triggers a playful effect."><strong id="live-stat-status">${liveStats.status || "Unavailable"}</strong><span>Bot status</span></button><article class="live-stat"><strong id="live-stat-servers">${formatCount(liveStats.servers)}</strong><span>Servers</span></article><article class="live-stat"><strong id="live-stat-users">${formatCount(liveStats.users)}</strong><span>Users</span></article><article class="live-stat"><strong id="live-stat-commands">${formatCount(liveStats.commands_run)}</strong><span>Commands run</span></article></div><p class="live-stats-note">Live counts from Observer.</p></section>
      <section class="feature-section" id="features"><div class="section-intro"><div><div class="eyebrow">A LITTLE MORE PEACE OF MIND</div><h2>One bot. A calmer server.</h2></div><p>Useful tools for the everyday work of keeping a community welcoming, safe, and running smoothly.</p></div>
        <div class="feature-grid"><article class="feature-card feature-featured"><div class="feature-icon icon-shield">⬡</div><span class="feature-index">01 / PROTECT</span><h3>See trouble sooner.</h3><p>Scam detection, raid safeguards, and audit logs help your moderators spot issues before they spread.</p><span class="feature-decoration">◈</span></article><article class="feature-card"><div class="feature-icon icon-gavel">⚖</div><span class="feature-index">02 / MODERATE</span><h3>Keep actions clear.</h3><p>Manage reports and moderation cases with a record your staff can follow.</p><span class="feature-decoration">↗</span></article><article class="feature-card"><div class="feature-icon icon-spark">✦</div><span class="feature-index">03 / RUN YOUR SERVER</span><h3>Make room for community.</h3><p>Configure helpful tools for tickets, roles, leveling, reminders, and more.</p><span class="feature-decoration">✦</span></article></div>
      </section>
      <section class="updates-section" id="updates" aria-labelledby="updates-heading">
        <div class="updates-intro"><div><div class="eyebrow">LATEST FROM OBSERVER</div><h2 id="updates-heading">Recent updates.</h2></div><p>New safeguards, useful community features, and steady improvements to the systems behind Observer.</p></div>
        <div class="updates-list" aria-live="polite"><p class="updates-loading">Loading recent updates…</p></div>
      </section>
      <section class="engineering-section" id="engineering"><div class="section-intro"><div><div class="eyebrow">DESIGNED FOR REAL SERVER WORK</div><h2>Useful systems, with room to grow.</h2></div><p>Observer is built as a set of focused features that share server settings and work together when your community needs them.</p></div><div class="project-engineering-grid"><article class="project-engineering-item"><span class="project-engineering-label">01 / MODULAR</span><h3>Features stay organized.</h3><p>Moderation, reports, tickets, leveling, and utilities live in separate cogs, so each part can be maintained and improved on its own.</p></article><article class="project-engineering-item"><span class="project-engineering-label">02 / PERSISTENT</span><h3>Settings belong to your server.</h3><p>Per-server configuration is stored in SQLite and remains available across bot restarts.</p></article><article class="project-engineering-item"><span class="project-engineering-label">03 / PERMISSION AWARE</span><h3>Staff controls matter.</h3><p>Moderation tools use Discord permissions and configured staff roles to help keep actions limited to the right people.</p></article></div><p class="engineering-note">Observer is an independent project built with Python and discord.py. Core features are designed to work without paid AI services.</p></section>
      <section class="workflow-section" id="workflow">
        <div class="workflow-intro"><div><div class="eyebrow"><span class="live-mark"></span> GET STARTED</div><h2>From invite to everyday use.</h2></div><p>Bring Observer into a server, choose the tools that fit, then let your team manage activity from Discord and the web dashboard.</p></div>
        <div class="workflow-steps"><article><span>01</span><div><h3>Connect Observer</h3><p>Sign in with Discord to choose a server you own and add the bot.</p></div></article><article><span>02</span><div><h3>Set up the essentials</h3><p>Choose staff roles, report destinations, channels, and the features your community wants to use.</p></div></article><article><span>03</span><div><h3>Keep your team in view</h3><p>Review reports, follow moderation history, and adjust server settings from the control panel.</p></div></article></div>
        <div class="workflow-action"><div class="workflow-action-copy"><span class="eyebrow">READY WHEN YOU ARE</span><strong>Set up Observer for your server.</strong><small>Connect your server and choose the tools your community needs.</small></div><div class="workflow-action-links">${session.signed_in ? `<button type="button" class="button button-primary" id="open-dashboard-workflow">Manage your servers</button>` : `<a class="button button-primary" href="${loginUrl}">${discordIcon()} Get started with Discord</a>`}<a class="workflow-source" href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Explore the source code <span>&#8599;</span></a></div></div>
      </section>
      <section class="support-section" id="support"><div class="section-intro"><div><div class="eyebrow">OPTIONAL COMMUNITY SUPPORT</div><h2>Help keep Observer going.</h2></div><p>Choose a one-time contribution or one of three monthly tiers. Each payment option receives the matching permanent supporter role or roles.</p></div><div class="support-grid">${supportCards}</div><p class="support-note">Support is optional. Payments are completed on Ko-fi; Observer does not process or store payment details. Supporter roles are recognition and community perks, not moderation permissions.</p></section>
      <section class="about-section" id="about"><div class="about-orb"></div><div class="about-copy"><div class="eyebrow">MADE FOR REAL COMMUNITIES</div><h2>Good moderation makes<br>everything else possible.</h2><p>Observer is an independent Discord bot project built to help community teams spend less time juggling tools and more time looking after their people.</p><a class="button button-primary" href="${loginUrl}">${discordIcon()} Get started with Discord</a></div><div class="about-details"><div class="detail-block"><span class="eyebrow">CREATED BY</span><a href="${esc(publicInfo.portfolio_url)}" target="_blank" rel="noreferrer">${esc(publicInfo.creator)} <span>&#8599;</span></a><small>Independent developer and creator of Observer.</small></div><div class="detail-block"><span class="eyebrow">COME SAY HELLO</span><a href="${esc(publicInfo.support_url)}" target="_blank" rel="noreferrer">Observer Support <span>&#8599;</span></a></div><div class="detail-block"><span class="eyebrow">SOURCE CODE</span><a href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Observer on GitHub <span>&#8599;</span></a></div></div></section>
    </main><footer class="site-footer"><div class="footer-brand">${brand()}<small>© ${new Date().getFullYear()} Observer. Independent community software.</small></div><div class="footer-links"><a href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Source code</a><a href="${esc(publicInfo.portfolio_url)}" target="_blank" rel="noreferrer">Creator portfolio</a><a href="${esc(publicInfo.support_url)}" target="_blank" rel="noreferrer">Support</a><details class="footer-legal"><summary>Privacy &amp; terms</summary><div class="legal-copy"><section><h3>Privacy</h3><p>Sign-in uses Discord OAuth to identify you and list servers where you are an owner or have moderation permissions. Observer stores a protected sign-in session, your basic Discord profile, and that manageable-server list for up to seven days. Discord access tokens are not stored. Server configuration changes are saved in Observer&#8217;s database for the bot to use.</p><p>You can end your sign-in session with “Sign out”. Contact the creator through the support server for questions about stored information.</p></section><section><h3>Terms</h3><p>Use Observer only in servers you own or are authorized to manage. Dashboard changes apply to the connected server and may affect its members. You are responsible for reviewing settings, permissions, and applicable laws before enabling features.</p><p>Observer is provided as an independent community project and is not affiliated with or endorsed by Discord Inc. Service availability and features may change.</p></section></div></details></div><span class="footer-disclaimer">Not affiliated with Discord Inc.</span><a class="footer-top" href="#top" onclick="window.scrollTo({top:0,behavior:'smooth'});return false">BACK TO TOP ↑</a></footer>
  </div>`;
  initLandingScrollReveal();
  root.querySelector("#live-stat-card")?.addEventListener("click", (event) => trackLiveStatusClick(event.currentTarget));
  document.querySelectorAll("#open-dashboard, #open-dashboard-hero, #open-dashboard-workflow").forEach((button) => button.addEventListener("click", openDashboard));
  void loadPublicUpdates();
}

async function loadPublicUpdates() {
  const list = root.querySelector(".updates-list");
  if (!list) return;
  try {
    const result = await api("/api/public-updates");
    const updates = Array.isArray(result.updates) ? result.updates : [];
    list.innerHTML = updates.length
      ? updates.map((update) => {
        const date = String(update.date || "");
        return `<article class="update-item"><time datetime="${esc(date)}">${esc(date)}</time><div class="update-copy"><span class="update-type">${esc(update.type || "Update")}</span><h3>${esc(update.title || "Untitled update")}</h3><p>${esc(update.description || "")}</p></div></article>`;
      }).join("")
      : `<p class="updates-loading">No updates published yet.</p>`;
    list.querySelectorAll(".update-item").forEach((item, index) => {
      item.dataset.scrollReveal = "";
      item.style.setProperty("--reveal-delay", `${index * 95}ms`);
      requestAnimationFrame(() => item.classList.add("is-visible"));
    });
  } catch {
    list.innerHTML = `<p class="updates-loading">Recent updates are temporarily unavailable.</p>`;
  }
}

function initLandingScrollReveal() {
  const revealGroups = [
    [".project-facts", 0],
    [".live-stats-head", 0],
    [".live-stat", 90],
    [".feature-section .section-intro", 0],
    [".feature-card", 90],
    [".updates-intro", 0],
    [".update-item", 95],
    [".engineering-section .section-intro", 0],
    [".project-engineering-item", 80],
    [".engineering-note", 0],
    [".workflow-intro", 0],
    [".workflow-steps > article", 90],
    [".workflow-action", 0],
    [".support-section .section-intro", 0],
    [".support-card", 80],
    [".support-note", 0],
    [".about-copy", 0],
    [".about-details", 100],
  ];
  const targets = new Set();
  for (const [selector, stagger] of revealGroups) {
    document.querySelectorAll(selector).forEach((element, index) => {
      element.dataset.scrollReveal = "";
      element.style.setProperty("--reveal-delay", `${index * stagger}ms`);
      targets.add(element);
    });
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !("IntersectionObserver" in window)) {
    targets.forEach((element) => element.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    }
  }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
  targets.forEach((element) => observer.observe(element));
}

async function openDashboard() {
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png?v=3" alt="Observer logo"><p>Finding your servers&#8230;</p></main>`;
  try {
    const [session, result] = await Promise.all([api("/api/session"), api("/api/servers")]);
    if (!session.signed_in) { location.assign(loginUrl); return; }
    const rows = result.servers.map((server) => {
      const icon = server.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(server.id)}/${encodeURIComponent(server.icon)}.png?size=96" alt="">` : `<span class="server-avatar-fallback">${esc(server.name.slice(0, 1).toUpperCase())}</span>`;
      const action = server.bot_present
        ? server.is_owner
          ? `<div class="server-dashboard-cards"><a class="server-dashboard-card server-config-card" href="/setup?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">CONFIGURATION</span><strong>Configure Observer</strong><small>Manage cogs and server settings</small><span class="server-card-arrow">&#8599;</span></a><a class="server-dashboard-card server-mod-card" href="/moderate?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">REPORTS &amp; MODERATION</span><strong>Review server activity</strong><small>Reports, claims, and moderation actions</small><span class="server-mod-link">View moderation page &#8599;</span></a></div>`
          : `<a class="server-action server-configure" href="/moderate?guild_id=${encodeURIComponent(server.id)}">Moderator dashboard <span>&#8599;</span></a>`
        : server.is_owner
          ? `<a class="server-action server-add" href="${esc(result.invite_url)}&guild_id=${encodeURIComponent(server.id)}&disable_guild_select=true">Add Observer <span>+</span></a>`
          : `<span class="server-action server-secondary">Ask an owner to add Observer</span>`;
      const embedCard = `<a class="server-dashboard-card server-embed-card" href="/embeds?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">SERVER TOOLS</span><strong>Custom messages</strong><small>Create, send, and edit saved embeds or clean cards</small><span class="server-card-arrow">&#8599;</span></a>`;
      const hasEmbedCard = server.bot_present && server.can_send_embeds;
      let serverAction = action;
      if (hasEmbedCard && server.is_owner) serverAction = action.replace('<a class="server-dashboard-card server-config-card"', `${embedCard}<a class="server-dashboard-card server-config-card"`);
      else if (hasEmbedCard && server.can_moderate) serverAction = `<div class="server-dashboard-cards"><a class="server-dashboard-card server-mod-card" href="/moderate?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">REPORTS &amp; MODERATION</span><strong>Review server activity</strong><small>Reports, claims, and moderation actions</small><span class="server-mod-link">View moderation page &#8599;</span></a>${embedCard}</div>`;
      else if (hasEmbedCard) serverAction = `<a class="server-action server-embed-action" href="/embeds?guild_id=${encodeURIComponent(server.id)}">Send a custom embed <span>&#8599;</span></a>`;
      const hasCards = server.bot_present && (server.is_owner || (hasEmbedCard && server.can_moderate));
      return `<article class="server-row${hasCards ? " has-dashboard-cards" : ""}"><div class="server-identity">${icon}<div class="server-copy"><strong>${esc(server.name)}</strong><span>${server.bot_present ? `<i class="presence-dot"></i> Observer is in this server` : `<i class="presence-dot presence-off"></i> Observer isn&#8217;t here yet`}</span></div></div>${serverAction}</article>`;
    }).join("");
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><a href="/diary">My diary</a><span class="signed-in-label">SIGNED IN AS ${esc(session.user.username).toUpperCase()}</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="server-picker"><a class="back-link" href="/">&#8592; Back to Observer</a><div class="eyebrow"><span class="live-mark"></span> DISCORD CONNECTION</div><h1>Your communities.</h1><p class="picker-lede">Choose a server where you&#8217;re an owner or moderator. Moderators can review reports; owners can also configure Observer.</p><div class="server-list">${rows || `<div class="server-empty"><span class="empty-shape">&#9672;</span><h2>No servers found</h2><p>No servers with moderation access were found for this Discord account.</p></div>`}</div><p class="picker-footnote">${rows ? `Showing ${result.servers.length} server${result.servers.length === 1 ? "" : "s"} where you can moderate.` : "You can switch Discord accounts by signing out."}</p></main></div>`;
    document.querySelector("#signout")?.addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>We couldn&#8217;t load your servers.</h1><p>${esc(error.message)}</p><a class="button button-primary" href="${loginUrl}">${discordIcon()} Sign in again</a><a class="back-link" href="/">Return home</a></main>`;
  }
}

async function start() {
  try {
    if (location.pathname === "/privacy" || location.pathname === "/terms") { const { renderLegalPage } = await import("./legal_pages.js"); renderLegalPage(location.pathname === "/privacy" ? "privacy" : "terms"); return; }
    if (location.pathname === "/staff") { await import("./staff.js"); return; }
    if (location.pathname === "/setup") { await import("./control_panel.js"); return; }
    if (location.pathname === "/embeds") { await import("./embed_sender.js"); return; }
    if (location.pathname === "/moderate") { await import("./moderation_dashboard.js"); return; }
    if (location.pathname === "/diary") { await import("./diary.js"); return; }
    if (new URLSearchParams(location.search).has("dashboard")) { await openDashboard(); return; }
    await loadLanding();
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Observer is taking a moment.</h1><p>${esc(error.message)}</p><a class="back-link" href="/">Try again</a></main>`;
  }
}

void syncAccountThemePreference();
start();



