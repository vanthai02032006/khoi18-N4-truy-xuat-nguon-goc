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
  const parts = String(value).split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : String(value);
}

const TOAST_DURATION_MS = 4500;

/** Hiển thị thông báo ở góc phải trên: type = "success" | "error" | "info". */
function toast(message, type = "info") {
  const container = $("toast-container");
  const element = document.createElement("div");
  element.className = `toast toast--${type}`;
  element.setAttribute("role", type === "error" ? "alert" : "status");
  element.textContent = message;
  container.appendChild(element);
  window.setTimeout(() => element.remove(), TOAST_DURATION_MS);
}

/** Bật/tắt trạng thái "đang gửi" của nút submit (tránh bấm 2 lần). */
function setButtonLoading(button, isLoading, loadingText, idleText) {
  button.disabled = isLoading;
  button.textContent = isLoading ? loadingText : idleText;
}

/* -------------------------------------------------------- 3. Gọi API --- */
/**
 * Gọi API backend và trả về dữ liệu JSON.
 * Mặc định gửi kèm tài khoản đang đăng nhập (xác thực HTTP Basic);
 * truyền `auth: false` cho request không cần xác thực (VD: đăng nhập).
 * Ném Error với thông điệp tiếng Việt dễ đọc nếu request thất bại.
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

  // Lỗi nghiệp vụ: 404 farm không tồn tại, 500 lỗi database...
  if (typeof detail === "string") {
    return detail;
  }

  // Lỗi validate của Pydantic (422): detail là mảng các lỗi.
  if (Array.isArray(detail)) {
    return detail
      .map((item) => `${(item.loc || []).join(".")}: ${item.msg}`)
      .join(" | ");
  }

  return `Yêu cầu thất bại (HTTP ${status}).`;
}

/* ------------------------------------------------------- 4. Trạng thái --- */
// Dữ liệu đang hiển thị trên giao diện.
let farms = [];
let batches = [];
let users = [];

// ID bản ghi đang được SỬA trên form (null = form đang ở chế độ "thêm mới").
// Sprint 5: bấm nút "Sửa" ở bảng -> form phía trên đổ sẵn dữ liệu và nút submit
// gọi PUT thay vì POST.
let editingFarmId = null;
let editingBatchId = null;

// Phiên đăng nhập hiện tại: { username, role, password } hoặc null (chưa đăng nhập).
// Sprint 4 không dùng JWT: client giữ lại thông tin đăng nhập để gửi kèm header
// `Authorization: Basic ...` trong mỗi request.
let session = null;

/* --------------------------------------------------------- 5. Đăng nhập --- */
/**
 * Mã hoá chuỗi "username:password" sang Base64 theo chuẩn HTTP Basic.
 * Dùng TextEncoder để hỗ trợ tiếng Việt (btoa chỉ nhận ký tự Latin-1).
 */
function encodeBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

/** Header xác thực của tài khoản đang đăng nhập (rỗng nếu chưa đăng nhập). */
function authHeader() {
  if (session === null) {
    return {};
  }
  const token = encodeBase64(`${session.username}:${session.password}`);
  return { Authorization: `Basic ${token}` };
}

/** POST /auth/login - kiểm tra tài khoản; ném Error nếu sai (backend trả 401). */
function requestLogin(username, password) {
  return apiRequest("/auth/login", {
    method: "POST",
    body: { username, password },
    auth: false, // request đăng nhập không gửi kèm header của phiên cũ
  });
}

/** Lưu phiên đăng nhập vào bộ nhớ + sessionStorage (giữ được khi F5 trong tab). */
function startSession({ username, role, password }) {
  session = { username, role, password };
  window.sessionStorage.setItem(
    SESSION_STORAGE_KEY,
    JSON.stringify({ username, password })
  );
  applySessionToUi();
}

/** Đọc phiên đăng nhập đã lưu trong tab; trả null nếu chưa có hoặc dữ liệu hỏng. */
function restoreSession() {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    const saved = raw ? JSON.parse(raw) : null;
    if (saved && saved.username && saved.password) {
      return saved;
    }
  } catch (error) {
    // Dữ liệu lưu không hợp lệ -> coi như chưa đăng nhập.
  }
  return null;
}

/** Xoá phiên đăng nhập khỏi bộ nhớ và sessionStorage. */
function clearSession() {
  session = null;
  window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
}

/**
 * Cập nhật giao diện theo trạng thái đăng nhập và vai trò (role):
 * - chưa đăng nhập: chỉ hiện màn hình login;
 * - farmer: hiện chức năng quản lý nông sản (vùng trồng, lô nông sản);
 * - admin: hiện toàn bộ, thêm mục quản trị tài khoản.
 * Đây chỉ là phân quyền ở giao diện; backend vẫn kiểm tra lại bằng
 * `require_farmer` / `require_admin` nên gọi API trái phép sẽ nhận 401/403.
 */
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
  $("cold-chain-card").hidden = !isLoggedIn;
  $("integrity-card").hidden = !isLoggedIn;
  $("backup-card").hidden = !isAdmin;
  $("invitation-card").hidden = !isAdmin;

  const coldChainAdmin = $("coldchain-admin-actions");
  if (coldChainAdmin) coldChainAdmin.hidden = !isAdmin;

  const integrityAdmin = $("integrity-admin-actions");
  if (integrityAdmin) integrityAdmin.hidden = !isAdmin;
}


/**
 * Quyền xoá dữ liệu ở giao diện: **chỉ admin** (Sprint 5).
 * Farmer dùng giao diện sẽ không thấy nút Xoá; nếu cố gọi API xoá thì backend
 * trả `403 Forbidden` (`require_admin`) - đây chỉ là lớp bảo vệ ở UI.
 */
function canDelete() {
  return session !== null && session.role === ROLE_ADMIN;
}

/** Xử lý submit form đăng nhập -> POST /auth/login. */
async function handleLoginSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const username = $("login-username").value.trim();
  const password = $("login-password").value; // không trim mật khẩu

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

/** Đăng xuất: xoá phiên, xoá dữ liệu đang hiển thị và quay về màn hình login. */
function handleLogout() {
  const username = session ? session.username : "";
  clearSession();

  farms = [];
  batches = [];
  users = [];
  resetFarmForm(); // bỏ chế độ sửa (nếu đang sửa) trước khi vẽ lại bảng rỗng
  resetBatchForm();
  renderFarms();
  renderFarmOptions();
  renderBatches();
  renderUsers();

  applySessionToUi();
  toast(username ? `Đã đăng xuất tài khoản ${username}.` : "Đã đăng xuất.", "info");
  $("login-username").focus();
}

