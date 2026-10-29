import privacyPolicy from "./policies/privacy-policy.txt?raw";
import termsOfService from "./policies/terms-of-service.txt?raw";

const root = document.querySelector("#app");
const esc = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const subheadings = new Set([
  "Data Controller",
  "Discord Account Information",
  "Discord Server Information",
  "Message and Interaction Information",
  "Persistent Feature Data",
  "Technical and Security Information",
  "Right of Access",
  "Right to Rectification",
  "Right to Erasure",
  "Right to Restriction of Processing",
  "Right to Data Portability",
  "Right to Object",
  "Right to Withdraw Consent",
  "Right Regarding Automated Decision-Making",
]);

function renderDocument(text, title) {
  const output = [];
  let paragraph = [];
  let list = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${esc(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.length) return;
    output.push(`<ul>${list.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`);
    list = [];
  };

  for (const sourceLine of text.replace(/\r/g, "").split("\n")) {
    const line = sourceLine.trim();
    if (!line || line === title.toUpperCase() || line === "TERMS OF SERVICE") {
      flushParagraph();
      flushList();
      continue;
    }
    if (line.startsWith("● ")) {
      flushParagraph();
      list.push(line.slice(2));
      continue;
    }
    flushList();
    if (/^(Effective Date|Last Updated):/.test(line)) {
      flushParagraph();
      output.push(`<p class="policy-meta">${esc(line)}</p>`);
    } else if (/^\d{1,2}\.\s/.test(line)) {
      flushParagraph();
      output.push(`<h2>${esc(line)}</h2>`);
    } else if (/^[A-D]\.\s/.test(line) || subheadings.has(line)) {
      flushParagraph();
      output.push(`<h3>${esc(line)}</h3>`);
    } else {
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return output.join("");
}

export function renderLegalPage(kind) {
  const isPrivacy = kind === "privacy";
  const title = isPrivacy ? "Privacy Policy" : "Terms of Service";
  const text = isPrivacy ? privacyPolicy : termsOfService;
  document.title = `${title} · Observer`;
  root.innerHTML = `<div class="dashboard-shell policy-shell"><nav class="topbar"><a class="brand" href="/" aria-label="Observer home"><span class="brand-mark"><img src="/observer.png?v=3" alt=""></span><span>OBSERVER</span></a><div class="topnav"><a href="/">Back to Observer</a></div></nav><main class="policy-page"><a class="back-link" href="/">&#8592; Back to Observer</a><header class="policy-page-head"><div class="eyebrow">OBSERVER LEGAL</div><h1>${title}</h1></header><article class="policy-document">${renderDocument(text, isPrivacy ? "Privacy Policy for Observer" : "Terms of Service")}</article></main></div>`;
}

