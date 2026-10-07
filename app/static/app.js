/* CIRCULATE front end. Plain JavaScript, no build step. Talks to the FastAPI engine. */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const inr = v => v == null ? "–" : (v < 0 ? "−" : "") + "₹" + Math.abs(Math.round(v)).toLocaleString("en-IN");
const num = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("en-IN", {maximumFractionDigits: d, minimumFractionDigits: d});
const pct = v => Math.round(v * 100) + "%";

async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const t = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = t.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map(x => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ") : r.statusText);
  }
  return t;
}

/* ---------------- state and navigation ---------------- */
const LANES = {durable: {name: "Durable", cls: "np", scen: ["steel", "sheets", "bars"]}, fresh: {name: "Fresh", cls: "pe", scen: ["food"]}};
const ROLES = {buyer: "Buyer", seller: "Seller", admin: "Admin"};
const NAV = {
  buyer: [["browse", "Browse surplus"], ["match", "Order and match"], ["deals", "My deals"]],
  seller: [["connect", "Connect your system"], ["gapfill", "Sold before it's cut", "durable"], ["offers", "Offers and recovery plan"], ["stock", "Stock and passports"]],
  admin: [["monitor", "Deal monitor"], ["quality", "Data quality"], ["prices", "Price references"], ["rules", "Rules"], ["sim", "Simulation lab"]],
};
const TITLES = {
  connect: ["Connect your system", "Import from ERP or POS export (CSV). Upload the stock file your system exports; the engine checks every row and finds the surplus."],
  browse: ["Browse surplus", "Every piece and lot on offer, drawn to scale, with its seller, distance and price range."],
  match: ["Order and match", "Say what you need. The engine checks every seller's stock, nests the parts and finds the cheapest pickup."],
  deals: ["My deals", "Track each order from agreed to dispatched to delivered."],
  gapfill: ["Sold before it's cut", "Fill the empty space in your cutting plan with parts your neighbours have already ordered."],
  offers: ["Offers and recovery plan", "Which buyers to try, in which order, before the deadline. Scrap or disposal is the last step."],
  stock: ["Stock and passports", "Every piece or lot, its scrap value, and a passport with a QR code."],
  monitor: ["Deal monitor", "Every deal by stage. Simulated deals are never counted as verified."],
  quality: ["Data quality", "How much of what the app shows is measured, sample, simulated or verified."],
  prices: ["Price references", "Every price the engine uses, with its source and date."],
  rules: ["Rules", "What the engine checks before it accepts a piece or a lot."],
  sim: ["Simulation lab", "Gap Fill tested on 5,000 simulated sheets. Simulated, sample prices."],
};
const st = {lane: "durable", role: "seller", view: "gapfill", scen: {durable: "steel", fresh: "food"}, ord: {}, rate: {}, off: {},
  plan: {rejected: [], elapsed: 0, key: null}, deadline: {}, gf: null, gfDeclined: [], sellerFilter: "", lastDeal: null,
  imp: {durable: {}, fresh: {}}, br: {durable: {q: "", mat: "", thick: "", seller: "", sort: "distance"}, fresh: {q: "", mat: "", thick: "", seller: "", sort: "hours"}}, impLimit: {days: 30, hours: 24}, impMeasured: false,
  job: {sheet_length_mm: 2500, sheet_width_mm: 1250, thickness_mm: 6, grade: "E250", part_name: "base plate", part_length_mm: 450, part_width_mm: 350, quantity: 9}};

