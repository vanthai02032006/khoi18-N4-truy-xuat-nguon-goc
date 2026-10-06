/* =====================================================================
   Frontend demo - Truy xuất nguồn gốc và giám sát chuỗi lạnh nông sản
   JavaScript thuần (không framework, không thư viện ngoài).
   Cấu trúc: cấu hình -> tiện ích -> gọi API -> trạng thái -> render -> sự kiện.
   ===================================================================== */

"use strict";

// Địa chỉ backend FastAPI: tự động nhận diện localhost/live server hoặc production HTTPS
const API_BASE_URL =
  (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost") &&
  window.location.port === "5500"
    ? "http://127.0.0.1:8000"
    : "";

// Khoá lưu phiên đăng nhập trong sessionStorage (tự mất khi đóng tab).
const SESSION_STORAGE_KEY = "ttcs.session";

// Tên vai trò admin do backend quy định (dùng để phân quyền ở giao diện).
const ROLE_ADMIN = "admin";

/* ---------------------------------------------------------- 2. Tiện ích --- */
/** Lấy element theo id cho ngắn gọn. */
const $ = (id) => document.getElementById(id);

/** Chống XSS: escape dữ liệu do người dùng nhập trước khi chèn vào HTML. */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** 120.5 -> "120,5" (định dạng số kiểu Việt Nam). */
function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("vi-VN").format(number) : "—";
}

/** "2026-01-15" (từ input type=date / API) -> "15/01/2026". */
function formatDate(value) {
  if (!value) return "—";
  const parts = String(value).split("T")[0].split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : String(value);
}

/** "2026-10-06T15:30:00" -> "15:30 06/10/2026". */
function formatDateTime(value) {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (isNaN(d.getTime())) return String(value);
    const time = d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    const dateStr = d.toLocaleDateString("vi-VN");
    return `${time} ${dateStr}`;
  } catch (e) {
    return String(value);
  }
}

const TOAST_DURATION_MS = 5000;

/** Hiển thị thông báo ở góc phải dưới: type = "success" | "error" | "info". */
function toast(message, type = "info") {
  const container = $("toast-container");
  if (!container) return;
  const element = document.createElement("div");
  element.className = `toast toast--${type}`;
  element.setAttribute("role", type === "error" ? "alert" : "status");
  element.textContent = message;
  container.appendChild(element);
  window.setTimeout(() => element.remove(), TOAST_DURATION_MS);
}

/** Bật/tắt trạng thái "đang gửi" của nút submit (tránh bấm 2 lần). */
function setButtonLoading(button, isLoading, loadingText, idleText) {
  if (!button) return;
  button.disabled = isLoading;
  button.textContent = isLoading ? loadingText : idleText;
}

/* -------------------------------------------------------- 3. Gọi API --- */
/**
 * Gọi API backend và trả về dữ liệu JSON.
 * Mặc định gửi kèm tài khoản đang đăng nhập (xác thực HTTP Basic);
 * truyền `auth: false` cho request không cần xác thực (VD: đăng nhập).
 */
async function apiRequest(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body) {
    headers["Content-Type"] = "application/json";
  }
  if (auth) {
    Object.assign(headers, authHeader());
  }

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    throw new Error(
      `Không kết nối được backend (${API_BASE_URL}). Hãy chắc chắn uvicorn đang chạy.`
    );
  }

  const data = await readJson(response);
  if (!response.ok) {
    throw new Error(describeError(data, response.status));
  }
  return data;
}

/** Đọc JSON an toàn (response lỗi có thể không phải JSON). */
async function readJson(response) {
  try {
    return await response.json();
  } catch (error) {
    return null;
  }
}

/** Chuyển lỗi của FastAPI (`detail`) thành câu thông báo dễ hiểu. */
function describeError(data, status) {
  const detail = data ? data.detail : null;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    return detail
      .map((item) => `${(item.loc || []).join(".")}: ${item.msg}`)
      .join(" | ");
  }

  return `Yêu cầu thất bại (HTTP ${status}).`;
}

