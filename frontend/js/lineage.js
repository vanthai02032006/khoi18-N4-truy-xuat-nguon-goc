/* =====================================================================
 * AGRI-TRACE · Giao diện Sprint 3–4
 *   - Chi tiết & phả hệ lô (S-25 / SCRUM-97): lô mẹ, lô con trực tiếp
 *   - Nguồn gốc (S-21 / SCRUM-105): truy ngược tới vùng trồng gốc
 *   - Hậu duệ (S-26, S-27 / SCRUM-107, SCRUM-108): truy xuôi + nơi đang giữ
 *   - Sơ đồ phả hệ (S-28 / SCRUM-109): tổ tiên trái, lô hiện tại giữa, hậu duệ phải
 *   - Nhật ký thao tác (S-31 / SCRUM-90) và nhập CSV (S-32 / SCRUM-99)
 * Dùng chung các hàm của app.js: $, apiRequest, escapeHtml, formatNumber, toast,
 * session, switchTab, authHeader.
 * ===================================================================== */

let lineageBatchId = null;
let lineageView = "overview";
let lineageNetwork = null;
let lineageGraphData = null;
let lineageExpanded = new Set();
const LINEAGE_DEFAULT_DEPTH = 2;

/* ------------------------------------------------------------ mở / tải --- */
function openLineage(batchId, view = "overview") {
  lineageBatchId = Number(batchId);
  lineageView = view;
  lineageExpanded = new Set();
  switchTab("lineage");
  const input = $("lineage-batch-input");
  const known = (typeof batches !== "undefined" ? batches : []).find((b) => b.id === lineageBatchId);
  if (input) input.value = known ? known.batch_code || `#${known.id}` : `#${lineageBatchId}`;
  loadLineage();
  const card = $("lineage-card");
  if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function resolveBatchInput(raw) {
  const value = (raw || "").trim();
  if (!value) return null;
  const numeric = value.replace(/^#/, "");
  if (/^\d+$/.test(numeric)) return Number(numeric);
  const found = await apiRequest(`/batches?search=${encodeURIComponent(value)}&limit=5`);
  const exact = (found || []).find((b) => (b.batch_code || "").toLowerCase() === value.toLowerCase());
  return (exact || (found || [])[0] || {}).id || null;
}

async function handleLineageLookup(event) {
  if (event) event.preventDefault();
  try {
    const id = await resolveBatchInput($("lineage-batch-input").value);
    if (!id) {
      toast("Không tìm thấy lô với mã này.", "error");
      return;
    }
    lineageBatchId = id;
    lineageExpanded = new Set();
    loadLineage();
  } catch (error) {
    toast(error.message, "error");
  }
}

function setLineageView(view) {
  lineageView = view;
  document.querySelectorAll("[data-lineage-view]").forEach((btn) => {
    const active = btn.dataset.lineageView === view;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".lineage-panel").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== view;
  });
  if (lineageBatchId) loadLineagePanel(view);
}

function loadLineage() {
  $("lineage-empty").hidden = Boolean(lineageBatchId);
  $("lineage-body").hidden = !lineageBatchId;
  if (lineageBatchId) refreshLineageTitle();
  setLineageView(lineageView);
}

async function refreshLineageTitle() {
  try {
    const d = await apiRequest(`/batches/${lineageBatchId}`);
    $("lineage-title").textContent = `${d.batch_code || "#" + d.id} — ${d.product_name}`;
  } catch (error) {
    $("lineage-title").textContent = "Chi tiết & phả hệ lô hàng";
  }
}

function lineageMessage(target, message, tone = "muted") {
  const colors = { muted: "#64748b", error: "#b91c1c" };
  target.innerHTML = `<p class="lineage-note" style="color:${colors[tone]}">${escapeHtml(message)}</p>`;
}

async function loadLineagePanel(view) {
  const target = $(`lineage-panel-${view}`);
  if (!target) return;
  if (view === "timeline") {
    lineageMessage(target, "Đang mở dòng thời gian...");
    if (typeof openTimelineModal === "function") openTimelineModal(lineageBatchId);
    target.innerHTML = `<button type="button" class="btn btn--secondary btn--sm" onclick="openTimelineModal(${lineageBatchId})">Mở lại dòng thời gian & kiểm toàn vẹn</button>`;
    return;
  }
  if (view !== "graph") lineageMessage(target, "Đang tải...");
  try {
    if (view === "overview") renderLineageOverview(target, await apiRequest(`/batches/${lineageBatchId}/direct-summary`));
    if (view === "origin") renderLineageOrigin(target, await apiRequest(`/batches/${lineageBatchId}/trace-back`));
    if (view === "descendants") renderLineageDescendants(target, await apiRequest(`/batches/${lineageBatchId}/trace-forward`));
    if (view === "graph") {
      lineageGraphData = await apiRequest(`/batches/${lineageBatchId}/lineage-graph`);
      renderLineageGraph();
    }
  } catch (error) {
    if (view === "graph") {
      if (lineageNetwork) { lineageNetwork.destroy(); lineageNetwork = null; }
      $("lineage-graph-note").textContent = error.message;
      $("lineage-graph-note").style.color = "#b91c1c";
    } else {
      lineageMessage(target, error.message, "error");
    }
  }
}

/* ------------------------------------------------- S-25: tổng quan lô --- */
function lineageLink(id, code) {
  return `<button type="button" class="lineage-link" onclick="openLineage(${Number(id)}, '${lineageView}')">${escapeHtml(code)}</button>`;
}

function relationList(items, emptyText) {
  if (!items.length) return `<p class="lineage-note">${escapeHtml(emptyText)}</p>`;
  return `<ul class="lineage-rel-list">${items
    .map(
      (r) => `<li>
        ${lineageLink(r.batch_id, r.batch_code)}
        <span>${escapeHtml(r.product_name)}</span>
        <span class="lineage-tag">${r.relation_type === "MERGE" ? "Gộp" : "Tách"}</span>
        <span class="font-mono">${r.used_quantity != null ? formatNumber(r.used_quantity) + " kg" : formatNumber(r.quantity) + " kg"}</span>
      </li>`
    )
    .join("")}</ul>`;
}

function renderLineageOverview(target, d) {
  $("lineage-title").textContent = `${d.batch_code} — ${d.product_name}`;
  const farm = d.farm || {};
  const holder = d.holder_org || {};
  const consumed = (d.product || {}).status === "consumed";
  target.innerHTML = `
    <dl class="lineage-facts">
      <div><dt>Mã lô</dt><dd class="font-mono">${escapeHtml(d.batch_code)}</dd></div>
      <div><dt>Sản phẩm</dt><dd>${escapeHtml(d.product_name)}</dd></div>
      <div><dt>Thửa gốc</dt><dd>${escapeHtml(farm.plot_name || "—")}<br><small>${escapeHtml(farm.location || "")}</small></dd></div>
      <div><dt>Khối lượng còn</dt><dd class="font-mono">${consumed ? "0 kg (đã tiêu thụ)" : formatNumber(d.quantity) + " kg"}</dd></div>
      <div><dt>Tổ chức đang giữ</dt><dd>${escapeHtml(d.current_holder_org || "—")}</dd></div>
      <div><dt>Địa điểm</dt><dd>${escapeHtml(holder.location || "Chưa gắn địa điểm")}</dd></div>
      <div><dt>Ngày thu hoạch</dt><dd>${escapeHtml(formatDate ? formatDate(d.harvest_date) : d.harvest_date)}</dd></div>
      <div><dt>Số sự kiện</dt><dd>${escapeHtml(d.events_count)}</dd></div>
    </dl>
    <div class="lineage-two-col">
      <section><h4>Lô mẹ trực tiếp</h4>${relationList(d.direct_parents, "Lô thu hoạch gốc — không có lô mẹ.")}</section>
      <section><h4>Lô con trực tiếp</h4>${relationList(d.direct_children, "Chưa tách hay gộp ra lô nào.")}</section>
    </div>`;
}

/* --------------------------------------------- S-21: nguồn gốc (ngược) --- */
function renderLineageOrigin(target, d) {
  const intro = d.is_standalone
    ? "Lô này chưa từng tách hay gộp — nguồn gốc chính là nó."
    : `Đi ngược ${d.depth} tầng qua ${d.all_ancestor_codes.length} lô tổ tiên, tới ${d.origin_farms.length} lô thu hoạch gốc.`;
  target.innerHTML = `
    <p class="lineage-note">${escapeHtml(intro)}</p>
    <div class="lineage-origin-grid">
      ${d.origin_farms
        .map(
          (f) => `<article class="lineage-origin">
            <header>${lineageLink(f.batch_id, f.batch_code)}<span>${escapeHtml(f.harvest_date)}</span></header>
            <strong>${escapeHtml(f.farm_name)}</strong>
            <span>${escapeHtml(f.plot_name)}</span>
            <span>${escapeHtml(f.farm_location)}</span>
            <span>Chủ vùng trồng: ${escapeHtml(f.farm_owner)}</span>
            <span class="font-mono">Hiện còn ${formatNumber(f.initial_quantity)} kg · ${escapeHtml(f.product_name)}</span>
          </article>`
        )
        .join("")}
    </div>
    ${
      d.all_ancestor_codes.length
        ? `<p class="lineage-note">Toàn bộ ${d.all_ancestor_codes.length} lô tổ tiên (tầng gần trước): <span class="font-mono">${d.all_ancestor_codes.map(escapeHtml).join(", ")}</span></p>`
        : ""
    }`;
}

/* ------------------------------------ S-26, S-27: hậu duệ (xuôi) + nơi giữ --- */
function renderLineageDescendants(target, d) {
  if (!d.total_descendants) {
    target.innerHTML = `<p class="lineage-note">Lô ${escapeHtml(d.target_batch_code)} chưa sinh ra lô con nào.</p>`;
    return;
  }
  const hidden = d.hidden_count
    ? `<p class="lineage-warn">Còn ${d.hidden_count} lô hậu duệ thuộc tổ chức khác mà tài khoản của bạn không được xem chi tiết. Cán bộ kiểm tra thấy đủ danh sách.</p>`
    : "";
  target.innerHTML = `
    <div class="lineage-stats">
      <div><span>Tổng hậu duệ</span><strong>${d.total_descendants}</strong></div>
      <div><span>Còn trên kệ / trong kho</span><strong class="font-mono">${formatNumber(d.total_remaining_quantity)} kg</strong></div>
      <div><span>Đã tiêu thụ</span><strong>${d.consumed_count} lô</strong></div>
    </div>
    ${hidden}
    <div class="table-wrapper lineage-scroll">
      <table class="data-table">
        <thead><tr>
          <th>TẦNG</th><th>MÃ LÔ</th><th>SẢN PHẨM</th><th>TỔ CHỨC ĐANG GIỮ</th><th>ĐỊA ĐIỂM</th>
          <th class="is-right">CÒN LẠI (KG)</th><th>TRẠNG THÁI</th>
        </tr></thead>
        <tbody>${d.descendants
          .map(
            (x) => `<tr>
              <td class="is-center">${x.depth}</td>
              <td>${lineageLink(x.batch_id, x.batch_code)}${x.is_merged_from_multiple_sources ? ' <span class="lineage-tag lineage-tag--warn">Gộp nhiều nguồn</span>' : ""}</td>
              <td>${escapeHtml(x.product_name)}</td>
              <td>${escapeHtml(x.current_holder_org)}</td>
              <td>${escapeHtml(x.location)}</td>
              <td class="is-right font-mono">${formatNumber(x.remaining_quantity)}</td>
              <td><span class="lineage-tag ${x.status === "IN_STOCK" ? "lineage-tag--ok" : ""}">${escapeHtml(x.status_label)}</span></td>
            </tr>`
          )
          .join("")}</tbody>
      </table>
    </div>`;
}

/* ------------------------------------------------- S-28: sơ đồ phả hệ --- */
const LINEAGE_COLORS = {
  ancestor: { background: "#e0f2fe", border: "#0369a1" },
  current: { background: "#047857", border: "#064e3b" },
  descendant: { background: "#fef3c7", border: "#b45309" },
};

function visibleGraph() {
  const { nodes, edges } = lineageGraphData;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const outgoing = new Map();
  const incoming = new Map();
  edges.forEach((e) => {
    if (!outgoing.has(e.from_node)) outgoing.set(e.from_node, []);
    if (!incoming.has(e.to_node)) incoming.set(e.to_node, []);
    outgoing.get(e.from_node).push(e.to_node);
    incoming.get(e.to_node).push(e.from_node);
  });
  // Nhánh sâu hơn LINEAGE_DEFAULT_DEPTH tầng bị thu gọn; bấm nút "+" để mở thêm.
  const shown = new Set(nodes.filter((n) => Math.abs(n.level) <= LINEAGE_DEFAULT_DEPTH).map((n) => n.id));
  lineageExpanded.forEach((id) => {
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop();
      const node = byId.get(cur);
      if (!node) continue;
      shown.add(cur);
      const next = node.level < 0 ? incoming.get(cur) || [] : outgoing.get(cur) || [];
      next.forEach((nid) => {
        if (!shown.has(nid)) stack.push(nid);
      });
    }
  });
  const hiddenCount = (id) => {
    const node = byId.get(id);
    const next = node.level < 0 ? incoming.get(id) || [] : node.level > 0 ? outgoing.get(id) || [] : [];
    return next.filter((nid) => !shown.has(nid)).length;
  };
  return { shown, hiddenCount, byId };
}

