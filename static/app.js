const $ = (s) => document.querySelector(s);
const money = (v) => (v == null ? "" : "$" + Number(v).toFixed(2));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");

const ACTION_LABEL = { refund_full: "Full refund", refund_partial: "Partial refund", reject: "Reject", ask_info: "Ask for info" };
const ACTION_CLASS = { refund_full: "p-ok", refund_partial: "p-info", reject: "p-bad", ask_info: "p-warn" };
const STATUS_CLASS = { proposed: "p-info", needs_human: "p-warn", error: "p-bad", refunded: "p-ok", rejected: "p-muted",
  replied: "p-muted", queued: "p-muted", analyzing: "p-muted", new: "p-muted" };
const RISK_CLASS = { low: "p-ok", medium: "p-warn", high: "p-bad" };
const DONE = ["refunded", "rejected", "replied"];
const STATE_KEY = "refund-desk-grid-v1";
const isRefund = (a) => a === "refund_full" || a === "refund_partial";

let view = "todo";
let rows = [];
let status = null;
const charts = {};

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 4000);
}

const pill = (text, cls) => `<span class="pill ${cls}">${esc(text)}</span>`;

function hoursWaiting(r) {
  if (!r.received_at) return null;
  const end = r.decided_at ? new Date(r.decided_at) : new Date();
  return Math.max(0, (end - new Date(r.received_at)) / 36e5);
}

function readState() {
  try { return JSON.parse(localStorage.getItem(STATE_KEY)) || undefined; } catch { return undefined; }
}

function saveState(state) {
  try {
    const { columnOrder, columnSizing, sort, columnPinning } = state;
    localStorage.setItem(STATE_KEY, JSON.stringify({ columnOrder, columnSizing, sort, columnPinning }));
  } catch { /* private window: layout just isn't remembered */ }
}

// ---------- grid ----------

const columnDefs = [
  { field: "id", headerName: "#", width: 70, pinned: "left", filter: "agNumberColumnFilter" },
  { field: "status", width: 125, pinned: "left",
    cellRenderer: (p) => pill(p.value === "analyzing" || p.value === "queued" ? "AI working…" : p.value.replace("_", " "), STATUS_CLASS[p.value] || "p-muted") },
  { colId: "waiting", headerName: "Waiting", width: 105, filter: "agNumberColumnFilter",
    headerTooltip: "Time since the buyer wrote (until a decision). Green under 24h, amber under 48h, red after.",
    valueGetter: (p) => (p.data && p.data.id ? hoursWaiting(p.data) : null),
    valueFormatter: (p) => (p.value == null ? "" : p.value < 1 ? "<1h" : p.value < 48 ? `${Math.round(p.value)}h` : `${(p.value / 24).toFixed(1)}d`),
    cellClass: (p) => (p.value == null ? "" : ["wait", DONE.includes(p.data.status) ? "wait-done" : p.value < 24 ? "wait-ok" : p.value < 48 ? "wait-warn" : "wait-bad"]) },
  { field: "buyer_name", headerName: "Buyer", width: 150 },
  { field: "channel", width: 130, cellRenderer: (p) => (p.value === "paypal_dispute" ? pill("PayPal dispute", "p-warn") : "Email"),
    filterValueGetter: (p) => (p.data.channel === "paypal_dispute" ? "PayPal dispute" : "Email") },
  { field: "subject", flex: 1, minWidth: 180, tooltipField: "message" },
  { field: "item", width: 190 },
  { field: "order_amount", headerName: "Paid", width: 95, valueFormatter: (p) => money(p.value), type: "rightAligned", filter: "agNumberColumnFilter" },
  { field: "intent", width: 140, valueFormatter: (p) => (p.value || "").replaceAll("_", " ") },
  { field: "sentiment", width: 115, cellRenderer: (p) => (p.value ? pill(p.value, { calm: "p-muted", frustrated: "p-warn", angry: "p-bad" }[p.value]) : "") },
  { field: "action", width: 150, editable: (p) => p.data.status === "proposed",
    cellEditor: "agSelectCellEditor", cellEditorParams: { values: Object.keys(ACTION_LABEL) },
    cellRenderer: (p) => (p.value ? pill(ACTION_LABEL[p.value], ACTION_CLASS[p.value]) : "") },
  { field: "amount", headerName: "Refund", width: 105, type: "rightAligned", filter: "agNumberColumnFilter",
    editable: (p) => p.data.status === "proposed" && isRefund(p.data.action),
    cellEditor: "agNumberCellEditor", cellEditorParams: { min: 0, precision: 2 }, valueFormatter: (p) => (p.value ? money(p.value) : "—") },
  { field: "confidence", headerName: "AI confidence", width: 140, filter: "agNumberColumnFilter",
    cellRenderer: (p) => (p.value == null ? "" : `<div class="conf"><div class="bar"><i style="width:${p.value * 100}%"></i></div>${Math.round(p.value * 100)}%</div>`) },
  { field: "risk", width: 95, cellRenderer: (p) => (p.value ? pill(p.value, RISK_CLASS[p.value]) : "") },
  { field: "reply", headerName: "Reply to buyer", width: 260, editable: (p) => p.data.status === "proposed",
    cellEditor: "agLargeTextCellEditor", cellEditorPopup: true, cellEditorParams: { maxLength: 1200, rows: 8, cols: 60 }, tooltipField: "reply" },
  { field: "decided_at", headerName: "Decided", width: 165, valueFormatter: (p) => when(p.value), tooltipField: "decided_by" },
  { field: "refund_id", headerName: "PayPal refund", width: 170 },
  { field: "agent", headerName: "Decided by AI", width: 190 },
  { colId: "approve", headerName: "", width: 112, pinned: "right", sortable: false, filter: false, resizable: false,
    cellRenderer: (p) => (p.data.status === "proposed" ? `<button class="rowbtn" data-approve="${p.data.id}">Approve</button>` : "") },
];