/* ------------------------------------------------------- 4. Trạng thái --- */
let farms = [];
let batches = [];
let handovers = [];
let events = [];
let users = [];

let editingFarmId = null;
let editingBatchId = null;

let session = null;

/* --------------------------------------------------------- 5. Đăng nhập --- */
function encodeBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

function authHeader() {
  if (session === null) {
    return {};
  }
  const token = encodeBase64(`${session.username}:${session.password}`);
  return { Authorization: `Basic ${token}` };
}

function requestLogin(username, password) {
  return apiRequest("/auth/login", {
    method: "POST",
    body: { username, password },
    auth: false,
  });
}

function startSession({ username, role, password }) {
  session = { username, role, password };
  window.sessionStorage.setItem(
    SESSION_STORAGE_KEY,
    JSON.stringify({ username, password })
  );
  applySessionToUi();
}

function restoreSession() {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    const saved = raw ? JSON.parse(raw) : null;
    if (saved && saved.username && saved.password) {
      return saved;
    }
  } catch (error) {
    return null;
  }
  return null;
}

function clearSession() {
  session = null;
  window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
}

function applySessionToUi() {
  const isLoggedIn = session !== null;
  const isAdmin = isLoggedIn && session.role === ROLE_ADMIN;

  $("login-view").hidden = isLoggedIn;
  $("app-view").hidden = !isLoggedIn;
  $("btn-reload").hidden = !isLoggedIn;
  $("btn-logout").hidden = !isLoggedIn;

  const badge = $("user-badge");
  if (badge) {
    badge.hidden = !isLoggedIn;
    if (isLoggedIn) {
      const displayName = session.username === "admin" ? "Quản Trị Viên (Admin)" : "Hộ Nông Dân Canh Tác";
      badge.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg> <span>${escapeHtml(displayName)}</span>`;
    }
  }

  $("users-card").hidden = !isAdmin;
}

function canDelete() {
  return session !== null && session.role === ROLE_ADMIN;
}

async function handleLoginSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const username = $("login-username").value.trim();
  const password = $("login-password").value;

  const button = $("login-submit");
  setButtonLoading(button, true, "Đang kiểm tra…", "Đăng nhập");

  try {
    const data = await requestLogin(username, password);
    startSession({ username: data.username, role: data.role, password });
    form.reset();
    toast(`Xin chào ${data.username} (role: ${data.role}).`, "success");
    await reloadAll({ silent: true });
  } catch (error) {
    toast(`Đăng nhập thất bại: ${error.message}`, "error");
    $("login-password").select();
  } finally {
    setButtonLoading(button, false, "Đang kiểm tra…", "Đăng nhập");
  }
}

function handleLogout() {
  const username = session ? session.username : "";
  clearSession();

  farms = [];
  batches = [];
  handovers = [];
  events = [];
  users = [];
  resetFarmForm();
  resetBatchForm();
  renderFarms();
  renderFarmOptions();
  renderBatches();
  renderHandovers();
  renderEvents();
  renderUsers();

  applySessionToUi();
  toast(username ? `Đã đăng xuất tài khoản ${username}.` : "Đã đăng xuất.", "info");
  $("login-username").focus();
}

/* ------------------------------------------- 6. Kiểm tra backend sống --- */
async function checkHealth() {
  try {
    await apiRequest("/health");
  } catch (error) {
    toast("Không thể kết nối đến máy chủ: " + error.message, "error");
  }
}

/* ------------------------------------------------------- 7. Vùng trồng --- */
async function loadFarms() {
  try {
    const data = await apiRequest("/farms");
    farms = Array.isArray(data) ? data : [];
    renderFarms();
    renderFarmOptions();
  } catch (error) {
    toast(`Không tải được danh sách vùng trồng: ${error.message}`, "error");
  }
}

function renderFarms() {
  $("farm-table-body").innerHTML = farms
    .map(
      (farm) => `
      <tr class="${farm.id === editingFarmId ? "is-editing" : ""}">
        <td class="id-cell">#${escapeHtml(farm.id)}</td>
        <td><strong>${escapeHtml(farm.name)}</strong></td>
        <td>${escapeHtml(farm.location)}</td>
        <td class="is-right font-mono">${formatNumber(farm.area)}</td>
        <td>${escapeHtml(farm.owner)}</td>
        <td>
          <div class="table__actions">
            <button class="btn btn--primary btn--sm" type="button"
                    data-action="edit" data-entity="farm"
                    data-id="${escapeHtml(farm.id)}">Sửa</button>
            ${
              canDelete()
                ? `<button class="btn btn--danger btn--sm" type="button"
                    data-action="delete" data-entity="farm"
                    data-id="${escapeHtml(farm.id)}">Xoá</button>`
                : ""
            }
          </div>
        </td>
      </tr>`
    )
    .join("");

  $("farm-empty").hidden = farms.length > 0;
  renderStats();
}

function renderFarmOptions() {
  const select = $("batch-farm-id");
  if (!select) return;
  const selected = select.value;

  if (farms.length === 0) {
    select.innerHTML = '<option value="">— Chưa có vùng trồng, hãy thêm ở mục 1 —</option>';
    select.disabled = true;
    return;
  }

  select.disabled = false;
  select.innerHTML = farms
    .map(
      (farm) =>
        `<option value="${escapeHtml(farm.id)}">#${escapeHtml(farm.id)} — ${escapeHtml(farm.name)}</option>`
    )
    .join("");

  if (selected && farms.some((farm) => String(farm.id) === selected)) {
    select.value = selected;
  }
}

function farmSubmitLabel() {
  return editingFarmId === null ? "Lưu thửa đất canh tác" : "Cập nhật thửa đất";
}

async function handleFarmSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const payload = {
    name: $("farm-name").value.trim(),
    location: $("farm-location").value.trim(),
    area: Number($("farm-area").value),
    owner: $("farm-owner").value.trim(),
  };

  const isEditing = editingFarmId !== null;
  const button = $("farm-submit");
  setButtonLoading(button, true, "Đang lưu…", farmSubmitLabel());

  try {
    if (isEditing) {
      const updated = await apiRequest(`/farms/${editingFarmId}`, { method: "PUT", body: payload });
      toast(`Đã cập nhật vùng trồng #${updated.id}: ${updated.name}`, "success");
    } else {
      const created = await apiRequest("/farms", { method: "POST", body: payload });
      toast(`Thêm thành công vùng trồng #${created.id}: ${created.name}`, "success");
    }
    resetFarmForm();
    await loadFarms();
    await loadBatches();
    $("farm-name").focus();
  } catch (error) {
    toast(`${isEditing ? "Cập nhật" : "Thêm"} vùng trồng thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(button, false, "Đang lưu…", farmSubmitLabel());
  }
}

function resetFarmForm() {
  editingFarmId = null;
  $("farm-form").reset();
  $("farm-form-mode").hidden = true;
  $("farm-cancel").hidden = true;
  $("farm-submit").textContent = farmSubmitLabel();
}

function startEditFarm(farmId) {
  const farm = farms.find((item) => item.id === farmId);
  if (!farm) {
    toast(`Không tìm thấy vùng trồng #${farmId} trong dữ liệu đang hiển thị.`, "error");
    return;
  }

  editingFarmId = farm.id;
  $("farm-name").value = farm.name;
  $("farm-location").value = farm.location;
  $("farm-area").value = farm.area;
  $("farm-owner").value = farm.owner;

  const mode = $("farm-form-mode");
  mode.textContent = `Đang sửa vùng trồng #${farm.id} — ${farm.name}. Bấm "Cập nhật thửa đất" để lưu.`;
  mode.hidden = false;
  $("farm-cancel").hidden = false;
  $("farm-submit").textContent = farmSubmitLabel();

  renderFarms();
  $("farm-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("farm-name").focus();
}

async function deleteFarm(farmId) {
  const farm = farms.find((item) => item.id === farmId);
  const label = farm ? `#${farm.id} — ${farm.name}` : `#${farmId}`;
  const childCount = batches.filter((batch) => batch.farm_id === farmId).length;

  const question =
    `Xoá vùng trồng ${label}?` +
    (childCount > 0 ? `\n${childCount} lô nông sản của vùng này cũng bị xoá theo.` : "") +
    "\nHành động này không thể hoàn tác.";
  if (!window.confirm(question)) {
    return;
  }

  try {
    const result = await apiRequest(`/farms/${farmId}`, { method: "DELETE" });
    toast(result && result.message ? result.message : `Đã xoá vùng trồng #${farmId}.`, "success");

    if (editingFarmId === farmId) {
      resetFarmForm();
    }
    await loadFarms();
    await loadBatches();
    await loadHandovers();
    await loadEvents();
  } catch (error) {
    toast(`Xoá vùng trồng thất bại: ${error.message}`, "error");
  }
}

/* ------------------------------------------------------ 8. Lô nông sản --- */
async function loadBatches() {
  try {
    const data = await apiRequest("/batches");
    batches = Array.isArray(data) ? data : [];
    renderBatches();
    renderBatchOptionsForHandover();
  } catch (error) {
    toast(`Không tải được danh sách lô nông sản: ${error.message}`, "error");
  }
}

function farmLabel(farmId) {
  const farm = farms.find((item) => item.id === farmId);
  return farm ? farm.name : `#${farmId}`;
}

function renderBatches() {
  $("batch-table-body").innerHTML = batches
    .map(
      (batch) => `
      <tr class="${batch.id === editingBatchId ? "is-editing" : ""}">
        <td class="id-cell">#${escapeHtml(batch.id)}</td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td><strong>${escapeHtml(batch.product_name)}</strong></td>
        <td class="is-right font-mono">${formatNumber(batch.quantity)}</td>
        <td>${escapeHtml(formatDate(batch.harvest_date))}</td>
        <td>
          <span class="owner-pill owner-pill--giver">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            ${escapeHtml(batch.current_owner || "Hộ nông dân")}
          </span>
        </td>
        <td>
          <div class="table__actions">
            <button class="btn btn--primary btn--sm" type="button"
                    data-action="edit" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}">Sửa</button>
            ${
              canDelete()
                ? `<button class="btn btn--danger btn--sm" type="button"
                    data-action="delete" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}">Xoá</button>`
                : ""
            }
          </div>
        </td>
      </tr>`
    )
    .join("");

  $("batch-empty").hidden = batches.length > 0;
  renderStats();
}

function batchSubmitLabel() {
  return editingBatchId === null ? "Ghi nhận lô thu hoạch" : "Cập nhật lô thu hoạch";
}

async function handleBatchSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const farmSelect = $("batch-farm-id");
  if (!farmSelect.value) {
    toast("Chưa có vùng trồng nào. Hãy thêm vùng trồng ở mục 1 trước khi tạo lô.", "error");
    return;
  }

  const payload = {
    farm_id: Number(farmSelect.value),
    product_name: $("batch-product-name").value.trim(),
    quantity: Number($("batch-quantity").value),
    harvest_date: $("batch-harvest-date").value,
  };

  const isEditing = editingBatchId !== null;
  const button = $("batch-submit");
  setButtonLoading(button, true, "Đang lưu…", batchSubmitLabel());

  try {
    if (isEditing) {
      const updated = await apiRequest(`/batches/${editingBatchId}`, { method: "PUT", body: payload });
      toast(`Đã cập nhật lô #${updated.id} "${updated.product_name}"`, "success");
    } else {
      const created = await apiRequest("/batches", { method: "POST", body: payload });
      toast(
        `Tạo thành công lô #${created.id} "${created.product_name}" (sự kiện đã ghi theo T-25)`,
        "success"
      );
    }
    resetBatchForm();
    farmSelect.value = String(payload.farm_id);
    await loadBatches();
    await loadEvents();
  } catch (error) {
    toast(`${isEditing ? "Cập nhật" : "Tạo"} lô nông sản thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(button, false, "Đang lưu…", batchSubmitLabel());
  }
}

function resetBatchForm() {
  editingBatchId = null;
  $("batch-form").reset();
  $("batch-form-mode").hidden = true;
  $("batch-cancel").hidden = true;
  $("batch-submit").textContent = batchSubmitLabel();
}

function startEditBatch(batchId) {
  const batch = batches.find((item) => item.id === batchId);
  if (!batch) {
    toast(`Không tìm thấy lô nông sản #${batchId} trong dữ liệu đang hiển thị.`, "error");
    return;
  }

  editingBatchId = batch.id;
  $("batch-farm-id").value = String(batch.farm_id);
  $("batch-product-name").value = batch.product_name;
  $("batch-quantity").value = batch.quantity;
  $("batch-harvest-date").value = batch.harvest_date;

  const mode = $("batch-form-mode");
  mode.textContent = `Đang sửa lô #${batch.id} — ${batch.product_name}. Bấm "Cập nhật lô thu hoạch" để lưu.`;
  mode.hidden = false;
  $("batch-cancel").hidden = false;
  $("batch-submit").textContent = batchSubmitLabel();

  renderBatches();
  $("batch-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("batch-product-name").focus();
}

async function deleteBatch(batchId) {
  const batch = batches.find((item) => item.id === batchId);
  const label = batch ? `#${batch.id} — ${batch.product_name}` : `#${batchId}`;

  if (!window.confirm(`Xoá lô nông sản ${label}?\nHành động này không thể hoàn tác.`)) {
    return;
  }

  try {
    const result = await apiRequest(`/batches/${batchId}`, { method: "DELETE" });
    toast(result && result.message ? result.message : `Đã xoá lô nông sản #${batchId}.`, "success");

    if (editingBatchId === batchId) {
      resetBatchForm();
    }
    await loadBatches();
    await loadHandovers();
    await loadEvents();
  } catch (error) {
    toast(`Xoá lô nông sản thất bại: ${error.message}`, "error");
  }
}

/* -------------------------------------------- 9. Bàn giao lô hàng (T-33) --- */
function renderBatchOptionsForHandover() {
  const select = $("handover-batch-id");
  if (!select) return;

  if (batches.length === 0) {
    select.innerHTML = '<option value="">— Chưa có lô nông sản nào để bàn giao —</option>';
    select.disabled = true;
    return;
  }

  select.disabled = false;
  select.innerHTML = batches
    .map(
      (b) =>
        `<option value="${escapeHtml(b.id)}">Lô #${escapeHtml(b.id)}: ${escapeHtml(b.product_name)} (${formatNumber(b.quantity)} kg) — Chủ sở hữu: ${escapeHtml(b.current_owner || "Hộ nông dân")}</option>`
    )
    .join("");
}

async function loadHandovers() {
  try {
    const data = await apiRequest("/handovers");
    handovers = Array.isArray(data) ? data : [];
    renderHandovers();
  } catch (error) {
    toast(`Không tải được danh sách bàn giao: ${error.message}`, "error");
  }
}

function renderHandovers() {
  const container = $("handover-table-body");
  if (!container) return;

  container.innerHTML = handovers
    .map((h) => {
      let statusBadge = "";
      if (h.status === "pending") {
        statusBadge = `<span class="status-badge status-badge--pending"><span class="live-dot" style="background:#d97706;" aria-hidden="true"></span> Chờ xử lý</span>`;
      } else if (h.status === "accepted") {
        statusBadge = `<span class="status-badge status-badge--accepted"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Đã nhận</span>`;
      } else {
        statusBadge = `<span class="status-badge status-badge--rejected"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Từ chối</span>`;
      }

      const batchObj = batches.find((b) => b.id === h.batch_id);
      const batchLabel = batchObj ? `#${h.batch_id} (${batchObj.product_name})` : `#${h.batch_id}`;

      let ownerText = "";
      if (h.status === "pending") {
        ownerText = `<span class="owner-pill owner-pill--giver">Vẫn thuộc bên giao: <strong>${escapeHtml(h.sender_name)}</strong></span>`;
      } else if (h.status === "accepted") {
        ownerText = `<span class="owner-pill" style="background:#d1fae5; color:#065f46;">Đã chuyển sang: <strong>${escapeHtml(h.receiver_name)}</strong></span>`;
      } else {
        ownerText = `<span class="owner-pill">Giữ nguyên: <strong>${escapeHtml(h.sender_name)}</strong></span>`;
      }

      const actions =
        h.status === "pending"
          ? `<div class="table__actions">
              <button class="btn btn--success btn--sm" type="button"
                      data-action="accept-handover" data-id="${escapeHtml(h.id)}">Tiếp nhận</button>
              <button class="btn btn--danger btn--sm" type="button"
                      data-action="reject-handover" data-id="${escapeHtml(h.id)}">Từ chối</button>
            </div>`
          : `<span style="font-size:0.8rem; color:var(--slate-400); font-weight:600;">Đã hoàn tất</span>`;

      return `
      <tr>
        <td class="id-cell">#${escapeHtml(h.id)}</td>
        <td><strong>${escapeHtml(batchLabel)}</strong></td>
        <td>${escapeHtml(h.sender_name)}</td>
        <td><strong>${escapeHtml(h.receiver_name)}</strong></td>
        <td>${statusBadge}</td>
        <td>${ownerText}</td>
        <td class="font-mono">${escapeHtml(formatDateTime(h.created_at))}</td>
        <td class="is-center">${actions}</td>
      </tr>`;
    })
    .join("");

  $("handover-empty").hidden = handovers.length > 0;
  renderStats();
}

async function handleHandoverSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const batchSelect = $("handover-batch-id");
  if (!batchSelect.value) {
    toast("Chưa chọn lô nông sản nào để bàn giao.", "error");
    return;
  }

  const payload = {
    batch_id: Number(batchSelect.value),
    receiver_name: $("handover-receiver-name").value.trim(),
    notes: $("handover-notes").value.trim() || null,
  };

  const button = $("handover-submit");
  setButtonLoading(button, true, "Đang khởi tạo…", "Khởi tạo yêu cầu bàn giao (Trạng thái Chờ)");

  try {
    const created = await apiRequest("/handovers", { method: "POST", body: payload });
    toast(
      `Khởi tạo bàn giao #${created.id} thành công (Lô #${created.batch_id} vẫn thuộc bên giao trong thời gian chờ).`,
      "success"
    );
    form.reset();
    renderBatchOptionsForHandover();
    await loadHandovers();
    await loadBatches();
    await loadEvents();
  } catch (error) {
    // Nếu cố tình tạo lần 2 khi đang có bàn giao chờ -> thông báo lỗi chi tiết theo đúng DoD
    toast(error.message, "error");
  } finally {
    setButtonLoading(button, false, "Đang khởi tạo…", "Khởi tạo yêu cầu bàn giao (Trạng thái Chờ)");
  }
}

