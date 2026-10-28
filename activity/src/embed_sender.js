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
  return `<a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></a>`;
}

function safeImageUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function renderInlineDiscordText(source) {
  return esc(source)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<u>$1</u>')
    .replace(/~~([^~]+)~~/g, '<s>$1</s>')
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
}

function renderEmbedText(value) {
  return value.split("\n").map(renderInlineDiscordText).join("<br>");
}

function renderDiscordText(value) {
  return value.split("\n").map((line) => {
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) return `<div class="preview-v2-heading preview-v2-heading-${heading[1].length}">${renderInlineDiscordText(heading[2])}</div>`;
    if (!line.trim()) return '<div class="preview-v2-spacer"></div>';
    return `<div class="preview-v2-line">${renderInlineDiscordText(line)}</div>`;
  }).join("");
}

async function start() {
  if (!guildId) { location.replace("/?dashboard=1"); return; }
  root.innerHTML = `<main class="dashboard-loading"><img class="loading-mark" src="/observer.png?v=3" alt="Observer logo"><p>Loading message sender...</p></main>`;
  try {
    const data = await api(`/api/custom-embed/${encodeURIComponent(guildId)}`);
    const channels = data.channels || [];
    const savedMessages = data.messages || [];
    root.innerHTML = `<div class="dashboard-shell"><nav class="topbar">${brand()}<div class="topnav"><span class="signed-in-label">SERVER TOOLS</span><button class="signout-button" id="signout">Sign out</button></div></nav><main class="embed-sender-page"><a class="back-link" href="/?dashboard=1">â† All servers</a><div class="eyebrow"><span class="live-mark"></span> ${esc(data.guild.name)}</div><h1>Custom message sender</h1><p class="embed-sender-lede">Create a Discord embed or a clean card, then reopen saved messages by pasting their Discord link.</p><div class="embed-composer-layout"><form id="embed-form" class="embed-form"><div class="saved-message-picker"><label>Discord message URL <span>Paste the link to a message sent with this tool to load and edit it.</span><input name="message_url" type="url" maxlength="500" placeholder="https://discord.com/channels/server/channel/message"></label><div class="saved-message-actions"><button class="button button-secondary" id="load-message" type="button">Load message</button><button class="button button-secondary" id="new-message" type="button">New message</button></div><div class="saved-message-list-wrap"><div class="saved-message-heading">Recent saved messages</div><div id="saved-message-list" class="saved-message-list"></div></div></div><label>Message style<select name="style"><option value="embed">Standard embed</option><option value="card">Clean card (Components V2)</option></select><span>Cards can include an image and optional colored buttons.</span></label><label>Channel<select name="channel_id" required ${channels.length ? "" : "disabled"}><option value="">Choose a channel</option>${channels.map((channel) => `<option value="${esc(channel.id)}" data-can-embed="${channel.can_embed ? "true" : "false"}">${channel.category ? `${esc(channel.category)} / ` : ""}#${esc(channel.name)}${channel.can_embed ? "" : " (card only)"}</option>`).join("")}</select></label><label>Title <span>Optional Â· up to 256 characters</span><input name="title" maxlength="256" placeholder="Announcement title"></label><label>Description <span>Optional Â· up to 4,096 characters</span><textarea name="description" maxlength="4096" rows="7" placeholder="Write your message..."></textarea></label><div class="embed-only embed-details"><div class="embed-details-heading">Message content and embed details</div><label>Message content <span>Optional text displayed above the embed &#183; up to 2,000 characters</span><textarea name="content" maxlength="2000" rows="3" placeholder="Normal text sent with the embed"></textarea></label><div class="embed-form-row"><label>Author name<input name="author_name" maxlength="256" placeholder="Author name"></label><label>Author URL<input name="author_url" type="url" maxlength="1000" placeholder="https://example.com"></label></div><label>Author icon URL<input name="author_icon_url" type="url" maxlength="1000" placeholder="https://example.com/icon.png"></label><div class="embed-form-row"><label>Title URL<input name="title_url" type="url" maxlength="1000" placeholder="https://example.com"></label><label>Thumbnail URL<input name="thumbnail_url" type="url" maxlength="1000" placeholder="https://example.com/thumbnail.png"></label></div><div class="embed-field-builder"><div class="embed-field-heading"><span>Embed fields</span><button class="button button-secondary" id="add-embed-field" type="button">Add field</button></div><div id="embed-field-list"></div></div><div class="embed-form-row"><label>Footer icon URL<input name="footer_icon_url" type="url" maxlength="1000" placeholder="https://example.com/icon.png"></label><label class="embed-timestamp-option"><span>Timestamp</span><span><input name="timestamp" type="checkbox"> Show current time</span></label></div></div><div class="embed-only"><div class="embed-form-row"><label>Embed color<input name="color" type="color" value="#42d7e8"></label></div></div><div class="card-only" hidden><p class="embed-form-note">The card uses Discord's native Components V2 layout and has no embed color stripe.</p><h2>Optional colored link buttons</h2><p class="embed-form-note">Discord only colors interaction buttons. Clicking one opens a private message with the link.</p>${[1, 2, 3].map((i) => `<div class="embed-form-row embed-button-row"><label>Button ${i} label<input name="button_${i}_label" maxlength="80" placeholder="e.g. Read more"></label><label>Button ${i} URL<input name="button_${i}_url" type="url" maxlength="512" placeholder="https://example.com"></label><label>Button color<select name="button_${i}_style"><option value="primary">Blue</option><option value="secondary" selected>Gray</option><option value="success">Green</option><option value="danger">Red</option></select></label></div>`).join("")}</div><label>Footer <span>Optional Â· appears in either message style</span><input name="footer" maxlength="2048" placeholder="Footer text"></label><label>Image URL <span>Optional Â· direct image URL (works in both styles)</span><input name="image_url" type="url" maxlength="1000" placeholder="https://example.com/image.png"></label>${channels.length ? "" : `<p class="embed-sender-empty">Observer needs a text channel with View Channel, Send Messages, and Read Message History.</p>`}<div class="embed-form-actions"><p id="embed-status" role="status" aria-live="polite"></p><button class="button button-primary" type="submit" ${channels.length ? "" : "disabled"}>Send embed <span>â†—</span></button></div></form><aside class="message-preview-panel"><div class="eyebrow">LIVE PREVIEW</div><h2>Message appearance</h2><div id="message-preview" class="message-preview"></div><p class="preview-disclaimer">Preview approximates Discord's layout. Discord may render text and media slightly differently.</p></aside></div></main></div>`;

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
    const preview = document.querySelector("#message-preview");
    const fieldList = document.querySelector("#embed-field-list");
    const imageUploadList = document.createElement("div");
    imageUploadList.className = "image-upload-list";
    const addImageButton = document.createElement("button");
    addImageButton.type = "button";
    addImageButton.className = "button button-secondary image-upload-add";
    addImageButton.textContent = "Add another image";
    const imageUploadLabel = document.createElement("div");
    imageUploadLabel.className = "image-upload-field";
    const imageUploadTitle = document.createElement("div");
    imageUploadTitle.textContent = "Upload images";
    imageUploadLabel.append(imageUploadTitle);
    const uploadHelp = document.createElement("span");
    uploadHelp.textContent = "Optional Â· add up to 10 PNG, JPG, GIF, or WebP images (8 MB each, 25 MB total). Uploaded images are shared through Discord.";
    imageUploadLabel.append(uploadHelp, imageUploadList, addImageButton);
    form.elements.image_url.closest("label").before(imageUploadLabel);
    const uploadedImagePreviewUrls = new Map();
    let savedImagePreviewUrls = [];
    const updateImagePickerButtons = () => {
      imageUploadList.querySelectorAll(".image-upload-remove").forEach((button) => {
        button.disabled = imageUploadList.children.length <= 1;
      });
      addImageButton.disabled = imageUploadList.children.length >= 10;
    };
    const addImagePicker = () => {
      if (imageUploadList.children.length >= 10) return;
      const row = document.createElement("div");
      row.className = "image-upload-row";
      const input = document.createElement("input");
      input.name = "image_file";
      input.type = "file";
      input.accept = "image/png,image/jpeg,image/gif,image/webp";
      input.setAttribute("aria-label", `Upload image ${imageUploadList.children.length + 1}`);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button button-secondary image-upload-remove";
      remove.textContent = "Remove";
      row.append(input, remove);
      imageUploadList.append(row);
      updateImagePickerButtons();
    };
    const resetImagePickers = () => {
      for (const url of uploadedImagePreviewUrls.values()) URL.revokeObjectURL(url);
      uploadedImagePreviewUrls.clear();
      imageUploadList.replaceChildren();
      addImagePicker();
    };
    addImagePicker();
    addImageButton.addEventListener("click", () => addImagePicker());
    imageUploadList.addEventListener("change", (event) => {
      const input = event.target.closest('input[type="file"]');
      if (!input) return;
      const previousUrl = uploadedImagePreviewUrls.get(input);
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      const file = input.files[0];
      if (file) uploadedImagePreviewUrls.set(input, URL.createObjectURL(file));
      else uploadedImagePreviewUrls.delete(input);
      renderPreview();
    });
    imageUploadList.addEventListener("click", (event) => {
      const remove = event.target.closest(".image-upload-remove");
      if (!remove || imageUploadList.children.length <= 1) return;
      const row = remove.closest(".image-upload-row");
      const input = row.querySelector('input[type="file"]');
      const previewUrl = uploadedImagePreviewUrls.get(input);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      uploadedImagePreviewUrls.delete(input);
      row.remove();
      updateImagePickerButtons();
      renderPreview();
    });

    const addEmbedField = (field = {}) => {
      if (fieldList.children.length >= 25) return;
      const row = document.createElement("div");
      row.className = "embed-field-row";
      row.innerHTML = `<label>Field name<input data-field-name maxlength="256" placeholder="Field name" value="${esc(field.name || "")}"></label><label>Field value<textarea data-field-value maxlength="1024" rows="2" placeholder="Field value">${esc(field.value || "")}</textarea></label><label class="embed-field-inline"><input data-field-inline type="checkbox" ${field.inline ? "checked" : ""}> Inline</label><button class="embed-field-remove" type="button" aria-label="Remove field">Ã—</button>`;
      fieldList.append(row);
    };

    const setEmbedFields = (fields = []) => {
      fieldList.replaceChildren();
      fields.slice(0, 25).forEach(addEmbedField);
    };

    const readEmbedFields = () => [...fieldList.querySelectorAll(".embed-field-row")].map((row) => ({
      name: row.querySelector("[data-field-name]").value.trim(),
      value: row.querySelector("[data-field-value]").value.trim(),
      inline: row.querySelector("[data-field-inline]").checked,
    })).filter((field) => field.name && field.value);

    const renderPreview = () => {
      const values = Object.fromEntries(new FormData(form));
      const isCard = styleSelect.value === "card";
      const title = values.title?.trim() || "Your message title";
      const description = values.description?.trim() || "Your message text will appear here as you write it.";
      const footer = values.footer?.trim();
      const uploadedImageUrls = [...uploadedImagePreviewUrls.values()];
      const imageUrls = uploadedImageUrls.length
        ? uploadedImageUrls
        : values.image_url?.trim()
          ? [safeImageUrl(values.image_url.trim())]
          : savedImagePreviewUrls;
      const thumbnailUrl = safeImageUrl(values.thumbnail_url?.trim() || "");
      const authorName = values.author_name?.trim();
      const authorIconUrl = safeImageUrl(values.author_icon_url?.trim() || "");
      const authorUrl = safeImageUrl(values.author_url?.trim() || "");
      const titleUrl = safeImageUrl(values.title_url?.trim() || "");
      const footerIconUrl = safeImageUrl(values.footer_icon_url?.trim() || "");
      const colorValue = /^#[\da-f]{6}$/i.test(values.color || "") ? values.color : "#42d7e8";
      const imagesMarkup = imageUrls.filter(Boolean).map((url, index) => `<img class="preview-message-image" src="${esc(url)}" alt="Message image ${index + 1} preview">`).join("");
      const embedAuthor = authorName ? `<div class="preview-embed-author">${authorIconUrl ? `<img src="${esc(authorIconUrl)}" alt="">` : ""}${authorUrl ? `<a href="${esc(authorUrl)}" target="_blank" rel="noreferrer">${esc(authorName)}</a>` : `<span>${esc(authorName)}</span>`}</div>` : "";
      const timestampMarkup = values.timestamp ? `<time>${new Date().toLocaleString([], { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" })}</time>` : "";
      const footerMarkup = footer ? `<div class="preview-message-footer">${!isCard && footerIconUrl ? `<img src="${esc(footerIconUrl)}" alt="">` : ""}<span>${esc(footer)}</span>${!isCard ? timestampMarkup : ""}</div>` : !isCard ? `<div class="preview-message-footer">${timestampMarkup}</div>` : "";
      const fields = readEmbedFields();
      const fieldsMarkup = fields.length ? `<div class="preview-embed-fields">${fields.map((field) => `<div class="preview-embed-field ${field.inline ? "is-inline" : ""}"><strong>${renderEmbedText(field.name)}</strong><div class="preview-embed-field-value">${renderDiscordText(field.value)}</div></div>`).join("")}</div>` : "";
      const thumbnail = thumbnailUrl ? `<img class="preview-embed-thumbnail" src="${esc(thumbnailUrl)}" alt="Embed thumbnail">` : "";
      const embedTitle = titleUrl ? `<a class="preview-embed-title" href="${esc(titleUrl)}" target="_blank" rel="noreferrer">${esc(title)}</a>` : `<div class="preview-embed-title">${esc(title)}</div>`;
      const cardText = [title ? `# ${title}` : "", description].filter(Boolean).join("\n\n");
      const buttons = [1, 2, 3].map((index) => ({
        label: values[`button_${index}_label`]?.trim(),
        url: safeImageUrl(values[`button_${index}_url`]?.trim() || ""),
        style: values[`button_${index}_style`] || "secondary",
      })).filter((button) => button.label && button.url);
      const buttonMarkup = buttons.length
        ? `<div class="preview-message-buttons">${buttons.map((button) => `<span class="button-style-${esc(button.style)}">${esc(button.label)}</span>`).join("")}</div>`
        : "";
      const content = isCard
        ? `<div class="preview-v2-card">${renderDiscordText(cardText)}${footerMarkup}<div class="preview-message-images">${imagesMarkup}</div></div>${buttonMarkup}`
        : `<div class="preview-embed" style="--preview-accent:${colorValue}">${embedAuthor}<div class="preview-embed-body"><div class="preview-embed-copy">${embedTitle}<div class="preview-message-description">${renderDiscordText(description)}</div></div>${thumbnail}</div>${fieldsMarkup}<div class="preview-message-images">${imagesMarkup}</div>${footerMarkup}</div>`;
      const authorHeader = `<div class="preview-author"><img src="/observer.png?v=3" alt=""><span>Observer</span><small><b aria-hidden="true">âœ“</b> APP</small><time>${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time></div>`;
      const normalContent = !isCard && values.content?.trim() ? `<div class="preview-message-content">${renderDiscordText(values.content.trim())}</div>` : "";
      preview.innerHTML = `${authorHeader}${normalContent}${content}`;
    };

    document.querySelector("#add-embed-field").addEventListener("click", () => { addEmbedField(); renderPreview(); });
    fieldList.addEventListener("click", (event) => {
      if (event.target.closest(".embed-field-remove")) { event.target.closest(".embed-field-row").remove(); renderPreview(); }
    });

    const renderSavedMessages = () => {
      savedList.innerHTML = savedMessages.length
        ? savedMessages.map((message) => `<button class="saved-message-item" type="button" data-message-url="${esc(message.url)}"><span><strong>${esc(message.title || "Untitled message")}</strong><small>#${esc(message.channel_name)} Â· ${message.style === "card" ? "Card" : "Embed"}</small></span><span aria-hidden="true">â†—</span></button>`).join("")
        : `<p class="saved-message-empty">Sent messages will appear here for editing.</p>`;
    };
    renderSavedMessages();

    const updateStyle = () => {
      const isCard = styleSelect.value === "card";
      form.classList.toggle("is-card", isCard);
      form.querySelectorAll(".embed-only").forEach((section) => {
        section.hidden = isCard;
        section.querySelectorAll("input, textarea, select, button").forEach((control) => { control.disabled = isCard; });
      });
      const cardOptions = form.querySelector(".card-only");
      cardOptions.hidden = !isCard;
      cardOptions.querySelectorAll("input, textarea, select, button").forEach((control) => { control.disabled = !isCard; });
      form.elements.content.disabled = isCard;
      styleSelect.disabled = Boolean(form.elements.message_url.value.trim());
      submit.innerHTML = `${form.elements.message_url.value.trim() ? "Update" : "Send"} ${isCard ? "card" : "embed"} <span>â†—</span>`;
      for (const option of channelSelect.options) {
        if (option.value) option.disabled = !isCard && option.dataset.canEmbed !== "true";
      }
      if (channelSelect.selectedOptions[0]?.disabled) channelSelect.value = "";
      renderPreview();
    };
    styleSelect.addEventListener("change", updateStyle);
    form.elements.message_url.addEventListener("input", updateStyle);
    form.addEventListener("input", renderPreview);
    form.addEventListener("change", renderPreview);
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
        savedImagePreviewUrls = result.image_preview_urls || [];
        resetImagePickers();
        form.elements.channel_id.value = result.channel_id;
        form.elements.style.value = result.style;
        for (const name of ["title", "description", "content", "author_name", "author_url", "author_icon_url", "title_url", "thumbnail_url", "footer", "footer_icon_url", "image_url", "color", "button_1_label", "button_1_url", "button_1_style", "button_2_label", "button_2_url", "button_2_style", "button_3_label", "button_3_url", "button_3_style"]) {
          if (form.elements[name]) form.elements[name].value = saved[name] || (name === "color" ? "#42d7e8" : name.endsWith("_style") ? "secondary" : "");
        }
        form.elements.timestamp.checked = Boolean(saved.timestamp);
        setEmbedFields(Array.isArray(saved.fields) ? saved.fields : []);
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
      savedImagePreviewUrls = [];
      resetImagePickers();
      styleSelect.value = "embed";
      channelSelect.value = "";
      setEmbedFields([]);
      updateStyle();
      status.textContent = "Ready to compose a new message.";
      status.className = "";
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submittedForm = new FormData(form);
      const selectedImageFiles = submittedForm.getAll("image_file").filter((file) => file instanceof File && file.size > 0);
      submittedForm.delete("image_file");
      const values = Object.fromEntries(submittedForm);
      values.style = styleSelect.value;
      values.timestamp = form.elements.timestamp.checked;
      values.fields = values.style === "embed" ? readEmbedFields() : [];
      const hasBody = values.style === "card"
        ? Boolean(values.title.trim() || values.description.trim())
        : Boolean(values.title.trim() || values.description.trim() || values.fields.length);
      if (!hasBody) {
        status.textContent = values.style === "card" ? "Add a title or description before sending the card." : "Add a title, description, or at least one embed field before sending.";
        status.className = "is-error";
        return;
      }
      if (selectedImageFiles.length > 10 || selectedImageFiles.some((file) => file.size > 8 * 1024 * 1024)
        || selectedImageFiles.reduce((total, file) => total + file.size, 0) > 25 * 1024 * 1024) {
        status.textContent = "Add up to 10 images, 8 MB each and 25 MB total.";
        status.className = "is-error";
        return;
      }
      submit.disabled = true;
      status.textContent = values.message_url ? "Updating message..." : "Sending message...";
      status.className = "";
      try {
        let body;
        const headers = {};
        if (selectedImageFiles.length) {
          body = new FormData();
          body.append("payload", JSON.stringify(values));
          for (const file of selectedImageFiles) body.append("image_file", file, file.name);
        } else {
          headers["Content-Type"] = "application/json";
          body = JSON.stringify(values);
        }
        const result = await api(`/api/custom-embed/${encodeURIComponent(guildId)}`, {
          method: "POST", headers, body,
        });
        form.elements.message_url.value = result.message_url;
        savedImagePreviewUrls = result.image_preview_urls || [];
        resetImagePickers();
        updateStyle();
        const messageLink = document.createElement("a");
        messageLink.href = result.message_url;
        messageLink.target = "_blank";
        messageLink.rel = "noreferrer";
        messageLink.textContent = "Open message â†—";
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

