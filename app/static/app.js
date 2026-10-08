/* CIRCULATE front end. Plain JavaScript, no build step. Talks to the FastAPI engine. Design system: DESIGN.md. */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const inr = v => v == null ? "–" : (v < 0 ? "−" : "") + "₹" + Math.abs(Math.round(v)).toLocaleString("en-IN");
const num = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("en-IN", {maximumFractionDigits: d, minimumFractionDigits: d});
const pct = v => Math.round(v * 100) + "%";
const REDUCED = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

async function api(path, body, method, payload) {
  if (method) body = payload;
  const opt = body === undefined ? {} : {method: method || "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const t = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = t.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map(x => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ") : r.statusText);
  }
  return t;
}

/* ---------------- shared building blocks (see DESIGN.md) ---------------- */
const ic = (name, cls = "") => `<svg class="ic ${cls}" aria-hidden="true"><use href="/static/icons/sprite.svg#i-${name}"/></svg>`;
const term = (plain, technical) => `<span class="term" title="Technical term: ${esc(technical)}">${esc(plain)}</span>`;
const short = name => String(name || "").split(",")[0];
const cap1 = s => s ? s[0].toUpperCase() + s.slice(1) : "";
const words = v => cap1(String(v || "").replace(/_/g, " "));
const plural = s => /s$/.test(s) ? s : s + "s";
const isFood = o => !!(o && (o.categories || o.buyer_type));
const orderName = o => isFood(o) ? `${num(o.quantity, 0)} kg for ${short(o.buyer_name)}` : `${cap1(plural(o.part_name || "part"))} for ${short(o.buyer_name)}`;
const codeTag = id => id ? `<span class="code" title="Order code">${esc(id)}</span>` : "";
const BADGES = {
  sample: ["p-warn", "Sample", "Made-up demo data, not measured at a real business."],
  simulated: ["p-mut", "Simulated", "Made in this demo or by a simulation. Never counted as a real, verified deal."],
  modelled: ["p-mut", "Modelled", "Worked out by a model, not observed."],
  assumption: ["p-warn", "Assumption", "A stated assumption, not an observed value."],
  measured: ["p-ok", "Measured", "Entered by the seller as taken from their own records."],
  verified: ["p-ok", "Verified", "A real deal with delivery evidence and the buyer's confirmation."],
  illustrative: ["p-mut", "Illustrative photo", "A freely licensed photo of the same kind of thing, not the actual item."],
  planned: ["p-mut", "Planned", "Shown so you know it is coming. It does not work yet."],
  posted: ["p-np", "Posted by a buyer in this demo", "A requirement a buyer typed into this demo."],
};
function badge(kind, text) {
  const b = BADGES[kind];
  return b ? `<span class="pill ${b[0]}" title="${esc(b[2])}">${esc(text || b[1])}</span>` : `<span class="pill p-mut">${esc(text || kind)}</span>`;
}
const chip = l => badge(l);
function toast(html, kind = "ok") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = `${ic(kind === "ok" ? "circle-check" : "triangle-alert")}<div>${html}</div>`;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), 5000);
}
const emptyState = (icon, sentence, action = "") => `<div class="empty">${ic(icon, "lg")}<p>${sentence}</p>${action}</div>`;
const errBox = e => `<div class="err" role="alert">${ic("triangle-alert")}<div><b>That did not work.</b> ${esc(e.message || e)} <span>Check the values you entered and try again; if it keeps failing, reload the page.</span></div></div>`;
const fold = (title, body, open = false, id = "") => `<details class="fold" ${open ? "open" : ""} ${id ? `id="${id}"` : ""}><summary>${esc(title)} ${ic("chevron-down", "sm chev")}</summary><div class="fold-body">${body}</div></details>`;
/* The value ladder: scrap value -> transport -> value created. Reused wherever money is shown. */
function ladder({scrap, transport, gain, scrapLabel = "Scrap value", gainLabel = "Value created"}) {
  const parts = [Math.max(scrap, 0), Math.max(transport, 0), Math.max(gain, 0)], total = parts.reduce((a, b) => a + b, 0) || 1;
  const w = v => (v / total * 100).toFixed(2);
  return `<div class="ladder" role="img" aria-label="${esc(scrapLabel)} ${inr(scrap)}, then transport ${inr(transport)}, then ${esc(gainLabel)} ${inr(gain)}">
    <div class="rail-bar"><i class="s" style="width:${w(parts[0])}%"></i><i class="t" style="width:${w(parts[1])}%"></i><i class="g" style="width:${w(parts[2])}%"></i></div>
    <div class="keys"><div class="key"><span><i class="s"></i>${scrapLabel}</span><b>${inr(scrap)}</b></div><div class="key"><span><i class="t"></i>Transport</span><b>${inr(transport)}</b></div><div class="key g"><span><i class="g"></i>${gainLabel}</span><b>${inr(gain)}</b></div></div></div>`;
}
function split(v) {
  if (!v || v.buyer_value_W <= 0) return `<p class="note">Donation: no buyer payment. It avoids a disposal cost of ${inr(-v.salvage_value_S)} at a transport cost of ${inr(v.transport_T)}.</p>`;
  return ladder({scrap: v.salvage_value_S, transport: v.transport_T, gain: v.gain_G});
}
/* Count a number up once the drawing has settled (skipped when the visitor prefers reduced motion). */
function countUp(el, to, delay, fmt = inr) {
  if (!el) return;
  if (REDUCED()) { el.textContent = fmt(to); return; }
  el.textContent = fmt(0);
  setTimeout(() => {
    const t0 = performance.now(), dur = 700;
    const step = t => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.textContent = fmt(to * e); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }, delay);
}
/* Side drawer */
const drawer = {
  last: null,
  open(title, html) {
    this.last = document.activeElement;
    const d = $("#drawer");
    d.innerHTML = `<div class="drawer-head"><h2 id="drawerTitle">${title}</h2><button class="btn sm ghost" type="button" data-close-drawer aria-label="Close">${ic("x")}</button></div><div class="drawer-body">${html}</div>`;
    $("#drawerWrap").hidden = false; document.body.style.overflow = "hidden"; d.scrollTop = 0; d.focus();
    return d;
  },
  close() { $("#drawerWrap").hidden = true; document.body.style.overflow = ""; if (this.last && this.last.focus) this.last.focus(); },
};
document.addEventListener("click", e => { if (e.target.closest("[data-close-drawer]")) drawer.close(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#drawerWrap").hidden) drawer.close(); });
/* Photos: the seller's own photo always wins; otherwise a credited illustrative photo; otherwise none. */
function photoHtml(it) {
  if (it.photo) return `<figure class="media"><img src="${esc(it.photo)}" alt="Photo of ${esc(it.id)} uploaded by the seller" loading="lazy"><figcaption class="ptag seller">${ic("camera", "sm")}Photo uploaded by the seller</figcaption></figure>`;
  const il = it.illustrative;
  if (!il) return "";
  const credit = `${il.shows}. "${il.title}" by ${il.author}, ${il.licence}, via ${il.site || "Wikimedia Commons"}`;
  return `<figure class="media" title="Illustrative photo, not the actual item. ${esc(credit)}"><img src="${esc(il.url)}" alt="Illustrative photo: ${esc(il.shows)}" loading="lazy"><figcaption class="ptag">Illustrative photo · <a href="${esc(il.source_url)}" target="_blank" rel="noopener" title="${esc(credit)}">Credit</a></figcaption></figure>`;
}
let CREDITS = null;
async function credits() { if (!CREDITS) CREDITS = await fetch("/static/illustrative/credits.json").then(r => r.ok ? r.json() : {}).catch(() => ({})); return CREDITS; }
/* Dimension lines for scale drawings (mm). */
function dimLines(L, W, u) {
  const t = u * 0.9, fs = u * 1.15, y = -u * 2.2, x = -u * 2.2;
  return `<g class="dims" style="stroke:var(--mut);stroke-width:${u * 0.12}">
    <line x1="0" y1="${y}" x2="${L}" y2="${y}"/><line x1="0" y1="${y - t / 2}" x2="0" y2="${y + t / 2}"/><line x1="${L}" y1="${y - t / 2}" x2="${L}" y2="${y + t / 2}"/>
    <line x1="${x}" y1="0" x2="${x}" y2="${W}"/><line x1="${x - t / 2}" y1="0" x2="${x + t / 2}" y2="0"/><line x1="${x - t / 2}" y1="${W}" x2="${x + t / 2}" y2="${W}"/></g>
    <text x="${L / 2}" y="${y - u * 0.55}" text-anchor="middle" style="font:500 ${fs}px var(--mono);fill:var(--mut)">${num(L)} mm</text>
    <text transform="translate(${x - u * 0.6} ${W / 2}) rotate(-90)" text-anchor="middle" style="font:500 ${fs}px var(--mono);fill:var(--mut)">${num(W)} mm</text>`;
}

/* ---------------- state and navigation ---------------- */
const LANES = {durable: {name: "Durable", cls: "np", scen: ["steel", "sheets", "bars"]}, fresh: {name: "Fresh", cls: "pe", scen: ["food"]}};
const ROLES = {buyer: "Buyer", seller: "Seller", admin: "Admin"};
// [view, title, icon, lane only]
const NAV = {
  seller: [["My workshop", [["connect", "Connect your system", "plug"], ["gapfill", "Sold before it's cut", "scissors", "durable"], ["stock", "Stock and passports", "qr-code"], ["offers", "Offers and recovery plan", "route"]]],
    ["Logistics", [["pickup", "Pickup and delivery", "truck"]]]],
  buyer: [["Marketplace", [["post", "Post a requirement", "file-plus-2"], ["browse", "Browse surplus", "search"], ["match", "Order and match", "layers"]]],
    ["My orders", [["reqs", "My requirements", "clipboard-list"], ["deals", "My deals", "receipt"]]],
    ["Logistics", [["pickup", "Pickup and delivery", "truck"]]]],
  admin: [["Platform", [["monitor", "Deal monitor", "gauge"], ["quality", "Data quality", "shield-check"], ["prices", "Price references", "banknote"], ["rules", "Rules", "list-checks"], ["sim", "Simulation lab", "flask-conical"]]]],
};
const TITLES = {
  connect: ["Connect your system", "Bring in your stock from any ERP or billing software. The app checks every row and finds what is surplus."],
  post: ["Post a requirement", "Say what you need. Find it in sellers' stock today, or keep it open so a workshop can cut it from the spare part of its next sheet."],
  reqs: ["My requirements", "Everything you have posted, and where each one stands."],
  browse: ["Browse surplus", "Pieces and lots on offer near you, each with a fair price range and the seller's distance."],
  pickup: ["Pickup and delivery", "One route, the right vehicle and the papers for the driver. Transporters and bookings are simulated."],
  match: ["Order and match", "Pick an order. The app finds who can supply it, how to cut it and the cheapest pickup."],
  deals: ["My deals", "Track each order from agreed to dispatched to delivered."],
  gapfill: ["Sold before it's cut", "Sell the empty part of your next sheet to nearby buyers before you cut it."],
  offers: ["Offers and recovery plan", "Which buyers to try, in which order, before the deadline. Scrap or disposal is the last step."],
  stock: ["Stock and passports", "Every piece or lot, its scrap value, and a passport with a QR code."],
  monitor: ["Deal monitor", "Every deal by stage. Simulated deals are never counted as verified."],
  quality: ["Data quality", "How much of what the app shows is measured, sample, simulated or verified."],
  prices: ["Price references", "Every price the engine uses, with its source and date."],
  rules: ["Rules", "What the engine checks before it accepts a piece or a lot."],
  sim: ["Simulation lab", "Gap Fill tested on 5,000 simulated sheets. Simulated, sample prices."],
};
const st = {lane: "durable", role: "seller", view: "welcome", scen: {durable: "steel", fresh: "food"}, ord: {}, rate: {}, off: {},
  plan: {rejected: [], elapsed: 0, key: null}, deadline: {}, gf: null, gfDeclined: [], gfOpen: false, sellerFilter: "", lastDeal: null,
  imp: {durable: {}, fresh: {}}, ordId: {}, postKind: "flat", post: {flat: null, linear: null, perishable: null}, postErr: {}, postMsg: null,
  man: {durable: {material: "mild_steel", length_mm: "", width_mm: "", thickness_mm: "", grade: "E250", condition: "good", is_full_sheet: false, days_unused: "", seller_id: ""},
    fresh: {product: "", category: "vegetables", quantity_kg: "", storage: "ambient", hours_left: "", use_by: "", mode: "hours", seller_id: ""}},
  manPhoto: {durable: null, fresh: null}, manErr: {durable: {}, fresh: {}}, manRes: {durable: null, fresh: null}, manMeasured: {durable: false, fresh: false},
  br: {durable: {q: "", mat: "", thick: "", seller: "", dist: "", sort: "distance"}, fresh: {q: "", mat: "", thick: "", seller: "", dist: "", sort: "hours"}}, impLimit: {days: 30, hours: 24}, impMeasured: false,
  job: {sheet_length_mm: 2500, sheet_width_mm: 1250, thickness_mm: 6, grade: "E250", part_name: "base plate", part_length_mm: 450, part_width_mm: 350, quantity: 9}};

function navFor(role, lane) { return NAV[role].flatMap(([, items]) => items).filter(n => !n[3] || n[3] === lane); }
function readHash() {
  const h = location.hash.slice(1);
  if (!h) { st.view = "welcome"; return; }
  const [role, view, lane] = h.split("/");
  if (ROLES[role]) st.role = role;
  if (LANES[lane]) st.lane = lane;
  if (view && navFor(st.role, st.lane).some(n => n[0] === view)) st.view = view;
  else st.view = navFor(st.role, st.lane)[0][0];
}
function go(patch) {
  Object.assign(st, patch);
  if (st.view !== "welcome" && !navFor(st.role, st.lane).some(n => n[0] === st.view)) st.view = navFor(st.role, st.lane)[0][0];
  history.replaceState(null, "", st.view === "welcome" ? location.pathname + location.search : `#${st.role}/${st.view}/${st.lane}`);
  render();
}
function shell() {
  $("#lane").className = "seg " + LANES[st.lane].cls;
  $("#lane").innerHTML = Object.entries(LANES).map(([k, v]) => `<button type="button" data-lane="${k}" aria-pressed="${k === st.lane}">${v.name}</button>`).join("");
  $("#role").innerHTML = Object.entries(ROLES).map(([k, v]) => `<button type="button" data-role="${k}" aria-pressed="${k === st.role}">${v}</button>`).join("");
  $("#nav").innerHTML = NAV[st.role].map(([grp, items]) => {
    const vis = items.filter(n => !n[3] || n[3] === st.lane);
    return vis.length ? `<h6>${esc(grp)}</h6>` + vis.map(([k, t, icon]) => `<button type="button" data-view="${k}" ${k === st.view ? 'aria-current="page"' : ""}>${ic(icon)}<span>${esc(t)}</span></button>`).join("") : "";
  }).join("") + (st.role === "seller" && st.lane === "fresh" ? `<p class="railnote">Sold before it's cut is about cutting sheet, so it lives in the Durable lane.</p>` : "");
}
document.addEventListener("click", e => {
  const home = e.target.closest(".brand");
  if (home) { e.preventDefault(); go({view: "welcome"}); return; }
  const b = e.target.closest("[data-lane],[data-role],[data-view]");
  if (!b || !b.closest(".rail,.topbar")) return;
  if (b.dataset.lane) go({lane: b.dataset.lane});
  else if (b.dataset.role) go({role: b.dataset.role, view: navFor(b.dataset.role, st.lane)[0][0]});
  else go({view: b.dataset.view});
});
window.addEventListener("hashchange", () => { readHash(); render(); });

/* ---------------- shared pieces ---------------- */
const SC = {};
async function scen(key) {
  if (!SC[key]) {
    const raw = await api(`/api/scenario/${key}`);
    const meta = {};
    for (const k of ["scenario", "title", "engine", "plan_seller", "metric_word"]) { meta[k] = raw[k]; delete raw[k]; }
    SC[key] = {meta, data: raw};
  }
  return SC[key];
}
function laneScen() {
  const keys = LANES[st.lane].scen;
  if (!keys.includes(st.scen[st.lane])) st.scen[st.lane] = keys[0];
  return st.scen[st.lane];
}
async function scenSeg() {
  const keys = LANES[st.lane].scen;
  if (keys.length < 2) return "";
  const titles = await Promise.all(keys.map(k => scen(k).then(s => s.meta.title)));
  return `<span class="seg" role="group" aria-label="Material">${keys.map((k, i) => `<button type="button" data-scen="${k}" aria-pressed="${k === laneScen()}">${esc(titles[i])}</button>`).join("")}</span>`;
}
function bindScen(el) {
  el.querySelectorAll("[data-scen]").forEach(b => b.onclick = () => { st.scen[st.lane] = b.dataset.scen; st.plan = {rejected: [], elapsed: 0, key: null}; render(); });
}
function sheetSvg(a) {
  const L = a.length_mm, W = a.width_mm, sc = 300 / Math.max(L, W), w = L * sc, h = W * sc;
  return `<figure class="sheet"><svg viewBox="-2 -2 ${w + 4} ${h + 4}" role="img" aria-label="Cutting plan ${esc(a.resource_id)}"><rect class="piece" x="0" y="0" width="${w}" height="${h}"/>${
    (a.defects || []).map(d => `<rect x="${d.x_mm * sc}" y="${d.y_mm * sc}" width="${d.length_mm * sc}" height="${d.width_mm * sc}" style="fill:var(--bad);opacity:.35"/>`).join("")}${
    a.parts.map(p => `<rect class="pt" x="${p.x * sc}" y="${p.y * sc}" width="${p.length * sc}" height="${p.width * sc}"/>`).join("")}</svg><figcaption>${esc(a.resource_id)} · ${num(L)} × ${num(W)} mm · ${a.units} parts</figcaption></figure>`;
}

/* ---------------- What can this become? (Browse surplus drawer) ---------------- */
function barSvg(det) {
  const L = det.length_mm;
  return `<svg class="bplan" viewBox="0 0 ${L} 140" role="img" aria-label="${det.cuts.length} cuts along the bar"><rect x="0" y="40" width="${L}" height="60" style="fill:var(--piece);stroke:var(--mut);stroke-width:${L / 200}"/>${det.cuts.map(c => `<rect x="${c.start_mm}" y="40" width="${c.length_mm}" height="60" style="fill:var(--part);stroke:var(--partl);stroke-width:${L / 400}"/>`).join("")}</svg>`;
}
function becomeHtml(b, top) {
  const fresh = b.kind === "perishable", base = fresh ? "disposal" : "scrap";
  const money = o => {
    if (o.value_above_scrap == null) return `<span class="note">Count only: no new price for this material in the cluster.</span>`;
    if (o.is_donation) return `<span class="note">Donation: avoids ${inr(-o.scrap_value)} disposal, pickup ${inr(o.transport)}.</span>`;
    const v = o.value_above_scrap;
    return `<div><b class="bval ${v < 0 ? "neg" : ""}">${inr(Math.abs(v))}</b> <span class="note">${v < 0 ? "below" : "above"} ${base} value</span></div>`;
  };
  const what = o => fresh ? `${num(o.units, 1)} kg to ${esc(short(o.buyer_name))}` : `${num(o.units)} × ${esc(o.part_name)}`;
  const size = o => fresh ? `${num(o.hours_on_arrival, 1)} h left on arrival (needs ${num(o.min_hours_on_arrival)} h)` : o.part_mm ? `${num(o.part_mm[0])} × ${num(o.part_mm[1])} mm${o.wanted ? ` · order wants ${num(o.wanted)}` : ""}` : `${num(o.cut_length_mm)} mm cuts · order wants ${num(o.wanted)}`;
  const draw = o => o.detail && o.detail.shape === "sheet" ? sheetSvg({...o.detail, resource_id: "", units: o.units}).replace(/<figcaption>.*<\/figcaption>/, "") : o.detail && o.detail.shape === "bar" ? barSvg(o.detail) : "";
  const label = o => o.source === "reference" ? `<span class="pill p-mut" title="A common part size at this thickness. No buyer has ordered it yet.">${esc(o.label)}</span>` : `<span class="pill p-ok" title="A buyer has an open order for this.">${esc(o.label.replace(/^Ordered by (.*?)(, [^(]*)?( \(standing order\))?$/, (m, n, loc, s) => `Ordered by ${n}${s || ""}`))}</span>`;
  const card = o => `<div class="bopt ${o.source === "reference" ? "ref" : ""}">${draw(o) ? `<div class="bdraw">${draw(o)}</div>` : ""}<div class="bbody">${label(o)}
    <b>${what(o)}</b><span class="note">${size(o)}</span>${money(o)}</div></div>`;
  const opts = b.options, shown = opts.slice(0, top), more = opts.slice(top);
  const sc = b.scrap ? `Compared with its ${base} value of ${inr(Math.abs(b.scrap.value))} (₹${num(Math.abs(b.scrap.price_per_kg), 1)}/kg, ${esc(b.scrap.label)}, ${b.scrap.quoted_on ? "quoted " + esc(b.scrap.quoted_on) : "no date"}).` : "";
  return `<div class="bwrap"><p class="note">${sc} Real buyers first, then common parts. Prices are sample data.</p>
    ${shown.length ? shown.map(card).join("") : `<p class="note">Nothing in this lane can use it right now. The reasons are below.</p>`}
    ${more.length ? `<details><summary>${more.length} more option${more.length === 1 ? "" : "s"}</summary>${more.map(card).join("")}</details>` : ""}
    ${b.refused.length ? `<details><summary>Not possible (${b.refused.length})</summary><ul class="rej">${b.refused.map(r => `<li><b>${esc(r.label === "Common part, no buyer yet" ? "Common part" : r.label)}: ${esc(r.part_name)}</b> ${esc(r.reason)}</li>`).join("")}</ul></details>` : ""}</div>`;
}

/* ---------------- Buyer: post a requirement, and my requirements ---------------- */
function postDefaults(kind, O) {
  if (kind === "perishable") return {categories: ["vegetables"], quantity: "", storage_available: ["ambient"], min_hours_left_on_arrival: "6",
    buyer_type: "business", normal_price_per_kg: String(O.food_price ?? ""), buyer_fssai_licensed: false, needed_by: "", buyer_name: "",
    x_km: String(O.centre.food[0]), y_km: String(O.centre.food[1])};
  if (kind === "linear") { const pr = O.profiles[0];
    return {part_name: "", profile: pr.profile, cut_length_mm: "", quantity: "", accepted_grades: [O.bar_grades[0]], accepted_conditions: [...O.default_conditions],
      new_price_per_kg: String(pr.sample_price ?? ""), needed_by: "", buyer_name: "", x_km: String(O.centre.bars[0]), y_km: String(O.centre.bars[1])}; }
  const m = O.materials.find(x => x.key === "mild_steel");
  return {part_name: "", material: "mild_steel", length_mm: "", width_mm: "", thickness_mm: "", accepted_grades: m.grades.slice(0, 1), accepted_conditions: [...O.default_conditions],
    quantity: "", new_price_per_kg: String(m.sample_price ?? ""), needed_by: "", buyer_name: "", x_km: String(O.centre.steel[0]), y_km: String(O.centre.steel[1])};
}
async function vPost(el) {
  const O = await api("/api/requirements/options"), lane = st.lane;
  const kind = lane === "fresh" ? "perishable" : st.postKind === "linear" ? "linear" : "flat";
  const F = st.post[kind] || (st.post[kind] = postDefaults(kind, O)), E = st.postErr;
  const bad = k => E[k] ? 'aria-invalid="true"' : "";
  const err = k => `<span class="ferr" id="perr-${k}">${esc(E[k] || "")}</span>`;
  const fld = (k, label, input, cls = "") => `<label class="field mfield ${cls}">${label}${input}${err(k)}</label>`;
  const grp = (k, label, input, cls = "") => `<div class="field mfield ${cls}" role="group" aria-label="${esc(label)}"><span>${label}</span>${input}${err(k)}</div>`;
  const num_ = (k, mode = "decimal", ph = "") => `<input data-post="${k}" type="number" inputmode="${mode}" ${mode === "numeric" ? 'pattern="[0-9]*" step="1"' : 'step="any"'} min="0" placeholder="${ph}" value="${esc(F[k])}" ${bad(k)}>`;
  const txt = (k, ph = "") => `<input data-post="${k}" type="text" placeholder="${esc(ph)}" value="${esc(F[k])}" ${bad(k)}>`;
  const chips = (k, opts) => `<div class="chips">${opts.map(([v, t]) => `<label><input type="checkbox" data-chip="${k}" value="${esc(v)}" ${F[k].includes(v) ? "checked" : ""}>${esc(t)}</label>`).join("")}</div>`;
  const mat = O.materials.find(x => x.key === F.material);
  const group = (n, title, body) => `<div class="fgroup"><h3><span class="stepno">${n}</span>${title}</h3><div class="mgrid">${body.join("")}</div></div>`;
  let g1, g2;
  if (kind === "flat") {
    g1 = [fld("part_name", "Part name", txt("part_name", "e.g. mounting bracket"), "wide"),
      fld("material", "Material", `<select data-post="material" ${bad("material")}>${O.materials.map(m => `<option value="${m.key}" ${m.key === F.material ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>`),
      fld("quantity", "How many", num_("quantity", "numeric", "e.g. 50")),
      fld("length_mm", "Length (mm)", num_("length_mm", "decimal", "e.g. 150")), fld("width_mm", "Width (mm)", num_("width_mm", "decimal", "e.g. 100")),
      fld("thickness_mm", "Thickness (mm)", num_("thickness_mm", "decimal", "e.g. 2"))];
    g2 = [grp("accepted_grades", "Grades you accept", mat && mat.grades.length ? chips("accepted_grades", mat.grades.map(g => [g, g])) : txt("grades_text", "Type grades, separated by commas"), "wide"),
      grp("accepted_conditions", "Conditions you accept", chips("accepted_conditions", O.conditions.map(c => [c, words(c)])), "wide")];
  } else if (kind === "linear") {
    g1 = [fld("part_name", "Part name", txt("part_name", "e.g. baluster"), "wide"),
      fld("profile", "Bar or tube", `<select data-post="profile" ${bad("profile")}>${O.profiles.map(x => `<option value="${esc(x.profile)}" ${x.profile === F.profile ? "selected" : ""}>${esc(cap1(x.profile))} · ${num(x.mass_per_m_kg, 2)} kg/m</option>`).join("")}</select>`),
      fld("cut_length_mm", "Cut length (mm)", num_("cut_length_mm", "decimal", "e.g. 450")), fld("quantity", "How many", num_("quantity", "numeric", "e.g. 30"))];
    g2 = [grp("accepted_grades", "Grades you accept", chips("accepted_grades", O.bar_grades.map(g => [g, g])), "wide"),
      grp("accepted_conditions", "Conditions you accept", chips("accepted_conditions", O.conditions.map(c => [c, words(c)])), "wide")];
  } else {
    g1 = [grp("categories", "What you need", chips("categories", O.categories.map(c => [c, words(c)])), "wide"), fld("quantity", "How much (kg)", num_("quantity", "decimal", "e.g. 20"))];
    g2 = [grp("storage_available", "Storage you have", chips("storage_available", O.storage.map(c => [c, words(c)])), "wide"),
      fld("min_hours_left_on_arrival", "Shelf life needed on arrival (hours)", num_("min_hours_left_on_arrival", "decimal", "e.g. 6")),
      grp("buyer_type", "You are a", `<div class="opts">${O.buyer_types.map(b => `<label><input type="radio" name="postBt" value="${b.key}" ${F.buyer_type === b.key ? "checked" : ""}>${esc(b.name)}</label>`).join("")}</div>`, "wide"),
      F.buyer_type !== "food_bank" ? `<label class="check mfield wide"><input type="checkbox" data-post-check="buyer_fssai_licensed" ${F.buyer_fssai_licensed ? "checked" : ""}> We hold an FSSAI licence or registration</label>` : ""];
  }
  const priceKey = kind === "perishable" ? "normal_price_per_kg" : "new_price_per_kg";
  const g3 = [fld("buyer_name", "Your business name", txt("buyer_name", "e.g. Ezhil Engineering Works"), "wide"),
    fld("needed_by", "Needed by", `<input data-post="needed_by" type="date" min="${new Date().toISOString().slice(0, 10)}" value="${esc(F.needed_by)}" ${bad("needed_by")}>`),
    kind === "perishable" && F.buyer_type === "food_bank" ? "" : fld(priceKey, kind === "perishable" ? "What you pay your usual supplier (₹/kg)" : "What new material costs you (₹/kg)", num_(priceKey, "decimal")),
    fld("x_km", "Your location: x (km on the demo map)", num_("x_km", "decimal")), fld("y_km", "Your location: y (km)", num_("y_km", "decimal"))];
  const msg = st.postMsg && st.postMsg.lane === lane ? `<div class="mres ok" role="status"><b>Saved as a standing order (${esc(st.postMsg.demand_id)})</b><p>Workshops see it in Sold before it's cut, and it shows in "What can this become?" as "Ordered by ${esc(short(st.postMsg.buyer))}".</p><div class="ctrl"><button class="btn sm" type="button" id="toReqs">See my requirements</button></div></div>` : "";
  el.innerHTML = `<div class="card"><form id="postForm" class="form" novalidate>
      <div class="ctrl"><h2>Your requirement</h2>${badge("sample", "Demo")}${lane === "durable" ? `<div class="grow"></div><div class="opts kindsw" role="radiogroup" aria-label="What kind of part"><label><input type="radio" name="postKind" value="flat" ${kind === "flat" ? "checked" : ""}>Sheet parts</label><label><input type="radio" name="postKind" value="linear" ${kind === "linear" ? "checked" : ""}>Bars and tubes</label></div>` : ""}</div>
      ${group(1, kind === "perishable" ? "What you need" : "The part", g1)}
      ${group(2, kind === "perishable" ? "How you can receive it" : "What you accept", g2)}
      ${group(3, "You, and when you need it", g3)}
      ${E.kind || E.standing ? `<p class="err">${ic("triangle-alert")}${esc(E.kind || E.standing)}</p>` : ""}
      <div class="actions"><button class="btn pri lg" type="submit" id="postFind">${ic("search")}Find it now</button>
        ${kind === "flat" ? `<button class="btn lg" type="button" id="postStanding">${ic("clock")}Keep it open as a standing order</button>` : ""}
        <p class="note">${kind === "flat" ? "Find it now searches every seller's stock today. A standing order stays open, so a workshop can cut your parts from the spare part of its next sheet." : "Find it now searches every seller's stock today."} The price is prefilled with the cluster's sample price; change it to what you really pay.</p></div>
    </form>${msg}</div>`;
  const form = $("#postForm", el);
  form.querySelectorAll("[data-post]").forEach(i => { i.oninput = i.onchange = () => {
    F[i.dataset.post] = i.value;
    if (i.dataset.post === "material") {  // new material: its grades and sample price
      const m = O.materials.find(x => x.key === i.value);
      F.accepted_grades = m.grades.slice(0, 1); F.new_price_per_kg = String(m.sample_price ?? "");
      const c = O.centre[i.value === "mild_steel" ? "steel" : "sheets"]; F.x_km = String(c[0]); F.y_km = String(c[1]);
      render();
    }
    if (i.dataset.post === "profile") { const pr = O.profiles.find(x => x.profile === i.value); F.new_price_per_kg = String(pr.sample_price ?? ""); }
  }; });
  form.querySelectorAll("[data-chip]").forEach(i => i.onchange = () => {
    const k = i.dataset.chip; F[k] = i.checked ? [...new Set([...F[k], i.value])] : F[k].filter(v => v !== i.value);
  });
  form.querySelectorAll("[data-post-check]").forEach(i => i.onchange = () => { F[i.dataset.postCheck] = i.checked; });
  form.querySelectorAll('input[name="postBt"]').forEach(i => i.onchange = () => { F.buyer_type = i.value; render(); });
  el.querySelectorAll('input[name="postKind"]').forEach(i => i.onchange = () => { st.postKind = i.value; st.postErr = {}; render(); });
  const tr = $("#toReqs", el); if (tr) tr.onclick = () => go({view: "reqs"});
  const send = async standing => {
    const fields = {...F}, needed_by = F.needed_by || null;
    delete fields.needed_by;
    if (kind === "flat" && fields.grades_text !== undefined) { fields.accepted_grades = fields.grades_text.split(",").map(x => x.trim()).filter(Boolean); delete fields.grades_text; }
    if (kind === "linear") { const pr = O.profiles.find(x => x.profile === F.profile); fields.mass_per_m_kg = pr.mass_per_m_kg; fields.material = pr.material; }
    if (kind === "perishable" && F.buyer_type === "food_bank") delete fields.normal_price_per_kg;
    let r, t;
    try { r = await fetch("/api/requirements", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({lane, kind, fields, needed_by, standing})}); t = await r.json().catch(() => ({})); }
    catch (x) { st.postErr = {kind: "The app could not be reached. Check that it is still running, then try again."}; return render(); }
    if (!r.ok) { st.postErr = t.detail && t.detail.errors ? t.detail.errors : {kind: typeof t.detail === "string" ? t.detail : "Something went wrong. Try again."}; st.postMsg = null; await render();
      const first = document.querySelector('#postForm [aria-invalid="true"], #postForm .ferr:not(:empty)'); if (first) (first.matches("input,select") ? first : first.parentElement.querySelector("input,select") || first).focus?.(); return; }
    st.postErr = {}; st.post[kind] = null;
    if (standing) { st.postMsg = {lane, demand_id: t.demand_id, buyer: t.demand.buyer_name}; toast(`Standing order ${esc(t.demand_id)} saved. Workshops can now cut it from their next sheet.`); return render(); }
    st.postMsg = null; st.scen[lane] = t.scenario; st.ordId[t.scenario] = t.demand_id; st.off[t.scenario] = new Set();
    toast(`Requirement ${esc(t.demand_id)} saved. Here is what the sellers can supply.`);
    go({view: "match"});
  };
  form.onsubmit = e => { e.preventDefault(); send(false); };
  const sb = $("#postStanding", el); if (sb) sb.onclick = () => send(true);
}
async function vReqs(el) {
  const r = await api(`/api/requirements?lane=${st.lane}`), list = r.requirements;
  if (!list.length) {
    el.innerHTML = emptyState("clipboard-list", "You have not posted anything yet. Post what you need and it will be listed here with its status.", `<button class="btn pri" type="button" id="toPost">${ic("file-plus-2")}Post a requirement</button>`);
    $("#toPost", el).onclick = () => go({view: "post"});
    return;
  }
  const pill = {open: "p-mut", reserved: "p-warn", matched: "p-ok", closed: "p-mut"};
  const what = q => { const d = q.demand;
    return q.kind === "perishable" ? `${num(d.quantity, 1)} kg of ${d.categories.map(c => c.replace(/_/g, " ")).join(", ")} · ${d.storage_available.join(", ")} · ${num(d.min_hours_left_on_arrival)} h on arrival${d.buyer_type === "ngo" ? " · donation" : ""}`
      : q.kind === "linear" ? `${num(d.quantity)} × ${esc(d.part_name)} · ${esc(d.profile)} · ${num(d.cut_length_mm)} mm`
      : `${num(d.quantity)} × ${esc(d.part_name)} · ${num(d.length_mm)} × ${num(d.width_mm)} × ${num(d.thickness_mm, d.thickness_mm % 1 ? 1 : 0)} mm ${esc(d.material.replace(/_/g, " "))} · ${d.accepted_grades.map(esc).join("/")}`; };
  el.innerHTML = `<div class="card"><div class="scroll"><table><tr><th>Requirement</th><th>What</th><th>Needed by</th><th>Status</th><th class="noprint"></th></tr>${list.map(q => `<tr ${q.status === "closed" ? 'class="closed"' : ""}>
      <td><b>${esc(orderName(q.demand))}</b> ${codeTag(q.demand_id)}${q.standing ? '<div><span class="pill p-mut" title="Kept open for workshops about to cut">Standing order</span></div>' : ""}</td><td class="w">${what(q)}</td>
      <td>${q.needed_by ? esc(q.needed_by) : "–"}</td><td class="w"><span class="pill ${pill[q.status]}">${esc(q.status)}</span><div class="note">${esc(q.status_text)}</div></td>
      <td class="noprint"><span class="ctrl">${q.status !== "closed" ? `<button class="btn sm" type="button" data-find="${q.id}">Find again</button>` : ""}${q.status === "open" || q.status === "reserved" ? `<button class="btn sm ghost" type="button" data-close="${q.id}">Close</button>` : ""}</span></td></tr>`).join("")}</table></div>
    <p class="note">${badge("posted")} Reserved means a workshop's cutting plan includes your parts; if the workshop declines, it goes back to Open.</p></div>`;
  el.querySelectorAll("[data-find]").forEach(b => b.onclick = () => {
    const q = list.find(x => x.id === +b.dataset.find);
    st.scen[st.lane] = q.scenario; st.ordId[q.scenario] = q.demand_id; st.off[q.scenario] = new Set(); go({view: "match"});
  });
  el.querySelectorAll("[data-close]").forEach(b => b.onclick = async () => { await api(`/api/requirements/${b.dataset.close}/close`, {}); toast("Requirement closed."); render(); });
}

/* ---------------- Buyer: browse surplus (marketplace) ---------------- */
const cap = cap1;
const labelChip = l => badge(l === "measured" ? "measured" : (l || "sample"));
function pieceSvg(it, dims) {
  if (it.kind === "linear") {
    const R = Math.max(6000, it.length_mm), u = R / 40;
    return `<svg viewBox="-40 ${dims ? 60 : 170} ${R + 80} ${dims ? 380 : 260}" role="img" aria-label="${num(it.length_mm)} mm bar drawn to scale against a 6 m length" preserveAspectRatio="xMidYMid meet">
      <rect x="0" y="230" width="${R}" height="140" rx="20" style="fill:none;stroke:var(--mut);stroke-width:8;stroke-dasharray:40 30"/>
      <rect x="0" y="230" width="${it.length_mm}" height="140" rx="20" style="fill:var(--piece);stroke:var(--np);stroke-width:12"/>
      ${dims ? `<g style="stroke:var(--mut);stroke-width:${u * 0.12}"><line x1="0" y1="190" x2="${it.length_mm}" y2="190"/><line x1="0" y1="${190 - u * 0.45}" x2="0" y2="${190 + u * 0.45}"/><line x1="${it.length_mm}" y1="${190 - u * 0.45}" x2="${it.length_mm}" y2="${190 + u * 0.45}"/></g><text x="${it.length_mm / 2}" y="${190 - u * 0.6}" text-anchor="middle" style="font:500 ${u * 1.15}px var(--mono);fill:var(--mut)">${num(it.length_mm)} mm</text>` : ""}</svg>`;
  }
  const RL = Math.max(2500, it.length_mm), RW = Math.max(1250, it.width_mm), sw = RL / 300, u = RL / 42;
  const pad = dims ? u * 4.2 : sw * 4;
  return `<svg viewBox="${-pad} ${-pad} ${RL + pad + sw * 4} ${RW + pad + sw * 4}" role="img" aria-label="${num(it.length_mm)} by ${num(it.width_mm)} mm piece drawn to scale against a 2,500 by 1,250 mm sheet${(it.defects || []).length ? ", defects in red" : ""}" preserveAspectRatio="xMidYMid meet">
    <rect x="0" y="0" width="${RL}" height="${RW}" style="fill:none;stroke:var(--mut);stroke-width:${sw};stroke-dasharray:${sw * 5} ${sw * 4}"/>
    <rect x="0" y="0" width="${it.length_mm}" height="${it.width_mm}" rx="${sw}" style="fill:var(--piece);stroke:var(--np);stroke-width:${sw * 1.6}"/>
    ${(it.defects || []).map(d => `<rect x="${d.x_mm}" y="${d.y_mm}" width="${d.length_mm}" height="${d.width_mm}" style="fill:var(--bad);opacity:.55"><title>${esc(d.note || "defect")}</title></rect>`).join("")}
    ${dims ? dimLines(it.length_mm, it.width_mm, u) : ""}</svg>`;
}
function itemTitle(it) {
  if (it.kind === "perishable") return it.product;
  if (it.kind === "linear") return `${cap1(it.profile)}, ${num(it.length_mm)} mm`;
  return `${it.material_name} ${it.is_full_sheet ? "sheet" : "offcut"}, ${num(it.length_mm)} × ${num(it.width_mm)} × ${num(it.thickness_mm, it.thickness_mm % 1 ? 1 : 0)} mm`;
}
function specChips(it) {
  const xs = it.kind === "perishable" ? [`${num(it.quantity_kg, 1)} kg`, words(it.storage), words(it.category)] : [it.grade, words(it.condition), `${num(it.kg, 1)} kg`];
  return `<div class="specchips">${xs.map(x => `<span>${esc(x)}</span>`).join("")}</div>`;
}
function priceLine(it) {
  if (it.kind === "perishable") return it.disposal ? `<div class="price"><b>${inr(-it.disposal.value)}</b><span>Disposal cost the seller avoids · ₹${num(Math.abs(it.disposal.price_per_kg), 1)}/kg, ${esc(it.disposal.label)}</span></div>` : `<div class="price"><b>–</b><span>No disposal cost entered yet</span></div>`;
  if (!it.floor) return `<div class="price"><b>–</b><span>No price yet: needs a dated scrap quote</span></div>`;
  const c = it.ceiling, src = `Lowest: scrap value at ₹${num(it.floor.price_per_kg, 1)}/kg (${it.floor.label}, ${it.floor.quoted_on ? "quoted " + it.floor.quoted_on : "no date"}). ${c ? `Highest: the same material bought new at ₹${num(c.price_per_kg, 1)}/kg (${c.label}).` : "No highest price: no buyer here pays a new price for this material."}`;
  return `<div class="price" title="${esc(src)}"><b>${inr(it.floor.value)}${c ? ` – ${inr(c.value)}` : ""}</b><span>From its scrap value to the cost of new ${it.kind === "flat" ? "sheet" : "stock"}</span></div>`;
}
function freshBar(h) {
  const cls = h < 12 ? "bad" : h < 24 ? "amber" : "good", barCls = h < 12 ? "bad" : h < 24 ? "amber" : "pe";
  return `<div class="fresh"><div class="row"><span class="lbl">Freshness</span><b class="${cls}">${num(h, h % 1 ? 1 : 0)} h left</b></div><div class="bar ${barCls}" role="img" aria-label="${num(h)} hours of shelf life left"><i style="width:${Math.max(3, Math.min(1, h / 72) * 100)}%"></i></div></div>`;
}
function itemMedia(it) {
  const p = photoHtml(it);
  if (p) return p;
  if (it.kind === "perishable") return `<div class="media blank">${ic("leaf")}</div>`;
  return `<div class="media draw">${pieceSvg(it)}</div>`;
}
function productCard(it, lane) {
  const uid = `${it.key}:${it.id}`, dist = it.distance_km == null ? "Location not given" : `${num(it.distance_km, 1)} km away`;
  return `<article class="pcard" tabindex="0" data-open="${esc(uid)}" aria-label="${esc(itemTitle(it))}">${itemMedia(it)}
    <div class="pbody"><div class="ptitle"><h3>${esc(itemTitle(it))}</h3>${labelChip(it.label)}</div>
      ${specChips(it)}${it.kind === "perishable" ? freshBar(it.hours_left) : ""}${priceLine(it)}
      <div class="who"><span title="${esc(it.seller_name)}">${ic("building-2", "sm")}${esc(short(it.seller_name))}</span><span>${ic("map-pin", "sm")}${dist}</span></div>
      <div class="pact"><button class="btn np" type="button" data-order="${esc(uid)}" ${it.order ? "" : "disabled"}>${lane === "fresh" ? "Order from this" : "Order parts from this"}</button><button class="btn" type="button" data-become="${esc(uid)}">What can this become?</button></div>
      ${it.order ? "" : `<p class="why">${ic("info", "sm")}<span>${esc(it.order_reason)}</span></p>`}</div></article>`;
}
async function openItem(it, focusBecome) {
  const lane = it.kind === "perishable" ? "fresh" : "durable", url = location.origin + it.passport;
  const specs = it.kind === "perishable"
    ? [["Product", it.product], ["Category", words(it.category)], ["Quantity", `${num(it.quantity_kg, 1)} kg`], ["Storage", words(it.storage)], ["Shelf life left", `${num(it.hours_left, 1)} hours`]]
    : it.kind === "flat" ? [["Material", `${it.material_name}, ${it.grade}`], ["Size", `${num(it.length_mm)} × ${num(it.width_mm)} mm`], ["Thickness", `${num(it.thickness_mm, it.thickness_mm % 1 ? 1 : 0)} mm`], ["Condition", words(it.condition)], ["Weight", `${num(it.kg, 1)} kg`]]
    : [["Profile", cap1(it.profile)], ["Length", `${num(it.length_mm)} mm`], ["Material", `${it.material_name}, ${it.grade}`], ["Condition", words(it.condition)], ["Weight", `${num(it.kg, 1)} kg`]];
  specs.push(["Seller", it.seller_name], ["Distance", it.distance_km == null ? "Location not given" : `${num(it.distance_km, 1)} km`], ["Item", it.id]);
  const d = drawer.open(esc(itemTitle(it)), `${photoHtml(it)}
    <div class="ctrl">${labelChip(it.label)}${it.rules ? `<span class="pill p-mut" title="${esc(it.rules.join("; "))}">${esc(it.source || "Imported")}</span>` : ""}</div>
    ${priceLine(it)}${it.kind === "perishable" ? freshBar(it.hours_left) : ""}
    <dl class="specs">${specs.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${it.kind !== "perishable" ? `<div><h4>Drawn to scale</h4><div class="media draw" style="aspect-ratio:auto">${pieceSvg(it, true)}</div><p class="note">Against a ${it.kind === "flat" ? "2,500 × 1,250 mm sheet" : "6 m length"} (dashed)${(it.defects || []).length ? "; red marks a defect the cutting plan avoids" : ""}.</p></div>` : ""}
    <div class="pact"><button class="btn np lg" type="button" id="dOrder" ${it.order ? "" : "disabled"}>${lane === "fresh" ? "Order from this" : "Order parts from this"}</button></div>
    ${it.order ? `<p class="note">Opens ${esc(short(it.order.buyer_name))}'s order with only this seller selected.</p>` : `<p class="why">${ic("info", "sm")}<span>${esc(it.order_reason)}</span></p>`}
    <div id="dBecome"><h3>What can this become?</h3><div class="skel" style="height:96px"></div></div>
    <div><h4>Passport</h4><div class="qrbox"><img alt="QR code for the passport of ${esc(it.id)}" src="/api/qr.svg?data=${encodeURIComponent(url)}"><div class="note">Scan to open this item's passport, or <a href="${esc(it.passport)}" target="_blank" rel="noopener">open it here ${ic("external-link", "sm")}</a>.</div></div></div>`);
  $("#dOrder", d).onclick = () => { drawer.close(); orderFrom(it); };
  if (focusBecome) $("#dBecome", d).scrollIntoView({block: "start"});
  const box = $("#dBecome", d);
  try { box.innerHTML = `<h3>What can this become?</h3>${becomeHtml(await api(`/api/become/${encodeURIComponent(it.key)}/${encodeURIComponent(it.id)}`), 3)}`; }
  catch (x) { box.innerHTML = `<h3>What can this become?</h3>${errBox(x)}`; }
}
async function orderFrom(it) {
  const k = it.order.scenario, S = await scen(k), lane = it.kind === "perishable" ? "fresh" : "durable";
  st.scen[lane] = k; st.ordId[k] = it.order.demand_id;
  st.off[k] = new Set(S.data.sellers.map(x => x.id).filter(id => id !== (it.match_seller || it.seller_id)));
  go({view: "match"});
}
async function vBrowse(el) {
  const lane = st.lane, F = st.br[lane], durable = lane === "durable";
  const r = await api(`/api/browse/${lane}`);
  const items = r.items, byUid = Object.fromEntries(items.map(it => [`${it.key}:${it.id}`, it]));
  const matOf = it => durable ? it.material_name : words(it.category);
  const uniq = xs => [...new Set(xs)].sort((a, b) => typeof a === "number" ? a - b : String(a).localeCompare(String(b)));
  const mats = uniq(items.map(matOf)), thicks = uniq(items.filter(it => it.kind === "flat").map(it => it.thickness_mm)), sellers = uniq(items.map(it => short(it.seller_name)));
  const sorts = durable ? [["distance", "Nearest first"], ["size", "Largest first"], ["price", "Lowest price first"]] : [["hours", "Soonest use-by first"], ["distance", "Nearest first"]];
  const fchip = (id, cur, all, opts) => `<span class="fchip ${cur ? "on" : ""}"><select id="${id}" aria-label="${esc(all)}"><option value="">${esc(all)}</option>${opts.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></span>`;
  el.innerHTML = `<div class="searchbar" role="search">${ic("search")}<input id="brQ" type="search" placeholder="${durable ? "Search steel, aluminium, plywood, a grade or a seller" : "Search tomatoes, paneer, bread or a seller"}" value="${esc(F.q)}" aria-label="Search surplus"></div>
    <div class="filters">${fchip("brMat", F.mat, durable ? "All materials" : "All categories", mats.map(m => [m, m]))}
      ${durable ? fchip("brThick", F.thick, "Any thickness", thicks.map(t => [t, `${num(t, t % 1 ? 1 : 0)} mm`])) : ""}
      ${fchip("brSeller", F.seller, "All sellers", sellers.map(s => [s, s]))}
      ${fchip("brDist", F.dist, "Any distance", [["2", "Within 2 km"], ["3", "Within 3 km"], ["5", "Within 5 km"]])}
      <div class="grow"></div><span class="lbl">Sort</span><span class="fchip"><select id="brSort" aria-label="Sort">${sorts.map(([k, t]) => `<option value="${k}" ${k === F.sort ? "selected" : ""}>${t}</option>`).join("")}</select></span></div>
    <div class="ctrl"><span id="brCount" class="count" aria-live="polite"></span><button class="btn sm ghost" id="brClear" type="button">Clear filters</button><div class="grow"></div><span class="note">Distances are straight-line km on the demo map. ${durable ? "" : "Food rules are platform rules that still need expert review."}</span></div>
    <div id="brGrid" class="pgrid"></div>`;
  const grid = $("#brGrid", el), count = $("#brCount", el);
  function paint() {
    const q = F.q.trim().toLowerCase();
    let xs = items.filter(it => (!F.mat || matOf(it) === F.mat) && (!F.thick || (it.kind === "flat" && String(it.thickness_mm) === String(F.thick))) && (!F.seller || short(it.seller_name) === F.seller)
      && (!F.dist || (it.distance_km != null && it.distance_km <= +F.dist))
      && (!q || [it.id, it.seller_name, it.grade, it.product, it.material_name, it.profile, it.category, it.condition].some(v => v && String(v).toLowerCase().includes(q))));
    const far = v => v == null ? Infinity : v;
    if (F.sort === "distance") xs.sort((a, b) => far(a.distance_km) - far(b.distance_km));
    else if (F.sort === "size") xs.sort((a, b) => (b.length_mm * (b.width_mm || 1)) - (a.length_mm * (a.width_mm || 1)));
    else if (F.sort === "price") xs.sort((a, b) => far(a.floor && a.floor.value) - far(b.floor && b.floor.value));
    else xs.sort((a, b) => a.hours_left - b.hours_left);
    count.textContent = `${xs.length} of ${items.length} ${durable ? "pieces" : "lots"}`;
    grid.innerHTML = !items.length ? emptyState("inbox", "Nothing is on offer in this lane yet. Sellers add stock under Connect your system.")
      : xs.length ? xs.map(it => productCard(it, lane)).join("") : emptyState("search", `No ${durable ? "pieces" : "lots"} match these filters.`, `<button class="btn" type="button" id="brClear2">Clear filters</button>`);
    const c2 = $("#brClear2", grid); if (c2) c2.onclick = clear;
  }
  function clear() { Object.assign(F, {q: "", mat: "", thick: "", seller: "", dist: ""}); render(); }
  $("#brQ", el).oninput = e => { F.q = e.target.value; paint(); };
  for (const [id, k] of [["brMat", "mat"], ["brThick", "thick"], ["brSeller", "seller"], ["brDist", "dist"], ["brSort", "sort"]]) {
    const s = $("#" + id, el); if (s) s.onchange = () => { F[k] = s.value; s.parentElement.classList.toggle("on", !!s.value && k !== "sort"); paint(); };
  }
  $("#brClear", el).onclick = clear;
  grid.addEventListener("click", e => {
    if (e.target.closest("a")) return;
    const bo = e.target.closest("[data-order]"), bb = e.target.closest("[data-become]"), card = e.target.closest("[data-open]");
    if (bo) return orderFrom(byUid[bo.dataset.order]);
    if (bb) return openItem(byUid[bb.dataset.become], true);
    if (card) openItem(byUid[card.dataset.open]);
  });
  grid.addEventListener("keydown", e => { const card = e.target.closest("[data-open]"); if (card && e.target === card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openItem(byUid[card.dataset.open]); } });
  paint();
}

/* ---------------- Buyer: order and match ---------------- */
async function vMatch(el) {
  const k = laneScen(), S = await scen(k), fresh = S.meta.engine === "perishable";
  const posted = (await api(`/api/requirements/demands/${k}`)).demands;  // the buyer's own requirements first
  const allD = [...posted, ...S.data.demands];
  const d = allD.find(x => x.id === st.ordId[k]) || allD[0];
  const off = st.off[k] || (st.off[k] = new Set());
  const rate = st.rate[k] ?? 1;
  const own = await api(`/api/own-stock/${k}`);  // imported, auto-synced and hand-entered items, as "Your workshop"
  const D = {...S.data, demands: allD, ...(own.resources.length ? {sellers: [...S.data.sellers, own.seller], resources: [...S.data.resources, ...own.resources]} : {})};
  const data = structuredClone(D);
  data.transport.rate_multiplier = rate;
  const sellerIds = D.sellers.map(s => s.id).filter(id => !off.has(id));
  const sellerName = id => (D.sellers.find(s => s.id === id) || {}).name || id;
  let f, err;
  try { f = await api("/api/fulfil", {demand_id: d.id, data, seller_ids: sellerIds, allow_partial: true}); } catch (e) { err = e; }
  const optLabel = x => `${orderName(x)} · ${fresh ? num(x.quantity, 0) + " kg" : num(x.quantity) + " parts"} · ${x.id}${x.status === "matched" ? " · matched" : ""}`;
  const ticked = D.sellers.filter(s => !off.has(s.id)).length;
  const ctrl = `<div class="card noprint"><div class="ctrl">${await scenSeg()}
      <label class="field grow">Order<select id="ordSel">${posted.length ? `<optgroup label="Your requirements">${posted.map(x => `<option value="${esc(x.id)}" ${x.id === d.id ? "selected" : ""}>${esc(optLabel(x))}</option>`).join("")}</optgroup><optgroup label="Sample orders">` : ""}${S.data.demands.map(x => `<option value="${esc(x.id)}" ${x.id === d.id ? "selected" : ""}>${esc(optLabel(x))}</option>`).join("")}${posted.length ? "</optgroup>" : ""}</select></label></div>
    ${fold(`Sellers and transport: ${ticked} of ${D.sellers.length} sellers`, `<div class="ctrl">${D.sellers.map(s => `<label class="check"><input type="checkbox" data-seller="${esc(s.id)}" ${off.has(s.id) ? "" : "checked"}>${esc(short(s.name))}</label>`).join("")}</div>
      <label class="field">Transport rate ×<input id="rate" type="number" min="0.2" max="5" step="0.1" value="${rate}"></label>`, false, "fSellers")}</div>`;
  if (err) { el.innerHTML = ctrl + errBox(err); bind(); return; }
  const b = f.best;
  let h = ctrl;
  if (!b) h += `<div class="card">${emptyState("circle-x", `No seller can supply ${esc(orderName(d))} right now. ${esc(f.message)}`)}${fold(`Why each piece was refused (${f.rejections.length})`, rejList(f))}</div>`;
  else {
    const v = b.value, n = b.sellers_used.length, unit = fresh ? "kg" : f.unit_name;
    const what = `${num(b.units, fresh ? 1 : 0)} ${unit} from ${n} seller${n === 1 ? "" : "s"}, ${num(b.route_km, 1)} km pickup by ${esc(b.vehicle || "vehicle")}${b.trips > 1 ? ` (${b.trips} trips)` : ""}.`;
    h += `<div class="card"><div class="hero">
        <div class="stack" style="gap:12px"><div class="ctrl">${f.feasible ? `<span class="pill p-ok">${ic("circle-check", "sm")}Fully matched</span>` : `<span class="pill p-warn">${ic("triangle-alert", "sm")}Short by ${num(f.shortfall, 1)} ${esc(unit)}</span>`}${v.is_donation ? `<span class="pill p-pe">Donation</span>` : ""}${badge("sample")}</div>
          <h2>${esc(orderName(d))} ${codeTag(d.id)}</h2>
          <p class="hero-sub">${what}</p>
          ${v.is_donation ? `<div><div class="hero-num">${inr(-v.salvage_value_S)}</div><p class="hero-sub">disposal cost avoided, for ${inr(v.transport_T)} transport</p></div>`
            : `<div><div class="hero-num acc">${inr(v.gain_G)}</div><p class="hero-sub">value created, compared with selling it as scrap</p></div>`}
          <div class="actions noprint"><button class="btn pri lg" id="accept" type="button">${ic("circle-check")}Accept this order</button><span class="note">A demo deal: it is recorded as simulated and no real order is placed.</span></div></div>
        <div class="stack" style="gap:var(--s2)">${v.is_donation ? "" : `<h4>Where the buyer's money goes</h4>${ladder({scrap: v.salvage_value_S, transport: v.transport_T, gain: v.gain_G, scrapLabel: fresh ? "Disposal value" : "Scrap value"})}
          <p class="note">The buyer would pay ${inr(v.buyer_value_W)} for the same ${fresh ? "food from the usual supplier" : "parts cut from new sheet"}. Prices are sample data.</p>`}</div></div></div>`;
    const capOf = id => f.seller_capacity.find(c => c.seller_id === id) || {};
    h += `<div class="stack" style="gap:12px"><h2>Who supplies it</h2><div class="pgrid" style="grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr))">${b.per_seller.map(p => `<div class="card" style="gap:12px">
        <div class="ctrl" style="gap:10px">${ic("building-2", "lg")}<div><b>${esc(short(p.seller_name))}</b><div class="note">${esc(p.seller_name.split(", ")[1] || "")}</div></div></div>
        <div class="statrow tight"><div class="stat"><b>${num(p.units, fresh ? 1 : 0)}</b><span>${esc(unit)}</span></div><div class="stat"><b>${num(p.kg_shipped, 1)}</b><span>kg collected</span></div><div class="stat"><b>${num(capOf(p.seller_id).distance_km, 1)}</b><span>km away</span></div></div></div>`).join("")}</div></div>`;
    const sheets = b.allocations.filter(a => a.shape === "sheet" && a.parts && a.parts.length);
    if (sheets.length) h += `<div class="card"><div class="section-title"><h2>Cutting plans</h2><span class="mut">${sheets.length} piece${sheets.length === 1 ? "" : "s"}</span></div>
      <p class="mut">Each grey shape is a seller's piece, drawn to scale. The yellow parts are placed with a ${num(S.data.settings.kerf_mm, 1)} mm ${term("blade gap", "kerf")} and a ${num(S.data.settings.edge_margin_mm)} mm edge strip that is never cut; red marks a defect the plan avoids.</p>
      <div class="sheets">${sheets.slice(0, 12).map(sheetSvg).join("")}</div>${sheets.length > 12 ? `<p class="note">Showing 12 of ${sheets.length} pieces used.</p>` : ""}</div>`;
    else if (fresh) h += `<div class="card"><h2>Lots taken</h2><div class="scroll"><table><tr><th>Lot</th><th>Seller</th><th class="n">Taken kg</th><th class="n">Hours left</th><th>Storage</th></tr>${b.allocations.map(a => `<tr><td>${esc(a.resource_id)}</td><td>${esc(short(sellerName(a.seller_id)))}</td><td class="n">${num(a.taken_kg, 1)}</td><td class="n">${num(a.hours_left)}</td><td>${esc(words(a.storage))}</td></tr>`).join("")}</table></div><p class="note">Food rules are platform rules and still need expert review.</p></div>`;
    else h += `<div class="card"><h2>Pieces used</h2><div class="scroll"><table><tr><th>Piece</th><th>Seller</th><th class="n">Cut pieces</th></tr>${b.allocations.map(a => `<tr><td>${esc(a.resource_id)}</td><td>${esc(sellerName(a.seller_id))}</td><td class="n">${num(a.units)}</td></tr>`).join("")}</table></div></div>`;
    h += `<div class="card">${fold("Show details", `
        <div class="statrow"><div class="stat"><b>${inr(v.buyer_saving)}</b><span>The buyer saves</span></div><div class="stat"><b>${inr(v.seller_gain_above_salvage)}</b><span>Sellers earn above ${fresh ? "disposal" : "scrap"} value</span></div></div>
        <p class="note">Pickup order: ${b.pickup_order.map(id => esc(short(sellerName(id)))).join(" → ")}. ${term("What would happen otherwise", "counterfactual")}: the stock would be ${esc(b.counterfactual.join(", "))}. ${n > 1 ? `The order is ${term("combined from several sellers", "pooling")}.` : ""}</p>
        <div><h4>What every seller could supply</h4>${f.seller_capacity.map(c => `<div class="orow"><div>${esc(short(c.seller_name))}<div class="note">${num(c.distance_km, 1)} km away</div></div><div class="bar ${fresh ? "pe" : ""}"><i style="width:${Math.min(100, c.capacity / Math.max(1, ...f.seller_capacity.map(z => z.capacity)) * 100)}%"></i></div><div class="v">${num(c.capacity, fresh ? 1 : 0)}</div></div>`).join("")}</div>
        <div>${rejList(f)}</div>
        <div><h4>Other combinations the engine tried</h4><div class="scroll"><table><tr><th>Sellers</th><th class="n">Value created</th><th class="n">km</th></tr>${f.alternatives.map(a => `<tr><td>${a.sellers_used.map(id => esc(short(sellerName(id)))).join(" + ")}</td><td class="n">${inr(a.value.gain_G)}</td><td class="n">${num(a.route_km, 1)}</td></tr>`).join("") || '<tr><td colspan="3" class="mut">None</td></tr>'}</table></div></div>`)}</div>`;
  }
  el.innerHTML = h;
  bind();
  function bind() {
    bindScen(el);
    $("#ordSel", el).onchange = e => { st.ordId[k] = e.target.value; render(); };
    const rt = $("#rate", el); if (rt) rt.onchange = e => { const x = +e.target.value; if (x > 0) { st.rate[k] = x; render(); } };
    el.querySelectorAll("[data-seller]").forEach(c => c.onchange = () => { c.checked ? off.delete(c.dataset.seller) : off.add(c.dataset.seller); render(); });
    const fs = $("#fSellers", el); if (fs) { fs.open = !!st.sellersOpen; fs.ontoggle = () => { st.sellersOpen = fs.open; }; }
    const acc = $("#accept", el);
    if (acc) acc.onclick = async () => {
      acc.disabled = true;
      try {
        const deal = await api("/api/deals", {kind: S.meta.engine, counterfactual: b.counterfactual[0], demand_id: d.id, buyer_name: d.buyer_name,
          sellers: b.sellers_used, units: b.units, unit_name: f.unit_name, kg_in_units: b.kg_in_units, kg_shipped: b.kg_shipped,
          route_km: b.route_km, value_above_salvage: b.value.seller_gain_above_salvage, buyer_utilisation: d.buyer_utilisation ?? null,
          scenario: k, rate_multiplier: rate, requirement_id: d.requirement_id ?? null});
        st.lastDeal = deal.id;
        toast(`Deal #${deal.id} agreed (simulated). <a href="#buyer/pickup/${st.lane}">Book the pickup</a> or <a href="#buyer/deals/${st.lane}">track it in My deals</a>.`);
        acc.innerHTML = `${ic("circle-check")}Accepted as deal #${deal.id}`;
      } catch (e) { toast(esc(e.message), "bad"); acc.disabled = false; }
    };
  }
}
function rejList(f) {
  return `<h4>Refused by the rules (${f.rejections.length})</h4><ul class="rej">${f.rejections.map(r => `<li><code>${esc(r.resource_id)}</code> ${esc(r.reason)}</li>`).join("") || "<li>None</li>"}</ul>`;
}

/* ---------------- Deals (buyer) and deal monitor (admin) ---------------- */
async function vDeals(el, admin) {
  let r;
  try { r = await api("/api/ledger", {}); } catch (e) { el.innerHTML = errBox(e); return; }
  const ds = r.deals;
  const tiles = admin ? `<div class="tiles"><div class="tile"><b>${ds.length}</b><span>Deals</span></div><div class="tile"><b>${ds.filter(x => x.status === "dispatched").length}</b><span>Dispatched</span></div><div class="tile"><b>${ds.filter(x => x.status === "delivered").length}</b><span>Delivered</span></div><div class="tile hi"><b>${ds.filter(x => !x.is_simulated && x.status === "delivered").length}</b><span>Verified (real, with evidence)</span></div></div>` : "";
  if (!ds.length) {
    el.innerHTML = tiles + `<div class="empty">No deals yet. Go to <a href="#buyer/match/${st.lane}" id="toMatch">Order and match</a> and accept an order to create one.</div>`;
    $("#toMatch", el).onclick = e => { e.preventDefault(); go({role: "buyer", view: "match"}); };
    return;
  }
  const step = s => ({agreed: 1, dispatched: 2, delivered: 3})[s];
  let h = tiles + `<div class="card"><div class="ctrl"><h2>${admin ? "All deals" : "Your deals"}</h2><div class="grow"></div><button class="btn sm" id="resetDeals" type="button">Clear demo deals</button></div><div class="scroll"><table><tr><th>Deal</th><th>Buyer</th><th>Sellers</th><th>Lane</th><th>Stage</th><th>Data</th><th>Next step</th></tr>${ds.map(x => `<tr ${x.id === st.lastDeal ? 'class="sel"' : ""}><td>#${x.id}</td><td>${esc(x.buyer_name)}</td><td>${x.sellers.map(esc).join(", ")}</td><td>${x.kind === "perishable" ? "Fresh" : "Durable"}</td><td><span class="pill ${x.status === "delivered" ? "p-ok" : "p-mut"}">${step(x.status)}/3 ${esc(x.status)}</span></td><td>${chip(x.is_simulated ? "simulated" : "verified")}</td><td>${
    x.status === "agreed" ? `<button class="btn sm" data-dispatch="${x.id}" type="button">Seller marks dispatched</button>` :
    x.status === "dispatched" ? `<span class="ctrl"><input aria-label="Delivery evidence" placeholder="Challan no., e.g. DC-104" data-ev="${x.id}" style="width:170px"><label class="check note"><input type="checkbox" data-conf="${x.id}"> buyer confirms</label><button class="btn sm" data-deliver="${x.id}" type="button">Mark delivered</button></span>` : "Done"}</td></tr>`).join("")}</table></div><p id="dealMsg" class="note" aria-live="polite">A simulated deal stays simulated even after delivery evidence is added.</p></div>`;
  const sel = ds.find(x => x.id === st.lastDeal) || ds[0];
  h += `<div class="card"><h2>Impact ledger: deal #${sel.id}</h2><div class="scroll"><table><tr><th>Measure</th><th class="n">Value</th><th>Label</th></tr>${(sel.impact.metrics || []).map(m => `<tr><td class="w">${esc(m.metric)}${m.note ? `<div class="note">${esc(m.note)}</div>` : ""}${m.source ? `<div class="note">${esc(m.source)}</div>` : ""}</td><td class="n">${m.value == null ? "–" : m.unit === "INR" ? inr(m.value) : num(m.value, 1) + " " + esc(m.unit)}</td><td>${chip(m.label)}</td></tr>`).join("")}</table></div><p class="note">CO₂ stays empty until a cited emission factor is entered. No landfill diversion is claimed for metals, because they are recycled anyway.</p></div>`;
  el.innerHTML = h;
  const msg = t => { $("#dealMsg", el).innerHTML = t; };
  $("#resetDeals", el).onclick = async () => { await api("/api/reset", {}); st.lastDeal = null; render(); };
  el.querySelectorAll("[data-dispatch]").forEach(b => b.onclick = async () => {
    try { await api(`/api/deals/${b.dataset.dispatch}/advance`, {status: "dispatched"}); st.lastDeal = +b.dataset.dispatch; render(); } catch (e) { msg(`<span class="err">${esc(e.message)}</span>`); }
  });
  el.querySelectorAll("[data-deliver]").forEach(b => b.onclick = async () => {
    const id = b.dataset.deliver, ev = $(`[data-ev="${id}"]`, el).value.trim(), conf = $(`[data-conf="${id}"]`, el).checked;
    try { await api(`/api/deals/${id}/advance`, {status: "delivered", evidence: ev ? [ev] : [], buyer_confirmed: conf}); st.lastDeal = +id; render(); }
    catch (e) { msg(`<span class="err">${esc(e.message)}</span>`); }
  });
}

/* ---------------- Seller: connect your system (CSV import and surplus detection) ---------------- */
const IMP_COLS = {
  durable: "item_id, seller_id, material, length_mm, width_mm, thickness_mm, grade, condition, is_full_sheet, days_unused",
  fresh: "item_id, seller_id, product, category, quantity_kg, storage, hours_left",
};
function impDesc(lane, it) {
  return lane === "durable" ? `${num(it.length_mm)} × ${num(it.width_mm)} × ${num(it.thickness_mm, it.thickness_mm % 1 ? 1 : 0)} mm ${it.material.replace(/_/g, " ")} ${it.grade}, ${it.condition.replace(/_/g, " ")}`
    : `${it.product}, ${num(it.quantity_kg, 1)} kg, ${it.storage}, ${it.category.replace(/_/g, " ")}`;
}
async function impPreview(lane) {
  const S = st.imp[lane];
  S.busy = true; S.err = null; S.listed = null; render();
  try { S.result = await api("/api/import/preview", {lane, csv_text: S.text, days_unused_limit: st.impLimit.days, hours_left_limit: st.impLimit.hours}); }
  catch (e) { S.result = null; S.err = e; }
  S.busy = false; render();
}
function syncLogTable(log) {
  if (!log.length) return `<div class="empty">No syncs yet. Save a stock export (.csv) into the folder, then press Sync now.</div>`;
  return `<table><tr><th>Time</th><th>File</th><th>Result</th><th class="n">Rows read</th><th class="n">Surplus found</th><th class="n">Rejected rows</th></tr>${log.map(x => `<tr>
    <td>${esc(new Date(x.at).toLocaleString())}</td><td><code>${esc(x.file || "–")}</code></td>
    <td class="w">${x.status === "imported" ? `<span class="pill p-ok">Imported</span> ${x.listed} listed as ${esc(x.label)}` : `<span class="pill p-bad">Not imported</span>`}<div class="note">${esc(x.message)}</div></td>
    <td class="n">${x.rows_read ?? "–"}</td><td class="n">${x.surplus ?? "–"}</td>
    <td class="n">${x.rejected ? `<details><summary>${x.rejected}</summary><ul class="rej">${x.details.map(d => `<li>Row ${d.row} <code>${esc(d.item_id || "no item_id")}</code> ${esc(d.reason)}</li>`).join("")}</ul></details>` : x.rejected ?? "–"}</td></tr>`).join("")}</table>`;
}
async function vConnect(el) {
  const lane = st.lane, S = st.imp[lane], durable = lane === "durable", r = S.result;
  const [C, L, O] = await Promise.all([api("/api/connectors"), api(`/api/connectors/folder/${lane}/log`), api("/api/manual/options")]), fc = C.folder[lane];
  const lim = durable ? `<label class="field">Surplus if unused for at least (days)<input id="impN" type="number" min="0" max="36500" step="1" value="${st.impLimit.days}"></label>`
    : `<label class="field">Urgent if use-by within (hours)<input id="impH" type="number" min="0" max="8760" step="1" value="${st.impLimit.hours}"></label>`;
  let h = `<section class="conns" aria-label="Connectors"><p class="mut">${esc(C.works_with)}</p><div class="conngrid">${C.connectors.map(c => {
      const on = c.status === "active", inner = `<div class="ctrl"><b>${esc(c.name)}</b><div class="grow"></div><span class="pill ${on ? "p-ok" : "p-mut"}">${on ? "Active" : "Planned"}</span></div><p class="note">${esc(c.description)}</p>`;
      return on ? `<button type="button" class="conn" data-goto="conn-${esc(c.key)}">${inner}</button>` : `<div class="conn planned" aria-disabled="true">${inner}</div>`;
    }).join("")}</div></section>
    ${manualCard(lane, O)}
    <div class="card" id="conn-csv_upload"><div class="ctrl"><h2>Import from ERP or POS export (CSV)</h2><div class="grow"></div><a class="btn" href="/api/import/example/${lane}" download>Download example file</a></div>
    <p class="mut">There is no live link to any outside software. Export your ${durable ? "sheet and offcut" : "food"} stock list as a CSV file from the system you use, then upload it here. Nothing is saved until you list items on the marketplace.</p>
    <div class="ctrl"><label class="field">Stock file (CSV)<input id="impFile" type="file" accept=".csv,text/csv"></label>${lim}
      ${S.name ? `<span class="note">Loaded: <b>${esc(S.name)}</b></span>` : ""}</div>
    <p class="note">Columns: <code>${IMP_COLS[lane]}</code>. Extra columns are ignored. The example file has three rows with mistakes, so you can see how rejected rows look.</p>
    <p class="note">Surplus rules: ${durable ? `a piece that is not a full sheet is an offcut; any piece unused for ${num(st.impLimit.days)} days or more is surplus.` : `a lot with ${num(st.impLimit.hours)} hours or less to its use-by is surplus and marked urgent. Food rules are platform rules that still need expert review.`}</p></div>`;
  if (S.busy) h += `<div class="spin">Checking every row…</div>`;
  else if (S.err) h += `<div class="err" role="alert"><b>The file could not be read.</b> ${esc(S.err.message)}</div>`;
  else if (!r) h += `<div class="empty">No file yet. Upload a stock export, or download the example file above and upload that.</div>`;
  else {
    const lbl = st.impMeasured ? "measured" : "sample";
    h += `<div class="tiles"><div class="tile"><b>${r.rows_read}</b><span>Rows read</span></div><div class="tile hi"><b>${r.surplus.length}</b><span>Found as surplus</span></div><div class="tile"><b>${r.kept.length}</b><span>Kept as working stock</span></div><div class="tile"><b>${r.rejected.length}</b><span>Rejected rows</span></div></div>`;
    h += `<div class="card"><div class="ctrl"><h2>Found as surplus (${r.surplus.length})</h2>${chip(lbl)}</div>${r.surplus.length ? `<div class="scroll"><table><tr><th class="n">Row</th><th>Item</th><th>Seller</th><th>What it is</th>${durable ? '<th class="n">Days unused</th>' : '<th class="n">Hours left</th>'}<th>Caught by</th></tr>${r.surplus.map(x => `<tr><td class="n">${x.row}</td><td>${esc(x.item.id)}</td><td>${esc(x.item.seller_id)}</td><td class="w">${esc(impDesc(lane, x.item))}</td><td class="n">${durable ? num(x.item.days_unused) : num(x.item.hours_left, x.item.hours_left % 1 ? 1 : 0)}</td><td class="w">${x.urgent ? '<span class="pill p-bad">Urgent</span> ' : ""}${x.rules.map(esc).join("<br>")}</td></tr>`).join("")}</table></div>
      <div class="ctrl noprint"><label class="check"><input type="checkbox" id="impMeas" ${st.impMeasured ? "checked" : ""}>These are measured values</label><button class="btn pri" id="impList" type="button">List surplus on the marketplace</button><span id="impMsg" class="note" aria-live="polite">${S.listed ? `${S.listed.listed} item(s) listed as ${esc(S.listed.label)}. <a href="#seller/stock/${lane}" id="toStock">Open Stock and passports</a> to see passports and QR codes.` : ""}</span></div>
      <p class="note">Listed items are labelled ${lbl === "measured" ? "Measured" : "Sample"}. Tick the box only if the sizes and dates come from your own records. No scrap price is shown for imported items until a dated quote is entered.</p>`
      : `<div class="empty">No surplus found with these rules. Every accepted item is working stock.</div>`}</div>`;
    h += `<div class="cols"><div class="card"><h2>Kept as working stock (${r.kept.length})</h2>${r.kept.length ? `<div class="scroll"><table><tr><th class="n">Row</th><th>Item</th><th>What it is</th><th>Why it stays</th></tr>${r.kept.map(x => `<tr><td class="n">${x.row}</td><td>${esc(x.item.id)}</td><td class="w">${esc(impDesc(lane, x.item))}</td><td class="w">${esc(x.reason)}</td></tr>`).join("")}</table></div>` : `<div class="empty">Every accepted item was found as surplus.</div>`}</div>
      <div class="card"><h2>Rejected rows (${r.rejected.length})</h2><ul class="rej">${r.rejected.map(x => `<li>Row ${x.row} <code>${esc(x.item_id || "no item_id")}</code> ${esc(x.reason)}</li>`).join("") || "<li>None. Every row passed the checks.</li>"}</ul>
      ${r.rejected.length ? `<p class="note">Fix these rows in your export and upload the file again. Row numbers count the header as row 1.</p>` : ""}</div></div>`;
  }
  h += `<div class="card" id="conn-folder_sync"><div class="ctrl"><h2>Auto-sync folder</h2>${fc.enabled ? `<span class="pill p-ok">On · every ${C.every_seconds} s</span>` : '<span class="pill p-mut">Off</span>'}<div class="grow"></div><button class="btn" id="fsSync" type="button">Sync now</button></div>
    <p class="mut">Save ${durable ? "sheet and offcut" : "food"} stock exports (.csv) into a folder on this computer. Each new or changed file goes through the same row checks and surplus rules as an upload, and its surplus is listed with passports and QR codes. Files are only read: never moved, changed or deleted. An unchanged file is never imported twice.</p>
    <div class="ctrl"><label class="field fspath">Folder on this computer (${durable ? "Durable" : "Fresh"} lane)<input id="fsPath" type="text" spellcheck="false" value="${esc(fc.path)}"></label></div>
    <div class="ctrl"><label class="check"><input type="checkbox" id="fsOn" ${fc.enabled ? "checked" : ""}> Check every ${C.every_seconds} seconds</label><label class="check"><input type="checkbox" id="fsMeas" ${fc.measured ? "checked" : ""}> These are measured values</label><button class="btn pri" id="fsSave" type="button">Save folder settings</button><span id="fsMsg" class="note" aria-live="polite"></span></div>
    <p class="note">${fc.saved ? `Saved rules: ${durable ? `offcuts, and pieces unused for ${num(fc.days_unused_limit)} days or more` : `lots with ${num(fc.hours_left_limit)} hours or less to use-by`}. Saving takes the surplus rule set above.` : `Not saved yet. Default folder: <code>${esc(fc.default_path)}</code>.`} Imported surplus is listed as ${fc.measured ? "Measured" : "Sample"}.</p>
    <h4>Sync log</h4><div class="scroll" id="fsLog">${syncLogTable(L.log)}</div></div>`;
  if (durable) {
    const j = st.job, f = (label, k, extra = 'min="1" step="1"', type = "number") => `<label class="field">${label}<input data-job="${k}" type="${type}" value="${esc(j[k])}" ${extra}></label>`;
    h += `<div class="card noprint"><h2>Next cutting job</h2><p class="mut">Enter the job you are about to cut. It goes straight to Sold before it's cut, which fills the empty space with parts neighbours have already ordered.</p>
      <div class="ctrl"><span class="lbl">Sheet</span>${f("Length mm", "sheet_length_mm", 'min="200" step="10"')}${f("Width mm", "sheet_width_mm", 'min="200" step="10"')}
        <label class="field">Thickness mm<select data-job="thickness_mm">${[2, 3, 5, 6, 8, 10].map(t => `<option ${t === j.thickness_mm ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="field">Grade<select data-job="grade">${["E250", "E350"].map(g => `<option ${g === j.grade ? "selected" : ""}>${g}</option>`).join("")}</select></label></div>
      <div class="ctrl"><span class="lbl">Part</span>${f("Part", "part_name", 'style="width:130px"', "text")}${f("Length mm", "part_length_mm", 'min="10" step="10"')}${f("Width mm", "part_width_mm", 'min="10" step="10"')}${f("Quantity", "quantity")}</div>
      <div class="ctrl"><button class="btn pri" id="jobSend" type="button">Send to Sold before it's cut</button><span id="jobMsg" class="note" aria-live="polite"></span></div>
      <p class="note">The neighbours' standing orders on the next screen are sample data.</p></div>`;
  } else h += `<p class="note">Next cutting job applies to sheet cutting, so it lives in the Durable lane.</p>`;
  el.innerHTML = h;

  bindManual(el, lane);
  el.querySelectorAll("[data-goto]").forEach(b => b.onclick = () => { const t = document.getElementById(b.dataset.goto); if (t) t.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"}); });
  const fsMsg = t => { $("#fsMsg", el).innerHTML = t; };
  $("#fsSave", el).onclick = async () => {
    try {
      await api(`/api/connectors/folder/${lane}`, undefined, "PUT", {path: $("#fsPath", el).value, enabled: $("#fsOn", el).checked, measured: $("#fsMeas", el).checked,
        days_unused_limit: st.impLimit.days, hours_left_limit: st.impLimit.hours});
      render();
    } catch (e) { fsMsg(`<span class="err">${esc(e.message)}</span>`); }
  };
  $("#fsSync", el).onclick = async () => {
    const b = $("#fsSync", el); b.disabled = true; fsMsg("Checking the folder…");
    try {
      const res = await api(`/api/connectors/folder/${lane}/sync`, {});
      $("#fsLog", el).innerHTML = syncLogTable(res.log);
      const ok = res.entries.filter(x => x.status === "imported").length, bad = res.entries.length - ok;
      fsMsg(res.entries.length ? `${ok} file(s) imported${bad ? `, ${bad} could not be imported (see the log)` : ""}.` : "No new or changed files.");
    } catch (e) { fsMsg(`<span class="err">${esc(e.message)}</span>`); }
    b.disabled = false;
  };
  clearInterval(st.fsTimer);
  if (fc.enabled) st.fsTimer = setInterval(async () => {
    if (st.view !== "connect" || st.lane !== lane || !document.body.contains(el)) { clearInterval(st.fsTimer); return; }
    try { const x = await api(`/api/connectors/folder/${lane}/log`); const box = $("#fsLog", el); if (box) box.innerHTML = syncLogTable(x.log); } catch (e) { /* keep the last log on screen */ }
  }, 20000);

  $("#impFile", el).onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    S.name = file.name;
    try { S.text = await file.text(); } catch (x) { S.err = new Error("The browser could not read this file. Save it again as CSV and retry."); S.result = null; render(); return; }
    impPreview(lane);
  };
  const n = $("#impN", el), hh = $("#impH", el);
  if (n) n.onchange = () => { const v = Math.round(+n.value); if (v >= 0) { st.impLimit.days = v; if (S.text) impPreview(lane); else render(); } };
  if (hh) hh.onchange = () => { const v = +hh.value; if (v >= 0) { st.impLimit.hours = v; if (S.text) impPreview(lane); else render(); } };
  const meas = $("#impMeas", el); if (meas) meas.onchange = () => { st.impMeasured = meas.checked; render(); };
  const ts = $("#toStock", el); if (ts) ts.onclick = ev => { ev.preventDefault(); go({view: "stock"}); };
  const ls = $("#impList", el);
  if (ls) ls.onclick = async () => {
    ls.disabled = true;
    try {
      S.listed = await api("/api/import/list", {lane, csv_text: S.text, days_unused_limit: st.impLimit.days, hours_left_limit: st.impLimit.hours, measured: st.impMeasured});
      render();
    } catch (e) { $("#impMsg", el).innerHTML = `<span class="err">${esc(e.message)}</span>`; ls.disabled = false; }
  };
  el.querySelectorAll("[data-job]").forEach(inp => inp.onchange = () => {
    const k = inp.dataset.job, v = inp.type === "text" || k === "grade" ? inp.value : +inp.value;
    if (typeof v === "number" && !(v > 0)) return;
    st.job[k] = v;
  });
  const js = $("#jobSend", el);
  if (js) js.onclick = async () => {
    js.disabled = true;
    try {
      const r2 = await api("/api/import/cutting-job", st.job);
      st.gf = r2.gapfill; st.gfDeclined = [];
      go({view: "gapfill"});
    } catch (e) { $("#jobMsg", el).innerHTML = `<span class="err">${esc(e.message)}</span>`; js.disabled = false; }
  };
}

/* ---------------- Seller: add an item by hand (manual entry) ---------------- */
async function shrinkPhoto(file) {
  // Phone photos are large; send at most 1600 px as JPEG so the upload is quick on mobile data.
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => bad(new Error("This photo could not be opened. Try a JPEG or PNG.")); i.src = url; });
    const k = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
    const cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.round(img.naturalWidth * k)); cv.height = Math.max(1, Math.round(img.naturalHeight * k));
    cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
    return cv.toDataURL("image/jpeg", 0.85);
  } finally { URL.revokeObjectURL(url); }
}
function manualCard(lane, O) {
  const F = st.man[lane], E = st.manErr[lane], R = st.manRes[lane], durable = lane === "durable", ph = st.manPhoto[lane];
  const bad = k => E[k] ? 'aria-invalid="true"' : "";
  const err = k => `<span class="ferr" id="mferr-${k}">${esc(E[k] || "")}</span>`;
  const fld = (k, label, input, wide) => `<label class="field mfield ${wide ? "wide" : ""}">${label}${input}${err(k)}</label>`;
  const numIn = (k, mode = "decimal", extra = "") => `<input data-man="${k}" type="number" inputmode="${mode}" ${mode === "numeric" ? 'pattern="[0-9]*" step="1"' : 'step="any"'} min="0" value="${esc(F[k])}" ${bad(k)} ${extra}>`;
  const sel = (k, opts) => `<select data-man="${k}" ${bad(k)}>${opts.map(([v, t]) => `<option value="${esc(v)}" ${String(F[k]) === String(v) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  const words = v => v.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
  const photo = `<div class="mphoto"><label class="btn bigbtn"><input type="file" id="manPhoto" class="vh" accept="image/*" capture="environment">${ph ? "Retake or change photo" : "Take or choose a photo (optional)"}</label>
    ${ph ? `<img class="mthumb" src="${ph}" alt="Your photo of this item"><button class="btn sm" type="button" id="manPhotoDel">Remove photo</button>` : `<span class="note">Opens the camera on a phone. Only your own photo of this item.</span>`}${err("photo")}</div>`;
  const fields = durable ? [
    fld("material", "Material", sel("material", O.materials.map(m => [m.key, m.name]))),
    fld("is_full_sheet", "Full sheet or offcut", `<div class="opts" role="radiogroup" aria-label="Full sheet or offcut"><label><input type="radio" name="manFull" value="false" ${F.is_full_sheet ? "" : "checked"}>Offcut</label><label><input type="radio" name="manFull" value="true" ${F.is_full_sheet ? "checked" : ""}>Full sheet</label></div>`),
    fld("length_mm", "Length mm", numIn("length_mm", "decimal", 'placeholder="e.g. 1250"')), fld("width_mm", "Width mm", numIn("width_mm", "decimal", 'placeholder="e.g. 600"')),
    fld("thickness_mm", "Thickness mm", numIn("thickness_mm", "decimal", 'placeholder="e.g. 3"')), fld("grade", "Grade", `<input data-man="grade" type="text" autocapitalize="characters" value="${esc(F.grade)}" ${bad("grade")}>`),
    fld("condition", "Condition", sel("condition", O.conditions.map(c => [c, words(c)]))), fld("days_unused", "Days unused", numIn("days_unused", "numeric", 'placeholder="e.g. 45"')),
  ] : [
    fld("product", "Product", `<input data-man="product" type="text" autocapitalize="sentences" placeholder="e.g. Tomatoes" value="${esc(F.product)}" ${bad("product")}>`, true),
    fld("category", "Category", sel("category", O.categories.map(c => [c, words(c)]))), fld("storage", "Storage", sel("storage", O.storage.map(c => [c, words(c)]))),
    fld("quantity_kg", "Kg", numIn("quantity_kg", "decimal", 'placeholder="e.g. 12"')),
    fld("mode", "Shelf life", `<div class="opts" role="radiogroup" aria-label="How to give shelf life"><label><input type="radio" name="manMode" value="hours" ${F.mode === "hours" ? "checked" : ""}>Hours left</label><label><input type="radio" name="manMode" value="useby" ${F.mode === "useby" ? "checked" : ""}>Use-by time</label></div>`),
    F.mode === "useby" ? fld("use_by", "Use-by date and time", `<input data-man="use_by" type="datetime-local" value="${esc(F.use_by)}" ${bad("use_by")}>`)
      : fld("hours_left", "Hours left", numIn("hours_left", "decimal", 'placeholder="e.g. 18"')),
  ];
  let res = "";
  if (R && R.listed) res = `<div class="mres ok" role="status"><b>${R.surplus ? "Found as surplus" : "Listed by the seller"}</b><p>${R.surplus ? `Caught by: ${R.rules.map(esc).join("; ")}.` : `No surplus rule caught it (${esc(R.kept_reason)}), so it is listed with the rule "Listed by the seller".`} Listed as ${esc(cap1(R.label))}${R.photo ? ", with your photo" : ""}.</p>
      <div class="ctrl"><a class="btn sm" href="${esc(R.passport)}" target="_blank" rel="noopener">Open passport and QR code</a><button class="btn sm" type="button" id="manToBrowse">See it in Browse surplus</button></div></div>`;
  else if (R) res = `<div class="mres warn" role="status"><b>Not surplus</b><p>${esc(R.kept_reason)}. It stays as working stock and is not listed.</p><div class="ctrl"><button class="btn pri" type="button" id="manAnyway">List it anyway</button><span class="note">It will carry the rule "Listed by the seller".</span></div></div>`;
  return `<div class="card manual" id="conn-manual"><div class="ctrl"><h2>Add an item by hand</h2><span class="pill p-ok">Manual entry</span></div>
    <p class="mut">No ERP? Add one ${durable ? "piece" : "lot"} here. It gets the same checks and surplus rules as an uploaded file, and a passport with a QR code if it is listed.</p>
    <form id="manForm" novalidate>${photo}<div class="mgrid">${fields.join("")}
      ${fld("seller_id", "Your business name", `<input data-man="seller_id" type="text" autocomplete="organization" placeholder="${durable ? "My workshop" : "My kitchen"}" value="${esc(F.seller_id)}">`, true)}</div>
      <label class="check"><input type="checkbox" id="manMeas" ${st.manMeasured[lane] ? "checked" : ""}> These are measured values</label>
      <div class="ctrl"><button class="btn pri bigbtn" type="submit" id="manSubmit">Check and list</button><span class="note">Surplus rule: ${durable ? `offcuts, or unused ${num(st.impLimit.days)} days or more` : `${num(st.impLimit.hours)} hours or less to use-by`} (set in the upload card below). Listed as ${st.manMeasured[lane] ? "Measured" : "Sample"}.</span></div>
      ${E.item ? `<p class="err">${esc(E.item)}</p>` : ""}</form>
    <div id="manRes" aria-live="polite">${res}</div></div>`;
}
function bindManual(el, lane) {
  const F = st.man[lane], form = $("#manForm", el);
  if (!form) return;
  form.querySelectorAll("[data-man]").forEach(i => { i.oninput = i.onchange = () => { F[i.dataset.man] = i.value; }; });
  form.querySelectorAll('input[name="manFull"]').forEach(i => i.onchange = () => { F.is_full_sheet = i.value === "true"; });
  form.querySelectorAll('input[name="manMode"]').forEach(i => i.onchange = () => { F.mode = i.value; render(); });
  $("#manMeas", el).onchange = e => { st.manMeasured[lane] = e.target.checked; render(); };
  $("#manPhoto", el).onchange = async e => {
    const f = e.target.files[0];
    if (!f) return;
    try { st.manPhoto[lane] = await shrinkPhoto(f); delete st.manErr[lane].photo; } catch (x) { st.manErr[lane].photo = x.message; }
    render();
  };
  const pd = $("#manPhotoDel", el); if (pd) pd.onclick = () => { st.manPhoto[lane] = null; render(); };
  const tb = $("#manToBrowse", el); if (tb) tb.onclick = () => go({role: "buyer", view: "browse"});
  const send = async anyway => {
    const fields = {...F}, body = {fields, days_unused_limit: st.impLimit.days, hours_left_limit: st.impLimit.hours, measured: st.manMeasured[lane], list_anyway: anyway, photo: st.manPhoto[lane]};
    delete fields.mode; delete fields.use_by;
    if (lane === "fresh" && F.mode === "useby") { delete fields.hours_left; body.use_by = F.use_by || null; if (!F.use_by) { st.manErr[lane] = {use_by: "Pick the use-by date and time."}; st.manRes[lane] = null; await render(); return; } }
    if (anyway && st.manRes[lane]) fields.item_id = st.manRes[lane].item_id;
    const b = $("#manSubmit", el); if (b) { b.disabled = true; b.textContent = "Checking…"; }
    let r, t;
    try { r = await fetch(`/api/manual/${lane}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}); t = await r.json().catch(() => ({})); }
    catch (x) { st.manErr[lane] = {item: "The app could not be reached. Check that it is still running and try again."}; st.manRes[lane] = null; await render(); return; }
    if (r.status === 422 && t.detail && t.detail.errors) { st.manErr[lane] = t.detail.errors; st.manRes[lane] = null; }
    else if (!r.ok) { st.manErr[lane] = {item: typeof t.detail === "string" ? t.detail : "Something went wrong. Try again."}; st.manRes[lane] = null; }
    else {
      st.manErr[lane] = {}; st.manRes[lane] = t;
      if (t.listed) {  // ready for the next item: keep the choices, clear the measurements and the photo
        st.manPhoto[lane] = null;
        for (const k of lane === "durable" ? ["length_mm", "width_mm", "thickness_mm", "days_unused"] : ["product", "quantity_kg", "hours_left", "use_by"]) F[k] = "";
      }
    }
    await render();
    const first = document.querySelector('#manForm [aria-invalid="true"]');
    if (first) first.focus(); else { const box = document.getElementById("manRes"); if (box && box.firstChild) box.scrollIntoView({block: "nearest"}); }
  };
  form.onsubmit = e => { e.preventDefault(); send(false); };
  const any = $("#manAnyway", el); if (any) any.onclick = () => send(true);
}

/* ---------------- Seller: Sold before it's cut (Gap Fill) ---------------- */
const OCOL = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];
function gapSvg(r, after, colours) {
  const s = r.sheet, L = s.length_mm, W = s.width_mm, m = r.edge_margin_mm, id = after ? "ha" : "hb", u = L / 42;
  let g = `<defs><pattern id="${id}" patternUnits="userSpaceOnUse" width="36" height="36" patternTransform="rotate(45)"><rect width="36" height="36" style="fill:var(--panel-2)"/><line x1="0" y1="0" x2="0" y2="36" style="stroke:var(--scrap);stroke-width:9"/></pattern></defs>`;
  g += `<rect x="0" y="0" width="${L}" height="${W}" rx="${u * 0.2}" style="fill:var(--piece);stroke:var(--mut);stroke-width:1.5" vector-effect="non-scaling-stroke"/>`;
  g += r.free_rects.map(f => `<rect x="${f.x}" y="${f.y}" width="${f.w}" height="${f.h}" style="fill:url(#${id})"/>`).join("");
  g += `<g class="own-g">${r.own.parts.map(p => `<rect x="${p.x}" y="${p.y}" width="${p.length}" height="${p.width}" rx="6" style="fill:var(--own);stroke:var(--ownl);stroke-width:1.5" vector-effect="non-scaling-stroke"><title>Your ${esc(r.own.part_name)}</title></rect>${p.length >= 200 && p.width >= 120 ? `<text x="${p.x + p.length / 2}" y="${p.y + p.width / 2 + 15}" text-anchor="middle" style="font:600 44px var(--disp);fill:#fff;letter-spacing:2px">YOURS</text>` : ""}`).join("")}</g>`;
  if (after) r.neighbours.forEach((n, i) => {
    const c = colours[n.order_id];
    g += `<g class="drop" style="--i:${i}">${n.parts.map(p => `<rect x="${p.x}" y="${p.y}" width="${p.length}" height="${p.width}" rx="4" style="fill:${c};stroke:rgba(16,24,31,.55);stroke-width:1" vector-effect="non-scaling-stroke"><title>${esc(orderName(n))}</title></rect>`).join("")}</g>`;
  });
  g += `<rect x="${m}" y="${m}" width="${L - 2 * m}" height="${W - 2 * m}" style="fill:none;stroke:var(--mut);stroke-width:1;stroke-dasharray:6 6" vector-effect="non-scaling-stroke"/>`;
  g += dimLines(L, W, u);
  const pad = u * 4;
  return `<svg viewBox="${-pad} ${-pad} ${L + pad + 10} ${W + pad + 10}" role="img" aria-label="${after ? `The same sheet with ${r.neighbours.length} buyers' parts placed in the empty space` : "Your cutting plan; the hatched area would go to scrap"}">${g}</svg>`;
}
function gField(label, path, type = "number", extra = "") {
  const [a, b] = path.split(".");
  const v = b ? st.gf[a][b] : st.gf[a];
  return `<label class="field">${label}<input data-gf="${path}" type="${type}" value="${esc(v)}" ${extra}></label>`;
}
async function vGap(el) {
  const demo = await api("/api/gapfill/demo");  // sample orders plus standing orders buyers posted in this demo
  if (!st.gf) st.gf = demo;
  else st.gf.orders = [...st.gf.orders.filter(o => !o.posted), ...demo.orders.filter(o => o.posted)];
  const byId = Object.fromEntries(st.gf.orders.map(o => [o.id, o]));
  const postedTag = id => (byId[id] || {}).posted ? " " + badge("posted") : "";
  let r, err;
  try { r = await api("/api/gapfill?record=true", {...st.gf, declined_order_ids: st.gfDeclined}); } catch (e) { err = e; }
  const s = st.gf.sheet, j = st.gf.job;
  const ctrl = `<details class="panel noprint" id="gfPanel" ${st.gfOpen ? "open" : ""}><summary>${ic("sliders-horizontal")}Change the job <span class="mut" style="font-weight:400">· ${num(s.length_mm)} × ${num(s.width_mm)} × ${num(s.thickness_mm)} mm ${esc(s.grade)} sheet, ${num(j.quantity)} ${esc(plural(j.part_name))} ${num(j.length_mm)} × ${num(j.width_mm)} mm</span>${ic("chevron-down", "sm chev")}</summary><div class="fold-body">
    <div class="ctrl"><span class="lbl">Sheet</span>${gField("Length (mm)", "sheet.length_mm", "number", 'min="200" step="10"')}${gField("Width (mm)", "sheet.width_mm", "number", 'min="200" step="10"')}
      <label class="field">Thickness (mm)<select data-gf="sheet.thickness_mm">${[2, 3, 5, 6, 8, 10].map(t => `<option ${t === s.thickness_mm ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="field">Grade<select data-gf="sheet.grade">${["E250", "E350"].map(g => `<option ${g === s.grade ? "selected" : ""}>${g}</option>`).join("")}</select></label></div>
    <div class="ctrl"><span class="lbl">Your job</span>${gField("Part", "job.part_name", "text", 'style="width:140px"')}${gField("Length (mm)", "job.length_mm", "number", 'min="10" step="10"')}${gField("Width (mm)", "job.width_mm", "number", 'min="10" step="10"')}${gField("How many", "job.quantity", "number", 'min="1" step="1"')}</div>
    <div class="ctrl"><span class="lbl">Prices ${badge("sample")}</span>${gField("Scrap (₹/kg)", "prices.scrap_price_per_kg", "number", 'min="0" step="1"')}${gField("Pickup (₹)", "prices.pickup", "number", 'min="0" step="50"')}<div class="grow"></div><button class="btn" id="gfReset" type="button">${ic("refresh-cw")}Reset the demo</button></div></div></details>`;
  if (err) { el.innerHTML = ctrl + errBox(err); bindG(); return; }
  const mo = r.money, colours = {};
  r.neighbours.forEach((n, i) => colours[n.order_id] = OCOL[i % OCOL.length]);
  const parts = r.neighbours.reduce((a, n) => a + n.units, 0), buyers = new Set(r.neighbours.map(n => short(n.buyer_name))).size;
  let h = `<div class="card"><div class="hero">
      <div class="stack" style="gap:12px">
        <p class="idea">Before you cut your own job, the empty part of the sheet is cut into parts that nearby buyers have already ordered, so it is sold instead of scrapped.</p>
        ${r.worth_doing ? `<div><div class="hero-num acc" id="gfHero">+${inr(mo.net_gain)}</div><p class="hero-sub">extra for this sheet's leftover: <b>${inr(mo.total_with_gap_fill)}</b> instead of <b>${inr(mo.scrap_alone)}</b> as scrap</p></div>
          <div class="ctrl"><span class="pill p-ok" title="Every part was re-checked by an independent checker">${ic("shield-check", "sm")}No overlaps, inside the edge strip</span>${badge("sample", "Sample prices")}<span class="mut">${parts} parts for ${buyers} buyer${buyers === 1 ? "" : "s"}</span></div>`
          : `<div><div class="hero-num">${inr(mo.scrap_alone)}</div><p class="hero-sub">Selling the leftover as scrap is the better choice for this job: filling the gap would not beat it after pickup.</p></div>`}
      </div>
      <div class="stack" style="gap:var(--s2)"><h4>Where the leftover's value comes from</h4>${ladder({scrap: mo.scrap_alone, transport: mo.pickup, gain: r.worth_doing ? mo.net_gain : 0, scrapLabel: "Scrap value of the leftover", gainLabel: "Extra for you"})}
        <p class="note">Each part is valued at what its buyer would pay for the same steel from a new sheet, less handling and cutting. All prices are sample data.</p></div></div></div>`;
  h += ctrl;
  h += `<div class="card"><div class="ctrl"><h2>One sheet, two kinds of customer</h2><div class="grow"></div><button class="btn sm noprint" id="gfPrint" type="button">${ic("printer", "sm")}Print the cutting plan</button></div>
    <div class="gf-draw ${REDUCED() ? "" : "anim"}"><figure><figcaption><span class="pill p-mut">Before</span><span class="mut">Your job; the hatched area goes to scrap</span></figcaption>${gapSvg(r, false, colours)}</figure>
      <figure><figcaption><span class="pill p-ok">After</span><span class="mut">The empty space is sold before it is cut</span></figcaption>${gapSvg(r, true, colours)}</figure></div>
    <div class="leg"><span><i style="background:var(--own)"></i>Your ${esc(plural(r.own.part_name))} × ${r.own.units}</span><span><i style="background:repeating-linear-gradient(45deg,var(--scrap) 0 3px,var(--panel-2) 3px 6px)"></i>Scrap</span>${r.neighbours.map(n => `<span><i style="background:${colours[n.order_id]}"></i>${esc(orderName(n))} × ${n.units}<span class="code">${esc(n.order_id)}</span></span>`).join("")}</div>
    <p class="note">Parts are placed with a ${num(st.gf.kerf_mm, 0)} mm ${term("blade gap", "kerf")} and a ${num(r.edge_margin_mm)} mm edge strip (dashed) that is never cut. Measurements in mm.</p></div>`;
  h += `<div class="card"><div class="section-title"><h2>Reserved for nearby buyers</h2><span class="mut">If a buyer declines, the plan is worked out again at once and the space goes to the next best order.</span></div>
    ${r.neighbours.length ? `<div class="olist">${r.neighbours.map(n => `<div class="orow2"><span class="sw" style="background:${colours[n.order_id]}"></span>
        <div class="nm"><b>${esc(orderName(n))} ${codeTag(n.order_id)}${postedTag(n.order_id)}</b><span class="note">${n.units} part${n.units === 1 ? "" : "s"}${byId[n.order_id] ? ` · ${num(byId[n.order_id].length_mm)} × ${num(byId[n.order_id].width_mm)} mm` : ""} · ${esc(n.buyer_name)}</span></div>
        <span class="num">${inr(n.value)}</span><button class="btn sm noprint" data-decline="${esc(n.order_id)}" type="button">Decline</button></div>`).join("")}</div>`
      : emptyState("scissors", "Nothing is reserved: filling the gap would not beat selling the leftover as scrap.")}
    ${fold(`Show details: orders not used (${r.refused.length}) and all standing orders`, `<ul class="rej">${r.refused.map(x => `<li><b>${esc(byId[x.order_id] ? orderName(byId[x.order_id]) : x.order_id)}</b> ${codeTag(x.order_id)}: ${esc(x.reason)}${st.gfDeclined.includes(x.order_id) ? ` <button class="btn sm noprint" data-restore="${esc(x.order_id)}" type="button">Restore</button>` : ""}</li>`).join("") || "<li>None</li>"}</ul>
      <div class="scroll"><table><tr><th>Order</th><th>Part size</th><th class="n">Thickness</th><th>Grades</th><th class="n">How many</th></tr>${st.gf.orders.map(o => `<tr><td class="w"><b>${esc(orderName(o))}</b> ${codeTag(o.id)}${postedTag(o.id)}</td><td>${num(o.length_mm)} × ${num(o.width_mm)} mm</td><td class="n">${num(o.thickness_mm)} mm</td><td>${o.accepted_grades.map(esc).join(", ")}</td><td class="n">${o.quantity}</td></tr>`).join("")}</table></div>
      <p class="note">Sold before it's cut = buyers' parts ${inr(mo.neighbours_value)} + scrap of what is still left ${inr(mo.scrap_of_rest)} − pickup ${inr(mo.pickup)}.</p>`)}</div>`;
  el.innerHTML = h;
  bindG();
  if (r.worth_doing) countUp($("#gfHero", el), mo.net_gain, r.neighbours.length * 160 + 750, v => "+" + inr(v));
  function bindG() {
    el.querySelectorAll("[data-gf]").forEach(inp => inp.onchange = () => {
      const [a, b] = inp.dataset.gf.split(".");
      let v = inp.type === "text" ? inp.value : +inp.value;
      if (inp.tagName === "SELECT") v = isNaN(+inp.value) ? inp.value : +inp.value;
      if (inp.type === "number" && !(v >= 0)) return;
      st.gf[a][b] = v; render();
    });
    const pn = $("#gfPanel", el); if (pn) pn.ontoggle = () => { st.gfOpen = pn.open; };
    const rs = $("#gfReset", el); if (rs) rs.onclick = () => { st.gf = null; st.gfDeclined = []; toast("The demo job is back to its starting point."); render(); };
    const pr = $("#gfPrint", el); if (pr) pr.onclick = () => window.print();
    el.querySelectorAll("[data-decline]").forEach(b => b.onclick = () => { st.gfDeclined.push(b.dataset.decline); toast(`${esc(byId[b.dataset.decline] ? orderName(byId[b.dataset.decline]) : b.dataset.decline)} declined. The plan was worked out again.`); render(); });
    el.querySelectorAll("[data-restore]").forEach(b => b.onclick = () => { st.gfDeclined = st.gfDeclined.filter(x => x !== b.dataset.restore); render(); });
  }
}

/* ---------------- Seller: offers and recovery plan ---------------- */
async function vOffers(el) {
  const k = laneScen(), S = await scen(k), fresh = S.meta.engine === "perishable";
  if (st.plan.key !== k) st.plan = {rejected: [], elapsed: 0, key: k};
  const deadline = st.deadline[k] ?? 12, stockist = st.deadline["stk_" + k] ?? 40;
  const ctrl = `<div class="card"><div class="ctrl">${await scenSeg()}<label class="field">Clear within (${fresh ? "hours" : "days"})<input id="dl" type="number" min="1" max="720" value="${deadline}"></label>${fresh ? "" : `<label class="field">Stockist buy-back ₹/kg<input id="stk" type="number" min="0" step="1" value="${stockist}"></label>`}</div></div>`;
  let r;
  try {
    r = await api("/api/seller-plan", {seller_id: S.meta.plan_seller, kind: S.meta.engine, data: S.data, deadline,
      stockist_price_per_kg: fresh ? null : stockist, rejected_route_ids: st.plan.rejected, elapsed: st.plan.elapsed});
  } catch (e) { el.innerHTML = ctrl + errBox(e) + `<button class="btn" id="rp" type="button">Reset plan</button>`; bindO(); $("#rp", el).onclick = () => { st.plan = {rejected: [], elapsed: 0, key: k}; render(); }; return; }
  const p = r.plan, b = p.best, T = p.time_left, tu = p.time_unit;
  let steps = (b ? b.steps : []).map(s => `<div class="step ${fresh ? "pe" : ""}" style="left:${s.start / T * 100}%;width:${(s.end - s.start) / T * 100}%"><span>${esc(s.name)}</span><small>reach ${pct(s.p_reach)} · close ${pct(s.p_close)}</small></div>`).join("");
  const last = b && b.steps.length ? b.steps[b.steps.length - 1].end : 0;
  if (last < T) steps += `<div class="step sc" style="left:${last / T * 100}%;width:${(T - last) / T * 100}%"><span>${fresh ? "Disposal" : "Scrap"}</span><small>last resort</small></div>`;
  let h = ctrl + `<div class="card"><div class="ctrl"><h2>Recovery plan for ${esc(r.seller_name)}</h2>${st.plan.rejected.length ? `<span class="pill p-warn">${st.plan.rejected.length} buyer(s) said no</span>` : ""}</div>
    <p class="mut">${esc(p.explanation || "")}</p><div class="tl">${steps}</div><div class="axis"><span>now</span><span>${num(T)} ${tu} left</span></div>
    ${b ? `<div class="fig"><div class="g"><b>${inr(b.ev)}</b><span>Expected value</span></div><div><b>${inr(p.salvage_value)}</b><span>${fresh ? "Disposal" : "Scrap"} estimate</span></div><div><b>${inr(b.above_salvage)}</b><span>Above ${fresh ? "disposal" : "scrap"}</span></div><div><b>${pct(b.p_salvage)}</b><span>Chance of ending at ${fresh ? "disposal" : "scrap"}</span></div><div><b>${p.sequences_evaluated}</b><span>Orderings compared</span></div></div>` : ""}
    <div class="ctrl">${b && b.steps.length ? `<button class="btn pri" id="sayNo" type="button">First buyer says no</button>` : ""}<button class="btn" id="rp" type="button">Reset plan</button></div>
    <p class="note">Chances of closing are assumptions, not observed rates. ${p.salvage_executable ? "" : `The ${fresh ? "disposal" : "scrap"} value is an estimate; get a firm, current offer before calling it a floor.`}</p></div>`;
  h += `<div class="card"><h2>Every route the engine considered</h2><div class="scroll"><table><tr><th>Route</th><th>Type</th><th class="n">Value to seller</th><th class="n">Chance</th><th class="n">Window</th></tr>${r.routes.map(x => `<tr ${st.plan.rejected.includes(x.id) ? 'style="opacity:.5"' : ""}><td>${esc(x.name)}${st.plan.rejected.includes(x.id) ? ' <span class="pill p-bad">said no</span>' : ""}</td><td>${esc(x.kind.replace("_", " "))}</td><td class="n">${inr(x.value_to_seller)}</td><td class="n">${pct(x.p_close)} ${chip(x.p_label)}</td><td class="n">${num(x.window)} ${tu}</td></tr>`).join("")}</table></div></div>`;
  el.innerHTML = h;
  bindO();
  function bindO() {
    bindScen(el);
    const dl = $("#dl", el); if (dl) dl.onchange = () => { st.deadline[k] = Math.max(1, +dl.value || 12); st.plan = {rejected: [], elapsed: 0, key: k}; render(); };
    const sk = $("#stk", el); if (sk) sk.onchange = () => { st.deadline["stk_" + k] = Math.max(0, +sk.value || 0); render(); };
    const no = $("#sayNo", el); if (no) no.onclick = () => { const s0 = b.steps[0]; st.plan.rejected.push(s0.route_id); st.plan.elapsed += s0.end - s0.start; render(); };
    const rp = $("#rp", el); if (rp) rp.onclick = () => { st.plan = {rejected: [], elapsed: 0, key: k}; render(); };
  }
}

/* ---------------- Seller: stock and passports ---------------- */
async function vStock(el) {
  const k = laneScen(), S = await scen(k), fresh = S.meta.engine === "perishable";
  let r, imp;
  try { [r, imp] = await Promise.all([api(`/api/stock/${k}`), api(`/api/stock/import_${st.lane}`)]); } catch (e) { el.innerHTML = errBox(e); return; }
  let items = r.items.filter(x => !st.sellerFilter || x.seller_id === st.sellerFilter);
  if (fresh) items = items.slice().sort((a, b) => a.hours_left - b.hours_left);
  const maxH = 72;
  const total = items.reduce((a, x) => a + x.scrap_value, 0), kg = items.reduce((a, x) => a + x.kg, 0);
  let h = `<div class="card"><div class="ctrl">${await scenSeg()}<label class="field">Seller<select id="sf"><option value="">All sellers</option>${S.data.sellers.map(s => `<option value="${esc(s.id)}" ${s.id === st.sellerFilter ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label><div class="grow"></div><a class="btn" href="/labels/${k}" target="_blank" rel="noopener">Print QR labels</a></div></div>`;
  h += `<div class="tiles"><div class="tile"><b>${items.length}</b><span>${fresh ? "Lots" : "Pieces"} in stock</span></div><div class="tile"><b>${num(kg, 0)} kg</b><span>Total weight</span></div><div class="tile"><b>${inr(total)}</b><span>${fresh ? "Disposal value (negative = a cost)" : "Worth as scrap today"}</span></div>${fresh ? `<div class="tile hi"><b>${items.filter(x => x.hours_left < 12).length}</b><span>Lots with under 12 hours left</span></div>` : ""}</div>`;
  h += `<div class="card"><h2>${fresh ? "Lots, most urgent first" : "Pieces"}</h2>${items.length ? `<div class="scroll"><table><tr><th>Passport</th><th>ID</th><th>Seller</th><th>What it is</th>${fresh ? "<th>Shelf life left</th>" : ""}<th class="n">kg</th><th class="n">${fresh ? "Disposal" : "Scrap value"}</th><th>Data</th></tr>${items.map(x => {
    const url = location.origin + x.passport;
    return `<tr><td><a href="${esc(x.passport)}" target="_blank" rel="noopener" title="Open passport"><img class="qr" alt="QR code for ${esc(x.id)}" src="/api/qr.svg?data=${encodeURIComponent(url)}"></a></td><td><a href="${esc(x.passport)}" target="_blank" rel="noopener">${esc(x.id)}</a></td><td>${esc(x.seller_name)}</td><td class="w">${esc(x.description)}</td>${fresh ? `<td style="min-width:140px"><div class="bar ${x.hours_left < 12 ? "warn" : "pe"}"><i style="width:${Math.max(3, Math.min(1, x.hours_left / maxH) * 100)}%"></i></div><span class="note">${num(x.hours_left)} h</span></td>` : ""}<td class="n">${num(x.kg, 1)}</td><td class="n">${inr(x.scrap_value)}</td><td>${chip(x.label)}</td></tr>`;
  }).join("")}</table></div>` : `<div class="empty">No stock for this seller.</div>`}
  <p class="note">Each QR opens the item's passport on this computer's address. For phones on the same Wi-Fi, open the app at the laptop's network address (see README) before printing labels.</p></div>`;
  if (imp.items.length) h = `<div class="card"><div class="ctrl"><h2>Listed from your import (${imp.items.length})</h2><div class="grow"></div><a class="btn" href="/labels/import_${st.lane}" target="_blank" rel="noopener">Print QR labels</a><button class="btn" id="impClear" type="button">Remove imported stock</button></div>
    <p class="mut">Listed from Connect your system: a CSV import, the auto-sync folder, or an item added by hand.</p>
    <div class="scroll"><table><tr><th>Passport</th><th>ID</th><th>Seller</th><th>What it is</th><th>Found by</th><th class="n">kg</th><th class="n">Scrap value</th><th>Data</th></tr>${imp.items.map(x => `<tr><td><a href="${esc(x.passport)}" target="_blank" rel="noopener" title="Open passport"><img class="qr" alt="QR code for ${esc(x.id)}" src="/api/qr.svg?data=${encodeURIComponent(location.origin + x.passport)}"></a></td><td><a href="${esc(x.passport)}" target="_blank" rel="noopener">${esc(x.id)}</a></td><td>${esc(x.seller_name)}</td><td class="w">${esc(x.description)}</td><td class="w">${x.urgent ? '<span class="pill p-bad">Urgent</span> ' : ""}${x.rules.map(esc).join("<br>")}</td><td class="n">${num(x.kg, 1)}</td><td class="n">–</td><td>${chip(x.label)}</td></tr>`).join("")}</table></div>
    <p class="note">No scrap price is shown for imported items until a dated quote is entered.</p></div>` + h;
  el.innerHTML = h;
  bindScen(el);
  const ic = $("#impClear", el); if (ic) ic.onclick = async () => { await api(`/api/import/clear?lane=${st.lane}`, {}); render(); };
  $("#sf", el).onchange = e => { st.sellerFilter = e.target.value; render(); };
}

/* ---------------- Buyer and seller: pickup and delivery (simulated transport) ---------------- */
const STAGES = [["agreed", "Agreed"], ["booked", "Pickup booked"], ["dispatched", "Collected"], ["delivered", "Delivered"]];
function routeSvg(lg) {
  const pts = [{name: lg.buyer.name, x: lg.buyer.x_km, y: lg.buyer.y_km, buyer: true}, ...lg.collect.map(c => ({name: c.seller_name, x: c.x_km, y: c.y_km, n: c.stop}))];
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y), pad = 1.2;
  const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
  const W = innerWidth < 600 ? 380 : 640, H = Math.max(260, Math.min(420, W * (y1 - y0) / Math.max(1, x1 - x0))), sc = Math.min(W / (x1 - x0), H / (y1 - y0));
  const X = x => (x - x0) * sc + (W - (x1 - x0) * sc) / 2, Y = y => H - ((y - y0) * sc + (H - (y1 - y0) * sc) / 2);
  const stops = [pts[0], ...pts.slice(1), pts[0]];
  let g = `<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" style="fill:var(--acc)"/></marker></defs>`;
  for (let k = Math.ceil(x0); k <= x1; k++) g += `<line x1="${X(k)}" y1="0" x2="${X(k)}" y2="${H}" style="stroke:var(--line);stroke-width:1"/>`;
  for (let k = Math.ceil(y0); k <= y1; k++) g += `<line x1="0" y1="${Y(k)}" x2="${W}" y2="${Y(k)}" style="stroke:var(--line);stroke-width:1"/>`;
  lg.legs.forEach((l, i) => {
    const a = stops[i], b = stops[i + 1], ax = X(a.x), ay = Y(a.y), bx = X(b.x), by = Y(b.y), len = Math.hypot(bx - ax, by - ay) || 1;
    const ux = (bx - ax) / len, uy = (by - ay) / len, r = 16, mx = (ax + bx) / 2, my = (ay + by) / 2;
    g += `<line x1="${ax + ux * r}" y1="${ay + uy * r}" x2="${bx - ux * (r + 2)}" y2="${by - uy * (r + 2)}" marker-end="url(#arr)" style="stroke:var(--acc);stroke-width:2.5"/>`;
    g += `<g transform="translate(${mx - uy * 14},${my + ux * 14})"><circle r="11" style="fill:var(--panel);stroke:var(--acc);stroke-width:2"/><text y="4" text-anchor="middle" style="font:600 11px var(--mono);fill:var(--fg)">${l.n}</text></g>`;
    g += `<text x="${mx + uy * 30}" y="${my - ux * 30 + 4}" text-anchor="middle" class="halo" style="font:500 11px var(--mono);fill:var(--mut)">${num(l.km, 2)} km</text>`;
  });
  pts.forEach(p => {
    const x = X(p.x), y = Y(p.y);
    g += p.buyer ? `<rect x="${x - 13}" y="${y - 13}" width="26" height="26" rx="4" style="fill:var(--np);stroke:var(--panel);stroke-width:2"/><text x="${x}" y="${y + 4}" text-anchor="middle" style="font:700 11px var(--body);fill:#fff">B</text>`
      : `<circle cx="${x}" cy="${y}" r="13" style="fill:var(--own);stroke:var(--panel);stroke-width:2"/><text x="${x}" y="${y + 4}" text-anchor="middle" style="font:700 11px var(--body);fill:#fff">S${p.n}</text>`;
    g += `<text x="${x}" y="${y + 30}" text-anchor="middle" class="halo" style="font:600 12px var(--body);fill:var(--fg)">${esc(p.name)}</text>`;
  });
  return `<svg class="routemap" viewBox="0 0 ${W} ${H}" role="img" aria-label="Pickup route: ${esc(lg.legs.map(l => `${l.n}. ${l.from} to ${l.to}, ${num(l.km, 2)} km`).join("; "))}">${g}</svg>`;
}
async function vPickup(el) {
  const r = await api("/api/ledger", {});
  const fresh = st.lane === "fresh", mine = r.deals.filter(d => (d.kind === "perishable") === fresh);
  if (!mine.length) {
    el.innerHTML = `<p class="callout note">Transporters and bookings are simulated in this prototype. No live booking with any logistics company.</p>
      <div class="empty">No accepted ${fresh ? "Fresh" : "Durable"} deals yet. Accept an order in <a href="#buyer/match/${st.lane}" id="toMatch">Buyer &gt; Order and match</a>, then come back to book the pickup.</div>`;
    $("#toMatch", el).onclick = e => { e.preventDefault(); go({role: "buyer", view: "match"}); };
    return;
  }
  const id = mine.some(d => d.id === st.lastDeal) ? st.lastDeal : mine[0].id;
  const lg = await api(`/api/deals/${id}/logistics`);
  const dl = lg.deal, bk = lg.booking, v = lg.vehicle;
  const reached = {agreed: true, booked: !!bk || dl.status !== "agreed", dispatched: dl.status !== "agreed", delivered: dl.status === "delivered"};
  const cur = dl.status === "delivered" ? "delivered" : dl.status === "dispatched" ? "dispatched" : bk ? "booked" : "agreed";
  const tomorrow = new Date(Date.now() + 864e5).toISOString().slice(0, 10), today = new Date().toISOString().slice(0, 10);
  const cap = (v.payload_kg || 0) * (v.trips || 1);
  let h = `<p class="callout note">Transporters and bookings are simulated in this prototype. No live booking with any logistics company.</p>
    <div class="card noprint"><div class="ctrl"><label class="field">Deal<select id="pkDeal">${mine.map(d => `<option value="${d.id}" ${d.id === id ? "selected" : ""}>#${d.id} · ${esc(d.buyer_name)} · from ${d.sellers.map(esc).join(", ")}</option>`).join("")}</select></label>
      <div class="grow"></div><a class="btn" href="/deals/${id}/pickup-sheet" target="_blank" rel="noopener">Print pickup sheet</a><a class="btn" href="/deals/${id}/challan" target="_blank" rel="noopener">Print delivery challan</a></div></div>`;
  h += `<div class="card"><div class="ctrl"><h2>Deal #${id} status</h2>${chip(dl.is_simulated ? "simulated" : "verified")}</div><ol class="tracker">${STAGES.map(([k, t]) => `<li class="${reached[k] ? "done" : ""} ${k === cur ? "cur" : ""}"><span>${t}</span></li>`).join("")}</ol>
    <div class="ctrl" id="pkAct">${
      dl.status === "agreed" ? `<button class="btn ${bk ? "pri" : ""}" id="pkCollect" type="button" ${bk ? "" : "disabled"}>Mark collected</button><span class="note">${bk ? "Press when the driver has collected from every seller." : "Book a pickup slot first."}</span>`
      : dl.status === "dispatched" ? `<label class="field">Delivery evidence<input id="pkEv" placeholder="Signed challan no., e.g. SIM-${String(id).padStart(5, "0")}" style="width:260px"></label><label class="check"><input type="checkbox" id="pkConf"> Buyer confirms receipt</label><button class="btn pri" id="pkDeliver" type="button" disabled>Mark delivered</button>`
      : `<span class="pill p-ok">Delivered</span><span class="note">Evidence: ${dl.evidence.map(esc).join(", ") || "none"} · buyer ${dl.buyer_confirmed ? "confirmed" : "has not confirmed"}.</span>`}</div>
    <p class="note" id="pkMsg" aria-live="polite">Delivered needs delivery evidence and the buyer's confirmation. A simulated deal stays simulated.</p></div>`;
  h += `<div class="cols"><div class="card"><h2>Pickup route</h2>${routeSvg(lg)}
      <div class="leg"><span><i style="background:var(--np)"></i>B = buyer, start and end</span><span><i style="background:var(--own)"></i>S1, S2 … = pickup stops in the engine's order</span></div>
      <div class="scroll"><table><tr><th class="n">Leg</th><th>From</th><th>To</th><th class="n">km</th></tr>${lg.legs.map(l => `<tr><td class="n">${l.n}</td><td>${esc(l.from)}</td><td>${esc(l.to)}</td><td class="n">${num(l.km, 2)}</td></tr>`).join("")}<tr><td></td><td colspan="2"><b>Round trip (engine)</b></td><td class="n"><b>${num(lg.route_km, 2)}</b></td></tr></table></div>
      <p class="note">Straight lines on the sample km grid, not roads.</p></div>
    <div class="card"><h2>Vehicle the engine chose</h2><div class="fig"><div class="g"><b>${inr(v.transport_cost)}</b><span>Transport cost</span></div><div><b>${esc(cap1(v.name))}</b><span>${v.trips} ${v.trips === 1 ? "trip" : "trips"}</span></div><div><b>${num(lg.tour_hours * 60)} min</b><span>Tour at ${num(lg.speed_kmph)} km/h</span></div></div>
      <div><div class="ctrl"><span class="lbl">Load against payload</span><div class="grow"></div><span class="note">${num(v.load_kg, 1)} kg of ${num(cap)} kg</span></div><div class="bar ${v.load_kg > cap ? "bad" : "warn"}" role="img" aria-label="${num(v.load_kg, 1)} kg of ${num(cap)} kg payload"><i style="width:${Math.min(100, v.load_kg / Math.max(cap, 1) * 100)}%"></i></div></div>
      <p class="note">Cheapest vehicle for this load and route, from the engine. Rates are ${esc(v.label)} data${v.rate_multiplier !== 1 ? `, × ${num(v.rate_multiplier, 1)} as when the deal was agreed` : ""}.</p>
      ${lg.warnings.map(w => `<p class="err">${esc(w)}</p>`).join("")}</div></div>`;
  h += `<div class="card"><h2>Pickup schedule</h2><div class="ctrl noprint"><label class="field">Date<input id="pkDate" type="date" min="${today}" value="${esc(bk ? bk.date : tomorrow)}" ${dl.status === "agreed" ? "" : "disabled"}></label>
      <label class="field">Time slot<select id="pkSlot" ${dl.status === "agreed" ? "" : "disabled"}>${lg.slots.map(x => `<option ${bk && bk.slot === x ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      <button class="btn pri" id="pkBook" type="button" ${dl.status === "agreed" ? "" : "disabled"}>${bk ? "Change booking" : "Book pickup"}</button>
      <span class="note">${bk ? `Booked: ${esc(bk.date)}, ${esc(bk.slot)}${lg.arrival ? ` · expected at ${esc(lg.buyer.name)} about ${esc(lg.arrival.slice(11, 16))}` : ""}` : "Not booked yet."}</span></div>
    ${fresh ? `<div class="scroll"><table><tr><th>Lot</th><th>Seller</th><th>Product</th><th class="n">kg</th><th>Storage</th><th class="n">Hours left now</th><th class="n">On arrival if collected now</th><th class="n">On arrival at booked slot</th><th class="n">Buyer needs</th><th></th></tr>${lg.lots.map(x => `<tr><td>${esc(x.resource_id)}</td><td>${esc(x.seller_name)}</td><td>${esc(x.product)}</td><td class="n">${num(x.kg, 1)}</td><td>${x.storage === "ambient" ? "ambient" : `<span class="pill p-warn">${esc(x.storage)}</span>`}</td><td class="n">${num(x.hours_left, 1)} h</td><td class="n">${num(x.on_arrival_if_collected_now, 1)} h</td><td class="n">${x.on_arrival_at_booked_slot == null ? "–" : num(x.on_arrival_at_booked_slot, 1) + " h"}</td><td class="n">${num(x.min_on_arrival)} h</td><td>${x.ok ? '<span class="pill p-ok">OK</span>' : '<span class="pill p-bad">Too late</span>'}</td></tr>`).join("")}</table></div>
      <p class="note">"If collected now" is the engine's own check: hours left minus the pickup tour. At the booked slot, the hours until the slot are subtracted too. Food rules are platform rules that still need expert review.</p>` : ""}</div>`;
  h += `<div class="card"><h2>What to collect, in order</h2><div class="scroll"><table><tr><th class="n">Stop</th><th>Seller</th><th>Item</th><th class="n">${fresh ? "kg" : esc(lg.unit_name)}</th><th class="n">Stop total kg</th></tr>${lg.collect.map(c => c.items.map((it, j) => `<tr><td class="n">${j ? "" : "S" + c.stop}</td><td>${j ? "" : esc(c.seller_name)}</td><td class="w"><code>${esc(it.resource_id)}</code> ${esc(it.what)}</td><td class="n">${num(fresh ? it.kg : it.units, fresh ? 1 : 0)}</td><td class="n">${j ? "" : num(c.kg, 1)}</td></tr>`).join("")).join("")}</table></div>
    <p class="note">Then deliver everything to ${esc(lg.buyer.name)}. The printable pickup sheet has contact lines for the driver to fill in.</p></div>`;
  el.innerHTML = h;
  const msg = t => { $("#pkMsg", el).innerHTML = t; };
  $("#pkDeal", el).onchange = e => { st.lastDeal = +e.target.value; render(); };
  const book = $("#pkBook", el);
  if (book) book.onclick = async () => {
    book.disabled = true;
    try { await api(`/api/deals/${id}/booking`, {pickup_date: $("#pkDate", el).value, slot: $("#pkSlot", el).value}); st.lastDeal = id; render(); }
    catch (e) { msg(`<span class="err">${esc(e.message)}</span>`); book.disabled = false; }
  };
  const col = $("#pkCollect", el);
  if (col) col.onclick = async () => {
    try { await api(`/api/deals/${id}/advance`, {status: "dispatched"}); st.lastDeal = id; render(); } catch (e) { msg(`<span class="err">${esc(e.message)}</span>`); }
  };
  const del = $("#pkDeliver", el), ev = $("#pkEv", el), conf = $("#pkConf", el);
  if (del) {
    const ready = () => { del.disabled = !(ev.value.trim() && conf.checked); };
    ev.oninput = ready; conf.onchange = ready;
    del.onclick = async () => {
      try { await api(`/api/deals/${id}/advance`, {status: "delivered", evidence: [ev.value.trim()], buyer_confirmed: true}); st.lastDeal = id; render(); }
      catch (e) { msg(`<span class="err">${esc(e.message)}</span>`); }
    };
  }
}

/* ---------------- Admin ---------------- */
async function vQuality(el) {
  let r;
  try { r = await api("/api/data-quality"); } catch (e) { el.innerHTML = errBox(e); return; }
  const tot = {};
  r.rows.forEach(x => tot[x.label] = (tot[x.label] || 0) + x.count);
  const order = ["measured", "verified", "sample", "assumption", "simulated"];
  el.innerHTML = `<div class="tiles">${order.map(l => `<div class="tile ${l === "measured" || l === "verified" ? "hi" : ""}"><b>${num(tot[l] || 0)}</b><span>${l[0].toUpperCase() + l.slice(1)}</span></div>`).join("")}</div>
  <div class="card"><h2>Where the data comes from</h2><div class="scroll"><table><tr><th>Area</th><th>Label</th><th class="n">Items</th></tr>${r.rows.map(x => `<tr><td>${esc(x.area)}</td><td>${chip(x.label)}</td><td class="n">${num(x.count)}</td></tr>`).join("")}</table></div>
  <p class="note callout">Nothing measured yet. The next step is one real cutting job and dated scrap and plate quotes from a Chennai workshop, entered as Measured.</p></div>`;
}
async function vPrices(el) {
  const keys = LANES[st.lane].scen;
  let h = "";
  for (const k of keys) {
    const S = await scen(k);
    h += `<div class="card"><h2>${esc(S.meta.title)}</h2><div class="scroll"><table><tr><th>Seller</th><th>Applies to</th><th class="n">₹/kg</th><th>Quoted on</th><th>Firm offer</th><th>Data</th></tr>${S.data.sellers.flatMap(s => s.salvage.map(o => `<tr><td>${esc(s.name)}</td><td>${esc(o.applies_to.replace(/_/g, " "))}</td><td class="n">${num(o.price_per_kg, 1)}${o.price_per_kg < 0 ? " (cost)" : ""}</td><td>${o.quoted_on ? esc(o.quoted_on) : '<span class="pill p-warn">No date</span>'}</td><td>${o.executable ? "Yes" : "No"}</td><td>${chip(o.label)}</td></tr>`)).join("")}</table></div>
    ${S.data.demands.some(d => d.new_price_per_kg) ? `<h4>What buyers pay for new material</h4><div class="scroll"><table><tr><th>Buyer</th><th>Part</th><th class="n">₹/kg new</th><th>Data</th></tr>${S.data.demands.filter(d => d.new_price_per_kg).map(d => `<tr><td>${esc(d.buyer_name)}</td><td>${esc(d.part_name)}</td><td class="n">${num(d.new_price_per_kg, 1)}</td><td>${chip(d.label)}</td></tr>`).join("")}</table></div>` : ""}</div>`;
  }
  if (st.lane === "durable") {
    const g = st.gf || await api("/api/gapfill/demo");
    h += `<div class="card"><h2>Sold before it's cut</h2><div class="scroll"><table><tr><th>Price</th><th class="n">Value</th><th>Data</th></tr><tr><td>Scrap</td><td class="n">₹${num(g.prices.scrap_price_per_kg, 1)}/kg</td><td>${chip(g.prices.label)}</td></tr><tr><td>Handling per part</td><td class="n">₹${num(g.prices.handling_per_part, 1)}</td><td>${chip(g.prices.label)}</td></tr><tr><td>Cutting per metre</td><td class="n">₹${num(g.prices.cut_cost_per_m, 1)}</td><td>${chip(g.prices.label)}</td></tr><tr><td>Pickup run</td><td class="n">${inr(g.prices.pickup)}</td><td>${chip(g.prices.label)}</td></tr></table></div></div>`;
  }
  el.innerHTML = `<p class="note callout">Every price below is sample data with no date. A real deployment needs a dated quote and its source for each one; quotes older than 14 days should be refreshed.</p>` + h;
}
function vRules(el) {
  const durable = [["Material", "Must match the order (mild steel, stainless 304, aluminium, plywood, acrylic)"], ["Thickness", "Must equal the order's thickness, within the set tolerance"], ["Grade", "Must be one the buyer accepts, such as E250 or E350"], ["Condition", "Only conditions the buyer accepts; bent or damaged stock is refused unless accepted"], ["Defects", "Rust patches, holes and cracks are marked and never cut through"], ["Grain", "Plywood parts follow the grain when the buyer says grain matters"], ["Machine bed", "A piece larger than the buyer's machine is refused"], ["Kerf and edge margin", "Every part is grown by the kerf; a strip along each edge is never cut"], ["Bars and tubes", "Same profile and kg per metre; end trims removed before cutting"], ["Independent check", "Every cutting plan is re-checked for overlaps and margins before it is shown"]];
  const fresh = [["Shelf life on arrival", "A lot is refused if it will not have the buyer's minimum hours left when it arrives, after the pickup route"], ["Storage", "Chilled or frozen food goes only to buyers with that storage"], ["Category", "Only categories the buyer takes"], ["Licence", "A food-business sale needs an FSSAI licence or registration on both sides"], ["Cooked meals", "Donation only (platform policy)"], ["Used cooking oil", "Only to a RUCO-enrolled collector"], ["Donation", "Shown clearly; no price is charged"]];
  const t = rows => `<div class="scroll"><table><tr><th>Rule</th><th>What the engine does</th></tr>${rows.map(r => `<tr><td>${esc(r[0])}</td><td class="w">${esc(r[1])}</td></tr>`).join("")}</table></div>`;
  el.innerHTML = st.lane === "durable" ? `<div class="card"><h2>Durable lane</h2>${t(durable)}</div>` :
    `<div class="card"><h2>Fresh lane</h2><p class="err">These are platform rules for a prototype, not legal advice. They need review by a food-safety expert before real use.</p>${t(fresh)}</div>`;
  el.innerHTML += `<p class="note">Every refusal shows its reason to the user, so a seller knows what to fix.</p>`;
}
async function vSim(el) {
  let s;
  try { s = await api("/api/sim-summary"); } catch (e) { el.innerHTML = errBox(e); return; }
  const L = s.labels, pol = s.pol, MAX = 2.2, ks = ["post1_compact", "post2_remnant", "precut_remnant", "shadow"], cl = ["k0", "k1", "k2", "k3"];
  const rn = {"mixed-scarce": "Mixed parts, few orders", "large-scarce": "Large parts, few orders", "mixed-balanced": "Mixed parts, balanced", "large-balanced": "Large parts, balanced", "mixed-plentiful": "Mixed parts, many orders"};
  const mxT = Math.max(...Object.values(s.thick));
  el.innerHTML = `<div class="tiles"><div class="tile hi"><b>×${num(pol.precut_remnant[0], 2)}</b><span>Median value against scrap, Gap Fill before the cut</span></div><div class="tile"><b>₹${num(s.perkg[0])}/kg</b><span>Median gain over scrap per kg of leftover</span></div><div class="tile"><b>+${num((pol.shadow[0] / pol.precut_remnant[0] - 1) * 100)}%</b><span>Extra from reshaping the layout</span></div><div class="tile"><b>×1.34</b><span>Cautious case (scrap ₹41/kg)</span></div></div>
  <div class="card"><h2>Value of the leftover, by policy</h2>${ks.map((k, i) => `<div class="orow"><div>${esc(L[k])}</div><div class="bar"><i class="${cl[i]}" style="width:${pol[k][0] / MAX * 100}%"></i></div><div class="v">×${num(pol[k][0], 2)}</div></div>`).join("")}<p class="note">Median over ${num(s.n)} simulated sheets, as a multiple of selling the same leftover as scrap. Middle half for Gap Fill: ×${num(pol.precut_remnant[1], 2)} to ×${num(pol.precut_remnant[2], 2)}.</p></div>
  <div class="cols"><div class="card"><h2>It depends on how many orders exist</h2><div class="leg"><span><i class="k0"></i>After cut, ordinary</span><span><i class="k1"></i>After cut, careful</span><span><i class="k2"></i>Gap Fill</span><span><i class="k3"></i>Gap Fill + shaping</span></div>${Object.keys(rn).map(g => `<div><b>${rn[g]}</b>${ks.map((k, i) => `<div class="mini"><span class="${cl[i]}"></span><div class="mt"><i class="${cl[i]}" style="width:${s.reg[g][k] / MAX * 100}%"></i></div><b>×${num(s.reg[g][k], 2)}</b></div>`).join("")}</div>`).join("")}<p class="note callout">With few matching orders the gain falls to about ×1.6. Whether enough buyers exist is a field question.</p></div>
  <div class="card"><h2>Example sheet</h2><img class="ex" alt="Cutting plan with 9 base plates and neighbours' parts" src="/static/sold_before_cut_example.png"><h4>Median gain over scrap per sheet</h4>${Object.entries(s.thick).map(([t, v]) => `<div class="orow"><div>${t} mm plate</div><div class="bar"><i style="width:${v / mxT * 100}%"></i></div><div class="v">${inr(v)}</div></div>`).join("")}</div></div>
  <div class="card"><h2>Assumptions</h2><div class="scroll"><table><tr><th>Input</th><th>Value</th><th>Status</th></tr><tr><td>Sheet, kerf, edge margin</td><td>2500 × 1250 mm, 3 mm, 10 mm</td><td>Typical</td></tr><tr><td>New plate / scrap</td><td>₹65/kg / ₹32/kg</td><td>${chip("assumption")}</td></tr><tr><td>Handling, cutting, pickup</td><td>₹2 per part, ₹6 per m, ₹300 per run</td><td>${chip("assumption")}</td></tr><tr><td>Demand</td><td>13 part types, five demand levels</td><td>${chip("sample")}</td></tr></table></div><p class="note">Source: research/gap_fill_sim.py. Run it again with real prices to update these figures.</p></div>`;
}

/* ---------------- Welcome ---------------- */
const MARK = `<svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="7" width="13" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><rect x="8.5" y="13" width="5" height="6" rx=".5" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="1.6 1.4" opacity=".7"/><rect x="17" y="7" width="13" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2" opacity=".55"/><rect x="19.5" y="11" width="6" height="7" rx="1" fill="#f2a33a"/><path d="M13 21.5h4.2" stroke="#f2a33a" stroke-width="1.6" stroke-linecap="round"/><path d="M15.6 19.9l1.8 1.6-1.8 1.6" fill="none" stroke="#f2a33a" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
async function vWelcome(el) {
  const C = await credits(), hero = Object.values(C).find(c => c.category === (st.lane === "fresh" ? "hero_kitchen" : "hero_workshop"));
  const credit = hero ? `${hero.shows}. "${hero.title}" by ${hero.author}, ${hero.licence}, via ${hero.site || "Wikimedia Commons"}` : "";
  el.innerHTML = `<div class="welcome">
    <section class="wh">${hero ? `<img src="${esc(hero.file)}" alt="${esc(hero.shows)}"><span class="ptag" title="Illustrative photo. ${esc(credit)}">Illustrative photo · <a href="${esc(hero.source_url)}" target="_blank" rel="noopener" title="${esc(credit)}">Credit</a></span>` : ""}
      <div class="wh-in">${MARK}<h1>Surplus, sold before it becomes scrap</h1><p>An ERP-connected B2B marketplace for industrial and food surplus.</p></div></section>
    <section class="flow" aria-label="How it works">
      <div class="fstep">${ic("plug")}<div><b>Find surplus in your system</b><span>Connect an ERP or billing export; offcuts, idle stock and food near its use-by are spotted for you.</span></div></div>${ic("arrow-right", "lg")}
      <div class="fstep">${ic("store")}<div><b>Match it on the marketplace</b><span>Buyers' orders are matched to the pieces and lots that fit, at a fair price.</span></div></div>${ic("arrow-right", "lg")}
      <div class="fstep">${ic("truck")}<div><b>Deliver it</b><span>One pickup route, the right vehicle and the papers for the driver.</span></div></div></section>
    <section class="roles" aria-label="Choose your role">
      <div class="role"><span class="rico">${ic("shopping-cart")}</span><h2>Buyer</h2><p>Post what you need or browse surplus near you, and order parts cut from it.</p><button class="btn" type="button" data-go="buyer/browse">Open the marketplace ${ic("arrow-right", "sm")}</button></div>
      <div class="role"><span class="rico">${ic("factory")}</span><h2>Seller</h2><p>Connect your stock, see what is surplus, and sell the spare part of your next sheet before you cut it.</p><button class="btn" type="button" data-go="seller/connect">Open my workshop ${ic("arrow-right", "sm")}</button></div>
      <div class="role"><span class="rico">${ic("shield-check")}</span><h2>Admin</h2><p>Follow every deal, see where each number comes from, and review the rules.</p><button class="btn" type="button" data-go="admin/monitor">Open the platform ${ic("arrow-right", "sm")}</button></div></section>
    <section class="start"><div><h2>New here? Start with the main idea</h2><p class="mut">A workshop sells the empty part of its next steel sheet to nearby buyers, before it cuts it. One screen, one number.</p></div>
      <button class="btn pri lg" type="button" data-go="seller/gapfill/durable">Start here ${ic("arrow-right")}</button></section>
    <p class="note">Every business, price and quantity in this demo is made up (Sample). The matching engine is real.</p></div>`;
  el.querySelectorAll("[data-go]").forEach(b => b.onclick = () => { const [role, view, lane] = b.dataset.go.split("/"); go({role, view, ...(lane ? {lane} : {})}); });
}

/* ---------------- render ---------------- */
const VIEWS = {welcome: vWelcome, post: vPost, reqs: vReqs, connect: vConnect, browse: vBrowse, pickup: vPickup, match: vMatch, deals: el => vDeals(el, false), gapfill: vGap, offers: vOffers, stock: vStock, monitor: el => vDeals(el, true), quality: vQuality, prices: vPrices, rules: vRules, sim: vSim};
let ticket = 0;
async function render() {
  shell();
  const my = ++ticket, main = $("#main"), welcome = st.view === "welcome";
  const [t, sub] = welcome ? ["CIRCULATE", ""] : TITLES[st.view];
  const head = welcome ? "" : `<header><h1>${esc(t)}</h1><p class="lead">${esc(sub)}</p></header>`;
  const body = document.createElement("div");
  body.className = "stack";
  const key = st.view + st.lane + st.role;
  if (main.dataset.view !== key) main.innerHTML = head + `<div class="skel-page" aria-busy="true"><div class="skel"></div><div class="skel"></div></div>`;
  try { await VIEWS[st.view](body); } catch (e) { body.innerHTML = errBox(e); }
  if (my !== ticket) return;
  const fresh = main.dataset.view !== key;
  main.dataset.view = key;
  main.replaceChildren();
  if (head) main.insertAdjacentHTML("beforeend", head);
  main.appendChild(body);
  document.title = welcome ? "CIRCULATE · Surplus, sold before it becomes scrap" : `${t} · CIRCULATE`;
  if (fresh) window.scrollTo(0, 0);
}
readHash();
if (st.view !== "welcome") history.replaceState(null, "", `#${st.role}/${st.view}/${st.lane}`);
render();