async function acceptHandover(handoverId) {
  if (!window.confirm(`Xác nhận tiếp nhận bàn giao #${handoverId}?\nQuyền quản lý lô hàng sẽ chính thức chuyển sang bên nhận.`)) {
    return;
  }

  try {
    const result = await apiRequest(`/handovers/${handoverId}/accept`, {
      method: "POST",
      body: { notes: "Đã tiếp nhận đầy đủ số lượng và kiểm tra đạt tiêu chuẩn." },
    });
    toast(`Tiếp nhận bàn giao #${result.id} thành công! Quyền quản lý lô hàng đã được cập nhật.`, "success");
    await loadHandovers();
    await loadBatches();
    await loadEvents();
  } catch (error) {
    toast(`Lỗi tiếp nhận bàn giao: ${error.message}`, "error");
  }
}

async function rejectHandover(handoverId) {
  const reason = window.prompt("Nhập lý do từ chối tiếp nhận bàn giao:", "Hàng không đạt yêu cầu tiêu chuẩn chất lượng");
  if (reason === null) return;

  try {
    const result = await apiRequest(`/handovers/${handoverId}/reject`, {
      method: "POST",
      body: { notes: reason || "Từ chối tiếp nhận" },
    });
    toast(`Đã từ chối bàn giao #${result.id}. Lô hàng vẫn thuộc quyền quản lý của bên giao.`, "info");
    await loadHandovers();
    await loadBatches();
    await loadEvents();
  } catch (error) {
    toast(`Lỗi từ chối bàn giao: ${error.message}`, "error");
  }
}