/* ------------------------------------------- 6. Kiểm tra backend sống --- */
async function checkHealth() {
  const badge = $("health-badge");
  try {
    await apiRequest("/health");
    if (badge) {
      badge.hidden = true; // Ẩn badge kỹ thuật Backend: running theo yêu cầu
    }
  } catch (error) {
    if (badge) {
      badge.hidden = false;
      badge.textContent = "Mất kết nối máy chủ";
      badge.className = "status-pill status-pill--error";
    }
    toast("Không thể kết nối đến máy chủ: " + error.message, "error");
  }
}

/* ------------------------------------------------------- 7. Vùng trồng --- */
/** GET /farms -> cập nhật bảng danh sách + select vùng trồng của form lô. */
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

/** Vẽ bảng danh sách vùng trồng (kèm cột "Thao tác": Sửa/Xoá). */
function renderFarms() {
  $("farm-table-body").innerHTML = farms
    .map(
      (farm) => `
      <tr class="${farm.id === editingFarmId ? "is-editing" : ""}">
        <td class="id-cell">${escapeHtml(farm.id)}</td>
        <td>${escapeHtml(farm.name)}</td>
        <td>${escapeHtml(farm.location)}</td>
        <td class="is-right">${formatNumber(farm.area)}</td>
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
  renderStats(); // thẻ "Tổng vùng trồng" lấy từ mảng `farms`
}

/** Đổ danh sách vùng trồng vào select `farm_id` của form tạo lô. */
function renderFarmOptions() {
  const select = $("batch-farm-id");
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

  // Giữ lại lựa chọn cũ nếu vùng trồng đó vẫn còn.
  if (selected && farms.some((farm) => String(farm.id) === selected)) {
    select.value = selected;
  }
}

/** Nhãn nút submit form vùng trồng theo chế độ hiện tại (thêm mới / sửa). */
function farmSubmitLabel() {
  return editingFarmId === null ? "Thêm vùng trồng" : "Cập nhật vùng trồng";
}

/**
 * Xử lý submit form vùng trồng:
 * - chế độ thêm mới (`editingFarmId === null`) -> POST /farms;
 * - chế độ sửa (đã bấm nút "Sửa" ở bảng)       -> PUT /farms/{id}.
 */
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
    resetFarmForm(); // về lại chế độ "thêm mới"
    await loadFarms(); // bảng lô nông sản cũng hiển thị tên vùng trồng -> tải lại
    await loadBatches();
    $("farm-name").focus();
  } catch (error) {
    toast(`${isEditing ? "Cập nhật" : "Thêm"} vùng trồng thất bại: ${error.message}`, "error");
  } finally {
    // `farmSubmitLabel()` đọc `editingFarmId` hiện tại -> sau khi lưu xong form
    // đã về chế độ "thêm mới" nên nhãn nút cũng trở lại bình thường.
    setButtonLoading(button, false, "Đang lưu…", farmSubmitLabel());
  }
}

/** Đưa form vùng trồng về chế độ "thêm mới" (bỏ dữ liệu đang sửa). */
function resetFarmForm() {
  editingFarmId = null;
  $("farm-form").reset();
  $("farm-form-mode").hidden = true;
  $("farm-cancel").hidden = true;
  $("farm-submit").textContent = farmSubmitLabel();
}

/**
 * Bấm nút "Sửa" ở bảng -> đổ dữ liệu vùng trồng lên form và chuyển sang chế độ
 * sửa (nút submit sẽ gọi ``PUT /farms/{id}``).
 */
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
  mode.textContent = `Đang sửa vùng trồng #${farm.id} — ${farm.name}. Bấm "Cập nhật vùng trồng" để lưu.`;
  mode.hidden = false;
  $("farm-cancel").hidden = false;
  $("farm-submit").textContent = farmSubmitLabel();

  renderFarms(); // tô nền dòng đang sửa trong bảng
  $("farm-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("farm-name").focus();
}

/**
 * Xoá vùng trồng (chỉ admin) -> ``DELETE /farms/{id}``.
 * Backend xoá kèm mọi lô nông sản của vùng đó nên giao diện phải tải lại cả
 * hai bảng; số lô bị xoá kèm được backend trả về trong ``deleted_batches``.
 */
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
      resetFarmForm(); // vùng trồng đang sửa đã bị xoá -> form về chế độ thêm mới
    }
    await loadFarms();
    await loadBatches(); // các lô của vùng trồng vừa xoá cũng biến mất
  } catch (error) {
    toast(`Xoá vùng trồng thất bại: ${error.message}`, "error");
  }
}

/* ------------------------------------------------------ 8. Lô nông sản --- */
/** Hàm debounce chống spam request lên server khi người dùng gõ phím liên tục (SCRUM-50). */
function debounce(fn, delay = 300) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

// Trạng thái tìm kiếm & bộ lọc của bảng lô nông sản
let batchSearchQuery = "";
let batchFilterProduct = "";
let knownProductNames = new Set();

/** GET /batches -> cập nhật bảng danh sách lô kèm search, filter và phân quyền SCRUM-70. */
async function loadBatches() {
  const loadingEl = $("batch-loading");
  const errorEl = $("batch-error");
  const emptyEl = $("batch-empty");
  const tableContainer = $("batch-table-body");

  // Bật trạng thái Loading (SCRUM-50)
  if (loadingEl) loadingEl.hidden = false;
  if (errorEl) errorEl.hidden = true;
  if (emptyEl) emptyEl.hidden = true;

  const params = new URLSearchParams();
  if (batchSearchQuery.trim()) {
    params.set("search", batchSearchQuery.trim());
  }
  if (batchFilterProduct.trim()) {
    params.set("product_name", batchFilterProduct.trim());
  }

  const queryStr = params.toString() ? `?${params.toString()}` : "";

  try {
    const data = await apiRequest(`/batches${queryStr}`);
    batches = Array.isArray(data) ? data : [];

    // Tích luỹ danh sách các loại sản phẩm để đổ vào bộ lọc
    batches.forEach((b) => {
      if (b.product_name) knownProductNames.add(b.product_name.trim());
    });
    renderProductFilterOptions();

    renderBatches();

    // Cập nhật trạng thái hiển thị nút đặt lại bộ lọc
    const resetBtn = $("btn-reset-filters");
    const clearSearchBtn = $("batch-search-clear");
    const hasFilters = Boolean(batchSearchQuery.trim() || batchFilterProduct.trim());
    if (resetBtn) resetBtn.hidden = !hasFilters;
    if (clearSearchBtn) clearSearchBtn.hidden = !batchSearchQuery.trim();

  } catch (error) {
    if (errorEl) {
      errorEl.hidden = false;
      const desc = $("batch-error-desc");
      if (desc) desc.textContent = error.message;
    }
    tableContainer.innerHTML = "";
    toast(`Không tải được danh sách lô nông sản: ${error.message}`, "error");
  } finally {
    if (loadingEl) loadingEl.hidden = true;
  }
}