function navFor(role, lane) { return NAV[role].filter(n => !n[2] || n[2] === lane); }
function readHash() {
  const [role, view, lane] = location.hash.slice(1).split("/");
  if (ROLES[role]) st.role = role;
  if (LANES[lane]) st.lane = lane;
  if (view && navFor(st.role, st.lane).some(n => n[0] === view)) st.view = view;
  else if (!navFor(st.role, st.lane).some(n => n[0] === st.view)) st.view = navFor(st.role, st.lane)[0][0];
}
function go(patch) {
  Object.assign(st, patch);
  if (!navFor(st.role, st.lane).some(n => n[0] === st.view)) st.view = navFor(st.role, st.lane)[0][0];
  history.replaceState(null, "", `#${st.role}/${st.view}/${st.lane}`);
  render();
}
function shell() {
  $("#lane").className = "seg " + LANES[st.lane].cls;
  $("#lane").innerHTML = Object.entries(LANES).map(([k, v]) => `<button type="button" data-lane="${k}" aria-pressed="${k === st.lane}">${v.name}</button>`).join("");
  $("#role").innerHTML = Object.entries(ROLES).map(([k, v]) => `<button type="button" data-role="${k}" aria-pressed="${k === st.role}">${v}</button>`).join("");
  $("#nav").innerHTML = `<h6>${ROLES[st.role]} · ${LANES[st.lane].name}</h6>` + navFor(st.role, st.lane).map(([k, t]) =>
    `<button type="button" data-view="${k}" ${k === st.view ? 'aria-current="page"' : ""}>${esc(t)}</button>`).join("") +
    (st.role === "seller" && st.lane === "fresh" ? `<p class="railnote">Sold before it's cut applies to sheet cutting, so it lives in the Durable lane.</p>` : "");
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-lane],[data-role],[data-view]");
  if (!b || !b.closest(".rail,.topbar")) return;
  if (b.dataset.lane) go({lane: b.dataset.lane});
  else if (b.dataset.role) go({role: b.dataset.role, view: navFor(b.dataset.role, st.lane)[0][0]});
  else go({view: b.dataset.view});
});

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
const chip = l => {
  const m = {sample: "p-warn", assumption: "p-warn", simulated: "p-mut", modelled: "p-mut", measured: "p-ok", verified: "p-ok"};
  return `<span class="pill ${m[l] || "p-mut"}">${esc(l)}</span>`;
};
function split(v) {
  if (!v || v.buyer_value_W <= 0) return `<p class="note">Donation: no buyer payment. It avoids a disposal cost of ${inr(-v.salvage_value_S)} at a transport cost of ${inr(v.transport_T)}.</p>`;
  const W = v.buyer_value_W, s = Math.max(v.salvage_value_S, 0), t = v.transport_T, g = Math.max(v.gain_G, 0);
  return `<div class="split" role="img" aria-label="Split of the buyer's value"><i class="s" style="width:${s / W * 100}%"></i><i class="t" style="width:${t / W * 100}%"></i><i class="g" style="width:${g / W * 100}%"></i></div>
  <div class="leg"><span><i class="s"></i>Seller's alternative (scrap or disposal)</span><span><i class="t"></i>Transport</span><span><i class="g"></i>Value created</span></div>`;
}
function sheetSvg(a) {
  const L = a.length_mm, W = a.width_mm, sc = 300 / Math.max(L, W), w = L * sc, h = W * sc;
  return `<figure class="sheet"><svg viewBox="-2 -2 ${w + 4} ${h + 4}" role="img" aria-label="Cutting plan ${esc(a.resource_id)}"><rect class="piece" x="0" y="0" width="${w}" height="${h}"/>${
    (a.defects || []).map(d => `<rect x="${d.x_mm * sc}" y="${d.y_mm * sc}" width="${d.length_mm * sc}" height="${d.width_mm * sc}" style="fill:var(--bad);opacity:.35"/>`).join("")}${
    a.parts.map(p => `<rect class="pt" x="${p.x * sc}" y="${p.y * sc}" width="${p.length * sc}" height="${p.width * sc}"/>`).join("")}</svg><figcaption>${esc(a.resource_id)} · ${num(L)}×${num(W)} mm · ${a.units} parts</figcaption></figure>`;
}
const errBox = e => `<div class="err" role="alert">${esc(e.message || e)}</div>`;