/* -------------------------------------- 10. Nhật ký sự kiện (Task T-25) --- */
async function loadEvents() {
  try {
    const data = await apiRequest("/events");
    events = Array.isArray(data) ? data : [];
    renderEvents();
  } catch (error) {
    toast(`Không tải được nhật ký sự kiện: ${error.message}`, "error");
  }
}

function renderEvents() {
  const container = $("event-table-body");
  if (!container) return;

  container.innerHTML = events
    .map((e) => {
      let badge = "";
      if (e.event_type === "HANDOVER_PENDING") {
        badge = `<span class="event-badge event-badge--pending">HANDOVER_PENDING</span>`;
      } else if (e.event_type === "HANDOVER_ACCEPTED") {
        badge = `<span class="event-badge event-badge--accepted">HANDOVER_ACCEPTED</span>`;
      } else if (e.event_type === "HANDOVER_REJECTED") {
        badge = `<span class="event-badge event-badge--rejected">HANDOVER_REJECTED</span>`;
      } else {
        badge = `<span class="event-badge event-badge--created">${escapeHtml(e.event_type)}</span>`;
      }

      return `
      <tr>
        <td class="id-cell">#${escapeHtml(e.id)}</td>
        <td class="font-mono">#${escapeHtml(e.batch_id)}</td>
        <td>${badge}</td>
        <td><strong>${escapeHtml(e.actor_name || "Hệ thống")}</strong></td>
        <td>${escapeHtml(e.description || "—")}</td>
        <td class="font-mono">${escapeHtml(formatDateTime(e.created_at))}</td>
      </tr>`;
    })
    .join("");

  $("event-empty").hidden = events.length > 0;
}