/** Đổ các tuỳ chọn sản phẩm vào select bộ lọc (SCRUM-50). */
function renderProductFilterOptions() {
  const select = $("batch-filter-product");
  if (!select) return;

  const currentVal = select.value;
  const sortedProducts = Array.from(knownProductNames).sort();

  select.innerHTML = '<option value="">— Tất cả sản phẩm nông sản —</option>' +
    sortedProducts
      .map(
        (p) => `<option value="${escapeHtml(p)}" ${p === currentVal ? "selected" : ""}>${escapeHtml(p)}</option>`
      )
      .join("");

  if (currentVal && knownProductNames.has(currentVal)) {
    select.value = currentVal;
  }
}

/** Nhãn vùng trồng cho bảng lô (dùng lại dữ liệu đã tải từ GET /farms). */
function farmLabel(farmId) {
  const farm = farms.find((item) => item.id === farmId);
  return farm ? `#${farmId} — ${farm.name}` : `#${farmId}`;
}

/** Vẽ bảng danh sách lô nông sản với 8 cột chi tiết và hỗ trợ click xem chi tiết (SCRUM-50). */
function renderBatches() {
  const tbody = $("batch-table-body");
  if (!tbody) return;

  tbody.innerHTML = batches
    .map(
      (batch) => `
      <tr class="${batch.id === editingBatchId ? "is-editing" : ""}" data-batch-id="${escapeHtml(batch.id)}">
        <td class="id-cell font-mono">#${escapeHtml(batch.id)}</td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td><strong>${escapeHtml(batch.product_name)}</strong></td>
        <td class="is-right">${formatNumber(batch.quantity)}</td>
        <td class="is-right text-safe font-mono"><strong>${formatNumber(batch.remaining_quantity ?? batch.quantity)}</strong></td>
        <td><span class="badge-pill">${escapeHtml(batch.organization_id || "Mặc định")}</span></td>
        <td>${escapeHtml(formatDate(batch.harvest_date))}</td>
        <td>
          <div class="table__actions">
            <button class="btn btn--secondary btn--sm" type="button"
                    data-action="view" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}" title="Xem chi tiết phả hệ">Xem</button>
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

  const emptyEl = $("batch-empty");
  if (emptyEl) {
    emptyEl.hidden = batches.length > 0;
    const titleEl = $("batch-empty-title");
    const descEl = $("batch-empty-desc");
    if (batchSearchQuery.trim() || batchFilterProduct.trim()) {
      if (titleEl) titleEl.textContent = "Không tìm thấy lô nào phù hợp";
      if (descEl) descEl.textContent = `Không có kết quả khớp với từ khoá "${batchSearchQuery}" hoặc bộ lọc được chọn.`;
    } else {
      if (titleEl) titleEl.textContent = "Chưa có lô thu hoạch nào";
      if (descEl) descEl.textContent = "Vui lòng ghi nhận lô thu hoạch mới bằng biểu mẫu phía trên.";
    }
  }

  renderStats();
}

/** Nhãn nút submit form lô nông sản theo chế độ hiện tại (tạo mới / sửa). */
function batchSubmitLabel() {
  return editingBatchId === null ? "Ghi nhận lô thu hoạch" : "Cập nhật lô nông sản";
}


/**
 * Xử lý submit form lô nông sản:
 * - chế độ tạo mới (`editingBatchId === null`) -> POST /batches;
 * - chế độ sửa (đã bấm nút "Sửa" ở bảng)       -> PUT /batches/{id}.
 */
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
        `Tạo thành công lô #${created.id} "${created.product_name}" cho vùng trồng #${created.farm_id}`,
        "success"
      );
    }
    resetBatchForm(); // về lại chế độ "tạo mới"
    farmSelect.value = String(payload.farm_id); // giữ lại vùng trồng vừa chọn
    await loadBatches();
  } catch (error) {
    toast(`${isEditing ? "Cập nhật" : "Tạo"} lô nông sản thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(button, false, "Đang lưu…", batchSubmitLabel());
  }
}

/** Đưa form lô nông sản về chế độ "tạo mới" (bỏ dữ liệu đang sửa). */
function resetBatchForm() {
  editingBatchId = null;
  $("batch-form").reset();
  $("batch-form-mode").hidden = true;
  $("batch-cancel").hidden = true;
  $("batch-submit").textContent = batchSubmitLabel();
}

/**
 * Bấm nút "Sửa" ở bảng lô -> đổ dữ liệu lên form và chuyển sang chế độ sửa
 * (nút submit sẽ gọi ``PUT /batches/{id}``).
 */
function startEditBatch(batchId) {
  const batch = batches.find((item) => item.id === batchId);
  if (!batch) {
    toast(`Không tìm thấy lô nông sản #${batchId} trong dữ liệu đang hiển thị.`, "error");
    return;
  }

  editingBatchId = batch.id;
  $("batch-farm-id").value = String(batch.farm_id); // select đã được đổ ở loadFarms()
  $("batch-product-name").value = batch.product_name;
  $("batch-quantity").value = batch.quantity;
  $("batch-harvest-date").value = batch.harvest_date; // API trả sẵn dạng yyyy-MM-dd

  const mode = $("batch-form-mode");
  mode.textContent = `Đang sửa lô #${batch.id} — ${batch.product_name}. Bấm "Cập nhật lô nông sản" để lưu.`;
  mode.hidden = false;
  $("batch-cancel").hidden = false;
  $("batch-submit").textContent = batchSubmitLabel();

  renderBatches(); // tô nền dòng đang sửa trong bảng
  $("batch-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("batch-product-name").focus();
}

/** Xoá lô nông sản (chỉ admin) -> ``DELETE /batches/{id}``. */
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
      resetBatchForm(); // lô đang sửa đã bị xoá -> form về chế độ tạo mới
    }
    await loadBatches();
  } catch (error) {
    toast(`Xoá lô nông sản thất bại: ${error.message}`, "error");
  }
}

