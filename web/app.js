// KYA demo client: vanilla JS, live updates over Server-Sent Events.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const inr = (n) => (n == null ? "—" : "₹" + Number(n).toLocaleString("en-IN"));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const MCC = { 5411: "Grocery", 5814: "Food delivery", 4121: "Cabs", 6051: "Crypto", 5732: "Electronics" };
const TASK_PRICE = { groceries: "₹1,149", dinner: "₹380", cab: "₹640", stockup: "₹4,500 · step-up", balance: "" };
const ICON = { pass: "✓", fail: "✗", warn: "!", pending: "…", skip: "·" };

async function api(path, { method = "GET", body } = {}) {
  const r = await fetch(path, { method, headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

let STATE = null;
const decisions = []; // {d, extra, final, ts}
let current = null;

// ---------------------------------------------------------------- state / human panel
async function refresh() {
  STATE = await api("/api/state");
  renderHuman();
  renderAgentControls();
}
let refreshTimer = null;
const refreshSoon = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 150); };

function renderHuman() {
  const { user, mandate: m, mandate_status: ms, agent } = STATE;
  $("#userName").textContent = user.name;
  $("#userIdNo").textContent = user.id_number;
  $("#userDob").textContent = user.dob;
  $("#userKyc").textContent = user.kyc_ref;
  $("#acct").textContent = user.account;
  $("#balance").textContent = inr(STATE.balance_inr);
  const face = { synthetic: "synthetic template", live: "live-enrolled ✓", erased: "erased (consent withdrawn)" }[user.face_source] || user.face_source;
  $("#faceStatus").textContent = user.face_enrolled ? face : "none (erased)";
  $("#faceStatus").title = user.face_hash || "";
  const photo = $("#idPhoto");
  if (user.has_id_photo) { photo.style.backgroundImage = `url(/api/id_photo?t=${Date.now()})`; photo.textContent = ""; }
  else { photo.style.backgroundImage = ""; photo.textContent = user.name.split(" ").map((w) => w[0]).join(""); }

  const s = m.scope;
  const status = ms.revoked ? (ms.reason === "consent_withdrawn" ? "CONSENT WITHDRAWN" : "REVOKED") : ms.expired ? "EXPIRED" : "ACTIVE";
  const pill = $("#mandateStatus");
  pill.textContent = status;
  pill.className = "pill " + (status === "ACTIVE" ? "ok" : "bad");
  pill.title = ms.reason || "";
  $("#mandateCard").classList.toggle("revoked", status !== "ACTIVE");
  $("#mAgent").textContent = `${agent.name} · ${agent.pubkey.slice(8, 18)}…`;
  $("#mCap").textContent = inr(s.per_txn_cap_inr);
  $("#mCum").textContent = `${inr(ms.spent_inr)} of ${inr(s.cumulative_cap_inr)}`;
  $("#mCumBar").style.width = Math.min(100, (100 * ms.spent_inr) / s.cumulative_cap_inr) + "%";
  $("#mStep").textContent = inr(s.step_up_above_inr);
  $("#mMcc").innerHTML = s.mcc_allow.map((c) => `<span class="chip">${esc(MCC[c] || c)} ${c}</span>`).join("");
  $("#mExp").textContent = new Date(m.exp * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  $("#mPurpose").textContent = m.consent.purpose;
  $("#mSig").textContent = `${m.mandate_id} · signed by ${m.kid} · ${m.sig.slice(0, 40)}…`;
  $("#revokeBtn").classList.toggle("hidden", status !== "ACTIVE");
  $("#withdrawBtn").classList.toggle("hidden", status !== "ACTIVE");
  $("#regrantBtn").classList.toggle("hidden", status === "ACTIVE");
}

function notify(text, cls = "") {
  const ul = $("#notifs");
  if (ul.firstElementChild?.classList.contains("muted")) ul.innerHTML = "";
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<time>${clock(Date.now() / 1000)}</time>${text}`;
  ul.prepend(li);
}

// ---------------------------------------------------------------- agent panel
let agentControlsDone = false;
function renderAgentControls() {
  $("#transportBadge").textContent = STATE.transport === "mcp" ? "agent → bank over MCP" : "agent → bank in-process";
  const claudeOpt = $("#agentSel option[value=claude]");
  claudeOpt.disabled = !STATE.claude.available;
  claudeOpt.textContent = STATE.claude.available ? `Claude (${STATE.claude.model})` : "Claude (set ANTHROPIC_API_KEY)";
  if (agentControlsDone) return;
  agentControlsDone = true;
  if (STATE.claude.available) $("#agentSel").value = "claude";
  $("#taskChips").innerHTML = Object.entries(STATE.tasks)
    .map(([k, t]) => `<button class="taskchip" data-task="${k}" title="${esc(t)}">${esc(label(k))}${TASK_PRICE[k] ? ` · ${TASK_PRICE[k]}` : ""}</button>`)
    .join("");
  $$(".taskchip").forEach((b) => (b.onclick = () => runTask(b.dataset.task)));
  $("#attacks").innerHTML = STATE.attacks
    .map((a) => `<button class="attack" data-attack="${a.id}" title="${esc(a.threat)}"><span>${esc(a.title)}</span><span class="lyr">caught at ${a.layer}</span></button>`)
    .join("");
  $$(".attack").forEach((b) => (b.onclick = () => runAttack(b.dataset.attack)));
}
const label = (k) => ({ groceries: "Weekly groceries", dinner: "Dinner", cab: "Airport cab", stockup: "Monthly stock-up", balance: "Check balance" })[k] || k;

async function runTask(task) {
  try {
    await api("/api/agent/run", { method: "POST", body: { task, agent: $("#agentSel").value, poisoned: $("#poisonTgl").checked } });
  } catch (e) { sys(`⚠ ${e.message}`); }
}
$("#askForm").onsubmit = (e) => {
  e.preventDefault();
  const t = $("#askInput").value.trim();
  if (t) { runTask(t); $("#askInput").value = ""; }
};

async function runAttack(id) {
  try { await api(`/api/attack/${id}`, { method: "POST" }); } catch (e) { sys(`⚠ ${e.message}`); }
}

function tx(html, cls, tag = "div") {
  const box = $("#transcript");
  if (box.firstElementChild?.classList.contains("muted")) box.innerHTML = "";
  const el = document.createElement(tag);
  el.className = cls;
  el.innerHTML = html;
  box.append(el);
  box.scrollTop = box.scrollHeight;
  return el;
}
const sys = (t) => tx(esc(t), "msg sys");
let lastTool = null;

function fmtArgs(name, a) {
  if (name === "pay_merchant") return `${esc(a.merchant_id)}, ${inr(a.amount_inr)}`;
  if (name === "transfer_to_payee") return `${esc(a.payee_vpa)}, ${inr(a.amount_inr)}`;
  if (name === "read_webpage") return esc(a.url);
  return "";
}
function highlightInjection(text) {
  return esc(text).split("\n").map((l) =>
    /AI SHOPPING|pay_merchant\(|pre-authorised|Do not mention|&lt;span|&lt;\/span/.test(l) ? `<mark>${l}</mark>` : l).join("\n");
}

function onAgentEvent(type, d) {
  if (type === "agent.start") {
    $("#transcript").innerHTML = "";
    sys(`${d.agent === "claude" ? "Claude" : "Scripted"} agent · ${d.poisoned ? "poisoned page ON · " : ""}bank via ${d.transport}`);
  } else if (type === "agent.user") tx(esc(d.text), "msg user");
  else if (type === "agent.message") tx(esc(d.text), "msg agent");
  else if (type === "agent.tool_call") lastTool = tx(`→ ${esc(d.name)}(${fmtArgs(d.name, d.args)})`, "tool");
  else if (type === "agent.signed" && lastTool) lastTool.insertAdjacentHTML("beforeend", ` <span class="lock" title="Signed by the agent runtime's key, outside the model">🔏 signed</span>`);
  else if (type === "agent.tool_result") {
    if (d.name === "read_webpage") {
      const poisoned = /AI SHOPPING/.test(d.text);
      const det = tx(`<summary>${poisoned ? "⚠ page contains hidden instructions" : "page content"}</summary><pre>${highlightInjection(d.text)}</pre>`, "page", "details");
      det.open = poisoned;
    } else if (lastTool) {
      lastTool.classList.add(d.outcome);
      const res = d.outcome === "ALLOW" ? `✓ allowed${d.stepped_up ? " after face check" : ""}` : `✗ blocked${d.layer ? " at " + d.layer : ""}: ${d.reason}`;
      lastTool.insertAdjacentHTML("beforeend", `<span class="res">${esc(res)}</span>`);
    }
  } else if (type === "agent.done") sys("— done —");
}

// ---------------------------------------------------------------- verifier panel
function summaryLine(d) {
  const s = d.summary || {};
  const p = s.params || {};
  if (!s.action) return "Malformed request";
  let what = `<b>${esc(s.action)}</b>`;
  if (s.amount_inr != null || p.amount_inr != null) what += ` · <b>${inr(s.amount_inr ?? p.amount_inr)}</b>`;
  if (s.merchant_name) what += ` → ${esc(s.merchant_name)}${s.mcc ? ` <span class="mono">(${s.mcc})</span>` : ""}`;
  return `${what}<br><span class="mono">${esc(s.agent_id || "")}</span>`;
}

function renderDecision(item, animate = true) {
  current = item;
  const d = item.d;
  const outcome = item.final?.outcome || d.outcome;
  const layer = item.final ? "L9" : d.layer;
  const box = $("#decision");
  box.className = "decision " + outcome;
  $("#reqSummary").innerHTML = summaryLine(d);
  $("#outcome").textContent = (outcome === "STEP_UP" ? "STEP-UP" : outcome) + (outcome === "BLOCK" && layer ? ` at ${layer}` : "") +
    (item.final && outcome === "ALLOW" ? " after face check" : "");
  $("#reason").textContent = item.final?.reason || d.reason;

  const checks = d.checks.map((c) => (c.layer === "L9" && item.final?.check ? item.final.check : c));
  const ol = $("#checks");
  ol.innerHTML = checks.map((c) => `<li class="check ${c.status}"><span class="ic">${ICON[c.status] || "?"}</span><span class="ly">${c.layer}</span>
      <span><span class="nm">${esc(c.name)}</span><span class="dt">${esc(c.detail)}</span></span></li>`).join("");
  $$(".check", ol).forEach((li, i) => (animate ? setTimeout(() => li.classList.add("show"), 70 * i) : li.classList.add("show")));

  const rb = $("#riskBox");
  if (d.risk) {
    rb.classList.remove("hidden");
    $("#riskScore").textContent = `${d.risk.score} / ${d.risk.threshold}`;
    $("#riskBar").style.width = Math.min(100, d.risk.score) + "%";
    $("#riskThresh").style.left = d.risk.threshold + "%";
    $("#riskFeatures").innerHTML = d.risk.features.map((f) => `<li class="${f.hit ? "hit" : ""}"><span>${esc(f.detail)}</span><b>${f.hit ? "+" + f.points : "0"}</b></li>`).join("");
  } else rb.classList.add("hidden");
}

function renderHistory() {
  $("#history").innerHTML = decisions.slice().reverse().map((it, i) => {
    const d = it.d, s = d.summary || {};
    const outcome = it.final?.outcome || d.outcome;
    const layer = it.final ? "L9" : d.layer;
    const amt = s.amount_inr ?? s.params?.amount_inr;
    return `<li data-i="${decisions.length - 1 - i}"><span><time>${clock(it.ts)}</time>${esc(s.action || "malformed")}${amt != null ? " " + inr(amt) : ""}${s.merchant_name ? " · " + esc(s.merchant_name) : ""}</span>
      <span class="badge ${outcome}">${outcome === "STEP_UP" ? "STEP-UP" : outcome}${layer ? " " + layer : ""}</span></li>`;
  }).join("");
  $$("#history li").forEach((li) => (li.onclick = () => renderDecision(decisions[+li.dataset.i], false)));
}

function onDecision(out) {
  const item = { d: out.decision, extra: out, final: null, ts: Date.now() / 1000, challenge: out.challenge?.challenge_id };
  decisions.push(item);
  renderDecision(item);
  renderHistory();
  const s = out.decision.summary || {};
  const amt = s.amount_inr ?? s.params?.amount_inr;
  const what = s.action === "pay_merchant" ? `pay ${inr(amt)} to ${esc(s.merchant_name || s.params?.merchant_id)}`
    : s.action === "transfer_to_payee" ? `transfer ${inr(amt)} to ${esc(s.params?.payee_vpa)}` : esc(s.action || "a malformed request");
  if (out.decision.outcome === "ALLOW")
    notify(s.action === "pay_merchant" ? `Your agent paid ${inr(amt)} to ${esc(s.merchant_name)}.` : `Your agent ran <b>${esc(s.action)}</b>.`, "ALLOW");
  else if (out.decision.outcome === "BLOCK") notify(`<b>Blocked</b>: your agent tried to ${what}. ${esc(out.decision.reason)}`, "BLOCK");
  else notify(`<b>Approval needed</b>: your agent wants to ${what}.`, "STEP_UP");
  refreshSoon();
}

function onStepupResolved(res) {
  const item = decisions.find((x) => x.challenge === res.challenge_id);
  if (item) {
    item.final = res;
    if (current === item) renderDecision(item, false);
    renderHistory();
  }
  notify(res.outcome === "ALLOW" ? `Face check passed: payment released.` : `<b>Step-up failed</b>: ${esc(res.reason)}${res.mandate_revoked ? " Mandate auto-revoked after 3 failures." : ""}`, res.outcome);
  if (modal.cid === res.challenge_id && !modal.busy) showResult(res);
  refreshSoon();
}

// ---------------------------------------------------------------- camera + modal
const modal = { cid: null, busy: false, stream: null };
async function startCam() {
  $("#camWrap").classList.remove("hidden");
  camMsg("Starting camera…");
  try {
    modal.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
    $("#cam").srcObject = modal.stream;
    camMsg("Camera ready");
    return true;
  } catch (e) {
    camMsg("No camera available: use the simulate buttons");
    return false;
  }
}
function stopCam() { modal.stream?.getTracks().forEach((t) => t.stop()); modal.stream = null; }
function camMsg(t) { $("#camMsg").textContent = t; }
function arrow(dir) { $("#camArrow").textContent = dir === "LEFT" ? "←" : dir === "RIGHT" ? "→" : ""; }
function grab() {
  const c = $("#grab"), v = $("#cam");
  c.getContext("2d").drawImage(v, 0, 0, c.width, c.height); // raw, un-mirrored frame
  return c.toDataURL("image/jpeg", 0.85);
}
async function captureChallenge(direction) {
  const frames = [];
  camMsg("Look straight at the camera"); arrow("");
  await sleep(800);
  for (let i = 0; i < 16; i++) {
    if (i === 3) { camMsg(`Now turn your head ${direction}`); arrow(direction); }
    frames.push(grab());
    await sleep(170);
  }
  camMsg("Checking…"); arrow("");
  return frames;
}

function openModal({ eyebrow, title, body, actions }) {
  $("#mdEyebrow").textContent = eyebrow;
  $("#mdTitle").textContent = title;
  $("#mdBody").innerHTML = body;
  $("#mdResult").classList.add("hidden");
  setActions(actions);
  $("#modal").classList.remove("hidden");
}
function setActions(actions) {
  const box = $("#mdActions");
  box.innerHTML = "";
  for (const [text, cls, fn] of actions) {
    const b = document.createElement("button");
    b.className = "btn small " + (cls || "");
    b.textContent = text;
    b.onclick = fn;
    box.append(b);
  }
}
function closeModal() { $("#modal").classList.add("hidden"); $("#camWrap").classList.add("hidden"); stopCam(); modal.cid = null; modal.busy = false; }
$("#mdClose").onclick = closeModal;

function showResult(res) {
  const ok = res.outcome === "ALLOW" || res.passed === true;
  const el = $("#mdResult");
  el.className = "md-result " + (ok ? "ok" : "bad");
  const lv = res.face?.liveness || res.liveness || {};
  const sim = res.face?.similarity ?? res.similarity;
  el.innerHTML = `<b class="big">${ok ? "✓ Verified" : "✗ Rejected"}</b>${esc(res.reason)}
    <div class="mono muted" style="margin-top:6px">liveness: ${esc(lv.reason || "—")}${lv.simulated ? ` (simulated: ${esc(lv.simulated)})` : ""}
    ${sim != null ? ` · similarity ${sim} (threshold ${res.face?.threshold ?? res.threshold})` : ""}
    ${lv.yaw_track ? `<br>yaw: ${lv.yaw_track.map((y) => y.toFixed(2)).join(" ")}` : ""}</div>`;
  $("#camWrap").classList.add("hidden");
  stopCam();
  setActions([["Close", "", closeModal]]);
}

function openStepup(ch) {
  const d = ch.decision || {};
  const s = d.summary || {};
  const hits = (d.risk?.features || []).filter((f) => f.hit).map((f) => `<li>${esc(f.detail)}</li>`).join("");
  modal.cid = ch.challenge_id;
  openModal({
    eyebrow: "DemoBank · step-up verification",
    title: "Confirm it's really you",
    body: `<div>Your agent wants to <b>pay ${inr(s.amount_inr ?? s.params?.amount_inr)} to ${esc(s.merchant_name || "")}</b>. It's within the mandate, but it looks unusual:</div>
      <ul>${hits}</ul><div>Look at the camera, then turn your head <b>${ch.direction}</b> when the arrow appears. The direction is random, so a photo or a replayed video can't follow it.</div>`,
    actions: [
      ["Start live check", "", () => liveStepup(ch)],
      ["Hold up a photo (attack)", "danger", () => stepupCall(`/api/stepup/${ch.challenge_id}/photo_attack`)],
      ["Simulate genuine (no webcam)", "ghost", () => stepupCall(`/api/stepup/${ch.challenge_id}/simulate?who=genuine`)],
      ["Simulate impostor", "ghost", () => stepupCall(`/api/stepup/${ch.challenge_id}/simulate?who=impostor`)],
      ["Decline", "ghost", () => stepupCall(`/api/stepup/${ch.challenge_id}/deny`)],
    ],
  });
  startCam();
}
async function stepupCall(url, body) {
  modal.busy = true;
  try { showResult(await api(url, { method: "POST", body })); }
  catch (e) { showResult({ outcome: "BLOCK", reason: e.message }); }
  modal.busy = false;
}
async function liveStepup(ch) {
  if (!modal.stream && !(await startCam())) return;
  modal.busy = true;
  setActions([]);
  const frames = await captureChallenge(ch.direction);
  await stepupCall(`/api/stepup/${ch.challenge_id}/verify`, { frames });
}