/* ------------------------------------------------------ 11. Thống kê --- */
function renderStats() {
  const totalYield = batches.reduce((sum, batch) => sum + Number(batch.quantity || 0), 0);

  $("stat-farms").textContent = formatNumber(farms.length);
  $("stat-batches").textContent = formatNumber(batches.length);
  $("stat-yield").textContent = formatNumber(Math.round(totalYield * 100) / 100);
  if ($("stat-handovers")) {
    $("stat-handovers").textContent = formatNumber(handovers.length);
  }
}

/* ------------------------------------------ 12. Tài khoản (chỉ admin) --- */
async function loadUsers() {
  try {
    const data = await apiRequest("/users");
    users = Array.isArray(data) ? data : [];
    renderUsers();
  } catch (error) {
    users = [];
    renderUsers();
    toast(`Không tải được danh sách tài khoản: ${error.message}`, "error");
  }
}

function renderUsers() {
  $("user-table-body").innerHTML = users
    .map(
      (user) => `
      <tr>
        <td class="id-cell">#${escapeHtml(user.id)}</td>
        <td><strong>${escapeHtml(user.username)}</strong></td>
        <td><code>${escapeHtml(user.role)}</code></td>
        <td><span class="status-badge status-badge--accepted">Đã băm SHA-256</span></td>
      </tr>`
    )
    .join("");

  $("user-empty").hidden = users.length > 0;
}