// Plain values on the pinned totals row.
columnDefs.forEach((c) => {
  if (!c.cellRenderer) return;
  const inner = c.cellRenderer;
  c.cellRenderer = (p) => (p.node.rowPinned ? "" : inner(p));
});

const grid = agGrid.createGrid($("#grid"), {
  columnDefs,
  rowData: [],
  getRowId: (p) => String(p.data.id),
  defaultColDef: { sortable: true, filter: true, resizable: true, enableCellChangeFlash: true },
  initialState: readState(),
  onStateUpdated: (e) => saveState(e.state),
  rowSelection: { mode: "multiRow", checkboxes: true, headerCheckbox: true, enableClickSelection: false,
    isRowSelectable: (n) => ["proposed", "needs_human", "error"].includes(n.data.status) },
  selectionColumnDef: { pinned: "left", width: 50 },
  undoRedoCellEditing: true,
  undoRedoCellEditingLimit: 20,
  pagination: true,
  paginationPageSize: 20,
  paginationPageSizeSelector: [10, 20, 50],
  tooltipShowDelay: 300,
  overlayLoadingTemplate: '<span class="muted">Loading cases…</span>',
  overlayNoRowsTemplate: '<span class="muted">Inbox zero. Nothing waits for review. Try "+ New message".</span>',
  rowClassRules: { "row-high": (p) => p.data.risk === "high" && !DONE.includes(p.data.status),
    "row-medium": (p) => p.data.risk === "medium" && !DONE.includes(p.data.status) },
  isExternalFilterPresent: () => view !== "all",
  doesExternalFilterPass: (n) => (view === "done" ? DONE.includes(n.data.status) : !DONE.includes(n.data.status)),
  onCellValueChanged: saveEdit,
  onCellClicked: (e) => {
    const btn = e.event.target.closest("[data-approve]");
    if (btn) return confirmApprove([e.data]);
    if (e.column.getColId() !== "ag-Grid-SelectionColumn" && !e.event.target.closest(".ag-selection-checkbox")) openDrawer(e.data);
  },
  onSelectionChanged: updateButtons,
});

async function saveEdit(e) {
  if (e.source === "api" || !e.data.id) return;
  try {
    const updated = await api(`/api/cases/${e.data.id}`, { method: "PATCH", body: JSON.stringify({ [e.colDef.field]: e.newValue }) });
    toast(`Case #${e.data.id} updated. Guard rails passed. The AI will learn from this correction.`);
    mergeRow(updated);
    loadStatus();
  } catch (err) {
    toast(`Not saved: ${err.message}`);
    e.node.setDataValue(e.colDef.field, e.oldValue);
  }
}

function mergeRow(r) {
  const i = rows.findIndex((x) => x.id === r.id);
  if (i < 0) return;
  rows[i] = { ...rows[i], ...r, trace: rows[i].trace };
  grid.applyTransaction({ update: [rows[i]] });
  refreshSummary();
}

