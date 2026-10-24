const $ = (s) => document.querySelector(s);
const money = (v) => (v == null ? "" : "$" + Number(v).toFixed(2));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ACTION_LABEL = { refund_full: "Full refund", refund_partial: "Partial refund", reject: "Reject", ask_info: "Ask for info" };
const ACTION_CLASS = { refund_full: "p-ok", refund_partial: "p-info", reject: "p-bad", ask_info: "p-warn" };
const STATUS_CLASS = { proposed: "p-info", needs_human: "p-warn", error: "p-bad", refunded: "p-ok", rejected: "p-muted",
  replied: "p-muted", queued: "p-muted", analyzing: "p-muted", new: "p-muted" };
const DONE = ["refunded", "rejected", "replied"];
let view = "todo";
let rows = [];
let charts = {};

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
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

const pill = (text, cls) => `<span class="pill ${cls}">${esc(text)}</span>`;

const columnDefs = [
  { field: "id", headerName: "#", width: 70, pinned: "left" },
  { field: "status", width: 130, pinned: "left",
    cellRenderer: (p) => pill(p.value === "analyzing" || p.value === "queued" ? "AI working…" : p.value.replace("_", " "), STATUS_CLASS[p.value] || "p-muted") },
  { field: "buyer_name", headerName: "Buyer", width: 150 },
  { field: "channel", width: 120, cellRenderer: (p) => (p.value === "paypal_dispute" ? pill("PayPal dispute", "p-warn") : "Email") },
  { field: "subject", flex: 1, minWidth: 180, tooltipField: "message" },
  { field: "item", width: 190 },
  { field: "order_amount", headerName: "Paid", width: 95, valueFormatter: (p) => money(p.value), type: "rightAligned" },
  { field: "intent", width: 140, valueFormatter: (p) => (p.value || "").replaceAll("_", " ") },
  { field: "sentiment", width: 110,
    cellRenderer: (p) => (p.value ? pill(p.value, { calm: "p-muted", frustrated: "p-warn", angry: "p-bad" }[p.value]) : "") },
  { field: "action", width: 150, editable: (p) => p.data.status === "proposed",
    cellEditor: "agSelectCellEditor", cellEditorParams: { values: Object.keys(ACTION_LABEL) },
    cellRenderer: (p) => (p.value ? pill(ACTION_LABEL[p.value], ACTION_CLASS[p.value]) : "") },
  { field: "amount", headerName: "Refund", width: 105, type: "rightAligned", editable: (p) => p.data.status === "proposed" && p.data.action?.startsWith("refund"),
    cellEditor: "agNumberCellEditor", cellEditorParams: { min: 0, precision: 2 }, valueFormatter: (p) => (p.value ? money(p.value) : "—") },
  { field: "confidence", width: 130,
    cellRenderer: (p) => (p.value == null ? "" : `<div class="conf"><div class="bar"><i style="width:${p.value * 100}%"></i></div>${Math.round(p.value * 100)}%</div>`) },
  { field: "risk", width: 95, cellRenderer: (p) => (p.value ? pill(p.value, { low: "p-ok", medium: "p-warn", high: "p-bad" }[p.value]) : "") },
  { field: "reply", headerName: "Reply to buyer", width: 260, editable: (p) => p.data.status === "proposed",
    cellEditor: "agLargeTextCellEditor", cellEditorPopup: true, cellEditorParams: { maxLength: 1200, rows: 8, cols: 60 }, tooltipField: "reply" },
  { field: "refund_id", headerName: "PayPal refund", width: 170 },
  { field: "agent", width: 150 },
];

columnDefs.forEach((c) => {
  if (!c.cellRenderer) return;
  const inner = c.cellRenderer;
  c.cellRenderer = (p) => (p.node.rowPinned ? "" : inner(p));
});

const grid = agGrid.createGrid($("#grid"), {
  columnDefs,
  rowData: [],
  getRowId: (p) => String(p.data.id),
  defaultColDef: { sortable: true, filter: true, resizable: true, floatingFilter: false },
  rowSelection: { mode: "multiRow", checkboxes: true, headerCheckbox: true, enableClickSelection: false,
    isRowSelectable: (n) => n.data.status === "proposed" || n.data.status === "needs_human" || n.data.status === "error" },
  selectionColumnDef: { pinned: "left", width: 50 },
  tooltipShowDelay: 300,
  rowClassRules: { "row-high": (p) => p.data.risk === "high" && !DONE.includes(p.data.status),
    "row-medium": (p) => p.data.risk === "medium" && !DONE.includes(p.data.status) },
  isExternalFilterPresent: () => view !== "all",
  doesExternalFilterPass: (n) => (view === "done" ? DONE.includes(n.data.status) : !DONE.includes(n.data.status)),
  onCellValueChanged: saveEdit,
  onRowClicked: (e) => { if (!e.event.target.closest(".ag-selection-checkbox")) openDrawer(e.data); },
  onSelectionChanged: updateButtons,
  pinnedBottomRowData: [],
});