/* --------------------------------------------------------- 13. Sự kiện --- */
function bindEvents() {
  $("login-form").addEventListener("submit", handleLoginSubmit);
  $("btn-logout").addEventListener("click", handleLogout);
  $("farm-form").addEventListener("submit", handleFarmSubmit);
  $("batch-form").addEventListener("submit", handleBatchSubmit);
  if ($("handover-form")) {
    $("handover-form").addEventListener("submit", handleHandoverSubmit);
  }
  $("farm-cancel").addEventListener("click", () => cancelEdit("farm"));
  $("batch-cancel").addEventListener("click", () => cancelEdit("batch"));
  $("btn-reload").addEventListener("click", () => reloadAll());

  $("farm-table-body").addEventListener("click", handleTableAction);
  $("batch-table-body").addEventListener("click", handleTableAction);

  const handoverBody = $("handover-table-body");
  if (handoverBody) {
    handoverBody.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = Number(btn.dataset.id);
      const action = btn.dataset.action;
      if (action === "accept-handover") {
        acceptHandover(id);
      } else if (action === "reject-handover") {
        rejectHandover(id);
      }
    });
  }
}

function cancelEdit(entity) {
  if (entity === "farm") {
    resetFarmForm();
    renderFarms();
    toast("Đã huỷ chế độ sửa vùng trồng.", "info");
    return;
  }

  resetBatchForm();
  renderBatches();
  toast("Đã huỷ chế độ sửa lô nông sản.", "info");
}

