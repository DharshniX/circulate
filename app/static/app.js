/* CIRCULATE front end. Plain JavaScript, no build step. Talks to the FastAPI engine. */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const inr = v => v == null ? "–" : (v < 0 ? "−" : "") + "₹" + Math.abs(Math.round(v)).toLocaleString("en-IN");
const num = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("en-IN", {maximumFractionDigits: d, minimumFractionDigits: d});
const pct = v => Math.round(v * 100) + "%";

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

/* ---------------- state and navigation ---------------- */
const LANES = {durable: {name: "Durable", cls: "np", scen: ["steel", "sheets", "bars"]}, fresh: {name: "Fresh", cls: "pe", scen: ["food"]}};
const ROLES = {buyer: "Buyer", seller: "Seller", admin: "Admin"};
const NAV = {
  buyer: [["post", "Post a requirement", null, "Marketplace"], ["browse", "Browse surplus"], ["match", "Order and match"],
    ["reqs", "My requirements", null, "My orders"], ["deals", "My deals"], ["pickup", "Pickup and delivery"]],
  seller: [["connect", "Connect your system"], ["gapfill", "Sold before it's cut", "durable"], ["offers", "Offers and recovery plan"], ["stock", "Stock and passports"], ["pickup", "Pickup and delivery"]],
  admin: [["monitor", "Deal monitor"], ["quality", "Data quality"], ["prices", "Price references"], ["rules", "Rules"], ["sim", "Simulation lab"]],
};
const TITLES = {
  connect: ["Connect your system", "Import from ERP or POS export (CSV). Upload the stock file your system exports; the engine checks every row and finds the surplus."],
  post: ["Post a requirement", "Say what you need. Find it now in every seller's stock, or keep it open as a standing order for workshops about to cut."],
  reqs: ["My requirements", "Everything you posted, with its status. Find it again or close it."],
  browse: ["Browse surplus", "Every piece and lot on offer, drawn to scale, with its seller, distance and price range."],
  pickup: ["Pickup and delivery", "Route, vehicle and transport cost come from the engine. Book a slot, print the papers, track the deal."],
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
  imp: {durable: {}, fresh: {}}, ordId: {}, postKind: "flat", post: {flat: null, linear: null, perishable: null}, postErr: {}, postMsg: null,
  man: {durable: {material: "mild_steel", length_mm: "", width_mm: "", thickness_mm: "", grade: "E250", condition: "good", is_full_sheet: false, days_unused: "", seller_id: ""},
    fresh: {product: "", category: "vegetables", quantity_kg: "", storage: "ambient", hours_left: "", use_by: "", mode: "hours", seller_id: ""}},
  manPhoto: {durable: null, fresh: null}, manErr: {durable: {}, fresh: {}}, manRes: {durable: null, fresh: null}, manMeasured: {durable: false, fresh: false}, br: {durable: {q: "", mat: "", thick: "", seller: "", sort: "distance"}, fresh: {q: "", mat: "", thick: "", seller: "", sort: "hours"}}, impLimit: {days: 30, hours: 24}, impMeasured: false,
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
  $("#nav").innerHTML = `<h6>${ROLES[st.role]} · ${LANES[st.lane].name}</h6>` + navFor(st.role, st.lane).map(([k, t, , grp]) =>
    `${grp ? `<h6 class="grp">${esc(grp)}</h6>` : ""}<button type="button" data-view="${k}" ${k === st.view ? 'aria-current="page"' : ""}>${esc(t)}</button>`).join("") +
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

/* ---------------- What can this become? (shared by Browse surplus) ---------------- */
function barSvg(det) {
  const L = det.length_mm;
  return `<svg class="bplan" viewBox="0 0 ${L} 140" role="img" aria-label="${det.cuts.length} cuts along the bar"><rect x="0" y="40" width="${L}" height="60" style="fill:var(--piece);stroke:var(--mut);stroke-width:${L / 200}"/>${det.cuts.map(c => `<rect x="${c.start_mm}" y="40" width="${c.length_mm}" height="60" style="fill:var(--part);stroke:var(--partl);stroke-width:${L / 400}"/>`).join("")}</svg>`;
}
function becomeHtml(b, top) {
  const fresh = b.kind === "perishable", base = fresh ? "disposal" : "scrap";
  const money = o => {
    if (o.value_above_scrap == null) return `<span class="note">Count only: no new price for this material in the cluster.</span>`;
    if (o.is_donation) return `<span class="note">Donation: avoids ${inr(-o.scrap_value)} disposal; pickup ${inr(o.transport)}.</span>`;
    const v = o.value_above_scrap;
    return `<b class="bval ${v < 0 ? "neg" : ""}">${inr(Math.abs(v))}</b> <span class="note">${v < 0 ? "below" : "above"} ${base}</span>`;
  };
  const what = o => fresh ? `${num(o.units, 1)} kg to ${esc(o.buyer_name)}` : `${num(o.units)} × ${esc(o.part_name)}`;
  const size = o => fresh ? `${num(o.hours_on_arrival, 1)} h left on arrival (needs ${num(o.min_hours_on_arrival)} h)` : o.part_mm ? `${num(o.part_mm[0])} × ${num(o.part_mm[1])} mm${o.wanted ? ` · order wants ${num(o.wanted)}` : ""}` : `${num(o.cut_length_mm)} mm cuts · order wants ${num(o.wanted)}`;
  const draw = o => o.detail && o.detail.shape === "sheet" ? sheetSvg({...o.detail, resource_id: "", units: o.units}).replace(/<figcaption>.*<\/figcaption>/, "") : o.detail && o.detail.shape === "bar" ? barSvg(o.detail) : "";
  const card = o => `<div class="bopt ${o.source === "reference" ? "ref" : ""}">${draw(o) ? `<div class="bdraw">${draw(o)}</div>` : ""}<div class="bbody"><span class="pill ${o.source === "reference" ? "p-mut" : "p-ok"}">${esc(o.label)}</span>
    <b>${what(o)}</b><span class="note">${size(o)}</span><div>${money(o)}</div></div></div>`;
  const opts = b.options, shown = opts.slice(0, top), more = opts.slice(top);
  const sc = b.scrap ? `Compared with ${base}: ${inr(Math.abs(b.scrap.value))} at ₹${num(Math.abs(b.scrap.price_per_kg), 1)}/kg · ${esc(cap1(b.scrap.label))} · ${b.scrap.quoted_on ? "quoted " + esc(b.scrap.quoted_on) : "no date"}.` : "";
  return `<div class="bwrap"><p class="note">${sc} Ranked by value above ${base}. Prices are sample data.</p>
    ${shown.length ? shown.map(card).join("") : `<p class="note">Nothing in this lane can use it right now.</p>`}
    ${more.length ? `<details><summary>${more.length} more option(s)</summary>${more.map(card).join("")}</details>` : ""}
    ${b.refused.length ? `<details><summary>Not possible (${b.refused.length})</summary><ul class="rej">${b.refused.map(r => `<li><b>${esc(r.label)}: ${esc(r.part_name)}</b> ${esc(r.reason)}</li>`).join("")}</ul></details>` : ""}</div>`;
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
  const words = v => v.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
  const mat = O.materials.find(x => x.key === F.material);
  let fields;
  if (kind === "flat") fields = [
    fld("part_name", "Part name", txt("part_name", "e.g. mounting bracket"), "wide"),
    fld("material", "Material", `<select data-post="material" ${bad("material")}>${O.materials.map(m => `<option value="${m.key}" ${m.key === F.material ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>`),
    fld("length_mm", "Length mm", num_("length_mm", "decimal", "e.g. 150")), fld("width_mm", "Width mm", num_("width_mm", "decimal", "e.g. 100")),
    fld("thickness_mm", "Thickness mm", num_("thickness_mm", "decimal", "e.g. 2")), fld("quantity", "Quantity", num_("quantity", "numeric", "e.g. 50")),
    grp("accepted_grades", "Accepted grades", mat && mat.grades.length ? chips("accepted_grades", mat.grades.map(g => [g, g])) : txt("grades_text", "Type grades, comma separated"), "wide"),
    grp("accepted_conditions", "Accepted conditions", chips("accepted_conditions", O.conditions.map(c => [c, words(c)])), "wide")];
  else if (kind === "linear") fields = [
    fld("part_name", "Part name", txt("part_name", "e.g. baluster"), "wide"),
    fld("profile", "Profile", `<select data-post="profile" ${bad("profile")}>${O.profiles.map(x => `<option value="${esc(x.profile)}" ${x.profile === F.profile ? "selected" : ""}>${esc(x.profile)} · ${num(x.mass_per_m_kg, 2)} kg/m</option>`).join("")}</select>`),
    fld("cut_length_mm", "Cut length mm", num_("cut_length_mm", "decimal", "e.g. 450")), fld("quantity", "Quantity", num_("quantity", "numeric", "e.g. 30")),
    grp("accepted_grades", "Accepted grades", chips("accepted_grades", O.bar_grades.map(g => [g, g])), "wide"),
    grp("accepted_conditions", "Accepted conditions", chips("accepted_conditions", O.conditions.map(c => [c, words(c)])), "wide")];
  else fields = [
    grp("categories", "What is needed", chips("categories", O.categories.map(c => [c, words(c)])), "wide"),
    fld("quantity", "Kg", num_("quantity", "decimal", "e.g. 20")), fld("min_hours_left_on_arrival", "Minimum hours of shelf life on arrival", num_("min_hours_left_on_arrival", "decimal", "e.g. 6")),
    grp("storage_available", "Storage you have", chips("storage_available", O.storage.map(c => [c, words(c)])), "wide"),
    grp("buyer_type", "Buyer type", `<div class="opts">${O.buyer_types.map(b => `<label><input type="radio" name="postBt" value="${b.key}" ${F.buyer_type === b.key ? "checked" : ""}>${esc(b.name)}</label>`).join("")}</div>`, "wide")];
  const priceLabel = kind === "perishable" ? "What you pay your usual supplier ₹/kg" : "What you pay for new material ₹/kg";
  const priceNote = kind === "perishable" && F.buyer_type === "food_bank" ? "A donation: no price is charged." : "Prefilled with the cluster's sample price. Change it to what you really pay.";
  fields.push(
    kind === "perishable" && F.buyer_type === "food_bank" ? "" : fld(kind === "perishable" ? "normal_price_per_kg" : "new_price_per_kg", priceLabel, num_(kind === "perishable" ? "normal_price_per_kg" : "new_price_per_kg", "decimal")),
    kind === "perishable" && F.buyer_type !== "food_bank" ? `<label class="check mfield"><input type="checkbox" data-post-check="buyer_fssai_licensed" ${F.buyer_fssai_licensed ? "checked" : ""}> We hold an FSSAI licence or registration</label>` : "",
    fld("needed_by", "Needed by", `<input data-post="needed_by" type="date" min="${new Date().toISOString().slice(0, 10)}" value="${esc(F.needed_by)}" ${bad("needed_by")}>`),
    fld("buyer_name", "Your business name", txt("buyer_name", "e.g. Workshop W"), "wide"),
    fld("x_km", "Location x km", num_("x_km", "decimal")), fld("y_km", "Location y km", num_("y_km", "decimal")));
  const msg = st.postMsg && st.postMsg.lane === lane ? `<div class="mres ok" role="status"><b>Saved as a standing order: ${esc(st.postMsg.demand_id)}</b><p>It now appears in Seller &gt; Sold before it's cut and in "What can this become?" as "Ordered by ${esc(st.postMsg.buyer)}".</p><div class="ctrl"><button class="btn sm" type="button" id="toReqs">See My requirements</button></div></div>` : "";
  el.innerHTML = `<div class="card manual"><div class="ctrl"><h2>Your requirement</h2>${chip("sample")}<div class="grow"></div>
      ${lane === "durable" ? `<div class="opts kindsw" role="radiogroup" aria-label="What kind of part"><label><input type="radio" name="postKind" value="flat" ${kind === "flat" ? "checked" : ""}>Sheet parts</label><label><input type="radio" name="postKind" value="linear" ${kind === "linear" ? "checked" : ""}>Bars and tubes</label></div>` : ""}</div>
    <p class="mut">Checked with the same order rules the engine uses. Location is km on the demo grid; the default is near the centre of the cluster.</p>
    <form id="postForm" novalidate><div class="mgrid">${fields.join("")}</div>
      ${E.kind || E.standing ? `<p class="err">${esc(E.kind || E.standing)}</p>` : ""}
      <div class="ctrl"><button class="btn pri bigbtn" type="submit" id="postFind">Find it now</button>
        ${kind === "flat" ? `<button class="btn bigbtn" type="button" id="postStanding">Keep it open as a standing order</button>` : ""}
        <span class="note">${esc(priceNote)}</span></div></form>${msg}</div>`;
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
    catch (x) { st.postErr = {kind: "The app could not be reached. Check that it is still running and try again."}; return render(); }
    if (!r.ok) { st.postErr = t.detail && t.detail.errors ? t.detail.errors : {kind: typeof t.detail === "string" ? t.detail : "Something went wrong."}; st.postMsg = null; await render();
      const first = document.querySelector('#postForm [aria-invalid="true"], #postForm .ferr:not(:empty)'); if (first) (first.matches("input,select") ? first : first.parentElement.querySelector("input,select") || first).focus?.(); return; }
    st.postErr = {}; st.post[kind] = null;
    if (standing) { st.postMsg = {lane, demand_id: t.demand_id, buyer: t.demand.buyer_name}; return render(); }
    st.postMsg = null; st.scen[lane] = t.scenario; st.ordId[t.scenario] = t.demand_id; st.off[t.scenario] = new Set();
    go({view: "match"});
  };
  form.onsubmit = e => { e.preventDefault(); send(false); };
  const sb = $("#postStanding", el); if (sb) sb.onclick = () => send(true);
}
async function vReqs(el) {
  const r = await api(`/api/requirements?lane=${st.lane}`), list = r.requirements;
  if (!list.length) {
    el.innerHTML = `<div class="empty">No requirements yet. <a href="#buyer/post/${st.lane}" id="toPost">Post a requirement</a> and it will be listed here with its status.</div>`;
    $("#toPost", el).onclick = e => { e.preventDefault(); go({view: "post"}); };
    return;
  }
  const pill = {open: "p-mut", reserved: "p-warn", matched: "p-ok", closed: "p-mut"};
  const what = q => { const d = q.demand;
    return q.kind === "perishable" ? `${num(d.quantity, 1)} kg of ${d.categories.map(c => c.replace(/_/g, " ")).join(", ")} · ${d.storage_available.join(", ")} · ${num(d.min_hours_left_on_arrival)} h on arrival${d.buyer_type === "ngo" ? " · donation" : ""}`
      : q.kind === "linear" ? `${num(d.quantity)} × ${esc(d.part_name)} · ${esc(d.profile)} · ${num(d.cut_length_mm)} mm`
      : `${num(d.quantity)} × ${esc(d.part_name)} · ${num(d.length_mm)} × ${num(d.width_mm)} × ${num(d.thickness_mm, d.thickness_mm % 1 ? 1 : 0)} mm ${esc(d.material.replace(/_/g, " "))} · ${d.accepted_grades.map(esc).join("/")}`; };
  el.innerHTML = `<div class="card"><div class="scroll"><table><tr><th>Id</th><th>Buyer</th><th>What</th><th>Needed by</th><th>Status</th><th class="noprint"></th></tr>${list.map(q => `<tr ${q.status === "closed" ? 'class="closed"' : ""}>
      <td><code>${esc(q.demand_id)}</code>${q.standing ? '<div><span class="pill p-mut">Standing order</span></div>' : ""}</td><td>${esc(q.demand.buyer_name)}</td><td class="w">${what(q)}</td>
      <td>${q.needed_by ? esc(q.needed_by) : "–"}</td><td class="w"><span class="pill ${pill[q.status]}">${esc(q.status)}</span><div class="note">${esc(q.status_text)}</div></td>
      <td class="noprint"><span class="ctrl">${q.status !== "closed" ? `<button class="btn sm" type="button" data-find="${q.id}">Find again</button>` : ""}${q.status === "open" || q.status === "reserved" ? `<button class="btn sm" type="button" data-close="${q.id}">Close</button>` : ""}</span></td></tr>`).join("")}</table></div>
    <p class="note">${esc(r.requirements[0].note)}. Reserved means a seller's Sold before it's cut plan includes your parts; if the seller declines, it goes back to Open.</p></div>`;
  el.querySelectorAll("[data-find]").forEach(b => b.onclick = () => {
    const q = list.find(x => x.id === +b.dataset.find);
    st.scen[st.lane] = q.scenario; st.ordId[q.scenario] = q.demand_id; st.off[q.scenario] = new Set(); go({view: "match"});
  });
  el.querySelectorAll("[data-close]").forEach(b => b.onclick = async () => { await api(`/api/requirements/${b.dataset.close}/close`, {}); render(); });
}

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
  const il = it.illustrative, credit = il ? `Illustrative photo: ${il.shows}. "${il.title}" by ${il.author}, ${il.licence}, via Wikimedia Commons` : "";
  const photo = it.photo ? `<figure class="lphoto"><img src="${esc(it.photo)}" alt="Photo of ${esc(it.id)} uploaded by the seller" loading="lazy"><figcaption class="ptag seller">Photo uploaded by the seller</figcaption></figure>`
    : il ? `<figure class="lphoto illus" title="${esc(credit)}"><img src="${esc(il.url)}" alt="${esc(credit)}" loading="lazy"><figcaption class="ptag">Illustrative photo, not the actual item · <a href="${esc(il.source_url)}" target="_blank" rel="noopener" title="${esc(credit)}">Credit</a></figcaption></figure>` : "";
  const dist = it.distance_km == null ? "Location not given" : `${num(it.distance_km, 1)} km`;
  const uid = `${it.key}:${it.id}`;
  return `<article class="lcard ${durable ? "np" : "pe"}" aria-label="${esc(it.id)}">${photo}<div class="lmedia">${media}</div>
    <div class="lbody"><div class="ltop"><h3>${title}</h3>${labelChip(it.label)}</div><p class="mut">${spec}</p>
      <dl class="lfacts"><dt>Seller</dt><dd>${esc(it.seller_name)}</dd><dt>Distance</dt><dd>${dist}</dd><dt>Weight</dt><dd>${num(it.kg, 1)} kg</dd><dt>Item</dt><dd><code>${esc(it.id)}</code></dd></dl>
      ${price}
      ${it.rules ? `<p class="note">${esc(it.source || "Imported from a CSV export")}. ${it.rules.includes("Listed by the seller") ? "Listed by the seller; no surplus rule caught it." : `Found as surplus: ${it.rules.map(esc).join("; ")}.`}</p>` : ""}
      <div class="lact"><button class="btn pri sm" type="button" data-order="${esc(uid)}" ${it.order ? "" : "disabled"}>Order parts from this</button><a class="btn sm" href="${esc(it.passport)}" target="_blank" rel="noopener">Passport</a><button class="btn sm" type="button" data-become="${esc(uid)}" aria-expanded="false">What can this become?</button></div>
      <div class="become" id="bc-${esc(uid.replace(/[^\w-]/g, "_"))}" hidden></div>
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
    const bb = e.target.closest("[data-become]");
    if (bb) {
      const it = byUid[bb.dataset.become], box = document.getElementById("bc-" + bb.dataset.become.replace(/[^\w-]/g, "_"));
      const open = bb.getAttribute("aria-expanded") === "true";
      bb.setAttribute("aria-expanded", String(!open)); box.hidden = open;
      if (!open && !box.dataset.done) {
        box.innerHTML = `<p class="note">Trying every order and common part…</p>`;
        try { box.innerHTML = becomeHtml(await api(`/api/become/${encodeURIComponent(it.key)}/${encodeURIComponent(it.id)}`), 3); box.dataset.done = "1"; }
        catch (x) { box.innerHTML = `<p class="err">${esc(x.message)}</p>`; }
      }
      return;
    }
    const b = e.target.closest("[data-order]");
    if (!b) return;
    const it = byUid[b.dataset.order], k = it.order.scenario, S = await scen(k);
    st.scen[lane] = k; st.ordId[k] = it.order.demand_id;
    st.off[k] = new Set(S.data.sellers.map(x => x.id).filter(id => id !== (it.match_seller || it.seller_id)));
    go({view: "match"});
  });
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
  const ctrl = `<div class="card noprint"><div class="ctrl">${await scenSeg()}
    <label class="field">Order<select id="ordSel">${posted.length ? `<optgroup label="Your requirements">${posted.map(x => `<option value="${esc(x.id)}" ${x.id === d.id ? "selected" : ""}>${esc(x.id)} · ${esc(x.buyer_name)}: ${num(x.quantity, fresh ? 1 : 0)} ${esc(x.part_name)}${x.status === "matched" ? " (matched)" : ""}</option>`).join("")}</optgroup><optgroup label="Sample orders">` : ""}${S.data.demands.map(x => `<option value="${esc(x.id)}" ${x.id === d.id ? "selected" : ""}>${esc(x.buyer_name)}: ${num(x.quantity, fresh ? 1 : 0)} ${esc(x.part_name)}</option>`).join("")}${posted.length ? "</optgroup>" : ""}</select></label>
    <label class="field">Transport rate ×<input id="rate" type="number" min="0.2" max="5" step="0.1" value="${rate}"></label></div>
    <div class="ctrl"><span class="lbl">Sellers in the cluster</span>${D.sellers.map(s => `<label class="check"><input type="checkbox" data-seller="${esc(s.id)}" ${off.has(s.id) ? "" : "checked"}>${esc(s.name)}</label>`).join("")}</div></div>`;
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
    $("#ordSel", el).onchange = e => { st.ordId[k] = e.target.value; render(); };
    $("#rate", el).onchange = e => { const x = +e.target.value; if (x > 0) { st.rate[k] = x; render(); } };
    el.querySelectorAll("[data-seller]").forEach(c => c.onchange = () => { c.checked ? off.delete(c.dataset.seller) : off.add(c.dataset.seller); render(); });
    const acc = $("#accept", el);
    if (acc) acc.onclick = async () => {
      acc.disabled = true;
      try {
        const deal = await api("/api/deals", {kind: S.meta.engine, counterfactual: b.counterfactual[0], demand_id: d.id, buyer_name: d.buyer_name,
          sellers: b.sellers_used, units: b.units, unit_name: f.unit_name, kg_in_units: b.kg_in_units, kg_shipped: b.kg_shipped,
          route_km: b.route_km, value_above_salvage: b.value.seller_gain_above_salvage, buyer_utilisation: d.buyer_utilisation ?? null,
          scenario: k, rate_multiplier: rate, requirement_id: d.requirement_id ?? null});
        st.lastDeal = deal.id;
        $("#acceptMsg", el).innerHTML = `Deal ${deal.id} agreed (simulated). <a href="#buyer/deals/${st.lane}" id="toDeals">Track it in My deals</a> or <a href="#buyer/pickup/${st.lane}" id="toPickup">book the pickup</a>.`;
        $("#toDeals", el).onclick = ev => { ev.preventDefault(); go({view: "deals"}); };
        $("#toPickup", el).onclick = ev => { ev.preventDefault(); go({view: "pickup"}); };
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
  const demo = await api("/api/gapfill/demo");  // sample orders plus standing orders buyers posted in this demo
  if (!st.gf) st.gf = demo;
  else st.gf.orders = [...st.gf.orders.filter(o => !o.posted), ...demo.orders.filter(o => o.posted)];
  const postedTag = id => (st.gf.orders.find(o => o.id === id) || {}).posted ? ' <span class="pill p-warn">Posted by a buyer in this demo</span>' : "";
  let r, err;
  try { r = await api("/api/gapfill?record=true", {...st.gf, declined_order_ids: st.gfDeclined}); } catch (e) { err = e; }
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
  h += `<div class="cols"><div class="card"><h2>Reserved for neighbours</h2>${r.neighbours.length ? `<div class="scroll"><table><tr><th>Order</th><th>Buyer</th><th>Part</th><th class="n">Parts</th><th class="n">Value</th><th class="noprint"></th></tr>${r.neighbours.map(n => `<tr><td><span class="dot" style="background:${colours[n.order_id]}"></span>${esc(n.order_id)}</td><td>${esc(n.buyer_name)}${postedTag(n.order_id)}</td><td>${esc(n.part_name)}</td><td class="n">${n.units}</td><td class="n">${inr(n.value)}</td><td class="noprint"><button class="btn sm" data-decline="${esc(n.order_id)}" type="button">Decline</button></td></tr>`).join("")}</table></div>` : `<div class="empty">Nothing reserved. Filling the gap would not beat selling the leftover as scrap.</div>`}
    <p class="note">If a buyer declines, the plan is recalculated at once and their space goes to the next best order.</p></div>
    <div class="card"><h2>Orders not used (${r.refused.length})</h2><ul class="rej">${r.refused.map(x => `<li><code>${esc(x.order_id)}</code> ${esc(x.reason)}${st.gfDeclined.includes(x.order_id) ? ` <button class="btn sm noprint" data-restore="${esc(x.order_id)}" type="button">Restore</button>` : ""}</li>`).join("") || "<li>None</li>"}</ul>
    <h4>Standing orders in the cluster</h4><div class="scroll"><table><tr><th>Order</th><th>Buyer</th><th>Part mm</th><th class="n">t mm</th><th>Grades</th><th class="n">Qty</th></tr>${st.gf.orders.map(o => `<tr><td>${esc(o.id)}</td><td class="w">${esc(o.buyer_name)}${postedTag(o.id)}</td><td>${esc(o.part_name)} ${num(o.length_mm)}×${num(o.width_mm)}</td><td class="n">${num(o.thickness_mm)}</td><td>${o.accepted_grades.map(esc).join(", ")}</td><td class="n">${o.quantity}</td></tr>`).join("")}</table></div></div></div>`;
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
const cap1 = s => s ? s[0].toUpperCase() + s.slice(1) : "";

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
const VIEWS = {post: vPost, reqs: vReqs, connect: vConnect, browse: vBrowse, pickup: vPickup, match: vMatch, deals: el => vDeals(el, false), gapfill: vGap, offers: vOffers, stock: vStock, monitor: el => vDeals(el, true), quality: vQuality, prices: vPrices, rules: vRules, sim: vSim};
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