/* ------------------------------------------------------- 9. Thống kê --- */
/**
 * Cập nhật 3 thẻ thống kê trên dashboard từ dữ liệu đang hiển thị:
 * - **Tổng vùng trồng**: số phần tử của `farms` (nguồn: `GET /farms`);
 * - **Tổng lô nông sản**: số phần tử của `batches` (nguồn: `GET /batches`);
 * - **Tổng sản lượng (kg)**: cộng `quantity` của mọi lô (làm tròn 2 chữ số).
 *
 * Hàm được gọi lại mỗi khi vẽ xong bảng (`renderFarms` / `renderBatches`) nên
 * số liệu luôn khớp với dữ liệu vừa tải.
 */
function renderStats() {
  const totalYield = batches.reduce((sum, batch) => sum + Number(batch.quantity || 0), 0);

  $("stat-farms").textContent = formatNumber(farms.length);
  $("stat-batches").textContent = formatNumber(batches.length);
  $("stat-yield").textContent = formatNumber(Math.round(totalYield * 100) / 100);
}

/* ------------------------------------------ 10. Tài khoản (chỉ admin) --- */
/** GET /users (chỉ admin) -> cập nhật bảng tài khoản; farmer gọi sẽ nhận 403. */
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

/** Vẽ bảng tài khoản (SCRUM-91: username, email, role, org, trạng thái & thao tác). */
function renderUsers() {
  $("user-table-body").innerHTML = users
    .map((user) => {
      const isActive = user.is_active !== false;
      const statusBadge = isActive
        ? `<span class="badge-pill badge-pill--safe">Đang hoạt động</span>`
        : `<span class="badge-pill badge-pill--danger">Đã vô hiệu</span>`;
      const actionBtn = isActive
        ? `<button class="action-btn-sm action-btn-sm--danger" onclick="toggleMemberStatus(${user.id}, true)">Vô hiệu hoá</button>`
        : `<button class="action-btn-sm action-btn-sm--success" onclick="toggleMemberStatus(${user.id}, false)">Kích hoạt lại</button>`;

      return `
      <tr>
        <td class="id-cell">${escapeHtml(user.id)}</td>
        <td><strong>${escapeHtml(user.username)}</strong></td>
        <td>${escapeHtml(user.email || "—")}</td>
        <td><code>${escapeHtml(user.role)}</code></td>
        <td><span class="badge-pill badge-pill--mono">${escapeHtml(user.organization_id || "—")}</span></td>
        <td>${statusBadge}</td>
        <td>${actionBtn}</td>
      </tr>`;
    })
    .join("");

  $("user-empty").hidden = users.length > 0;
}


/* ------------------------------------- 8b. Chi tiết lô & Phả hệ (SCRUM-50 / SCRUM-70) --- */
/** Tính toán hash SHA-256 canonical của lô nông sản (SCRUM-40). */
async function computeClientBatchHash(batch) {
  try {
    const canonicalObj = {
      farm_id: batch.farm_id,
      harvest_date: String(batch.harvest_date),
      id: batch.id,
      product_name: String(batch.product_name).trim(),
      quantity: Number(batch.quantity),
    };
    const jsonStr = JSON.stringify(canonicalObj, Object.keys(canonicalObj).sort());
    const buffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(jsonStr));
    return Array.from(new Uint8Array(buffer))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch (e) {
    return "0".repeat(64);
  }
}

/** Mở modal chi tiết lô và cây phả hệ truy xuất nguồn gốc (SCRUM-50 / SCRUM-70). */
async function openBatchDetail(batchId) {
  const modal = $("batch-modal");
  if (!modal) return;

  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  const loading = $("modal-batch-loading");
  const content = $("modal-batch-content");
  if (loading) loading.hidden = false;
  if (content) content.hidden = true;
  $("modal-batch-title").textContent = `Thông tin lô nông sản #${batchId}`;

  try {
    const data = await apiRequest(`/batches/${batchId}`);
    $("modal-batch-title").textContent = `Lô Nông Sản #${data.id} — ${data.product_name}`;
    $("modal-batch-id").textContent = `#${data.id}`;
    $("modal-batch-product").textContent = data.product_name;
    $("modal-batch-farm").textContent = farmLabel(data.farm_id);
    $("modal-batch-org").textContent = data.organization_id || "Mặc định (Chưa gán)";
    $("modal-batch-qty").textContent = `${formatNumber(data.quantity)} kg`;
    $("modal-batch-rem").textContent = `${formatNumber(data.remaining_quantity ?? data.quantity)} kg`;
    $("modal-batch-date").textContent = formatDate(data.harvest_date);

    // Cây phả hệ lô mẹ (Parent Batches)
    const parentContainer = $("modal-parent-relations");
    if (parentContainer) {
      if (data.parent_relations && data.parent_relations.length > 0) {
        parentContainer.innerHTML = data.parent_relations
          .map(
            (rel) => `
            <div class="g-item">
              <span>Kế thừa từ <strong>Lô mẹ #${escapeHtml(rel.parent_batch_id)}</strong></span>
              <span class="badge-pill badge-pill--safe">${formatNumber(rel.used_quantity)} kg</span>
            </div>`
          )
          .join("");
      } else {
        parentContainer.innerHTML = '<span class="text-muted">Lô gốc thu hoạch trực tiếp từ thửa đất (Root Batch)</span>';
      }
    }

    // Cây phả hệ lô con (Child Batches)
    const childContainer = $("modal-child-relations");
    if (childContainer) {
      if (data.child_relations && data.child_relations.length > 0) {
        childContainer.innerHTML = data.child_relations
          .map(
            (rel) => `
            <div class="g-item">
              <span>Đã trích gộp vào <strong>Lô con #${escapeHtml(rel.child_batch_id)}</strong></span>
              <span class="badge-pill">${formatNumber(rel.used_quantity)} kg</span>
            </div>`
          )
          .join("");
      } else {
        childContainer.innerHTML = '<span class="text-muted">Chưa có lô con phái sinh</span>';
      }
    }

    // Mã băm kiểm định SHA-256
    const hash = await computeClientBatchHash(data);
    const hashEl = $("modal-batch-hash");
    if (hashEl) hashEl.textContent = hash;

    if (loading) loading.hidden = true;
    if (content) content.hidden = false;
  } catch (error) {
    if (loading) loading.hidden = true;
    toast(`Không xem được chi tiết lô #${batchId}: ${error.message}`, "error");
    closeBatchDetail();
  }
}

/** Đóng modal chi tiết lô nông sản. */
function closeBatchDetail() {
  const modal = $("batch-modal");
  if (modal) {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
  }
}