/* ---------------- Buyer: browse surplus (marketplace listings) ---------------- */
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";
const labelChip = l => l === "measured" ? chip("measured") : chip(l || "sample");
function pieceSvg(it) {
  if (it.kind === "linear") {
    const R = Math.max(6000, it.length_mm);
    return `<svg viewBox="-40 -40 ${R + 80} 680" role="img" aria-label="${num(it.length_mm)} mm bar drawn to scale against a 6 m length" preserveAspectRatio="xMidYMid meet">
      <rect x="0" y="230" width="${R}" height="140" rx="20" style="fill:none;stroke:var(--mut);stroke-width:8;stroke-dasharray:40 30"/>
      <rect x="0" y="230" width="${it.length_mm}" height="140" rx="20" style="fill:var(--piece);stroke:var(--np);stroke-width:12"/></svg>`;
  }
  const RL = Math.max(2500, it.length_mm), RW = Math.max(1250, it.width_mm), sw = RL / 300;
  return `<svg viewBox="${-sw * 4} ${-sw * 4} ${RL + sw * 8} ${RW + sw * 8}" role="img" aria-label="${num(it.length_mm)} by ${num(it.width_mm)} mm piece drawn to scale against a 2500 by 1250 mm sheet${(it.defects || []).length ? ", defects in red" : ""}" preserveAspectRatio="xMidYMid meet">
    <rect x="0" y="0" width="${RL}" height="${RW}" style="fill:none;stroke:var(--mut);stroke-width:${sw};stroke-dasharray:${sw * 5} ${sw * 4}"/>
    <rect x="0" y="0" width="${it.length_mm}" height="${it.width_mm}" style="fill:var(--piece);stroke:var(--np);stroke-width:${sw * 1.6}"/>
    ${(it.defects || []).map(d => `<rect x="${d.x_mm}" y="${d.y_mm}" width="${d.length_mm}" height="${d.width_mm}" style="fill:var(--bad);opacity:.55"><title>${esc(d.note || "defect")}</title></rect>`).join("")}</svg>`;
}
function priceSrc(o, what) {
  return `${what} ₹${num(Math.abs(o.price_per_kg), 1)}/kg · ${esc(cap(o.label))} · ${o.quoted_on ? "quoted " + esc(o.quoted_on) : "no date"}`;
}
function listingCard(it, lane) {
  const durable = lane === "durable", flat = it.kind === "flat";
  let title, spec, media, price;
  if (it.kind === "perishable") {
    const h = it.hours_left, urgent = h < 12;
    title = esc(it.product);
    spec = `${num(it.quantity_kg, 1)} kg · ${esc(it.storage)} · ${esc(it.category.replace(/_/g, " "))}`;
    media = `<div class="lclock"><div class="ctrl"><span class="lbl">Use-by</span><div class="grow"></div><b class="${urgent ? "bad" : ""}">${num(h, h % 1 ? 1 : 0)} h left</b></div>
      <div class="bar ${urgent ? "bad" : "pe"}" role="img" aria-label="${num(h)} hours left of a 72 hour scale"><i style="width:${Math.max(3, Math.min(1, h / 72) * 100)}%"></i></div>
      <span class="note">${urgent ? "Under 12 hours: collect today." : "Scale: 72 hours."}</span></div>`;
    price = it.disposal ? `<div class="lprice"><b>${inr(-it.disposal.value)}</b><span>Disposal cost the seller avoids</span></div><p class="note">${priceSrc(it.disposal, "Disposal")}</p>`
      : `<div class="lprice"><b>–</b><span>No disposal cost entered yet</span></div>`;
  } else {
    title = flat ? `${num(it.length_mm)} × ${num(it.width_mm)} mm` : `${esc(it.profile)}, ${num(it.length_mm)} mm`;
    spec = flat ? `${num(it.thickness_mm, it.thickness_mm % 1 ? 1 : 0)} mm ${esc(it.material_name)} · ${esc(it.grade)} · ${esc(it.condition.replace(/_/g, " "))} · ${it.is_full_sheet ? "full sheet" : "offcut"}`
      : `${esc(it.material_name)} · ${esc(it.grade)} · ${esc(it.condition.replace(/_/g, " "))}`;
    media = `<div class="lsvg">${pieceSvg(it)}</div><span class="note lcap">To scale against a ${flat ? "2,500 × 1,250 mm sheet" : "6 m length"} (dashed)${(it.defects || []).length ? "; red = defect" : ""}</span>`;
    if (it.floor) {
      const c = it.ceiling;
      price = `<div class="lprice"><b>${inr(it.floor.value)}${c ? ` – ${inr(c.value)}` : ""}</b><span>Price range for the whole ${flat ? "piece" : "length"}</span></div>
        <p class="note">Floor: scrap value, ${priceSrc(it.floor, "scrap")}.<br>${c ? `Ceiling: same ${flat ? "steel from new sheet" : "material from new stock"} at the buyer's new price ₹${num(c.price_per_kg, 1)}/kg · ${esc(cap(c.label))}.` : "No ceiling: no buyer in this cluster has a new price for this material."}</p>`;
    } else price = `<div class="lprice"><b>–</b><span>No price yet</span></div><p class="note">Imported stock has no scrap quote yet. A dated quote is needed before a price is shown.</p>`;
  }
  const photo = it.photo ? `<figure class="lphoto"><img src="${esc(it.photo)}" alt="Photo of ${esc(it.id)} uploaded by the seller" loading="lazy"><figcaption class="note">Photo uploaded by the seller</figcaption></figure>` : "";
  const dist = it.distance_km == null ? "Location not given" : `${num(it.distance_km, 1)} km`;
  const uid = `${it.key}:${it.id}`;
  return `<article class="lcard ${durable ? "np" : "pe"}" aria-label="${esc(it.id)}">${photo}<div class="lmedia">${media}</div>
    <div class="lbody"><div class="ltop"><h3>${title}</h3>${labelChip(it.label)}</div><p class="mut">${spec}</p>
      <dl class="lfacts"><dt>Seller</dt><dd>${esc(it.seller_name)}</dd><dt>Distance</dt><dd>${dist}</dd><dt>Weight</dt><dd>${num(it.kg, 1)} kg</dd><dt>Item</dt><dd><code>${esc(it.id)}</code></dd></dl>
      ${price}
      ${it.rules ? `<p class="note">Imported from a CSV export. Found as surplus: ${it.rules.map(esc).join("; ")}.</p>` : ""}
      <div class="lact"><button class="btn pri sm" type="button" data-order="${esc(uid)}" ${it.order ? "" : "disabled"}>Order parts from this</button><a class="btn sm" href="${esc(it.passport)}" target="_blank" rel="noopener">Passport</a></div>
      ${it.order ? `<p class="note">Opens ${esc(it.order.buyer_name)}'s order with only ${esc(it.seller_name)} ticked.</p>` : `<p class="note lwhy">Cannot order yet: ${esc(it.order_reason)}</p>`}</div></article>`;
}
async function vBrowse(el) {
  const lane = st.lane, F = st.br[lane], durable = lane === "durable";
  const r = await api(`/api/browse/${lane}`);
  const items = r.items, byUid = Object.fromEntries(items.map(it => [`${it.key}:${it.id}`, it]));
  const matOf = it => durable ? it.material_name : it.category.replace(/_/g, " ");
  const uniq = xs => [...new Set(xs)].sort((a, b) => typeof a === "number" ? a - b : String(a).localeCompare(String(b)));
  const mats = uniq(items.map(matOf)), thicks = uniq(items.filter(it => it.kind === "flat").map(it => it.thickness_mm)), sellers = uniq(items.map(it => it.seller_name));
  const sorts = durable ? [["distance", "Distance, nearest first"], ["size", "Size, largest first"]] : [["hours", "Hours left, soonest first"], ["distance", "Distance, nearest first"]];
  const sel = (id, label, cur, opts, all) => `<label class="field">${label}<select id="${id}"><option value="">${all}</option>${opts.map(o => `<option value="${esc(o)}" ${String(o) === String(cur) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  el.innerHTML = `<div class="card noprint"><div class="ctrl"><label class="field lsearch">Search<input id="brQ" type="search" placeholder="Item, seller, grade, product" value="${esc(F.q)}"></label>
      ${sel("brMat", durable ? "Material" : "Category", F.mat, mats, durable ? "All materials" : "All categories")}
      ${durable ? sel("brThick", "Thickness mm", F.thick, thicks, "Any thickness") : ""}
      ${sel("brSeller", "Seller", F.seller, sellers, "All sellers")}
      <label class="field">Sort by<select id="brSort">${sorts.map(([k, t]) => `<option value="${k}" ${k === F.sort ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <div class="grow"></div><button class="btn" id="brClear" type="button">Clear filters</button></div>
    <p class="note">Distances are straight-line km from the buyer of the first open order in each sample cluster (${Object.entries(r.distance_from).map(([k, b]) => esc(b)).join(", ")}). Prices and positions are sample data.${durable ? "" : " Food rules are platform rules that still need expert review."}</p></div>
    <div class="ctrl"><span id="brCount" class="mut" aria-live="polite"></span></div><div id="brGrid" class="lgrid"></div>`;
  const grid = $("#brGrid", el), count = $("#brCount", el);
  function paint() {
    const q = F.q.trim().toLowerCase();
    let xs = items.filter(it => (!F.mat || matOf(it) === F.mat) && (!F.thick || (it.kind === "flat" && String(it.thickness_mm) === String(F.thick))) && (!F.seller || it.seller_name === F.seller)
      && (!q || [it.id, it.seller_name, it.grade, it.product, it.material_name, it.profile, it.category, it.condition].some(v => v && String(v).toLowerCase().includes(q))));
    const far = v => v == null ? Infinity : v;
    if (F.sort === "distance") xs.sort((a, b) => far(a.distance_km) - far(b.distance_km));
    else if (F.sort === "size") xs.sort((a, b) => (b.length_mm * (b.width_mm || 1)) - (a.length_mm * (a.width_mm || 1)));
    else xs.sort((a, b) => a.hours_left - b.hours_left);
    count.textContent = `Showing ${xs.length} of ${items.length} ${durable ? "pieces" : "lots"}`;
    grid.innerHTML = !items.length ? `<div class="empty">Nothing is on offer in this lane yet. Sellers add stock under Connect your system.</div>`
      : xs.length ? xs.map(it => listingCard(it, lane)).join("") : `<div class="empty">No ${durable ? "pieces" : "lots"} match these filters. <button class="btn sm" type="button" id="brClear2">Clear filters</button></div>`;
    const c2 = $("#brClear2", grid); if (c2) c2.onclick = clear;
  }
  function clear() { Object.assign(F, {q: "", mat: "", thick: "", seller: ""}); render(); }
  $("#brQ", el).oninput = e => { F.q = e.target.value; paint(); };
  for (const [id, k] of [["brMat", "mat"], ["brThick", "thick"], ["brSeller", "seller"], ["brSort", "sort"]]) { const s = $("#" + id, el); if (s) s.onchange = () => { F[k] = s.value; paint(); }; }
  $("#brClear", el).onclick = clear;
  grid.addEventListener("click", async e => {
    const b = e.target.closest("[data-order]");
    if (!b) return;
    const it = byUid[b.dataset.order], k = it.order.scenario, S = await scen(k);
    st.scen[lane] = k; st.ord[k] = it.order.index;
    st.off[k] = new Set(S.data.sellers.map(x => x.id).filter(id => id !== it.seller_id));
    go({view: "match"});
  });
  paint();
}

/* ---------------- Buyer: order and match ---------------- */
async function vMatch(el) {
  const k = laneScen(), S = await scen(k), fresh = S.meta.engine === "perishable";
  const i = Math.min(st.ord[k] ?? 0, S.data.demands.length - 1), d = S.data.demands[i];
  const off = st.off[k] || (st.off[k] = new Set());
  const rate = st.rate[k] ?? 1;
  const data = structuredClone(S.data);
  data.transport.rate_multiplier = rate;
  const sellerIds = S.data.sellers.map(s => s.id).filter(id => !off.has(id));
  const sellerName = id => (S.data.sellers.find(s => s.id === id) || {}).name || id;
  let f, err;
  try { f = await api("/api/fulfil", {demand_id: d.id, data, seller_ids: sellerIds, allow_partial: true}); } catch (e) { err = e; }
  const ctrl = `<div class="card noprint"><div class="ctrl">${await scenSeg()}
    <label class="field">Order<select id="ordSel">${S.data.demands.map((x, j) => `<option value="${j}" ${j === i ? "selected" : ""}>${esc(x.buyer_name)}: ${num(x.quantity, fresh ? 1 : 0)} ${esc(x.part_name)}</option>`).join("")}</select></label>
    <label class="field">Transport rate ×<input id="rate" type="number" min="0.2" max="5" step="0.1" value="${rate}"></label></div>
    <div class="ctrl"><span class="lbl">Sellers in the cluster</span>${S.data.sellers.map(s => `<label class="check"><input type="checkbox" data-seller="${esc(s.id)}" ${off.has(s.id) ? "" : "checked"}>${esc(s.name)}</label>`).join("")}</div></div>`;
  if (err) { el.innerHTML = ctrl + errBox(err); bind(); return; }
  const b = f.best;
  let h = ctrl;
  if (!b) h += `<div class="card"><h2>No match</h2><p>${esc(f.message)}</p>${rejList(f)}</div>`;
  else {
    const v = b.value, kgCol = f.unit_name !== "kg";
    h += `<div class="card"><div class="ctrl"><h2>${esc(d.buyer_name)} needs ${num(d.quantity, fresh ? 1 : 0)} ${esc(d.part_name)}</h2>${f.feasible ? '<span class="pill p-ok">Fully matched</span>' : `<span class="pill p-warn">Short by ${num(f.shortfall, 1)}</span>`}${v.is_donation ? '<span class="pill p-mut">Donation</span>' : ""}</div>
      <p class="mut">${esc(f.message)}</p>
      <div class="fig"><div><b>${inr(v.buyer_value_W)}</b><span>${fresh ? "Worth to the buyer" : "Same parts from new sheet"}</span></div><div><b>${inr(v.salvage_value_S)}</b><span>${fresh ? "Seller's disposal alternative" : "Sold as scrap"}</span></div><div><b>${inr(v.transport_T)}</b><span>Transport</span></div><div class="g"><b>${inr(v.gain_G)}</b><span>Value created</span></div><div><b>${inr(v.buyer_saving)}</b><span>Buyer saves</span></div></div>
      ${split(v)}
      <p class="note">${esc(b.vehicle)} · ${num(b.route_km, 2)} km round trip · ${b.trips} ${b.trips === 1 ? "trip" : "trips"} · pickup ${b.pickup_order.map(esc).join(" → ")} · without this the stock would be ${esc(b.counterfactual.join(", "))}. Prices are sample data.</p>
      <div class="ctrl noprint"><button class="btn pri" id="accept" type="button">Accept this order</button><span id="acceptMsg" class="note" aria-live="polite"></span></div></div>`;
    h += `<div class="cols"><div class="card"><h2>Who supplies it</h2><div class="scroll"><table><tr><th>Seller</th><th class="n">${esc(f.unit_name)}</th>${kgCol ? '<th class="n">kg</th>' : ""}<th class="n">Alternative</th><th class="n">Gain</th></tr>${b.per_seller.map(p => `<tr><td>${esc(p.seller_name)}</td><td class="n">${num(p.units, fresh ? 1 : 0)}</td>${kgCol ? `<td class="n">${num(p.kg_shipped, 1)}</td>` : ""}<td class="n">${inr(p.salvage_value)}</td><td class="n">${inr(p.gain_above_salvage)}</td></tr>`).join("")}</table></div>
      <h4>What every seller could supply</h4>${f.seller_capacity.map(c => `<div class="orow"><div>${esc(c.seller_name)}<div class="note">${num(c.distance_km, 1)} km away</div></div><div class="bar ${fresh ? "pe" : ""}"><i style="width:${Math.min(100, c.capacity / Math.max(1, ...f.seller_capacity.map(z => z.capacity)) * 100)}%"></i></div><div class="v">${num(c.capacity, fresh ? 1 : 0)}</div></div>`).join("")}</div>
      <div class="card">${rejList(f)}<h4>Other combinations the engine tried</h4><div class="scroll"><table><tr><th>Sellers</th><th class="n">Value created</th><th class="n">km</th></tr>${f.alternatives.map(a => `<tr><td>${a.sellers_used.map(esc).join(" + ")}</td><td class="n">${inr(a.value.gain_G)}</td><td class="n">${num(a.route_km, 2)}</td></tr>`).join("") || '<tr><td colspan="3" class="mut">None</td></tr>'}</table></div></div></div>`;
    const sheets = b.allocations.filter(a => a.shape === "sheet" && a.parts && a.parts.length);
    if (sheets.length) h += `<div class="card"><h2>Cutting floor</h2><p class="mut">Grey is the seller's piece, drawn to scale; red marks a defect the engine cut around. Yellow shapes are the buyer's parts, placed with ${num(S.data.settings.kerf_mm, 1)} mm kerf and ${num(S.data.settings.edge_margin_mm)} mm edge margin.</p><div class="sheets">${sheets.slice(0, 12).map(sheetSvg).join("")}</div>${sheets.length > 12 ? `<p class="note">Showing 12 of ${sheets.length} pieces used.</p>` : ""}</div>`;
    else if (fresh) h += `<div class="card"><h2>Lots taken</h2><div class="scroll"><table><tr><th>Lot</th><th class="n">Taken kg</th><th class="n">Lot kg</th><th class="n">Hours left</th><th>Storage</th></tr>${b.allocations.map(a => `<tr><td>${esc(a.resource_id)}</td><td class="n">${num(a.taken_kg, 1)}</td><td class="n">${num(a.quantity_kg, 1)}</td><td class="n">${num(a.hours_left)}</td><td>${esc(a.storage)}</td></tr>`).join("")}</table></div><p class="note">Food rules are platform rules and still need expert review.</p></div>`;
    else h += `<div class="card"><h2>Pieces used</h2><div class="scroll"><table><tr><th>Piece</th><th>Seller</th><th class="n">Cut pieces</th></tr>${b.allocations.map(a => `<tr><td>${esc(a.resource_id)}</td><td>${esc(sellerName(a.seller_id))}</td><td class="n">${num(a.units)}</td></tr>`).join("")}</table></div></div>`;
  }
  el.innerHTML = h;
  bind();
  function bind() {
    bindScen(el);
    $("#ordSel", el).onchange = e => { st.ord[k] = +e.target.value; render(); };
    $("#rate", el).onchange = e => { const x = +e.target.value; if (x > 0) { st.rate[k] = x; render(); } };
    el.querySelectorAll("[data-seller]").forEach(c => c.onchange = () => { c.checked ? off.delete(c.dataset.seller) : off.add(c.dataset.seller); render(); });
    const acc = $("#accept", el);
    if (acc) acc.onclick = async () => {
      acc.disabled = true;
      try {
        const deal = await api("/api/deals", {kind: S.meta.engine, counterfactual: b.counterfactual[0], demand_id: d.id, buyer_name: d.buyer_name,
          sellers: b.sellers_used, units: b.units, unit_name: f.unit_name, kg_in_units: b.kg_in_units, kg_shipped: b.kg_shipped,
          route_km: b.route_km, value_above_salvage: b.value.seller_gain_above_salvage, buyer_utilisation: d.buyer_utilisation ?? null});
        st.lastDeal = deal.id;
        $("#acceptMsg", el).innerHTML = `Deal ${deal.id} agreed (simulated). <a href="#buyer/deals/${st.lane}" id="toDeals">Track it in My deals</a>`;
        $("#toDeals", el).onclick = ev => { ev.preventDefault(); go({view: "deals"}); };
      } catch (e) { $("#acceptMsg", el).innerHTML = `<span class="err">${esc(e.message)}</span>`; acc.disabled = false; }
    };
  }
}
function rejList(f) {
  return `<h2>Refused by the rules (${f.rejections.length})</h2><ul class="rej">${f.rejections.map(r => `<li><code>${esc(r.resource_id)}</code> ${esc(r.reason)}</li>`).join("") || "<li>None</li>"}</ul>`;
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
async function vConnect(el) {
  const lane = st.lane, S = st.imp[lane], durable = lane === "durable", r = S.result;
  const lim = durable ? `<label class="field">Surplus if unused for at least (days)<input id="impN" type="number" min="0" max="36500" step="1" value="${st.impLimit.days}"></label>`
    : `<label class="field">Urgent if use-by within (hours)<input id="impH" type="number" min="0" max="8760" step="1" value="${st.impLimit.hours}"></label>`;
  let h = `<div class="card"><div class="ctrl"><h2>Import from ERP or POS export (CSV)</h2><div class="grow"></div><a class="btn" href="/api/import/example/${lane}" download>Download example file</a></div>
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

/* ---------------- Seller: Sold before it's cut (Gap Fill) ---------------- */
const OCOL = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];
function gapSvg(r, after, colours) {
  const s = r.sheet, L = s.length_mm, W = s.width_mm, m = r.edge_margin_mm, id = after ? "ha" : "hb";
  let g = `<defs><pattern id="${id}" patternUnits="userSpaceOnUse" width="36" height="36" patternTransform="rotate(45)"><rect width="36" height="36" style="fill:var(--bg)"/><line x1="0" y1="0" x2="0" y2="36" style="stroke:var(--scrap);stroke-width:10"/></pattern></defs>`;
  g += `<rect x="0" y="0" width="${L}" height="${W}" style="fill:var(--piece);stroke:var(--mut);stroke-width:2" vector-effect="non-scaling-stroke"/>`;
  g += r.free_rects.map(f => `<rect x="${f.x}" y="${f.y}" width="${f.w}" height="${f.h}" style="fill:url(#${id})"/>`).join("");
  if (after) for (const n of r.neighbours) {
    const c = colours[n.order_id];
    g += n.parts.map(p => `<rect x="${p.x}" y="${p.y}" width="${p.length}" height="${p.width}" style="fill:${c};stroke:var(--fg);stroke-width:1" vector-effect="non-scaling-stroke"><title>${esc(n.buyer_name)}: ${esc(n.part_name)}</title></rect>${p.length >= 140 && p.width >= 90 ? `<text x="${p.x + p.length / 2}" y="${p.y + p.width / 2 + 16}" text-anchor="middle" style="font:600 46px var(--mono);fill:#10181f">${esc(n.order_id)}</text>` : ""}`).join("");
  }
  g += r.own.parts.map(p => `<rect x="${p.x}" y="${p.y}" width="${p.length}" height="${p.width}" style="fill:var(--own);stroke:var(--ownl);stroke-width:1.5" vector-effect="non-scaling-stroke"><title>Your ${esc(r.own.part_name)}</title></rect>${p.length >= 200 && p.width >= 120 ? `<text x="${p.x + p.length / 2}" y="${p.y + p.width / 2 + 16}" text-anchor="middle" style="font:600 46px var(--body);fill:#fff">YOURS</text>` : ""}`).join("");
  g += `<rect x="${m}" y="${m}" width="${L - 2 * m}" height="${W - 2 * m}" style="fill:none;stroke:var(--mut);stroke-width:1;stroke-dasharray:6 6" vector-effect="non-scaling-stroke"/>`;
  return `<svg viewBox="-10 -10 ${L + 20} ${W + 20}" role="img" aria-label="${after ? "Cutting plan with neighbours' parts" : "Your cutting plan and the empty space"}">${g}</svg>`;
}
function gField(label, path, type = "number", extra = "") {
  const [a, b] = path.split(".");
  const v = b ? st.gf[a][b] : st.gf[a];
  return `<label class="field">${label}<input data-gf="${path}" type="${type}" value="${esc(v)}" ${extra}></label>`;
}
async function vGap(el) {
  if (!st.gf) st.gf = await api("/api/gapfill/demo");
  let r, err;
  try { r = await api("/api/gapfill", {...st.gf, declined_order_ids: st.gfDeclined}); } catch (e) { err = e; }
  const ctrl = `<div class="card noprint"><div class="ctrl"><span class="lbl">Sheet</span>${gField("Length mm", "sheet.length_mm", "number", 'min="200" step="10"')}${gField("Width mm", "sheet.width_mm", "number", 'min="200" step="10"')}
    <label class="field">Thickness mm<select data-gf="sheet.thickness_mm">${[2, 3, 5, 6, 8, 10].map(t => `<option ${t === st.gf.sheet.thickness_mm ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <label class="field">Grade<select data-gf="sheet.grade">${["E250", "E350"].map(g => `<option ${g === st.gf.sheet.grade ? "selected" : ""}>${g}</option>`).join("")}</select></label></div>
    <div class="ctrl"><span class="lbl">Your job</span>${gField("Part", "job.part_name", "text", 'style="width:130px"')}${gField("Length mm", "job.length_mm", "number", 'min="10" step="10"')}${gField("Width mm", "job.width_mm", "number", 'min="10" step="10"')}${gField("Quantity", "job.quantity", "number", 'min="1" step="1"')}</div>
    <div class="ctrl"><span class="lbl">Prices (sample)</span>${gField("Scrap ₹/kg", "prices.scrap_price_per_kg", "number", 'min="0" step="1"')}${gField("Pickup ₹", "prices.pickup", "number", 'min="0" step="50"')}<div class="grow"></div><button class="btn" id="gfReset" type="button">Reset demo</button></div></div>`;
  if (err) { el.innerHTML = ctrl + errBox(err); bindG(); return; }
  const mo = r.money, colours = {};
  r.neighbours.forEach((n, i) => colours[n.order_id] = OCOL[i % OCOL.length]);
  const units = r.neighbours.reduce((a, n) => a + n.units, 0), mx = Math.max(mo.scrap_alone, mo.total_with_gap_fill, 1);
  let h = ctrl;
  h += `<div class="tiles"><div class="tile"><b>${inr(mo.scrap_alone)}</b><span>Leftover sold as scrap (${num(mo.leftover_kg, 1)} kg)</span></div><div class="tile"><b>${inr(mo.total_with_gap_fill)}</b><span>Same leftover, sold before it's cut</span></div><div class="tile hi"><b>${inr(mo.net_gain)}</b><span>Extra for you, after ${inr(mo.pickup)} pickup</span></div><div class="tile"><b>${mo.ratio ? "×" + num(mo.ratio, 2) : "–"}</b><span>${units} neighbour parts reserved</span></div></div>`;
  h += `<div class="card"><div class="ctrl"><h2>One sheet, two kinds of customer</h2>${r.checks.ok ? '<span class="pill p-ok">Checked: no overlaps, inside margins</span>' : `<span class="pill p-bad">Check failed</span>`}${chip(r.label)}<div class="grow"></div><button class="btn noprint" id="gfPrint" type="button">Print cutting plan</button></div>
    <p class="mut">${esc(r.message)}${r.worth_doing ? ` Best layout: ${esc(r.option)}.` : ""}</p>
    <div class="cols"><div class="big"><h4>Before: your job, the rest goes to scrap</h4>${gapSvg(r, false, colours)}</div><div class="big"><h4>After: the gap is sold before it's cut</h4>${gapSvg(r, true, colours)}</div></div>
    <div class="leg"><span><i style="background:var(--own)"></i>Your ${esc(r.own.part_name)} × ${r.own.units}</span><span><i style="background:repeating-linear-gradient(45deg,var(--scrap),var(--scrap) 3px,var(--bg) 3px,var(--bg) 6px)"></i>Empty space (scrap)</span>${r.neighbours.map(n => `<span><i style="background:${colours[n.order_id]}"></i>${esc(n.order_id)} ${esc(n.buyer_name)}: ${esc(n.part_name)} × ${n.units}</span>`).join("")}</div>
    <div><h4>Value of the leftover</h4><div class="orow"><div>Sold as scrap</div><div class="bar"><i style="width:${mo.scrap_alone / mx * 100}%;background:var(--scrap)"></i></div><div class="v">${inr(mo.scrap_alone)}</div></div><div class="orow"><div>Sold before it's cut</div><div class="bar warn"><i style="width:${mo.total_with_gap_fill / mx * 100}%"></i></div><div class="v">${inr(mo.total_with_gap_fill)}</div></div>
    <p class="note">Before it's cut = neighbours' parts ${inr(mo.neighbours_value)} + scrap of what is still left ${inr(mo.scrap_of_rest)} − pickup ${inr(mo.pickup)}. Part values are what each buyer would pay for the same steel from a new sheet, less handling and cutting. All prices are sample data.</p></div></div>`;
  h += `<div class="cols"><div class="card"><h2>Reserved for neighbours</h2>${r.neighbours.length ? `<div class="scroll"><table><tr><th>Order</th><th>Buyer</th><th>Part</th><th class="n">Parts</th><th class="n">Value</th><th class="noprint"></th></tr>${r.neighbours.map(n => `<tr><td><span class="dot" style="background:${colours[n.order_id]}"></span>${esc(n.order_id)}</td><td>${esc(n.buyer_name)}</td><td>${esc(n.part_name)}</td><td class="n">${n.units}</td><td class="n">${inr(n.value)}</td><td class="noprint"><button class="btn sm" data-decline="${esc(n.order_id)}" type="button">Decline</button></td></tr>`).join("")}</table></div>` : `<div class="empty">Nothing reserved. Filling the gap would not beat selling the leftover as scrap.</div>`}
    <p class="note">If a buyer declines, the plan is recalculated at once and their space goes to the next best order.</p></div>
    <div class="card"><h2>Orders not used (${r.refused.length})</h2><ul class="rej">${r.refused.map(x => `<li><code>${esc(x.order_id)}</code> ${esc(x.reason)}${st.gfDeclined.includes(x.order_id) ? ` <button class="btn sm noprint" data-restore="${esc(x.order_id)}" type="button">Restore</button>` : ""}</li>`).join("") || "<li>None</li>"}</ul>
    <h4>Standing orders in the cluster</h4><div class="scroll"><table><tr><th>Order</th><th>Buyer</th><th>Part mm</th><th class="n">t mm</th><th>Grades</th><th class="n">Qty</th></tr>${st.gf.orders.map(o => `<tr><td>${esc(o.id)}</td><td>${esc(o.buyer_name)}</td><td>${esc(o.part_name)} ${num(o.length_mm)}×${num(o.width_mm)}</td><td class="n">${num(o.thickness_mm)}</td><td>${o.accepted_grades.map(esc).join(", ")}</td><td class="n">${o.quantity}</td></tr>`).join("")}</table></div></div></div>`;
  el.innerHTML = h;
  bindG();
  function bindG() {
    el.querySelectorAll("[data-gf]").forEach(inp => inp.onchange = () => {
      const [a, b] = inp.dataset.gf.split(".");
      let v = inp.type === "text" ? inp.value : +inp.value;
      if (inp.tagName === "SELECT") v = isNaN(+inp.value) ? inp.value : +inp.value;
      if (inp.type === "number" && !(v >= 0)) return;
      st.gf[a][b] = v; render();
    });
    const rs = $("#gfReset", el); if (rs) rs.onclick = () => { st.gf = null; st.gfDeclined = []; render(); };
    const pr = $("#gfPrint", el); if (pr) pr.onclick = () => window.print();
    el.querySelectorAll("[data-decline]").forEach(b => b.onclick = () => { st.gfDeclined.push(b.dataset.decline); render(); });
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
    <p class="mut">Import from ERP or POS export (CSV). Surplus items listed from Connect your system.</p>
    <div class="scroll"><table><tr><th>Passport</th><th>ID</th><th>Seller</th><th>What it is</th><th>Found by</th><th class="n">kg</th><th class="n">Scrap value</th><th>Data</th></tr>${imp.items.map(x => `<tr><td><a href="${esc(x.passport)}" target="_blank" rel="noopener" title="Open passport"><img class="qr" alt="QR code for ${esc(x.id)}" src="/api/qr.svg?data=${encodeURIComponent(location.origin + x.passport)}"></a></td><td><a href="${esc(x.passport)}" target="_blank" rel="noopener">${esc(x.id)}</a></td><td>${esc(x.seller_name)}</td><td class="w">${esc(x.description)}</td><td class="w">${x.urgent ? '<span class="pill p-bad">Urgent</span> ' : ""}${x.rules.map(esc).join("<br>")}</td><td class="n">${num(x.kg, 1)}</td><td class="n">–</td><td>${chip(x.label)}</td></tr>`).join("")}</table></div>
    <p class="note">No scrap price is shown for imported items until a dated quote is entered.</p></div>` + h;
  el.innerHTML = h;
  bindScen(el);
  const ic = $("#impClear", el); if (ic) ic.onclick = async () => { await api(`/api/import/clear?lane=${st.lane}`, {}); render(); };
  $("#sf", el).onchange = e => { st.sellerFilter = e.target.value; render(); };
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

/* ---------------- render ---------------- */
const VIEWS = {connect: vConnect, browse: vBrowse, match: vMatch, deals: el => vDeals(el, false), gapfill: vGap, offers: vOffers, stock: vStock, monitor: el => vDeals(el, true), quality: vQuality, prices: vPrices, rules: vRules, sim: vSim};
let ticket = 0;
async function render() {
  shell();
  const my = ++ticket, main = $("#main");
  const [t, sub] = TITLES[st.view];
  const head = `<header><h1>${esc(t)}</h1><p class="mut">${esc(sub)}</p></header>`;
  const body = document.createElement("div");
  body.className = "stack";
  if (!main.dataset.view || main.dataset.view !== st.view + st.lane + st.role) main.innerHTML = head + `<div class="spin">Loading…</div>`;
  try { await VIEWS[st.view](body); } catch (e) { body.innerHTML = errBox(e); }
  if (my !== ticket) return;
  main.dataset.view = st.view + st.lane + st.role;
  main.replaceChildren();
  main.insertAdjacentHTML("beforeend", head);
  main.appendChild(body);
  document.title = `${t} · CIRCULATE`;
}
readHash();
history.replaceState(null, "", `#${st.role}/${st.view}/${st.lane}`);
render();
