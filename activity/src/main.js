import "./style.css";

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

function brand() { return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png" alt=""></span><span>OBSERVER</span></a>`; }

async function loadLanding() {
  const session = await api("/api/session");
  const publicInfo = await api("/api/public-info");
  const profile = session.signed_in ? `<button class="nav-profile" id="open-dashboard">${session.user.avatar ? `<img src="https://cdn.discordapp.com/avatars/${encodeURIComponent(session.user.id)}/${encodeURIComponent(session.user.avatar)}.png?size=64" alt="">` : ""}<span>Manage servers</span></button>` : `<a class="nav-login" href="${loginUrl}">Sign in <span>↗</span></a>`;
  const supportOptions = [
    { period: "ONE-TIME", themeClass: "one-time", name: "One-Time Supporter", price: "Any amount", roles: ["Supporter <3"], description: "A single contribution helps with Observer hosting, upkeep, and future improvements.", button: "Donate once on Ko-fi", benefits: ["A lasting thank-you for supporting Observer"] },
    { period: "TIER 1", themeClass: "tier-1", name: "Tier 1 - Cookie", price: "&euro;1.99 per month", roles: ["Monthly Supporter <3", "Tier 1 - Cookie :3"], description: "Your first monthly supporter tier with a special role and a place in the supporter community.", button: "Join Tier 1 on Ko-fi", benefits: ["Custom supporters channel", "Added to the supporter wall"] },
    { period: "TIER 2", themeClass: "tier-2", name: "Tier 2 - Cutie", price: "&euro;4.99 per month", roles: ["Monthly Supporter <3", "Tier 2 - Cutie <3"], description: "Everything in Tier 1, plus access to selected supporter-only Observer commands.", button: "Join Tier 2 on Ko-fi", benefits: ["Custom supporters channel", "Added to the supporter wall", "Selected supporter-only bot commands"] },
    { period: "TIER 3", themeClass: "tier-3", name: "Tier 3 - Goated", price: "&euro;7.99 per month", roles: ["Monthly Supporter <3", "Tier 3 - Goated"], description: "The highest tier, with priority help and direct-message support for Observer questions.", button: "Join Tier 3 on Ko-fi", benefits: ["Everything in the lower tiers", "Priority support and help", "Direct-message support for Observer questions"] },
  ];
  const supportCards = supportOptions.map((option, index) => `<article class="support-card support-card-${option.themeClass}"><div class="support-card-top"><span class="support-period">${esc(option.period)}</span><span class="support-symbol">${index === 0 ? "+" : index}</span></div><h3>${esc(option.name)}</h3><div class="support-price">${option.price}</div><p>${esc(option.description)}</p><div class="support-role-chips">${option.roles.map((role) => `<span>${esc(role)}</span>`).join("")}</div><ul class="support-benefits">${option.benefits.map((benefit) => `<li>${esc(benefit)}</li>`).join("")}</ul><a class="button button-quiet support-button" href="${esc(publicInfo.kofi_url)}" target="_blank" rel="noreferrer">${esc(option.button)} <span>&gt;</span></a></article>`).join("");
  root.innerHTML = `<div class="site-shell">
    <nav class="topbar">${brand()}<div class="topnav"><a href="#features">Features</a><a href="#support">Support</a><a href="#about">About</a>${profile}</div></nav>
    <main>
      <section class="hero">
        <div class="hero-copy"><div class="eyebrow"><span class="live-mark"></span> YOUR SERVER, IN CLEAR VIEW</div>
          <h1>Keep your community<br><span>in good hands.</span></h1>
          <p class="hero-lede">Observer brings moderation, security, and useful server tools together in one thoughtful Discord bot.</p>
          <div class="hero-actions">${session.signed_in ? `<button type="button" class="button button-primary" id="open-dashboard-hero">Manage your servers</button>` : `<a class="button button-primary" href="${loginUrl}">${discordIcon()} Continue with Discord</a>`}<a class="button button-quiet" href="#features">Explore Observer <span>↓</span></a></div>
          <div class="hero-note"><span class="note-check">✓</span> Discord sign-in · servers you own or moderate</div>
        </div>
        <div class="hero-art" aria-label="Preview of Observer moderation dashboard">
          <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div>
          <div class="preview-card"><div class="preview-head"><div class="preview-brand"><span class="mini-mark"><img src="/observer.png" alt=""></span><span>OBSERVER</span></div><span class="preview-live"><i></i> ALL SYSTEMS READY</span></div>
            <div class="preview-title"><div><span class="eyebrow">SERVER OVERVIEW</span><h2>Good evening, team.</h2></div><span class="server-pill"><span class="server-glyph">◈</span> Your community</span></div>
            <div class="preview-stats"><div><span>OPEN REPORTS</span><strong>03</strong><small class="stat-warm">Needs a look</small></div><div><span>MEMBERS SAFE</span><strong>1,284</strong><small>Across your server</small></div><div><span>BOT STATUS</span><strong class="status-green">Online</strong><small>Ready when you are</small></div></div>
            <div class="preview-activity"><div class="activity-head"><span>RECENT ACTIVITY</span><span>View all ↗</span></div><div class="activity-row"><span class="activity-icon activity-amber">!</span><div><b>Member report received</b><small>Spam in #general · 2m ago</small></div><span class="activity-tag">REVIEW</span></div><div class="activity-row"><span class="activity-icon activity-green">✓</span><div><b>Security scan complete</b><small>No threats found · 8m ago</small></div><span class="activity-tag tag-done">CLEAR</span></div></div>
            <div class="preview-glow"></div>
          </div><div class="floating-chip chip-top"><span class="chip-dot"></span> Security active</div><div class="floating-chip chip-bottom"><span class="chip-spark">✳</span> Built for your community</div>
        </div>
        <div class="hero-foot"><span>MODERATION · SECURITY · COMMUNITY</span><span>SCROLL TO DISCOVER <b>↓</b></span></div>
      </section>
      <section class="project-facts" aria-label="Observer project details"><span>BUILT WITH</span><b>Python</b><i></i><b>discord.py</b><i></i><b>SQLite</b><i></i><b>Modular cogs</b><span class="facts-status">IN ACTIVE DEVELOPMENT</span></section>
      <section class="feature-section" id="features"><div class="section-intro"><div><div class="eyebrow">A LITTLE MORE PEACE OF MIND</div><h2>One bot. A calmer server.</h2></div><p>Useful tools for the everyday work of keeping a community welcoming, safe, and running smoothly.</p></div>
        <div class="feature-grid"><article class="feature-card feature-featured"><div class="feature-icon icon-shield">⌑</div><span class="feature-index">01 / PROTECT</span><h3>See trouble sooner.</h3><p>Scam detection, raid safeguards, and audit logs help your moderators spot issues before they spread.</p><span class="feature-decoration">◉</span></article><article class="feature-card"><div class="feature-icon icon-gavel">⌁</div><span class="feature-index">02 / MODERATE</span><h3>Keep actions clear.</h3><p>Manage reports and moderation cases with a record your staff can follow.</p><span class="feature-decoration">↗</span></article><article class="feature-card"><div class="feature-icon icon-spark">✳</div><span class="feature-index">03 / RUN YOUR SERVER</span><h3>Make room for community.</h3><p>Configure helpful tools for tickets, roles, leveling, reminders, and more.</p><span class="feature-decoration">✦</span></article></div>
      </section>
      <section class="engineering-section" id="engineering"><div class="section-intro"><div><div class="eyebrow">DESIGNED FOR REAL SERVER WORK</div><h2>Useful systems, with room to grow.</h2></div><p>Observer is built as a set of focused features that share server settings and work together when your community needs them.</p></div><div class="project-engineering-grid"><article class="project-engineering-item"><span class="project-engineering-label">01 / MODULAR</span><h3>Features stay organized.</h3><p>Moderation, reports, tickets, leveling, and utilities live in separate cogs, so each part can be maintained and improved on its own.</p></article><article class="project-engineering-item"><span class="project-engineering-label">02 / PERSISTENT</span><h3>Settings belong to your server.</h3><p>Per-server configuration is stored in SQLite and remains available across bot restarts.</p></article><article class="project-engineering-item"><span class="project-engineering-label">03 / PERMISSION AWARE</span><h3>Staff controls matter.</h3><p>Moderation tools use Discord permissions and configured staff roles to help keep actions limited to the right people.</p></article></div><p class="engineering-note">Observer is an independent project built with Python and discord.py. Core features are designed to work without paid AI services.</p></section>
      <section class="workflow-section" id="workflow">
        <div class="workflow-intro"><div><div class="eyebrow"><span class="live-mark"></span> GET STARTED</div><h2>From invite to everyday use.</h2></div><p>Bring Observer into a server, choose the tools that fit, then let your team manage activity from Discord and the web dashboard.</p></div>
        <div class="workflow-steps"><article><span>01</span><div><h3>Connect Observer</h3><p>Sign in with Discord to choose a server you own and add the bot.</p></div></article><article><span>02</span><div><h3>Set up the essentials</h3><p>Choose staff roles, report destinations, channels, and the features your community wants to use.</p></div></article><article><span>03</span><div><h3>Keep your team in view</h3><p>Review reports, follow moderation history, and adjust server settings from the control panel.</p></div></article></div>
        <div class="workflow-action"><div class="workflow-action-copy"><span class="eyebrow">READY WHEN YOU ARE</span><strong>Set up Observer for your server.</strong><small>Connect your server and choose the tools your community needs.</small></div><div class="workflow-action-links">${session.signed_in ? `<button type="button" class="button button-primary" id="open-dashboard-workflow">Manage your servers</button>` : `<a class="button button-primary" href="${loginUrl}">${discordIcon()} Get started with Discord</a>`}<a class="workflow-source" href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Explore the source code <span>&#8599;</span></a></div></div>
      </section>
      <section class="support-section" id="support"><div class="section-intro"><div><div class="eyebrow">OPTIONAL COMMUNITY SUPPORT</div><h2>Help keep Observer going.</h2></div><p>Choose a one-time contribution or one of three monthly tiers. Each payment option receives the matching permanent supporter role or roles.</p></div><div class="support-grid">${supportCards}</div><p class="support-note">Support is optional. Payments are completed on Ko-fi; Observer does not process or store payment details. Supporter roles are recognition and community perks, not moderation permissions.</p></section>
      <section class="about-section" id="about"><div class="about-orb"></div><div class="about-copy"><div class="eyebrow">MADE FOR REAL COMMUNITIES</div><h2>Good moderation makes<br>everything else possible.</h2><p>Observer is an independent Discord bot project built to help community teams spend less time juggling tools and more time looking after their people.</p><a class="button button-primary" href="${loginUrl}">${discordIcon()} Get started with Discord</a></div><div class="about-details"><div class="detail-block"><span class="eyebrow">CREATED BY</span><a href="${esc(publicInfo.portfolio_url)}" target="_blank" rel="noreferrer">${esc(publicInfo.creator)} <span>↗</span></a><small>Independent developer and creator of Observer.</small></div><div class="detail-block"><span class="eyebrow">COME SAY HELLO</span><a href="${esc(publicInfo.support_url)}" target="_blank" rel="noreferrer">Observer Support <span>↗</span></a></div><div class="detail-block"><span class="eyebrow">SOURCE CODE</span><a href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Observer on GitHub <span>↗</span></a></div></div></section>
    </main><footer class="site-footer"><div class="footer-brand">${brand()}<small>© ${new Date().getFullYear()} Observer. Independent community software.</small></div><div class="footer-links"><a href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Source code</a><a href="${esc(publicInfo.portfolio_url)}" target="_blank" rel="noreferrer">Creator portfolio</a><a href="${esc(publicInfo.support_url)}" target="_blank" rel="noreferrer">Support</a><details class="footer-legal"><summary>Privacy &amp; terms</summary><div class="legal-copy"><section><h3>Privacy</h3><p>Sign-in uses Discord OAuth to identify you and list servers where you are an owner or have moderation permissions. Observer stores a protected sign-in session, your basic Discord profile, and that manageable-server list for up to seven days. Discord access tokens are not stored. Server configuration changes are saved in Observer’s database for the bot to use.</p><p>You can end your sign-in session with “Sign out”. Contact the creator through the support server for questions about stored information.</p></section><section><h3>Terms</h3><p>Use Observer only in servers you own or are authorized to manage. Dashboard changes apply to the connected server and may affect its members. You are responsible for reviewing settings, permissions, and applicable laws before enabling features.</p><p>Observer is provided as an independent community project and is not affiliated with or endorsed by Discord Inc. Service availability and features may change.</p></section></div></details></div><span class="footer-disclaimer">Not affiliated with Discord Inc.</span><a class="footer-top" href="#top" onclick="window.scrollTo({top:0,behavior:'smooth'});return false">BACK TO TOP ↑</a></footer>
  </div>`;
  initLandingScrollReveal();
  document.querySelectorAll("#open-dashboard, #open-dashboard-hero, #open-dashboard-workflow").forEach((button) => button.addEventListener("click", openDashboard));
}

function initLandingScrollReveal() {
  const revealGroups = [
    [".project-facts", 0],
    [".feature-section .section-intro", 0],
    [".feature-card", 90],
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
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png" alt="Observer logo"><p>Finding your servers…</p></main>`;
  try {
    const [session, result] = await Promise.all([api("/api/session"), api("/api/servers")]);
    if (!session.signed_in) { location.assign(loginUrl); return; }
    const rows = result.servers.map((server) => {
      const icon = server.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(server.id)}/${encodeURIComponent(server.icon)}.png?size=96" alt="">` : `<span class="server-avatar-fallback">${esc(server.name.slice(0, 1).toUpperCase())}</span>`;
      const action = server.bot_present
        ? server.is_owner
          ? `<div class="server-dashboard-cards"><a class="server-dashboard-card server-config-card" href="/setup?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">CONFIGURATION</span><strong>Configure Observer</strong><small>Manage cogs and server settings</small><span class="server-card-arrow">↗</span></a><a class="server-dashboard-card server-mod-card" href="/moderate?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">REPORTS &amp; MODERATION</span><strong>Review server activity</strong><small>Reports, claims, and moderation actions</small><span class="server-mod-link">View moderation page ↗</span></a></div>`
          : `<a class="server-action server-configure" href="/moderate?guild_id=${encodeURIComponent(server.id)}">Moderator dashboard <span>↗</span></a>`
        : server.is_owner
          ? `<a class="server-action server-add" href="${esc(result.invite_url)}&guild_id=${encodeURIComponent(server.id)}&disable_guild_select=true">Add Observer <span>+</span></a>`
          : `<span class="server-action server-secondary">Ask an owner to add Observer</span>`;
      const embedCard = `<a class="server-dashboard-card server-embed-card" href="/embeds?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">SERVER TOOLS</span><strong>Custom messages</strong><small>Create, send, and edit saved embeds or clean cards</small><span class="server-card-arrow">↗</span></a>`;
      const hasEmbedCard = server.bot_present && server.can_send_embeds;
      let serverAction = action;
      if (hasEmbedCard && server.is_owner) serverAction = action.replace('<a class="server-dashboard-card server-config-card"', `${embedCard}<a class="server-dashboard-card server-config-card"`);
      else if (hasEmbedCard && server.can_moderate) serverAction = `<div class="server-dashboard-cards"><a class="server-dashboard-card server-mod-card" href="/moderate?guild_id=${encodeURIComponent(server.id)}"><span class="server-card-kicker">REPORTS &amp; MODERATION</span><strong>Review server activity</strong><small>Reports, claims, and moderation actions</small><span class="server-mod-link">View moderation page ↗</span></a>${embedCard}</div>`;
      else if (hasEmbedCard) serverAction = `<a class="server-action server-embed-action" href="/embeds?guild_id=${encodeURIComponent(server.id)}">Send a custom embed <span>↗</span></a>`;
      const hasCards = server.bot_present && (server.is_owner || (hasEmbedCard && server.can_moderate));
      return `<article class="server-row${hasCards ? " has-dashboard-cards" : ""}"><div class="server-identity">${icon}<div class="server-copy"><strong>${esc(server.name)}</strong><span>${server.bot_present ? `<i class="presence-dot"></i> Observer is in this server` : `<i class="presence-dot presence-off"></i> Observer isn’t here yet`}</span></div></div>${serverAction}</article>`;
    }).join("");
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">SIGNED IN AS ${esc(session.user.username).toUpperCase()}</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="server-picker"><a class="back-link" href="/">← Back to Observer</a><div class="eyebrow"><span class="live-mark"></span> DISCORD CONNECTION</div><h1>Your communities.</h1><p class="picker-lede">Choose a server where you’re an owner or moderator. Moderators can review reports; owners can also configure Observer.</p><div class="server-list">${rows || `<div class="server-empty"><span class="empty-shape">◈</span><h2>No servers found</h2><p>No servers with moderation access were found for this Discord account.</p></div>`}</div><p class="picker-footnote">${rows ? `Showing ${result.servers.length} server${result.servers.length === 1 ? "" : "s"} where you can moderate.` : "You can switch Discord accounts by signing out."}</p></main></div>`;
    document.querySelector("#signout")?.addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>We couldn’t load your servers.</h1><p>${esc(error.message)}</p><a class="button button-primary" href="${loginUrl}">${discordIcon()} Sign in again</a><a class="back-link" href="/">Return home</a></main>`;
  }
}

async function start() {
  try {
    if (location.pathname === "/staff") { await import("./staff.js"); return; }
    if (location.pathname === "/setup") { await import("./control_panel.js"); return; }
    if (location.pathname === "/embeds") { await import("./embed_sender.js"); return; }
    if (location.pathname === "/moderate") { await import("./moderation_dashboard.js"); return; }
    if (new URLSearchParams(location.search).has("dashboard")) { await openDashboard(); return; }
    await loadLanding();
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Observer is taking a moment.</h1><p>${esc(error.message)}</p><a class="back-link" href="/">Try again</a></main>`;
  }
}

start();


