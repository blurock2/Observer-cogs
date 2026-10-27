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
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png" alt="Observer logo"><p>Loading message sender...</p></main>`;
  try {
    const data = await api(`/api/custom-embed/${encodeURIComponent(guildId)}`);
    const channels = data.channels || [];
    const savedMessages = data.messages || [];
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">SERVER TOOLS</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="embed-sender-page"><a class="back-link" href="/?dashboard=1">← All servers</a><div class="eyebrow"><span class="live-mark"></span> ${esc(data.guild.name)}</div><h1>Custom message sender</h1><p class="embed-sender-lede">Create a Discord embed or a clean card, then reopen saved messages by pasting their Discord link.</p><form id="embed-form" class="embed-form"><div class="saved-message-picker"><label>Discord message URL <span>Paste the link to a message sent with this tool to load and edit it.</span><input name="message_url" type="url" maxlength="500" placeholder="https://discord.com/channels/server/channel/message"></label><div class="saved-message-actions"><button class="button button-secondary" id="load-message" type="button">Load message</button><button class="button button-secondary" id="new-message" type="button">New message</button></div><div class="saved-message-list-wrap"><div class="saved-message-heading">Recent saved messages</div><div id="saved-message-list" class="saved-message-list"></div></div></div><label>Message style<select name="style"><option value="embed">Standard embed</option><option value="card">Clean card (Components V2)</option></select><span>Cards can include an image and optional link buttons.</span></label><label>Channel<select name="channel_id" required ${channels.length ? "" : "disabled"}><option value="">Choose a channel</option>${channels.map((channel) => `<option value="${esc(channel.id)}" data-can-embed="${channel.can_embed ? "true" : "false"}">${channel.category ? `${esc(channel.category)} / ` : ""}#${esc(channel.name)}${channel.can_embed ? "" : " (card only)"}</option>`).join("")}</select></label><label>Title <span>Optional · up to 256 characters</span><input name="title" maxlength="256" placeholder="Announcement title"></label><label>Description <span>Optional · up to 4,096 characters</span><textarea name="description" maxlength="4096" rows="7" placeholder="Write your message..."></textarea></label><div class="embed-only"><div class="embed-form-row"><label>Embed color<input name="color" type="color" value="#42d7e8"></label></div></div><div class="card-only" hidden><p class="embed-form-note">The card uses Discord's native Components V2 layout and has no embed color stripe.</p><h2>Optional link buttons</h2>${[1, 2, 3].map((i) => `<div class="embed-form-row embed-button-row"><label>Button ${i} label<input name="button_${i}_label" maxlength="80" placeholder="e.g. Read more"></label><label>Button ${i} URL<input name="button_${i}_url" type="url" maxlength="512" placeholder="https://example.com"></label></div>`).join("")}</div><label>Footer <span>Optional · appears in either message style</span><input name="footer" maxlength="2048" placeholder="Footer text"></label><label>Image URL <span>Optional · direct image URL (works in both styles)</span><input name="image_url" type="url" maxlength="1000" placeholder="https://example.com/image.png"></label>${channels.length ? "" : `<p class="embed-sender-empty">Observer needs a text channel with View Channel, Send Messages, and Read Message History.</p>`}<div class="embed-form-actions"><p id="embed-status" role="status" aria-live="polite"></p><button class="button button-primary" type="submit" ${channels.length ? "" : "disabled"}>Send embed <span>↗</span></button></div></form></main></div>`;

    document.querySelector("#signout").addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" });
      location.assign("/");
    });

    const form = document.querySelector("#embed-form");
    const styleSelect = form.elements.style;
    const channelSelect = form.elements.channel_id;
    const status = document.querySelector("#embed-status");
    const submit = form.querySelector('[type="submit"]');
    const savedList = document.querySelector("#saved-message-list");

    const renderSavedMessages = () => {
      savedList.innerHTML = savedMessages.length
        ? savedMessages.map((message) => `<button class="saved-message-item" type="button" data-message-url="${esc(message.url)}"><span><strong>${esc(message.title || "Untitled message")}</strong><small>#${esc(message.channel_name)} · ${message.style === "card" ? "Card" : "Embed"}</small></span><span aria-hidden="true">↗</span></button>`).join("")
        : `<p class="saved-message-empty">Sent messages will appear here for editing.</p>`;
    };
    renderSavedMessages();

    const updateStyle = () => {
      const isCard = styleSelect.value === "card";
      form.querySelector(".embed-only").hidden = isCard;
      form.querySelector(".card-only").hidden = !isCard;
      styleSelect.disabled = Boolean(form.elements.message_url.value.trim());
      submit.innerHTML = `${form.elements.message_url.value.trim() ? "Update" : "Send"} ${isCard ? "card" : "embed"} <span>↗</span>`;
      for (const option of channelSelect.options) {
        if (option.value) option.disabled = !isCard && option.dataset.canEmbed !== "true";
      }
      if (channelSelect.selectedOptions[0]?.disabled) channelSelect.value = "";
    };
    styleSelect.addEventListener("change", updateStyle);
    form.elements.message_url.addEventListener("input", updateStyle);
    updateStyle();

    const loadMessage = async (messageUrl) => {
      if (!messageUrl) {
        status.textContent = "Paste a Discord message URL first.";
        status.className = "is-error";
        return;
      }
      status.textContent = "Loading saved message...";
      status.className = "";
      try {
        const result = await api(`/api/custom-embed/${encodeURIComponent(guildId)}/message?url=${encodeURIComponent(messageUrl)}`);
        const saved = result.payload;
        form.elements.message_url.value = result.message_url;
        form.elements.channel_id.value = result.channel_id;
        form.elements.style.value = result.style;
        for (const name of ["title", "description", "footer", "image_url", "color", "button_1_label", "button_1_url", "button_2_label", "button_2_url", "button_3_label", "button_3_url"]) {
          if (form.elements[name]) form.elements[name].value = saved[name] || (name === "color" ? "#42d7e8" : "");
        }
        updateStyle();
        status.textContent = "Message loaded. Changes will update this Discord message.";
        status.className = "is-success";
      } catch (error) {
        status.textContent = error.message;
        status.className = "is-error";
      }
    };

    document.querySelector("#load-message").addEventListener("click", () => loadMessage(form.elements.message_url.value.trim()));
    savedList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-message-url]");
      if (button) loadMessage(button.dataset.messageUrl);
    });
    document.querySelector("#new-message").addEventListener("click", () => {
      form.reset();
      form.elements.message_url.value = "";
      styleSelect.value = "embed";
      channelSelect.value = "";
      updateStyle();
      status.textContent = "Ready to compose a new message.";
      status.className = "";
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form));
      values.style = styleSelect.value;
      if (!values.title.trim() && !values.description.trim()) {
        status.textContent = "Add a title or description before sending.";
        status.className = "is-error";
        return;
      }
      submit.disabled = true;
      status.textContent = values.message_url ? "Updating message..." : "Sending message...";
      status.className = "";
      try {
        const result = await api(`/api/custom-embed/${encodeURIComponent(guildId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        });
        form.elements.message_url.value = result.message_url;
        updateStyle();
        const messageLink = document.createElement("a");
        messageLink.href = result.message_url;
        messageLink.target = "_blank";
        messageLink.rel = "noreferrer";
        messageLink.textContent = "Open message ↗";
        status.replaceChildren(document.createTextNode(result.action === "updated" ? "Message updated. " : "Message sent and saved. "), messageLink);
        status.className = "is-success";
        const index = savedMessages.findIndex((message) => message.url === result.message_url);
        const saved = { url: result.message_url, style: values.style, title: values.title, channel_name: channelSelect.selectedOptions[0]?.textContent.replace(/ \(card only\)$/, "").split("#").pop() || "channel" };
        if (index >= 0) savedMessages.splice(index, 1);
        savedMessages.unshift(saved);
        savedMessages.splice(50);
        renderSavedMessages();
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