function renderLineageGraph() {
  const container = $("lineage-graph");
  const note = $("lineage-graph-note");
  if (!container || !lineageGraphData) return;
  if (typeof vis === "undefined") {
    lineageMessage(container, "Không tải được thư viện vẽ sơ đồ. Kiểm tra kết nối mạng rồi tải lại trang.", "error");
    return;
  }
  const { shown, hiddenCount, byId } = visibleGraph();
  const nodes = [...shown].map((id) => {
    const n = byId.get(id);
    const more = hiddenCount(id);
    return {
      id,
      level: n.level,
      label: `${n.id}\n${n.product_name}\n${n.status === "Đã tiêu thụ" ? "Đã tiêu thụ" : formatNumber(n.quantity) + " kg"}${more ? `\n＋ ${more} lô (bấm để mở)` : ""}`,
      title: `${n.holder_org}${n.farm_name ? " · " + n.farm_name : ""}`,
      color: LINEAGE_COLORS[n.group],
      font: { color: n.group === "current" ? "#ffffff" : "#0f172a", face: "Inter", size: 13, multi: false },
      borderWidth: n.group === "current" ? 3 : 1.5,
      shape: "box",
      margin: 10,
    };
  });
  const edges = lineageGraphData.edges
    .filter((e) => shown.has(e.from_node) && shown.has(e.to_node))
    .map((e) => ({
      from: e.from_node,
      to: e.to_node,
      arrows: "to",
      label: `${e.label === "MERGE" ? "gộp" : "tách"}${e.quantity != null ? " " + formatNumber(e.quantity) + " kg" : ""}`,
      font: { size: 11, align: "middle", color: "#475569" },
      color: { color: "#94a3b8" },
    }));
  const data = { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) };
  const options = {
    layout: { hierarchical: { direction: "LR", sortMethod: "directed", levelSeparation: 230, nodeSpacing: 110 } },
    physics: false,
    interaction: { hover: true, dragView: true, zoomView: true },
  };
  if (lineageNetwork) lineageNetwork.destroy();
  lineageNetwork = new vis.Network(container, data, options);
  lineageNetwork.on("click", (params) => {
    if (!params.nodes.length) return;
    const id = params.nodes[0];
    if (hiddenCount(id)) {
      lineageExpanded.add(id);
      renderLineageGraph();
    } else {
      const node = byId.get(id);
      if (node && node.batch_id && node.batch_id !== lineageBatchId) openLineage(node.batch_id, "graph");
    }
  });
  const total = lineageGraphData.nodes.length;
  note.style.color = "";
  note.textContent =
    shown.size < total
      ? `Đang hiện ${shown.size}/${total} lô. Nhánh sâu hơn ${LINEAGE_DEFAULT_DEPTH} tầng đã thu gọn — bấm ô có dấu ＋ để mở.`
      : `${total} lô trong phả hệ. Bấm một lô để xem phả hệ của lô đó.`;
}