async function saveEdit(e) {
  try {
    const updated = await api(`/api/cases/${e.data.id}`, { method: "PATCH", body: JSON.stringify({ [e.colDef.field]: e.newValue }) });
    toast(`Case #${e.data.id} updated. Guard rails passed.`);
    mergeRow(updated);
  } catch (err) {
    toast(`Not saved: ${err.message}`);
    e.node.setDataValue(e.colDef.field, e.oldValue);
  }
}

function mergeRow(r) {
  const i = rows.findIndex((x) => x.id === r.id);
  if (i >= 0) rows[i] = { ...rows[i], ...r };
  grid.applyTransaction({ update: [rows[i] || r] });
  refreshSummary();
}

function updateButtons() {
  const sel = grid.getSelectedRows();
  $("#btnApprove").disabled = !sel.some((r) => r.status === "proposed");
  $("#btnDecline").disabled = sel.length === 0;
  $("#btnRerun").disabled = sel.length === 0;
  $("#btnApprove").textContent = sel.length ? `Approve ${sel.length}` : "Approve selected";
}

function refreshSummary() {
  const refunded = rows.filter((r) => r.status === "refunded");
  const pending = rows.filter((r) => !DONE.includes(r.status));
  const askedTotal = rows.reduce((s, r) => s + r.order_amount, 0);
  const refundedTotal = refunded.reduce((s, r) => s + r.amount, 0);
  const proposedTotal = rows.filter((r) => r.status === "proposed" && r.action?.startsWith("refund")).reduce((s, r) => s + r.amount, 0);
  const done = rows.length - pending.length;
  const kpis = [
    [pending.length, "Cases to review"],
    [money(proposedTotal), "Refunds waiting for approval"],
    [money(refundedTotal), "Refunded via PayPal"],
    [money(askedTotal - refundedTotal - proposedTotal), "Kept by following policy"],
    [`${done * 6} min`, "Agent time saved (≈6 min/case)"],
    [rows.filter((r) => r.edited).length, "AI drafts edited by a person"],
  ];
  $("#kpis").innerHTML = kpis.map(([v, l]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  grid.setGridOption("pinnedBottomRowData", [{ id: null, subject: `${rows.length} cases`, order_amount: askedTotal,
    amount: refundedTotal + proposedTotal }]);
  drawCharts();
}

function drawCharts() {
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--muted").trim();
  const palette = ["#0b5fff", "#12a37a", "#e8a33b", "#c2410c", "#7c4ddb", "#0e9ac6", "#d0457b", "#6b7280"];
  const counts = {};
  rows.forEach((r) => { if (r.intent) counts[r.intent] = (counts[r.intent] || 0) + 1; });
  const labels = Object.keys(counts);
  const intentData = { labels: labels.map((l) => l.replaceAll("_", " ")), datasets: [{ data: labels.map((l) => counts[l]), backgroundColor: palette, borderWidth: 0 }] };
  const paid = rows.reduce((s, r) => s + r.order_amount, 0);
  const proposed = rows.filter((r) => r.status === "proposed" && r.action?.startsWith("refund")).reduce((s, r) => s + (r.amount || 0), 0);
  const refunded = rows.filter((r) => r.status === "refunded").reduce((s, r) => s + r.amount, 0);
  const moneyData = {
    labels: ["Paid by these buyers", "Waiting for approval", "Refunded", "Kept (policy)"],
    datasets: [{ data: [paid, proposed, refunded, paid - proposed - refunded],
      backgroundColor: ["#94a3b8", "#7c4ddb", "#12a37a", "#0b5fff"], borderRadius: 6 }],
  };
  if (!charts.intent) {
    charts.intent = new Chart($("#chartIntent"), { type: "doughnut", data: intentData,
      options: { plugins: { legend: { position: "right", labels: { color: ink } } }, maintainAspectRatio: false } });
    charts.money = new Chart($("#chartMoney"), { type: "bar", data: moneyData,
      options: { indexAxis: "y", plugins: { legend: { display: false } }, maintainAspectRatio: false,
        scales: { x: { ticks: { color: ink, callback: (v) => "$" + v } }, y: { ticks: { color: ink } } } } });
  } else {
    charts.intent.data = intentData; charts.intent.update();
    charts.money.data = moneyData; charts.money.update();
  }
}

function openDrawer(r) {
  if (!r.id) return;
  const trace = (r.trace || []).map((t) => {
    if (t.tool === "propose_resolution") {
      return `<div>→ propose_resolution(${esc(ACTION_LABEL[t.args.action] || t.args.action)}, ${money(t.args.amount)})${
        t.errors?.length ? `<br><span class="err">✗ guard rail: ${esc(t.errors.join("; "))}</span>` : " ✓ passed guard rails"}</div>`;
    }
    const res = t.result || {};
    const brief = t.tool === "get_payment"
      ? `PayPal capture ${esc(res.paypal_capture?.id)} · ${esc(res.paypal_capture?.status)} · refundable ${money(res.refundable_usd)} · ${esc(res.delivery_status)}`
      : `${res.count ?? 0} earlier case(s)`;
    return `<div>→ ${esc(t.tool)}() <br>&nbsp;&nbsp;${brief}</div>`;
  }).join("");
  $("#drawerBody").innerHTML = `
    <h2 style="margin:0 28px 4px 0">#${r.id} · ${esc(r.subject)}</h2>
    <div class="meta">${pill(r.status.replace("_", " "), STATUS_CLASS[r.status])} ${r.action ? pill(ACTION_LABEL[r.action], ACTION_CLASS[r.action]) : ""}
      ${r.risk ? pill("risk " + r.risk, { low: "p-ok", medium: "p-warn", high: "p-bad" }[r.risk]) : ""}</div>
    <h3>Buyer message · ${esc(r.buyer_name)} · ${r.channel === "paypal_dispute" ? "PayPal dispute" : "Email"}</h3>
    <div class="msg">${esc(r.message)}</div>
    <h3>Order</h3>
    <div>${esc(r.item)} · ${money(r.order_amount)} · ordered ${esc(r.order_date)} · ${esc(r.delivery_status)}${r.delivered_date ? " " + esc(r.delivered_date) : ""}<br>
      PayPal capture <code>${esc(r.capture_id)}</code> (${esc(r.capture_mode)}) · refundable ${money(r.refundable)}</div>
    ${r.reasoning ? `<h3>Why the AI proposes this</h3><div>${esc(r.reasoning)}</div>` : ""}
    ${r.reply ? `<h3>Reply to the buyer (sent on approval)</h3><div class="msg">${esc(r.reply)}</div>` : ""}
    ${r.final_message ? `<h3>Message sent</h3><div class="msg">${esc(r.final_message)}</div>` : ""}
    ${r.error ? `<h3>Error</h3><div class="msg" style="color:var(--bad)">${esc(r.error)}</div>` : ""}
    ${trace ? `<h3>Agent steps · ${esc(r.agent)}</h3><div class="trace">${trace}</div>` : ""}`;
  $("#drawer").hidden = false;
}

async function load() {
  rows = await api("/api/cases");
  grid.setGridOption("rowData", rows);
  refreshSummary();
  updateButtons();
  const working = rows.some((r) => r.status === "queued" || r.status === "analyzing");
  clearTimeout(load.timer);
  load.timer = setTimeout(load, working ? 2500 : 15000);
}

async function loadStatus() {
  const s = await api("/api/status");
  $("#modes").innerHTML = `
    <span class="mode">PayPal: <b>${s.paypal === "sandbox" ? "Sandbox (live API)" : "Demo (no keys)"}</b></span>
    <span class="mode">AI: <b>${esc(s.ai)}</b></span>
    <span class="mode">Senior review over <b>${money(s.limits.senior_review_usd)}</b> · ceiling <b>${money(s.limits.hard_ceiling_usd)}</b></span>`;
}

async function act(path, label) {
  const ids = grid.getSelectedRows().map((r) => r.id);
  if (!ids.length) return;
  const res = await api(path, { method: "POST", body: JSON.stringify({ ids }) });
  if (res.results) {
    const ok = res.results.filter((r) => r.ok);
    const bad = res.results.filter((r) => !r.ok);
    const refunds = ok.filter((r) => r.refund_id).length;
    toast(`${ok.length} approved${refunds ? `, ${refunds} PayPal refund(s) issued` : ""}${bad.length ? `. ${bad.length} failed: ${bad[0].error}` : ""}`);
  } else {
    toast(label);
  }
  grid.deselectAll();
  load();
}

$("#btnApprove").onclick = () => act("/api/cases/approve");
$("#btnDecline").onclick = () => act("/api/cases/decline", "Declined. The buyer gets a polite no.");
$("#btnRerun").onclick = () => act("/api/cases/reanalyze", "Sent back to the AI.");
$("#btnCsv").onclick = () => grid.exportDataAsCsv({ fileName: "refund-desk.csv" });
$("#btnReset").onclick = async () => { toast("Resetting demo data…"); await api("/api/reset", { method: "POST" }); load(); };
$("#search").oninput = (e) => grid.setGridOption("quickFilterText", e.target.value);
$("#drawerClose").onclick = () => ($("#drawer").hidden = true);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
document.querySelectorAll("#views button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("#views button").forEach((x) => x.classList.toggle("on", x === b));
  view = b.dataset.view;
  grid.onFilterChanged();
}));
$("#btnNew").onclick = async () => {
  const orders = await api("/api/orders");
  $("#newOrder").innerHTML = orders.map((o) => `<option value="${o.id}">${o.id} · ${esc(o.buyer_name)} · ${esc(o.item)}</option>`).join("");
  $("#newDialog").showModal();
};
$("#newForm").onsubmit = async (e) => {
  if (e.submitter?.value !== "ok") return;
  await api("/api/cases", { method: "POST", body: JSON.stringify({ order_id: $("#newOrder").value, subject: $("#newSubject").value, message: $("#newMessage").value }) });
  toast("Sent to the AI. Watch it appear in the grid.");
  load();
};

loadStatus();
load();
