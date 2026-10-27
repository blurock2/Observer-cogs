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
  const profile = session.signed_in ? `<button class="nav-profile" id="open-dashboard">${session.user.avatar ? `<img src="https://cdn.discordapp.com/avatars/${encodeURIComponent(session.user.id)}/${encodeURIComponent(session.user.avatar)}.png?size=64" alt="">` : ""}<span>${esc(session.user.username)}</span></button>` : `<a class="nav-login" href="${loginUrl}">Sign in <span>↗</span></a>`;
  root.innerHTML = `<div class="site-shell">
    <nav class="topbar">${brand()}<div class="topnav"><a href="#features">Features</a><a href="#about">About</a>${profile}</div></nav>
    <main>
      <section class="hero">
        <div class="hero-copy"><div class="eyebrow"><span class="live-mark"></span> YOUR SERVER, IN CLEAR VIEW</div>
          <h1>Keep your community<br><span>in good hands.</span></h1>
          <p class="hero-lede">Observer brings moderation, security, and useful server tools together in one thoughtful Discord bot.</p>
          <div class="hero-actions"><a class="button button-primary" href="${loginUrl}">${discordIcon()} Continue with Discord</a><a class="button button-quiet" href="#features">Explore Observer <span>↓</span></a></div>
          <div class="hero-note"><span class="note-check">✓</span> Discord profile + server list · only servers you own</div>
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
      <section class="feature-section" id="features"><div class="section-intro"><div><div class="eyebrow">A LITTLE MORE PEACE OF MIND</div><h2>One bot. A calmer server.</h2></div><p>Useful tools for the everyday work of keeping a community welcoming, safe, and running smoothly.</p></div>
        <div class="feature-grid"><article class="feature-card feature-featured"><div class="feature-icon icon-shield">⌑</div><span class="feature-index">01 / PROTECT</span><h3>See trouble sooner.</h3><p>Scam detection, raid safeguards, and audit logs help your moderators spot issues before they spread.</p><span class="feature-decoration">◉</span></article><article class="feature-card"><div class="feature-icon icon-gavel">⌁</div><span class="feature-index">02 / MODERATE</span><h3>Keep actions clear.</h3><p>Manage reports and moderation cases with a record your staff can follow.</p><span class="feature-decoration">↗</span></article><article class="feature-card"><div class="feature-icon icon-spark">✳</div><span class="feature-index">03 / RUN YOUR SERVER</span><h3>Make room for community.</h3><p>Configure helpful tools for tickets, roles, leveling, reminders, and more.</p><span class="feature-decoration">✦</span></article></div>
      </section>
      <section class="about-section" id="about"><div class="about-orb"></div><div class="about-copy"><div class="eyebrow">MADE FOR REAL COMMUNITIES</div><h2>Good moderation makes<br>everything else possible.</h2><p>Observer is an independent Discord bot project built to help community teams spend less time juggling tools and more time looking after their people.</p><a class="button button-primary" href="${loginUrl}">${discordIcon()} Get started with Discord</a></div><div class="about-details"><div class="detail-block"><span class="eyebrow">CREATED BY</span><strong>${esc(publicInfo.creator)}</strong></div><div class="detail-block"><span class="eyebrow">COME SAY HELLO</span><a href="${esc(publicInfo.support_url)}" target="_blank" rel="noreferrer">Observer Support <span>↗</span></a></div>${publicInfo.source_url ? `<div class="detail-block"><span class="eyebrow">SOURCE CODE</span><a href="${esc(publicInfo.source_url)}" target="_blank" rel="noreferrer">Explore on GitHub <span>↗</span></a></div>` : ""}</div></section>
    </main><footer class="site-footer">${brand()}<span>Independent project · Not affiliated with Discord Inc.</span><a href="#top" onclick="window.scrollTo({top:0,behavior:'smooth'});return false">BACK TO TOP ↑</a></footer>
  </div>`;
  document.querySelector("#open-dashboard")?.addEventListener("click", openDashboard);
}