/* --------------------------------------------------- S-31: nhật ký --- */
let auditCursor = null;

async function loadAuditLogs(append = false) {
  const tbody = $("audit-table-body");
  if (!tbody) return;
  const params = new URLSearchParams();
  const user = $("audit-filter-user").value.trim();
  const action = $("audit-filter-action").value.trim();
  if (user) params.set("username", user);
  if (action) params.set("action", action);
  if ($("audit-filter-failed").checked) params.set("only_failed", "true");
  if (append && auditCursor) params.set("cursor", auditCursor);
  try {
    const rows = await apiRequest(`/audit-logs?${params.toString()}`);
    const html = rows
      .map(
        (r) => `<tr class="${r.success ? "" : "audit-row--failed"}">
          <td class="font-mono">${escapeHtml(new Date(r.created_at + "Z").toLocaleString("vi-VN"))}</td>
          <td><strong>${escapeHtml(r.username || "—")}</strong><br><small>${escapeHtml(r.organization_id || "")}</small></td>
          <td>${escapeHtml(r.action)}${r.detail ? `<br><small>${escapeHtml(r.detail)}</small>` : ""}</td>
          <td class="font-mono">${escapeHtml(r.object_id || "—")}</td>
          <td class="font-mono">${escapeHtml(r.ip_address || "—")}</td>
          <td class="is-center">${r.success ? "✓" : "✕"} <small>${r.status_code}</small></td>
        </tr>`
      )
      .join("");
    tbody.innerHTML = append ? tbody.innerHTML + html : html;
    auditCursor = rows.length ? rows[rows.length - 1].id : auditCursor;
    $("audit-more").hidden = rows.length < 50;
    $("audit-empty").hidden = tbody.children.length > 0;
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------ S-32: nhập CSV --- */
async function importCsv(kind) {
  const input = $(`csv-file-${kind}`);
  const report = $(`csv-report-${kind}`);
  const file = input && input.files && input.files[0];
  if (!file) {
    toast("Chọn tệp CSV trước.", "error");
    return;
  }
  report.hidden = false;
  report.innerHTML = "Đang nhập...";
  try {
    const response = await fetch(`${API_BASE_URL}/imports/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "text/csv", ...authHeader() },
      body: await file.text(),
    });
    const data = await response.json().catch(() => null);
    if (response.ok) {
      report.innerHTML = `<span class="lineage-tag lineage-tag--ok">✓ ${escapeHtml(data.message)}</span>`;
      input.value = "";
      if (kind === "products" && typeof loadProducts === "function") loadProducts();
      if (kind === "farms" && typeof loadFarms === "function") loadFarms();
      return;
    }
    const detail = data && data.detail;
    if (detail && detail.errors) {
      report.innerHTML = `<p class="lineage-warn">${escapeHtml(detail.message)}</p>
        <table class="data-table csv-error-table"><thead><tr><th>Dòng</th><th>Cột</th><th>Lỗi</th></tr></thead>
        <tbody>${detail.errors
          .map((e) => `<tr><td class="is-center">${e.row}</td><td class="font-mono">${escapeHtml(e.column)}</td><td>${escapeHtml(e.message)}</td></tr>`)
          .join("")}</tbody></table>`;
    } else {
      report.innerHTML = `<p class="lineage-warn">${escapeHtml(typeof detail === "string" ? detail : "Nhập thất bại (HTTP " + response.status + ").")}</p>`;
    }
  } catch (error) {
    report.innerHTML = `<p class="lineage-warn">${escapeHtml(error.message)}</p>`;
  }
}

/* --------------------------------------------------------- gắn sự kiện --- */
function applySprint34Roles() {
  const isAdmin = session !== null && session.role === ROLE_ADMIN;
  ["tab-audit-btn", "csv-import-farms", "csv-import-products"].forEach((id) => {
    if ($(id)) $(id).hidden = !isAdmin;
  });
  if ($("audit-card") && !isAdmin) $("audit-card").hidden = true;
}

document.addEventListener("DOMContentLoaded", () => {
  const form = $("lineage-lookup-form");
  if (form) form.addEventListener("submit", handleLineageLookup);
  document.querySelectorAll("[data-lineage-view]").forEach((btn) =>
    btn.addEventListener("click", () => setLineageView(btn.dataset.lineageView))
  );
  if ($("btn-lineage-fit")) $("btn-lineage-fit").addEventListener("click", () => lineageNetwork && lineageNetwork.fit());
  if ($("audit-filter-form"))
    $("audit-filter-form").addEventListener("submit", (e) => {
      e.preventDefault();
      auditCursor = null;
      loadAuditLogs(false);
    });
  if ($("audit-more")) $("audit-more").addEventListener("click", () => loadAuditLogs(true));
  ["farms", "products"].forEach((kind) => {
    const btn = $(`btn-csv-${kind}`);
    if (btn) btn.addEventListener("click", () => importCsv(kind));
  });
});