/* --------------------------------------------------------- 11. Sự kiện --- */
function bindEvents() {
  $("login-form").addEventListener("submit", handleLoginSubmit);
  $("btn-logout").addEventListener("click", handleLogout);
  $("farm-form").addEventListener("submit", handleFarmSubmit);
  $("batch-form").addEventListener("submit", handleBatchSubmit);
  $("farm-cancel").addEventListener("click", () => cancelEdit("farm"));
  $("batch-cancel").addEventListener("click", () => cancelEdit("batch"));
  $("btn-reload").addEventListener("click", () => reloadAll());

  // Cột "Thao tác" và click dòng của 2 bảng
  $("farm-table-body").addEventListener("click", handleTableAction);
  $("batch-table-body").addEventListener("click", (e) => {
    // Nếu bấm vào nút thì handleTableAction xử lý
    if (e.target.closest("button[data-action]")) {
      handleTableAction(e);
      return;
    }
    // Nếu click vào dòng thì mở modal chi tiết lô (SCRUM-50)
    const tr = e.target.closest("tr[data-batch-id]");
    if (tr && tr.dataset.batchId) {
      openBatchDetail(Number(tr.dataset.batchId));
    }
  });

  // Tìm kiếm với Debounce (SCRUM-50)
  const debouncedSearch = debounce(() => {
    loadBatches();
  }, 300);

  const searchInput = $("batch-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      batchSearchQuery = e.target.value;
      const clearBtn = $("batch-search-clear");
      if (clearBtn) clearBtn.hidden = !batchSearchQuery.trim();
      debouncedSearch();
    });
  }

  const clearBtn = $("batch-search-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      batchSearchQuery = "";
      clearBtn.hidden = true;
      loadBatches();
      searchInput.focus();
    });
  }

  // Bộ lọc sản phẩm (SCRUM-50)
  const filterSelect = $("batch-filter-product");
  if (filterSelect) {
    filterSelect.addEventListener("change", (e) => {
      batchFilterProduct = e.target.value;
      loadBatches();
    });
  }

  // Nút đặt lại bộ lọc
  const resetFiltersBtn = $("btn-reset-filters");
  if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener("click", () => {
      batchSearchQuery = "";
      batchFilterProduct = "";
      if (searchInput) searchInput.value = "";
      if (filterSelect) filterSelect.value = "";
      if (clearBtn) clearBtn.hidden = true;
      resetFiltersBtn.hidden = true;
      loadBatches();
    });
  }

  // Nút thử lại khi gặp lỗi (Error state)
  const retryBtn = $("batch-btn-retry");
  if (retryBtn) {
    retryBtn.addEventListener("click", () => {
      loadBatches();
    });
  }

  // Đóng Modal chi tiết lô
  const modalClose = $("modal-batch-close");
  const modalBtnClose = $("modal-batch-btn-close");
  const modalBackdrop = $("batch-modal");

  if (modalClose) modalClose.addEventListener("click", closeBatchDetail);
  if (modalBtnClose) modalBtnClose.addEventListener("click", closeBatchDetail);
  if (modalBackdrop) {
    modalBackdrop.addEventListener("click", (e) => {
      if (e.target === modalBackdrop) closeBatchDetail();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modalBackdrop && !modalBackdrop.hidden) {
      closeBatchDetail();
    }
  });

  // SCRUM-126: Form nhiệt độ & Job tổng hợp
  const tempForm = $("temp-reading-form");
  if (tempForm) tempForm.addEventListener("submit", handleTempSubmit);
  const btnAgg = $("btn-run-aggregate");
  if (btnAgg) btnAgg.addEventListener("click", handleRunAggregate);

  // SCRUM-136: Job S-12
  const btnInteg = $("btn-run-integrity");
  if (btnInteg) btnInteg.addEventListener("click", handleRunIntegrity);

  // SCRUM-138: Backup & Diễn tập
  const btnBk = $("btn-run-backup");
  if (btnBk) btnBk.addEventListener("click", handleRunBackup);
  const btnReh = $("btn-run-rehearsal");
  if (btnReh) btnReh.addEventListener("click", () => runRehearsalForBackup(null));

  // SCRUM-91: Mời & Modal chấp nhận
  const inviteForm = $("invite-form");
  if (inviteForm) inviteForm.addEventListener("submit", handleInviteSubmit);

  const btnOpenAccept = $("btn-open-accept-modal");
  if (btnOpenAccept) btnOpenAccept.addEventListener("click", () => openAcceptModal());
  const btnCloseAccept = $("modal-invite-close");
  if (btnCloseAccept) btnCloseAccept.addEventListener("click", closeAcceptModal);
  const formAccept = $("invite-accept-form");
  if (formAccept) formAccept.addEventListener("submit", handleAcceptSubmit);
}

/** Huỷ chế độ sửa của form vùng trồng / lô nông sản (nút "Huỷ sửa"). */
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

/**
 * Xử lý click ở cột "Thao tác" của cả 2 bảng (nút Xem / Sửa / Xoá).
 */