async function openDashboard() {
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png" alt="Observer logo"><p>Finding your servers…</p></main>`;
  try {
    const [session, result] = await Promise.all([api("/api/session"), api("/api/servers")]);
    if (!session.signed_in) { location.assign(loginUrl); return; }
    const rows = result.servers.map((server) => {
      const icon = server.icon ? `<img src="https://cdn.discordapp.com/icons/${encodeURIComponent(server.id)}/${encodeURIComponent(server.icon)}.png?size=96" alt="">` : `<span class="server-avatar-fallback">${esc(server.name.slice(0, 1).toUpperCase())}</span>`;
      const action = server.bot_present
        ? `<a class="server-action server-configure" href="https://discord.com/channels/${encodeURIComponent(server.id)}" target="_blank" rel="noreferrer">Configure in Discord <span>↗</span></a>`
        : `<a class="server-action server-add" href="${esc(result.invite_url)}&guild_id=${encodeURIComponent(server.id)}&disable_guild_select=true">Add Observer <span>+</span></a>`;
      return `<article class="server-row">${icon}<div class="server-copy"><strong>${esc(server.name)}</strong><span>${server.bot_present ? `<i class="presence-dot"></i> Observer is in this server` : `<i class="presence-dot presence-off"></i> Observer isn’t here yet`}</span></div>${action}</article>`;
    }).join("");
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">SIGNED IN AS ${esc(session.user.username).toUpperCase()}</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="server-picker"><a class="back-link" href="/">← Back to Observer</a><div class="eyebrow"><span class="live-mark"></span> DISCORD CONNECTION</div><h1>Your communities.</h1><p class="picker-lede">Choose a server you own to continue. Observer only appears here when you’re its owner.</p><div class="server-list">${rows || `<div class="server-empty"><span class="empty-shape">◈</span><h2>No servers found</h2><p>This Discord account doesn’t own any servers. Sign in with the account that owns your community.</p></div>`}</div><p class="picker-footnote">${rows ? `Showing ${result.servers.length} server${result.servers.length === 1 ? "" : "s"} you own.` : "You can switch Discord accounts by signing out."}</p></main></div>`;
    document.querySelector("#signout")?.addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.assign("/"); });
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>We couldn’t load your servers.</h1><p>${esc(error.message)}</p><a class="button button-primary" href="${loginUrl}">${discordIcon()} Sign in again</a><a class="back-link" href="/">Return home</a></main>`;
  }
}

async function setupLanding() {
  const session = await api("/api/session");
  if (!session.signed_in) { location.replace(loginUrl); return; }
  const guildId = new URLSearchParams(location.search).get("guild_id");
  if (!guildId) { await openDashboard(); return; }
  const data = await api("/api/servers");
  const server = data.servers.find((item) => item.id === guildId && item.bot_present);
  if (!server) { await openDashboard(); return; }
  root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><button class="signout-button" id="back-servers">All servers</button></div></nav><main class="server-picker setup-ready"><div class="eyebrow"><span class="live-mark"></span> SERVER VERIFIED</div><h1>${esc(server.name)}</h1><p class="picker-lede">You own this server and Observer is connected. The web setup editor is the next step; for now, open the server and run <code>/setup</code>.</p><a class="button button-primary" href="https://discord.com/channels/${encodeURIComponent(server.id)}" target="_blank" rel="noreferrer">Open server <span>↗</span></a><button class="back-link back-button" id="back-servers2">← Choose another server</button></main></div>`;
  document.querySelector("#back-servers")?.addEventListener("click", openDashboard);
  document.querySelector("#back-servers2")?.addEventListener("click", openDashboard);
}

async function start() {
  try {
    if (location.pathname === "/staff") { await import("./staff.js"); return; }
    if (location.pathname === "/setup") { await setupLanding(); return; }
    await loadLanding();
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Observer is taking a moment.</h1><p>${esc(error.message)}</p><a class="back-link" href="/">Try again</a></main>`;
  }
}

start();