function handleTableAction(event) {
  const button = event.target.closest("button[data-action]");
  if (button === null) {
    return;
  }

  const id = Number(button.dataset.id);
  const entity = button.dataset.entity;
  const action = button.dataset.action;

  if (action === "edit") {
    if (entity === "farm") {
      startEditFarm(id);
    } else {
      startEditBatch(id);
    }
    return;
  }

  if (action === "delete") {
    if (!canDelete()) {
      toast("Chỉ tài khoản admin được phép xoá dữ liệu.", "error");
      return;
    }
    if (entity === "farm") {
      deleteFarm(id);
    } else {
      deleteBatch(id);
    }
  }
}

async function loadAllData() {
  await checkHealth();
  await loadFarms();
  await loadBatches();
  await loadHandovers();
  await loadEvents();
  if (session !== null && session.role === ROLE_ADMIN) {
    await loadUsers();
  }
}

async function reloadAll({ silent = false } = {}) {
  const button = $("btn-reload");
  setButtonLoading(button, true, "Đang tải…", "Làm mới");

  await loadAllData();

  setButtonLoading(button, false, "Đang tải…", "Làm mới");
  if (!silent) {
    toast(`Đã đồng bộ dữ liệu: ${farms.length} vùng trồng, ${batches.length} lô, ${handovers.length} bàn giao.`, "info");
  }
}

/* -------------------------------------------------------- 14. Khởi động --- */
async function init() {
  bindEvents();
  resetFarmForm();
  resetBatchForm();
  await checkHealth();

  const saved = restoreSession();
  if (saved !== null) {
    try {
      const data = await requestLogin(saved.username, saved.password);
      startSession({ username: data.username, role: data.role, password: saved.password });
      toast(`Đã khôi phục phiên: ${data.username} (role: ${data.role}).`, "info");
      await reloadAll({ silent: true });
      return;
    } catch (error) {
      clearSession();
    }
  }

  applySessionToUi();
  $("login-username").focus();
}

document.addEventListener("DOMContentLoaded", init);