// enrollment (KYC): ID photo + live head-turn, then a new mandate bound to the face
let idPhotoData = null;
function openEnroll() {
  idPhotoData = null;
  openModal({
    eyebrow: "KYC · synthetic identity, your face",
    title: "Verify it's you",
    body: `<div>1. Add an <b>ID photo</b>: any clear photo of your face. It stays on this machine.</div>
      <div class="uploader"><img id="idPrev" class="hidden"><input type="file" id="idFile" accept="image/*"><button class="btn ghost small" id="snapBtn">Use camera snapshot</button></div>
      <div>2. Pass a <b>live head-turn check</b>. The bank matches your live face to the ID photo, then binds a hash of your face template into a fresh mandate.</div>`,
    actions: [["Start live check", "", enrollRun], ["Cancel", "ghost", closeModal]],
  });
  startCam();
  $("#idFile").onchange = (e) => loadIdPhoto(e.target.files[0]);
  $("#snapBtn").onclick = () => { if (modal.stream) setIdPhoto(grab()); };
}
function setIdPhoto(url) { idPhotoData = url; const p = $("#idPrev"); p.src = url; p.classList.remove("hidden"); }
function loadIdPhoto(file) {
  const img = new Image();
  img.onload = () => {
    const scale = Math.min(1, 800 / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = img.width * scale; c.height = img.height * scale;
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    setIdPhoto(c.toDataURL("image/jpeg", 0.9));
  };
  img.src = URL.createObjectURL(file);
}
async function enrollRun() {
  if (!idPhotoData) { alert("Add an ID photo first."); return; }
  if (!modal.stream && !(await startCam())) return;
  modal.busy = true;
  setActions([]);
  try {
    const ch = await api("/api/kyc/challenge", { method: "POST" });
    const frames = await captureChallenge(ch.direction);
    showResult(await api("/api/kyc/enroll", { method: "POST", body: { challenge_id: ch.challenge_id, id_photo: idPhotoData, frames } }));
  } catch (e) { showResult({ passed: false, reason: e.message }); }
  modal.busy = false;
  refresh();
}
$("#enrollBtn").onclick = openEnroll;

// ---------------------------------------------------------------- mandate buttons
$("#revokeBtn").onclick = async () => { await api("/api/mandate/revoke", { method: "POST" }); refresh(); };
$("#withdrawBtn").onclick = async () => {
  if (confirm("Withdraw consent? This revokes the mandate and erases your face template.")) { await api("/api/consent/withdraw", { method: "POST" }); refresh(); }
};
$("#regrantBtn").onclick = async () => { await api("/api/mandate/grant", { method: "POST", body: {} }); refresh(); };
$("#resetBtn").onclick = async () => { if (confirm("Reset the demo database?")) { await api("/api/reset", { method: "POST" }); location.reload(); } };

// ---------------------------------------------------------------- audit
function auditDetail(e) {
  const p = e.payload;
  if (e.kind === "decision") return `${p.outcome}${p.layer ? " at " + p.layer : ""} · ${esc(p.action || "")} ${p.amount_inr != null ? inr(p.amount_inr) : ""} ${esc(p.merchant || "")}<br><span class="muted">${esc(p.reason)}</span>`;
  if (e.kind === "mandate.issued") return `${esc(p.mandate_id)} · cap ${inr(p.scope.per_txn_cap_inr)} · ${inr(p.scope.cumulative_cap_inr)}/${p.scope.period_days}d`;
  if (e.kind === "stepup.resolved") return `${p.outcome} · ${esc(p.reason)}`;
  return esc(JSON.stringify(p)).slice(0, 160);
}
async function loadAudit() {
  const { entries, verify: v } = await api("/api/audit");
  const b = $("#chainBanner");
  b.className = "banner " + (v.valid ? "ok" : "bad");
  b.textContent = v.valid ? `✓ Chain valid: ${v.length} entries · ${v.checkpoint.detail}`
    : `✗ Chain broken at #${v.first_broken}: ${v.reason}${v.checkpoint.ok ? "" : " · checkpoint: " + v.checkpoint.detail}`;
  $("#chainDot").className = "dot " + (v.valid ? "ok" : "bad");
  $("#auditRows").innerHTML = entries.slice().reverse().map((e) => {
    const st = v.status[e.seq] || "ok";
    return `<tr class="${st}"><td>${e.seq}</td><td>${clock(e.ts)}</td><td>${esc(e.kind)}${e.payload.tampered ? ' <span class="badge BLOCK">edited</span>' : ""}</td>
      <td>${auditDetail(e)}</td><td class="h">${e.prev_hash.slice(0, 12)}…</td><td class="h">${e.hash.slice(0, 12)}…</td>
      <td class="link ${st}">${st === "ok" ? "✓" : "✗"}</td></tr>`;
  }).join("");
}
$$("[data-tamper]").forEach((b) => (b.onclick = async () => {
  await api("/api/audit/tamper", { method: "POST", body: { mode: b.dataset.tamper, seq: +$("#tamperSeq").value } });
  loadAudit();
}));
let auditTimer = null;
const auditSoon = () => { clearTimeout(auditTimer); auditTimer = setTimeout(loadAudit, 250); };

// ---------------------------------------------------------------- eval
async function loadEval() {
  const r = await api("/api/eval");
  if (!r.available) return;
  const rows = r.results.map((x) => `<tr><td class="mono">${x.id}</td><td>${esc(x.category)}</td><td>${esc(x.title)}</td>
    <td><span class="badge ${x.expected}">${x.expected}</span> ${x.expected_layer || ""}</td>
    <td><span class="badge ${x.actual}">${x.actual}</span> ${x.actual_layer || ""}</td><td>${x.pass ? "✅" : "❌"}</td></tr>`).join("");
  $("#evalWrap").innerHTML = `<div class="eval-summary">
      <div class="stat"><b>${r.passed}/${r.total}</b><span>scenarios passed</span></div>
      <div class="stat"><b>${Math.round((100 * r.passed) / r.total)}%</b><span>pass rate</span></div>
      <div class="stat"><b>${r.layers_exercised}</b><span>distinct layers exercised</span></div></div>
    <table class="eval"><thead><tr><th>ID</th><th>Category</th><th>Scenario</th><th>Expected</th><th>Actual</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---------------------------------------------------------------- tabs + events
$$(".tab").forEach((t) => (t.onclick = () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
  $$(".tabpane").forEach((p) => p.classList.toggle("active", p.id === "tab-" + t.dataset.tab));
  if (t.dataset.tab === "audit") loadAudit();
  if (t.dataset.tab === "eval") loadEval();
}));

function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    const { type, data } = ev;
    if (type.startsWith("agent.")) onAgentEvent(type, data);
    else if (type === "decision") onDecision(data);
    else if (type === "stepup.requested") openStepup(data);
    else if (type === "stepup.resolved") onStepupResolved(data);
    else if (type === "attack.start") sys(`⚔ Attack: ${data.title} (expected to stop at ${data.layer})`);
    else if (type === "mandate.revoked") { notify(`Mandate revoked (${esc(data.reason)}).`, "BLOCK"); refreshSoon(); }
    else if (type === "mandate.issued") refreshSoon();
    else if (type === "audit") { if ($("#tab-audit").classList.contains("active")) auditSoon(); }
    else if (type === "reset") location.reload();
  };
}

(async () => {
  await refresh();
  connect();
  const pending = STATE.pending_stepups[0];
  if (pending) openStepup({ challenge_id: pending.challenge_id, direction: pending.direction, decision: pending.detail });
  api("/api/audit").then(({ verify }) => ($("#chainDot").className = "dot " + (verify.valid ? "ok" : "bad")));
})();