function handleTableAction(event) {
  const button = event.target.closest("button[data-action]");
  if (button === null) {
    return;
  }

  const id = Number(button.dataset.id);
  const entity = button.dataset.entity;
  const action = button.dataset.action;

  if (action === "view" && entity === "batch") {
    openBatchDetail(id);
    return;
  }

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


/* ------------------------------------- 13. Chuỗi lạnh & Nhiệt độ (SCRUM-126) --- */
let tempViolations = [];
let tempSummaries = [];

async function loadColdChainData() {
  try {
    const [violations, summaries] = await Promise.all([
      apiRequest("/cold-chain/readings/violations"),
      apiRequest("/cold-chain/summaries"),
    ]);
    tempViolations = Array.isArray(violations) ? violations : [];
    tempSummaries = Array.isArray(summaries) ? summaries : [];
    renderViolations();
    renderSummaries();
    populateBatchSelectForTemp();
  } catch (error) {
    console.error("Lỗi tải dữ liệu chuỗi lạnh:", error);
  }
}

function populateBatchSelectForTemp() {
  const select = $("temp-batch-id");
  if (!select) return;
  const currentVal = select.value;
  select.innerHTML = '<option value="">-- Cảm biến kho chung (không gắn lô) --</option>' +
    batches
      .map((b) => `<option value="${b.id}" ${String(b.id) === currentVal ? "selected" : ""}>#${b.id} — ${escapeHtml(b.product_name)} (${b.quantity}kg)</option>`)
      .join("");
}

function renderViolations() {
  const tbody = $("violation-table-body");
  const empty = $("violation-empty");
  if (!tbody) return;

  tbody.innerHTML = tempViolations
    .map(
      (v) => `
      <tr>
        <td class="font-mono" style="font-size: 13px;">${escapeHtml(formatDate(v.recorded_at.split("T")[0]))} ${v.recorded_at.split("T")[1]?.slice(0, 5) || ""}</td>
        <td><code>${escapeHtml(v.sensor_id)}</code></td>
        <td>${v.batch_id ? `#${v.batch_id}` : "Kho chung"}</td>
        <td><strong style="color: #dc2626; font-size: 14px;">${v.temperature}°C</strong></td>
        <td><span class="badge-pill badge-pill--danger">${escapeHtml(v.violation_reason || "Vi phạm ngưỡng")}</span></td>
      </tr>`
    )
    .join("");

  if (empty) empty.hidden = tempViolations.length > 0;
}

function renderSummaries() {
  const tbody = $("summary-table-body");
  const empty = $("summary-empty");
  if (!tbody) return;

  tbody.innerHTML = tempSummaries
    .map(
      (s) => `
      <tr>
        <td class="font-mono">${escapeHtml(s.hour_bucket.replace("T", " ").slice(0, 16))}</td>
        <td><code>${escapeHtml(s.sensor_id)}</code></td>
        <td>${s.batch_id ? `#${s.batch_id}` : "Kho chung"}</td>
        <td><strong>${s.min_temp}°C</strong></td>
        <td><strong>${s.max_temp}°C</strong></td>
        <td style="color: #059669;"><strong>${s.avg_temp}°C</strong></td>
        <td>${s.sample_count}</td>
        <td>${s.violation_count > 0 ? `<span class="badge-pill badge-pill--danger">${s.violation_count}</span>` : `<span class="badge-pill badge-pill--safe">0</span>`}</td>
      </tr>`
    )
    .join("");

  if (empty) empty.hidden = tempSummaries.length > 0;
}

async function handleTempSubmit(e) {
  e.preventDefault();
  const sensorId = $("temp-sensor-id").value.trim();
  const tempVal = parseFloat($("temp-value").value);
  const batchIdVal = $("temp-batch-id").value ? parseInt($("temp-batch-id").value) : null;

  if (!sensorId || isNaN(tempVal)) {
    toast("Vui lòng nhập đầy đủ mã cảm biến và nhiệt độ.", "error");
    return;
  }

  const btn = $("btn-submit-reading");
  setButtonLoading(btn, true, "Đang gửi…", "Ghi nhận số đo cảm biến");

  try {
    const result = await apiRequest("/cold-chain/readings", {
      method: "POST",
      body: { sensor_id: sensorId, temperature: tempVal, batch_id: batchIdVal },
    });
    if (result.is_violation) {
      toast(`⚠️ CẢNH BÁO VI PHẠM: ${result.violation_reason}`, "error");
    } else {
      toast(`✓ Đã ghi nhận số đo ${tempVal}°C từ cảm biến ${sensorId} (Chuẩn an toàn).`, "success");
    }
    $("temp-value").value = "";
    await loadColdChainData();
  } catch (err) {
    toast(`Lỗi ghi nhận nhiệt độ: ${err.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang gửi…", "Ghi nhận số đo cảm biến");
  }
}

async function handleRunAggregate() {
  if (!window.confirm("Kích hoạt job tổng hợp các số đo nhiệt độ cũ hơn 30 ngày theo từng giờ?\nSố đo bình thường sẽ được tổng hợp, các số đo vi phạm sẽ được bảo lưu nguyên vẹn.")) {
    return;
  }
  const btn = $("btn-run-aggregate");
  setButtonLoading(btn, true, "Đang tổng hợp…", "Tổng hợp dữ liệu cũ (>30 ngày)");
  try {
    const res = await apiRequest("/cold-chain/aggregate?days_threshold=30", { method: "POST" });
    toast(`Tổng hợp thành công ${res.aggregated_buckets} khung giờ. Đã dọn ${res.purged_raw_readings} số đo an toàn, bảo lưu ${res.retained_violation_readings} số đo vi phạm.`, "success");
    await loadColdChainData();
  } catch (err) {
    toast(`Lỗi tổng hợp: ${err.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang tổng hợp…", "Tổng hợp dữ liệu cũ (>30 ngày)");
  }
}

/* ------------------------------------- 14. Toàn vẹn dữ liệu S-12 (SCRUM-136) --- */
async function loadIntegrityData() {
  try {
    const data = await apiRequest("/integrity-check/latest");
    renderIntegrityStatus(data);
  } catch (err) {
    console.error("Lỗi tải kiểm định toàn vẹn:", err);
  }
}

function renderIntegrityStatus(data) {
  if (!data) return;
  const badge = $("integrity-status-badge");
  const ts = $("integrity-timestamp");
  const scanned = $("integrity-scanned");
  const errs = $("integrity-errors");
  const tbody = $("integrity-table-body");
  const empty = $("integrity-empty");

  if (badge) {
    if (data.status === "PASSED") {
      badge.textContent = "PASSED (ĐẠT CHUẨN)";
      badge.className = "badge-pill badge-pill--safe";
    } else {
      badge.textContent = "FAILED (PHÁT HIỆN LỖI)";
      badge.className = "badge-pill badge-pill--danger";
    }
  }

  if (ts) ts.textContent = data.timestamp ? data.timestamp.replace("T", " ").slice(0, 19) : "—";
  if (scanned) scanned.textContent = data.scanned_records || 0;
  if (errs) errs.textContent = data.error_count || 0;

  let violations = [];
  try {
    violations = JSON.parse(data.details || "[]");
  } catch (e) {
    violations = [];
  }

  if (tbody) {
    tbody.innerHTML = violations
      .map(
        (v) => `
        <tr>
          <td><span class="badge-pill badge-pill--danger">${escapeHtml(v.category)}</span></td>
          <td><code>${escapeHtml(v.table)}</code></td>
          <td class="font-mono">#${escapeHtml(v.record_id)}</td>
          <td style="color: #b91c1c;">${escapeHtml(v.message)}</td>
        </tr>`
      )
      .join("");
  }
  if (empty) empty.hidden = violations.length > 0;
}

async function handleRunIntegrity() {
  const btn = $("btn-run-integrity");
  setButtonLoading(btn, true, "Đang quét…", "Chạy kiểm định S-12");
  try {
    const res = await apiRequest("/integrity-check/run", { method: "POST" });
    renderIntegrityStatus(res.result);
    if (res.result.status === "PASSED") {
      toast("✓ Kiểm định Job S-12 thành công: Toàn bộ dữ liệu nhất quán 100%.", "success");
    } else {
      toast(`⚠️ Phát hiện ${res.result.error_count} lỗi toàn vẹn dữ liệu! Hãy kiểm tra bảng chi tiết.`, "error");
    }
  } catch (err) {
    toast(`Lỗi chạy kiểm định: ${err.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang quét…", "Chạy kiểm định S-12");
  }
}

/* ------------------------------------- 15. Sao lưu & Diễn tập khôi phục (SCRUM-138) --- */
let backupsList = [];

async function loadBackups() {
  try {
    const data = await apiRequest("/backups");
    backupsList = Array.isArray(data) ? data : [];
    renderBackups();
  } catch (err) {
    console.error("Lỗi tải danh sách sao lưu:", err);
  }
}

function renderBackups() {
  const tbody = $("backup-table-body");
  const empty = $("backup-empty");
  if (!tbody) return;

  tbody.innerHTML = backupsList
    .map(
      (b) => `
      <tr>
        <td class="id-cell">${b.id}</td>
        <td class="font-mono" style="font-size: 13px;">${b.timestamp ? b.timestamp.replace("T", " ").slice(0, 19) : "—"}</td>
        <td>${b.status === "SUCCESS" ? `<span class="badge-pill badge-pill--safe">SUCCESS</span>` : `<span class="badge-pill badge-pill--danger">FAILED</span>`}</td>
        <td>${formatNumber(Math.round(b.file_size_bytes / 1024))} KB</td>
        <td><code class="font-mono" style="font-size: 11px;">${escapeHtml(b.sha256_checksum.slice(0, 16))}...</code></td>
        <td>${b.rehearsal_verified ? `<span class="badge-pill badge-pill--safe">Đã xác minh</span>` : `<span class="badge-pill badge-pill--warning">Chưa thử</span>`}</td>
        <td>
          <button class="action-btn-sm action-btn-sm--success" onclick="runRehearsalForBackup(${b.id})">Thử khôi phục</button>
        </td>
      </tr>`
    )
    .join("");

  if (empty) empty.hidden = backupsList.length > 0;
}

async function handleRunBackup() {
  const btn = $("btn-run-backup");
  setButtonLoading(btn, true, "Đang sao lưu…", "Sao lưu Online ngay");
  try {
    const res = await apiRequest("/backups/run", { method: "POST" });
    toast(`✓ ${res.message} (Đang giữ ${res.retained_backups_count} bản sao lưu).`, "success");
    await loadBackups();
  } catch (err) {
    toast(`Lỗi sao lưu: ${err.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang sao lưu…", "Sao lưu Online ngay");
  }
}

async function runRehearsalForBackup(backupId = null) {
  try {
    const res = await apiRequest("/backups/rehearsal", {
      method: "POST",
      body: { backup_id: backupId },
    });
    alert(`[DIỄN TẬP KHÔI PHỤC THÀNH CÔNG]\nFile thử nghiệm: ${res.tested_file}\nKết quả PRAGMA integrity_check: ${res.integrity_check_output}\nSố lượng bản ghi các bảng: ${JSON.stringify(res.tables_checked, null, 2)}\n\nChi tiết: ${res.details}`);
    toast("✓ Diễn tập khôi phục trên CSDL tạm thành công!", "success");
    await loadBackups();
  } catch (err) {
    toast(`Lỗi diễn tập khôi phục: ${err.message}`, "error");
  }
}

/* ------------------------------------- 16. Mời thành viên & Phân quyền (SCRUM-91) --- */
let invitationsList = [];
let auditsList = [];

async function loadInvitations() {
  try {
    const data = await apiRequest("/invitations");
    invitationsList = Array.isArray(data) ? data : [];
    renderInvitations();
  } catch (err) {
    console.error("Lỗi tải lời mời:", err);
  }
}

function renderInvitations() {
  const tbody = $("invite-table-body");
  const empty = $("invite-empty");
  if (!tbody) return;

  tbody.innerHTML = invitationsList
    .map(
      (inv) => {
        let statusBadge = `<span class="badge-pill badge-pill--warning">Đang chờ</span>`;
        let actionBtn = `<button class="action-btn-sm action-btn-sm--danger" onclick="revokeInvitationAction(${inv.id})">Thu hồi</button>`;
        if (inv.is_used) {
          statusBadge = `<span class="badge-pill badge-pill--safe">Đã sử dụng</span>`;
          actionBtn = `<span class="text-muted">Đã tham gia</span>`;
        } else if (inv.is_revoked) {
          statusBadge = `<span class="badge-pill badge-pill--danger">Đã thu hồi</span>`;
          actionBtn = `<span class="text-muted">—</span>`;
        }

        return `
        <tr>
          <td><strong>${escapeHtml(inv.email)}</strong></td>
          <td><code>${escapeHtml(inv.role)}</code></td>
          <td><span class="badge-pill">${escapeHtml(inv.organization_id)}</span></td>
          <td class="font-mono" style="font-size: 13px;">${inv.expires_at ? inv.expires_at.replace("T", " ").slice(0, 16) : "—"}</td>
          <td>${statusBadge}</td>
          <td>${actionBtn}</td>
        </tr>`;
      }
    )
    .join("");

  if (empty) empty.hidden = invitationsList.length > 0;
}

async function loadAudits() {
  try {
    const data = await apiRequest("/invitations/audits");
    auditsList = Array.isArray(data) ? data : [];
    renderAudits();
  } catch (err) {
    console.error("Lỗi tải nhật ký kiểm toán:", err);
  }
}

function renderAudits() {
  const tbody = $("audit-table-body");
  const empty = $("audit-empty");
  if (!tbody) return;

  tbody.innerHTML = auditsList
    .map(
      (a) => `
      <tr>
        <td class="font-mono" style="font-size: 12px;">${a.created_at ? a.created_at.replace("T", " ").slice(0, 19) : "—"}</td>
        <td><strong>${escapeHtml(a.operator_username)}</strong></td>
        <td><code>${escapeHtml(a.action)}</code></td>
        <td>${escapeHtml(a.target_email_or_user || "—")}</td>
        <td style="font-size: 13px;">${escapeHtml(a.details || "—")}</td>
      </tr>`
    )
    .join("");

  if (empty) empty.hidden = auditsList.length > 0;
}

async function handleInviteSubmit(e) {
  e.preventDefault();
  const email = $("invite-email").value.trim();
  const role = $("invite-role").value;
  const hours = parseInt($("invite-expires").value) || 48;

  if (!email) {
    toast("Vui lòng nhập địa chỉ email người nhận.", "error");
    return;
  }

  const btn = $("btn-submit-invite");
  setButtonLoading(btn, true, "Đang gửi…", "Gửi lời mời tham gia");

  try {
    const res = await apiRequest("/invitations", {
      method: "POST",
      body: { email, role, expires_in_hours: hours },
    });
    alert(`[ĐÃ GỬI LỜI MỜI THÀNH CÔNG]\nEmail: ${res.email}\nVai trò: ${res.role}\nTổ chức: ${res.organization_id}\n\nĐường link kích hoạt tài khoản:\n${res.invite_link}\n(Token bí mật một lần: ${res.invite_token})`);
    toast(`Đã gửi lời mời tới ${email}.`, "success");
    $("invite-email").value = "";
    await loadInvitations();
    await loadAudits();
  } catch (err) {
    toast(`Lỗi gửi lời mời: ${err.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang gửi…", "Gửi lời mời tham gia");
  }
}

async function revokeInvitationAction(invId) {
  if (!window.confirm("Bạn có chắc chắn muốn thu hồi lời mời này? Người nhận sẽ không thể kích hoạt tài khoản nữa.")) {
    return;
  }
  try {
    await apiRequest(`/invitations/${invId}/revoke`, { method: "POST" });
    toast("Đã thu hồi lời mời thành công.", "success");
    await loadInvitations();
    await loadAudits();
  } catch (err) {
    toast(`Lỗi thu hồi lời mời: ${err.message}`, "error");
  }
}

async function toggleMemberStatus(userId, isCurrentlyActive) {
  const actionText = isCurrentlyActive ? "VÔ HIỆU HOÁ" : "KÍCH HOẠT LẠI";
  if (!window.confirm(`Xác nhận ${actionText} tài khoản ID #${userId}?`)) {
    return;
  }
  try {
    await apiRequest("/invitations/members/status", {
      method: "POST",
      body: { user_id: userId, active: !isCurrentlyActive, reason: `Thao tác bởi quản trị viên` },
    });
    toast(`Đã cập nhật trạng thái tài khoản #${userId}.`, "success");
    await loadUsers();
    await loadAudits();
  } catch (err) {
    toast(`Lỗi cập nhật trạng thái: ${err.message}`, "error");
  }
}

function openAcceptModal(token = "") {
  const modal = $("invite-modal");
  if (!modal) return;
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  if (token) {
    $("accept-token").value = token;
  }
  $("accept-username").focus();
}

function closeAcceptModal() {
  const modal = $("invite-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
}

async function handleAcceptSubmit(e) {
  e.preventDefault();
  const token = $("accept-token").value.trim();
  const username = $("accept-username").value.trim();
  const password = $("accept-password").value;

  if (!token || !username || !password) {
    toast("Vui lòng điền đầy đủ token, username và mật khẩu.", "error");
    return;
  }

  const btn = $("btn-submit-accept");
  setButtonLoading(btn, true, "Đang kích hoạt…", "Xác nhận & Kích hoạt tài khoản");

  try {
    const res = await apiRequest("/invitations/accept", {
      method: "POST",
      body: { token, username, password },
      auth: false,
    });
    toast(`✓ Kích hoạt tài khoản '${res.username}' thành công! Vui lòng đăng nhập.`, "success");
    closeAcceptModal();
    $("login-username").value = res.username;
    $("login-password").value = password;
  } catch (err) {
    toast(`Kích hoạt thất bại: ${err.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang kích hoạt…", "Xác nhận & Kích hoạt tài khoản");
  }
}


/** Tải dữ liệu dùng chung cho giao diện sau khi đăng nhập (theo phân quyền). */
async function loadAllData() {
  await checkHealth();
  await loadFarms(); // phải chạy trước để bảng lô hiển thị được tên vùng trồng
  await loadBatches();
  await loadColdChainData();
  await loadIntegrityData();
  if (session !== null && session.role === ROLE_ADMIN) {
    await loadUsers(); // chỉ admin gọi được GET /users
    await loadBackups();
    await loadInvitations();
    await loadAudits();
  }
}

/** Tải lại toàn bộ dữ liệu; `silent = true` để bỏ toast tổng kết. */
async function reloadAll({ silent = false } = {}) {
  const button = $("btn-reload");
  setButtonLoading(button, true, "Đang tải…", "Tải lại dữ liệu");

  await loadAllData();

  setButtonLoading(button, false, "Đang tải…", "Tải lại dữ liệu");
  if (!silent) {
    toast(`Đã tải lại: ${farms.length} vùng trồng, ${batches.length} lô nông sản.`, "info");
  }
}

/* -------------------------------------------------------- 12. Khởi động --- */
/**
 * Khởi động ứng dụng:
 * 1. gắn sự kiện + kiểm tra backend đang chạy;
 * 2. nếu tab còn phiên đăng nhập cũ (sessionStorage) thì xác thực lại với
 *    backend rồi vào thẳng giao diện;
 * 3. ngược lại, hiện màn hình đăng nhập.
 */
async function init() {
  if ($("stat-api")) {
    $("stat-api").textContent = API_BASE_URL;
  }
  bindEvents();
  resetFarmForm(); // 2 form luôn khởi động ở chế độ "thêm mới / tạo mới"
  resetBatchForm();
  await checkHealth(); // báo ngay nếu uvicorn chưa chạy

  // Kiểm tra URL hash (nếu có token lời mời: #accept-invite?token=xyz)
  if (window.location.hash.includes("accept-invite")) {
    const hashParams = new URLSearchParams(window.location.hash.split("?")[1] || "");
    const token = hashParams.get("token");
    if (token) {
      openAcceptModal(token);
    }
  }

  const saved = restoreSession();
  if (saved !== null) {
    try {
      const data = await requestLogin(saved.username, saved.password);
      startSession({ username: data.username, role: data.role, password: saved.password });
      toast(`Đã khôi phục phiên: ${data.username} (role: ${data.role}).`, "info");
      await reloadAll({ silent: true });
      return;
    } catch (error) {
      // Phiên cũ hết hiệu lực (đổi mật khẩu, xoá database...) -> yêu cầu đăng nhập lại.
      clearSession();
    }
  }

  applySessionToUi(); // chỉ hiện màn hình login
  $("login-username").focus();
}

document.addEventListener("DOMContentLoaded", init);