function updateButtons() {
  const sel = grid.getSelectedRows();
  const approvable = sel.filter((r) => r.status === "proposed");
  $("#btnApprove").disabled = approvable.length === 0;
  $("#btnDecline").disabled = sel.length === 0;
  $("#btnRerun").disabled = sel.length === 0;
  $("#btnApprove").textContent = approvable.length ? `Approve ${approvable.length}` : "Approve selected";
}

// ---------- quick filters ----------

const CHIPS = {
  none: null,
  high: { risk: { filterType: "text", type: "equals", filter: "high" } },
  dispute: { channel: { filterType: "text", type: "equals", filter: "PayPal dispute" } },
  lowconf: { confidence: { filterType: "number", type: "lessThan", filter: 0.6 } },
  edited: null,
};
let chip = "none";

function applyChip(name) {
  chip = name;
  document.querySelectorAll("#chips button").forEach((b) => b.classList.toggle("on", b.dataset.chip === name));
  grid.setFilterModel(CHIPS[name]);
  grid.onFilterChanged();
}

// "Edited by a person" isn't a column filter, so it rides on the external filter.
const baseExternal = grid.getGridOption("doesExternalFilterPass");
grid.setGridOption("isExternalFilterPresent", () => view !== "all" || chip === "edited");
grid.setGridOption("doesExternalFilterPass", (n) => (view === "all" || baseExternal(n)) && (chip !== "edited" || !!n.data.edited));

// ---------- summary + charts ----------

