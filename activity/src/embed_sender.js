import "./style.css";

const root = document.querySelector("#app");
const guildId = new URLSearchParams(location.search).get("guild_id");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function brand() {
  return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png" alt=""></span><span>OBSERVER</span></a>`;
}

async function start() {
  if (!guildId) { location.replace("/?dashboard=1"); return; }
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png" alt="Observer logo"><p>Loading embed sender…</p></main>`;
  try {
    const data = await api(`/api/custom-embed/${encodeURIComponent(guildId)}`);
    const channels = data.channels || [];
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">SERVER TOOLS</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="embed-sender-page"><a class="back-link" href="/?dashboard=1">← All servers</a><div class="eyebrow"><span class="live-mark"></span> ${esc(data.guild.name)}</div><h1>Custom message sender</h1><p class="embed-sender-lede">Choose a standard Discord embed or a clean card without the colored side stripe.</p><form id="embed-form" class="embed-form"><label>Message style<select name="style"><option value="embed">Standard embed</option><option value="card">Clean card (Components V2)</option></select><span>Cards can include an image and optional link buttons.</span></label><label>Channel<select name="channel_id" required ${channels.length ? "" : "disabled"}><option value="">Choose a channel</option>${channels.map((channel) => `<option value="${esc(channel.id)}" data-can-embed="${channel.can_embed ? "true" : "false"}">${channel.category ? `${esc(channel.category)} / ` : ""}#${esc(channel.name)}${channel.can_embed ? "" : " (card only)"}</option>`).join("")}</select></label><label>Title <span>Optional · up to 256 characters</span><input name="title" maxlength="256" placeholder="Announcement title"></label><label>Description <span>Optional · up to 4,096 characters</span><textarea name="description" maxlength="4096" rows="7" placeholder="Write your message…"></textarea></label><div class="embed-only"><div class="embed-form-row"><label>Embed color<input name="color" type="color" value="#42d7e8"></label><label>Footer <span>Optional</span><input name="footer" maxlength="2048" placeholder="Footer text"></label></div></div><div class="card-only" hidden><p class="embed-form-note">The card uses Discord’s native Components V2 layout and has no embed color stripe.</p><h2>Optional link buttons</h2>${[1, 2, 3].map((i) => `<div class="embed-form-row embed-button-row"><label>Button ${i} label<input name="button_${i}_label" maxlength="80" placeholder="e.g. Read more"></label><label>Button ${i} URL<input name="button_${i}_url" type="url" maxlength="512" placeholder="https://example.com"></label></div>`).join("")}</div><label>Image URL <span>Optional · direct image URL (works in both styles)</span><input name="image_url" type="url" maxlength="1000" placeholder="https://example.com/image.png"></label>${channels.length ? "" : `<p class="embed-sender-empty">Observer has no text channels where it can send messages.</p>`}<div class="embed-form-actions"><p id="embed-status" role="status" aria-live="polite"></p><button class="button button-primary" type="submit" ${channels.length ? "" : "disabled"}>Send embed <span>↗</span></button></div></form></main></div>`;

    document.querySelector("#signout").addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" });
      location.assign("/");
    });

    const form = document.querySelector("#embed-form");
    const styleSelect = form.elements.style;
    const channelSelect = form.elements.channel_id;
    const updateStyle = () => {
      const isCard = styleSelect.value === "card";
      form.querySelector(".embed-only").hidden = isCard;
      form.querySelector(".card-only").hidden = !isCard;
      form.querySelector('[type="submit"]').innerHTML = `Send ${isCard ? "card" : "embed"} <span>↗</span>`;
      for (const option of channelSelect.options) {
        if (option.value) option.disabled = !isCard && option.dataset.canEmbed !== "true";
      }
      if (channelSelect.selectedOptions[0]?.disabled) channelSelect.value = "";
      form.querySelector("#embed-status").textContent = "";
    };
    styleSelect.addEventListener("change", updateStyle);
    updateStyle();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form));
      const status = document.querySelector("#embed-status");
      const submit = form.querySelector('[type="submit"]');
      if (!values.title.trim() && !values.description.trim()) {
        status.textContent = "Add a title or description before sending.";
        status.className = "is-error";
        return;
      }
      submit.disabled = true;
      status.textContent = "Sending…";
      status.className = "";
      try {
        const result = await api(`/api/custom-embed/${encodeURIComponent(guildId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        });
        const messageLink = document.createElement("a");
        messageLink.href = result.message_url;
        messageLink.target = "_blank";
        messageLink.rel = "noreferrer";
        messageLink.textContent = "Open message ↗";
        status.replaceChildren(document.createTextNode(`${values.style === "card" ? "Card" : "Embed"} sent. `), messageLink);
        status.className = "is-success";
        for (const name of ["title", "description", "footer", "image_url", "button_1_label", "button_1_url", "button_2_label", "button_2_url", "button_3_label", "button_3_url"]) {
          if (form.elements[name]) form.elements[name].value = "";
        }
      } catch (error) {
        status.textContent = error.message;
        status.className = "is-error";
      } finally {
        submit.disabled = false;
      }
    });
  } catch (error) {
    root.innerHTML = `<main class="server-error"><div class="error-mark">!</div><h1>Could not open the message sender.</h1><p>${esc(error.message)}</p><a class="back-link" href="/?dashboard=1">Return to servers</a></main>`;
  }
}

start();