function refreshSummary() {
  const refunded = rows.filter((r) => r.status === "refunded");
  const pending = rows.filter((r) => !DONE.includes(r.status));
  const paid = rows.reduce((s, r) => s + r.order_amount, 0);
  const refundedTotal = refunded.reduce((s, r) => s + r.amount, 0);
  const proposedTotal = rows.filter((r) => r.status === "proposed" && isRefund(r.action)).reduce((s, r) => s + r.amount, 0);
  const done = rows.length - pending.length;
  const overdue = pending.filter((r) => (hoursWaiting(r) || 0) >= 48).length;
  const kpis = [
    [pending.length, "Cases to review", overdue ? `${overdue} waiting over 48h` : "none overdue"],
    [money(proposedTotal), "Refunds waiting for approval", "nothing is paid without a person"],
    [money(refundedTotal), "Refunded via PayPal", `${refunded.length} refund(s)`],
    [money(paid - refundedTotal - proposedTotal), "Kept by following policy", "rejections, partials, info requests"],
    [`${done * 6} min`, "Reviewer time saved", "≈6 min of reading and lookup per case"],
    [rows.filter((r) => r.edited).length, "AI drafts corrected", `${status?.corrections ?? 0} fed back to the AI`],
  ];
  $("#kpis").innerHTML = kpis.map(([v, l, s]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="s">${esc(s)}</div></div>`).join("");
  grid.setGridOption("pinnedBottomRowData", [{ id: null, subject: `${rows.length} cases`, order_amount: paid, amount: refundedTotal + proposedTotal }]);
  drawCharts(paid, proposedTotal, refundedTotal);
}

function chartTheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  return { baseTheme: dark ? "ag-default-dark" : "ag-default",
    palette: { fills: ["#0b5fff", "#12a37a", "#e8a33b", "#c2410c", "#7c4ddb", "#0e9ac6", "#d0457b", "#6b7280"], strokes: ["transparent"] },
    overrides: { common: { background: { visible: false } } } };
}

function drawCharts(paid, proposed, refunded) {
  const counts = {};
  rows.forEach((r) => { if (r.intent) counts[r.intent] = (counts[r.intent] || 0) + 1; });
  const intentData = Object.entries(counts).map(([k, v]) => ({ reason: k.replaceAll("_", " "), cases: v }));
  const moneyData = [
    { label: "Paid by these buyers", value: paid, color: "#94a3b8" },
    { label: "Waiting for approval", value: proposed, color: "#7c4ddb" },
    { label: "Refunded", value: refunded, color: "#12a37a" },
    { label: "Kept (policy)", value: paid - proposed - refunded, color: "#0b5fff" },
  ];
  const intentOptions = {
    container: $("#chartIntent"), theme: chartTheme(), data: intentData,
    series: [{ type: "donut", angleKey: "cases", legendItemKey: "reason", innerRadiusRatio: 0.62,
      calloutLabel: { enabled: false }, tooltip: { renderer: ({ datum }) => ({ title: datum.reason, content: `${datum.cases} case(s)` }) } }],
    legend: { position: "right" },
  };
  const moneyOptions = {
    container: $("#chartMoney"), theme: chartTheme(), data: moneyData,
    series: [{ type: "bar", direction: "horizontal", xKey: "label", yKey: "value", cornerRadius: 4,
      itemStyler: ({ datum }) => ({ fill: datum.color }),
      tooltip: { renderer: ({ datum }) => ({ title: datum.label, content: money(datum.value) }) } }],
    axes: [{ type: "category", position: "left" }, { type: "number", position: "bottom", label: { formatter: ({ value }) => "$" + value } }],
  };
  if (!charts.intent) {
    charts.intent = agCharts.AgCharts.create(intentOptions);
    charts.money = agCharts.AgCharts.create(moneyOptions);
  } else {
    charts.intent.update(intentOptions);
    charts.money.update(moneyOptions);
  }
}

// ---------- details drawer ----------

function traceHtml(r) {
  return (r.trace || []).map((t) => {
    if (t.tool === "propose_resolution") {
      return `<div>→ propose_resolution(${esc(ACTION_LABEL[t.args.action] || t.args.action)}${t.args.amount ? ", " + money(t.args.amount) : ""})${
        t.errors?.length ? `<br><span class="err">✗ blocked by guard rails: ${esc(t.errors.join("; "))}</span>` : " ✓ passed guard rails"}</div>`;
    }
    const res = t.result || {};
    const brief = t.tool === "get_payment"
      ? `PayPal capture ${esc(res.paypal_capture?.id)} · ${esc(res.paypal_capture?.status)} · refundable ${money(res.refundable_usd)} · ${esc(res.delivery_status)}${res.days_since_delivery != null ? `, delivered ${res.days_since_delivery}d ago` : ""}`
      : `${res.count ?? 0} earlier case(s) from this buyer`;
    return `<div>→ ${esc(t.tool)}()<br>&nbsp;&nbsp;${brief}</div>`;
  }).join("");
}

function openDrawer(r) {
  if (!r || !r.id) return;
  const trace = traceHtml(r);
  const changed = r.ai_action && (r.ai_action !== r.action || Number(r.ai_amount || 0) !== Number(r.amount || 0) || r.ai_reply !== r.reply);
  $("#drawerBody").innerHTML = `
    <h2 style="margin:0 28px 4px 0">#${r.id} · ${esc(r.subject)}</h2>
    <div class="meta">${pill(r.status.replace("_", " "), STATUS_CLASS[r.status])} ${r.action ? pill(ACTION_LABEL[r.action], ACTION_CLASS[r.action]) : ""}
      ${r.risk ? pill("risk " + r.risk, RISK_CLASS[r.risk]) : ""} ${r.confidence != null ? pill(`AI ${Math.round(r.confidence * 100)}% sure`, "p-muted") : ""}</div>
    <h3>Buyer message · ${esc(r.buyer_name)} · ${r.channel === "paypal_dispute" ? "PayPal dispute" : "Email"} · ${esc(when(r.received_at))}</h3>
    <div class="msg">${esc(r.message)}</div>
    <h3>Order</h3>
    <div>${esc(r.item)} · ${money(r.order_amount)} · ordered ${esc(r.order_date)} · ${esc(r.delivery_status)}${r.delivered_date ? " " + esc(r.delivered_date) : ""}<br>
      PayPal capture <code>${esc(r.capture_id)}</code> (${esc(r.capture_mode)}) · still refundable ${money(r.refundable)}</div>
    ${r.reasoning ? `<h3>Why the AI proposes this</h3><div>${esc(r.reasoning)}</div>` : ""}
    ${r.learned_from ? `<p class="muted">The AI saw ${r.learned_from} recent reviewer correction(s) when deciding this.</p>` : ""}
    ${changed ? `<h3>The AI first proposed</h3><div>${esc(ACTION_LABEL[r.ai_action])}${r.ai_amount ? " " + money(r.ai_amount) : ""}${r.ai_reply !== r.reply ? ` with a different reply` : ""}; a person changed it.</div>` : ""}
    ${r.reply ? `<h3>Reply to the buyer ${DONE.includes(r.status) ? "" : "(sent on approval)"}</h3><div class="msg">${esc(r.reply)}</div>` : ""}
    ${r.final_message ? `<h3>Message sent</h3><div class="msg">${esc(r.final_message)}</div>` : ""}
    ${r.decided_at ? `<h3>Audit</h3><dl class="audit"><dt>Decision</dt><dd>${esc(r.status)}</dd><dt>By</dt><dd>${esc(r.decided_by)}</dd><dt>When</dt><dd>${esc(when(r.decided_at))}</dd>
      ${r.refund_id ? `<dt>PayPal refund</dt><dd><code>${esc(r.refund_id)}</code> <button class="ghost" id="btnRefundStatus">Check status at PayPal</button><span id="refundStatus"></span></dd>` : ""}</dl>` : ""}
    ${r.error ? `<h3>Error</h3><div class="msg" style="color:var(--bad)">${esc(r.error)}</div>` : ""}
    ${trace ? `<h3>What the AI did · ${esc(r.agent)}</h3><div class="trace">${trace}</div>` : ""}
    ${r.status === "proposed" ? `<div class="row" style="margin-top:16px;display:flex;gap:8px;justify-content:flex-end"><button class="primary" id="drawerApprove">Approve…</button></div>` : ""}`;
  $("#drawer").hidden = false;
  $("#drawerApprove")?.addEventListener("click", () => confirmApprove([r]));
  $("#btnRefundStatus")?.addEventListener("click", async () => {
    $("#refundStatus").textContent = " checking…";
    try {
      const s = await api(`/api/cases/${r.id}/refund-status`);
      $("#refundStatus").textContent = ` ${s.status}${s.amount ? ` · ${s.amount} ${s.currency}` : ""} (${s.source === "demo" ? "demo" : "from PayPal"})`;
    } catch (err) {
      $("#refundStatus").textContent = ` ${err.message}`;
    }
  });
}

// ---------- approve with confirmation ----------

let pendingApprove = [];

function confirmApprove(list) {
  pendingApprove = list.filter((r) => r.status === "proposed");
  if (!pendingApprove.length) return;
  const refunds = pendingApprove.filter((r) => isRefund(r.action));
  const total = refunds.reduce((s, r) => s + r.amount, 0);
  $("#confirmTitle").textContent = `Approve ${pendingApprove.length} case(s)`;
  $("#confirmList").innerHTML = pendingApprove.map((r) => `<li><span>#${r.id} ${esc(r.buyer_name)} · ${esc(ACTION_LABEL[r.action])}${r.risk === "high" ? " " + pill("high risk", "p-bad") : ""}</span><span>${isRefund(r.action) ? money(r.amount) : "—"}</span></li>`).join("")
    + `<li class="total"><span>${refunds.length} PayPal refund(s)</span><span>${money(total)}</span></li>`;
  const unsure = pendingApprove.filter((r) => (r.confidence ?? 1) < 0.6);
  const high = pendingApprove.filter((r) => r.risk === "high");
  const warn = [];
  if (high.length) warn.push(`${high.length} high-risk refund(s) over ${money(status?.limits.senior_review_usd)}: worth a second look.`);
  if (unsure.length) warn.push(`The AI was unsure (< 60%) about #${unsure.map((r) => r.id).join(", #")}. Open them before approving.`);
  $("#confirmWarn").hidden = !warn.length;
  $("#confirmWarn").textContent = warn.join(" ");
  $("#confirmOk").textContent = refunds.length ? `Approve and refund ${money(total)}` : "Approve and send replies";
  $("#confirmDialog").showModal();
}

$("#confirmDialog").addEventListener("close", async () => {
  if ($("#confirmDialog").returnValue !== "ok" || !pendingApprove.length) return;
  const ids = pendingApprove.map((r) => r.id);
  pendingApprove = [];
  try {
    const res = await api("/api/cases/approve", { method: "POST", body: JSON.stringify({ ids }) });
    const ok = res.results.filter((r) => r.ok);
    const bad = res.results.filter((r) => !r.ok);
    const refunds = ok.filter((r) => r.refund_id).length;
    toast(`${ok.length} approved${refunds ? `, ${refunds} PayPal refund(s) issued` : ""}${bad.length ? `. ${bad.length} failed: ${bad[0].error}` : ""}`);
  } catch (err) {
    toast(`Approve failed: ${err.message}`);
  }
  grid.deselectAll();
  $("#drawer").hidden = true;
  load();
});

// ---------- loading ----------

async function load() {
  try {
    rows = await api("/api/cases");
  } catch (err) {
    toast(`Couldn't load cases: ${err.message}`);
    clearTimeout(load.timer);
    load.timer = setTimeout(load, 5000);
    return;
  }
  grid.setGridOption("rowData", rows);
  refreshSummary();
  updateButtons();
  const working = rows.some((r) => r.status === "queued" || r.status === "analyzing");
  clearTimeout(load.timer);
  load.timer = setTimeout(load, working ? 2500 : 15000);
}

async function loadStatus() {
  status = await api("/api/status");
  const s = status;
  $("#modes").innerHTML = `
    <span class="mode" title="${esc(JSON.stringify(s.payments))}">PayPal: <b>${s.paypal !== "sandbox" ? "Demo (no keys)"
      : s.payments.sandbox ? `Sandbox · ${s.payments.sandbox} live payment(s)` : "Sandbox keys set · payments simulated"}</b></span>
    <span class="mode">AI: <b>${esc(s.ai)}</b></span>`;
  $("#railsSummary").textContent = `refunds over ${money(s.limits.senior_review_usd)} flagged · ${money(s.limits.hard_ceiling_usd)} ceiling · ${s.corrections} reviewer correction(s) learned`;
  $("#railsList").innerHTML = [
    "A person approves every refund. The AI only proposes.",
    "A refund can never exceed what is still refundable on the PayPal capture.",
    `Proposals over ${money(s.limits.hard_ceiling_usd)} are refused; over ${money(s.limits.senior_review_usd)} are marked high risk.`,
    "The reply drafted before approval may not promise money, an amount, a percentage or a timeline.",
    "The reply must be in the language the buyer wrote in, never guessed from a name.",
    "A proposal that breaks a rule goes back to the AI with the reason, and it must fix it.",
    "Refund calls carry a PayPal-Request-Id, so a retry can never pay twice.",
    "The buyer is told the amount only after PayPal confirms the refund.",
  ].map((t) => `<li>${esc(t)}</li>`).join("");
  $("#policyText").textContent = s.policy;
}

// ---------- toolbar ----------

async function act(path, label) {
  const ids = grid.getSelectedRows().map((r) => r.id);
  if (!ids.length) return;
  try {
    await api(path, { method: "POST", body: JSON.stringify({ ids }) });
    toast(label);
  } catch (err) {
    toast(err.message);
  }
  grid.deselectAll();
  load();
}

$("#btnApprove").onclick = () => confirmApprove(grid.getSelectedRows());
$("#btnDecline").onclick = () => act("/api/cases/decline", "Declined. The buyer gets a polite no.");
$("#btnRerun").onclick = () => act("/api/cases/reanalyze", "Sent back to the AI.");
$("#btnCsv").onclick = () => grid.exportDataAsCsv({ fileName: "refund-desk.csv", columnKeys: columnDefs.map((c) => c.colId || c.field).filter((k) => k !== "approve") });
$("#btnReset").onclick = async () => {
  try { await api("/api/reset", { method: "POST" }); toast("Demo data restored."); } catch (err) { toast(err.message); }
  $("#drawer").hidden = true;
  load();
  loadStatus();
};
$("#search").oninput = (e) => grid.setGridOption("quickFilterText", e.target.value);
$("#drawerClose").onclick = () => ($("#drawer").hidden = true);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
document.querySelectorAll("#views button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("#views button").forEach((x) => x.classList.toggle("on", x === b));
  view = b.dataset.view;
  grid.onFilterChanged();
}));
document.querySelectorAll("#chips button").forEach((b) => (b.onclick = () => applyChip(b.dataset.chip)));
$("#btnNew").onclick = async () => {
  const orders = await api("/api/orders");
  $("#newOrder").innerHTML = orders.map((o) => `<option value="${o.id}">${o.id} · ${esc(o.buyer_name)} · ${esc(o.item)}</option>`).join("");
  $("#newDialog").showModal();
};
$("#newForm").onsubmit = async (e) => {
  if (e.submitter?.value !== "ok") return;
  try {
    await api("/api/cases", { method: "POST", body: JSON.stringify({ order_id: $("#newOrder").value, subject: $("#newSubject").value, message: $("#newMessage").value }) });
    toast("Sent to the AI. Watch it appear in the grid.");
    if (view === "done") document.querySelector('#views button[data-view="todo"]').click();
  } catch (err) {
    toast(err.message);
  }
  load();
};
function applyTheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  $("#grid").classList.toggle("ag-theme-quartz", !dark);
  $("#grid").classList.toggle("ag-theme-quartz-dark", dark);
}
applyTheme();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { applyTheme(); refreshSummary(); });

grid.setGridOption("loading", true);
loadStatus().then(load).then(() => {
  grid.setGridOption("loading", false);
  const m = location.hash.match(/case=(\d+)/);  // #case=3 opens that case, handy for links and demos
  if (m) openDrawer(rows.find((r) => r.id === Number(m[1])));
});
