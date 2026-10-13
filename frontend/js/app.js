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
const ROLE_INSPECTOR = "inspector";

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
let orders = [];
let thresholds = [];
let violations = [];
let products = [];
let handovers = [];

// Tab đang chọn trong thanh điều hướng phân hệ
let activeTab = "all";

// Trạng thái gộp nhiều lô nông sản (T-45)
let isMergeMode = false;
let selectedBatchIds = new Set();

// Danh sách các tổ chức đối tác mẫu phục vụ bàn giao chuỗi cung ứng
const PARTNER_ORGANIZATIONS = [
  { id: 1, name: "Hợp tác xã Nông nghiệp Sạch Mỹ Xương" },
  { id: 2, name: "Công ty Cổ phần Chế biến & Xuất khẩu Nông sản Mekong" },
  { id: 3, name: "Chuỗi Siêu thị Thực phẩm WinCommerce VietGAP" },
  { id: 4, name: "Trung tâm Logistics & Kho vận Chuỗi Lạnh Satra" },
  { id: 5, name: "Công ty TNHH Xuất khẩu Trái cây Cao cấp VinaFresh" },
  { id: 6, name: "Hệ thống Bán lẻ Nông sản Hữu cơ GreenFood" },
];

// ID bản ghi đang được SỬA trên form (null = form đang ở chế độ "thêm mới").
// Sprint 5: bấm nút "Sửa" ở bảng -> form phía trên đổ sẵn dữ liệu và nút submit
// gọi PUT thay vì POST.
let editingFarmId = null;
let editingBatchId = null;
let editingProductId = null;
let activeViewingOrderId = null;
let editingThresholdId = null;

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
  const isInspector = isLoggedIn && session.role === ROLE_INSPECTOR;

  $("login-view").hidden = isLoggedIn;
  $("app-view").hidden = !isLoggedIn;
  $("btn-reload").hidden = !isLoggedIn;
  $("btn-logout").hidden = !isLoggedIn;

  const badge = $("user-badge");
  if (badge) {
    badge.hidden = !isLoggedIn;
    if (isLoggedIn) {
      let displayName = "Hộ Nông Dân Canh Tác";
      if (session.role === ROLE_ADMIN) {
        displayName = "Quản Trị Viên (Admin)";
      } else if (session.role === ROLE_INSPECTOR) {
        displayName = "Cán Bộ Kiểm Tra (Inspector)";
      }
      badge.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg> <span>${escapeHtml(displayName)}</span>`;
    }
  }

  // 1. Quản trị tài khoản: chỉ admin
  if ($("tab-users-btn")) {
    $("tab-users-btn").hidden = !isAdmin;
  }
  $("users-card").hidden = !isAdmin;

  // 2. Form vùng trồng và lô thu hoạch: chỉ farmer hoặc admin (inspector xem dạng kiểm tra/đối chiếu)
  if ($("farm-form")) {
    $("farm-form").hidden = isInspector;
  }
  if ($("batch-form")) {
    $("batch-form").hidden = isInspector;
  }
  if ($("farm-inspector-note")) {
    $("farm-inspector-note").hidden = !isInspector;
  }
  if ($("batch-inspector-note")) {
    $("batch-inspector-note").hidden = !isInspector;
  }

  // 3. Form ban hành lệnh kiểm tra / thu hồi: chỉ inspector hoặc admin
  if ($("order-form")) {
    $("order-form").hidden = !isInspector && !isAdmin;
  }
  if ($("order-farmer-note")) {
    $("order-farmer-note").hidden = isInspector || isAdmin;
  }

  // 4. Form cấu hình ngưỡng chuỗi lạnh: chỉ admin mới sửa ngưỡng gốc
  if ($("threshold-form")) {
    $("threshold-form").hidden = !isAdmin;
  }

  // 5. Form sản phẩm dùng chung: chỉ admin mới thêm/sửa sản phẩm
  if ($("product-form")) {
    $("product-form").hidden = !isAdmin;
  }

  populateHandoverOrgs();
  switchTab(activeTab);
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
  orders = [];
  thresholds = [];
  violations = [];
  resetFarmForm(); // bỏ chế độ sửa (nếu đang sửa) trước khi vẽ lại bảng rỗng
  resetBatchForm();
  renderFarms();
  renderFarmOptions();
  renderBatches();
  renderUsers();
  renderOrders();
  renderViolations();

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
            ${
              session && session.role !== ROLE_INSPECTOR
                ? `<button class="btn btn--primary btn--sm" type="button"
                    data-action="edit" data-entity="farm"
                    data-id="${escapeHtml(farm.id)}">Sửa</button>`
                : `<span style="font-size: 0.82rem; color: #64748b; font-style: italic;">Chỉ xem</span>`
            }
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
/** GET /batches -> cập nhật bảng danh sách lô (hỗ trợ tìm kiếm & lọc). */
async function loadBatches(search = "") {
  try {
    const url = search ? `/batches?search=${encodeURIComponent(search)}` : "/batches";
    const data = await apiRequest(url);
    batches = Array.isArray(data) ? data : [];
    renderBatches();
    populateHandoverBatches();
    if ($("btn-batch-search-clear")) {
      $("btn-batch-search-clear").hidden = !search;
    }
    if (search && batches.length === 0) {
      toast(`Không tìm thấy lô nông sản nào khớp với "${search}".`, "info");
    }
  } catch (error) {
    toast(`Không tải được danh sách lô nông sản: ${error.message}`, "error");
  }
}

/** Nhãn vùng trồng cho bảng lô (dùng lại dữ liệu đã tải từ GET /farms). */
function farmLabel(farmId) {
  const farm = farms.find((item) => item.id === farmId);
  return farm ? `#${farmId} — ${farm.name}` : `#${farmId}`;
}

/** Vẽ bảng danh sách lô nông sản (kèm cột "Thao tác": Tách/Sửa/Xoá). */
function renderBatches() {
  const tbody = $("batch-table-body");
  if (!tbody) return;

  tbody.innerHTML = batches
    .map(
      (batch) => `
      <tr class="${batch.id === editingBatchId ? "is-editing" : ""}">
        <td class="id-cell font-mono" style="font-weight: 700; color: #047857;">
          ${
            isMergeMode
              ? `<input type="checkbox" class="batch-merge-cb" data-batch-id="${batch.id}"
                        ${selectedBatchIds.has(batch.id) ? "checked" : ""} style="margin-right: 6px; cursor: pointer;" />`
              : ""
          }
          ${escapeHtml(batch.batch_code || `LOT-${batch.id}`)}
        </td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td><strong>${escapeHtml(batch.product_name)}</strong></td>
        <td class="is-right font-mono">${formatNumber(batch.quantity)}</td>
        <td>${escapeHtml(formatDate(batch.harvest_date))}</td>
        <td><span style="display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0;">${escapeHtml(batch.current_holder_org || batch.owner || "HTX Nông Nghiệp Số 4")}</span></td>
        <td>
          <div class="table__actions">
            <button class="btn btn--sm" style="background: #e0e7ff; color: #4338ca; border: 1px solid #c7d2fe;" type="button"
                    data-action="timeline" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}"
                    title="Xem dòng thời gian lịch sử & tính toàn vẹn chuỗi">Lịch sử</button>
            ${
              session && session.role !== ROLE_INSPECTOR
                ? `<button class="btn btn--sm btn-table-split" type="button"
                    data-action="split" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}"
                    title="Tách nhập nhiều dòng lô con (T-40)">Tách</button>
                   <button class="btn btn--primary btn--sm" type="button"
                    data-action="edit" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}">Sửa</button>`
                : ""
            }
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
  renderStats(); // thẻ "Tổng lô nông sản" + "Tổng sản lượng" lấy từ mảng `batches`
}

/** Nhãn nút submit form lô nông sản theo chế độ hiện tại (tạo mới / sửa). */
function batchSubmitLabel() {
  return editingBatchId === null ? "Tạo lô nông sản" : "Cập nhật lô nông sản";
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
      if ($("batch-created-banner")) $("batch-created-banner").hidden = true;
    } else {
      const created = await apiRequest("/batches", { method: "POST", body: payload });
      toast(
        `Tạo thành công lô ${created.batch_code || `#${created.id}`} "${created.product_name}" (${formatNumber(created.quantity)} kg)`,
        "success"
      );

      // Hiển thị mã lô mới tạo tự động với kích cỡ lớn cho người dùng
      const banner = $("batch-created-banner");
      if (banner) {
        banner.hidden = false;
        $("batch-created-code-large").textContent = created.batch_code || `LOT-${created.id}`;
        $("batch-created-detail").textContent = `${created.product_name} · ${formatNumber(created.quantity)} kg · Thu hoạch: ${formatDate(created.harvest_date)}`;
        $("batch-created-org").textContent = `Tổ chức đang giữ: ${created.current_holder_org || "HTX của tôi"}`;
        banner.scrollIntoView({ behavior: "smooth", block: "center" });
      }
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

/* ---------------------------------- 8b. Tách nhập nhiều dòng lô con (T-40) --- */
let activeSplittingBatch = null;
let splitRows = []; // mảng chứa các dòng: [ { id, quantity, note } ]
let nextSplitRowId = 1;

/** Mở modal tách lô nhiều dòng từ dòng lô được chọn. */
function openSplitModal(batchId) {
  const batch = batches.find((item) => item.id === batchId);
  if (!batch) {
    toast(`Không tìm thấy lô nông sản #${batchId}.`, "error");
    return;
  }

  activeSplittingBatch = batch;
  $("split-parent-code").textContent = `BATCH #${batch.id}`;
  $("split-parent-product").textContent = `${batch.product_name} (${farmLabel(batch.farm_id)})`;
  $("split-parent-initial").textContent = `${formatNumber(batch.quantity)} kg`;
  $("split-parent-remaining").textContent = `${formatNumber(batch.quantity)} kg`;

  // Reset giao diện modal
  $("split-error-alert").hidden = true;
  $("split-result-section").hidden = true;
  $("btn-submit-split").hidden = false;
  $("btn-submit-split").disabled = false;
  $("btn-cancel-split").textContent = "Huỷ bỏ";

  // Khởi tạo sẵn 2 dòng lô con mặc định
  splitRows = [
    { id: nextSplitRowId++, quantity: "", note: "Kiện hàng 1 - Phân loại A" },
    { id: nextSplitRowId++, quantity: "", note: "Kiện hàng 2 - Phân loại B" },
  ];

  renderSplitRows();
  updateSplitCalculation();

  $("split-modal-backdrop").hidden = false;
}

/** Đóng modal tách lô. */
function closeSplitModal() {
  $("split-modal-backdrop").hidden = true;
  activeSplittingBatch = null;
  splitRows = [];
}

/** Thêm một dòng lô con mới. */
function addSplitRow() {
  splitRows.push({
    id: nextSplitRowId++,
    quantity: "",
    note: `Kiện hàng ${splitRows.length + 1}`,
  });
  renderSplitRows();
  updateSplitCalculation();
}

/** Xoá một dòng lô con theo ID dòng. */
function removeSplitRow(rowId) {
  if (splitRows.length <= 1) {
    toast("Cần ít nhất 1 dòng lô con để thực hiện tách lô.", "info");
    return;
  }
  splitRows = splitRows.filter((r) => r.id !== rowId);
  renderSplitRows();
  updateSplitCalculation();
}

/** Vẽ danh sách các dòng input tách lô con. */
function renderSplitRows() {
  const tbody = $("split-rows-body");
  tbody.innerHTML = splitRows
    .map(
      (row, index) => `
      <tr data-row-id="${row.id}">
        <td class="id-cell" style="text-align: center;">${index + 1}</td>
        <td>
          <input type="number" step="0.1" min="0.1"
                 class="split-input split-input-qty"
                 placeholder="VD: 50.5"
                 value="${row.quantity !== "" ? row.quantity : ""}"
                 data-id="${row.id}" required />
        </td>
        <td>
          <input type="text" maxlength="255"
                 class="split-input split-input-note"
                 placeholder="Ghi chú phân loại / đóng thùng"
                 value="${escapeHtml(row.note || "")}"
                 data-id="${row.id}" />
        </td>
        <td class="is-center">
          <button type="button" class="btn-row-del"
                  data-action="del-row" data-id="${row.id}"
                  ${splitRows.length <= 1 ? "disabled" : ""}
                  title="Xoá dòng này">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
        </td>
      </tr>`
    )
    .join("");
}

/** Tính toán tổng đang nhập so với phần còn lại của lô mẹ và kiểm tra vô hiệu hoá nút Lưu. */
function updateSplitCalculation() {
  if (!activeSplittingBatch) return;

  const parentQty = Number(activeSplittingBatch.quantity || 0);

  // Tính tổng số lượng từ các input
  let totalEntered = 0;
  splitRows.forEach((r) => {
    const q = Number(r.quantity);
    if (!isNaN(q) && q > 0) {
      totalEntered += q;
    }
  });

  totalEntered = Math.round(totalEntered * 1000) / 1000;
  const remainingAfter = Math.round((parentQty - totalEntered) * 1000) / 1000;

  $("split-sum-entered").textContent = `${formatNumber(totalEntered)} kg`;
  $("split-sum-after").textContent = `${formatNumber(remainingAfter)} kg`;

  const errorAlert = $("split-error-alert");
  const errorText = $("split-error-text");
  const submitBtn = $("btn-submit-split");

  // Quy tắc nghiệm thu:
  // Nếu tổng nhập vượt quá phần còn lại thì nút Lưu bị vô hiệu hóa kèm cảnh báo đỏ.
  if (totalEntered > parentQty) {
    const diff = Math.round((totalEntered - parentQty) * 1000) / 1000;
    errorText.textContent = `Tổng nhập (${formatNumber(totalEntered)} kg) vượt quá phần còn lại của lô mẹ (${formatNumber(parentQty)} kg) là +${formatNumber(diff)} kg! Vui lòng điều chỉnh lại.`;
    errorAlert.hidden = false;
    submitBtn.disabled = true;
    submitBtn.classList.add("btn-disabled");
    $("split-sum-after").style.color = "var(--red-600)";
  } else if (totalEntered <= 0) {
    errorAlert.hidden = true;
    submitBtn.disabled = true; // chưa nhập khối lượng
    $("split-sum-after").style.color = "";
  } else {
    errorAlert.hidden = true;
    submitBtn.disabled = false;
    submitBtn.classList.remove("btn-disabled");
    $("split-sum-after").style.color = "var(--emerald-600)";
  }
}

/** Xử lý gửi yêu cầu tách lô con lên backend. */
async function handleSplitSubmit() {
  if (!activeSplittingBatch) return;

  // Thu thập các dòng khối lượng
  const items = [];
  for (const r of splitRows) {
    const q = Number(r.quantity);
    if (isNaN(q) || q <= 0) {
      toast("Vui lòng nhập khối lượng hợp lệ (> 0) cho tất cả các dòng lô con.", "error");
      return;
    }
    items.push({
      quantity: q,
      note: (r.note || "").trim() || null,
    });
  }

  const parentQty = Number(activeSplittingBatch.quantity || 0);
  const totalEntered = items.reduce((s, it) => s + it.quantity, 0);

  if (totalEntered > parentQty) {
    toast("Tổng khối lượng nhập vượt quá phần còn lại của lô mẹ. Không thể lưu!", "error");
    return;
  }

  const submitBtn = $("btn-submit-split");
  setButtonLoading(submitBtn, true, "Đang tách lô…", "Xác nhận Lưu & Tách Lô");

  try {
    const payload = { items };
    const result = await apiRequest(`/batches/${activeSplittingBatch.id}/split`, {
      method: "POST",
      body: payload,
    });

    toast(result.message || "Tách lô con thành công!", "success");

    // Hiển thị danh sách mã lô con cỡ chữ lớn phục vụ in dán thùng hàng (T-40 / T-20)
    renderSplitResults(result.child_batches, result.parent_remaining_quantity);

    // Tải lại danh sách lô phía sau để cập nhật số dư lô mẹ và các lô con mới
    await loadBatches();
  } catch (error) {
    toast(`Tách lô thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(submitBtn, false, "Đang tách lô…", "Xác nhận Lưu & Tách Lô");
  }
}

/** Hiển thị danh sách mã lô con sau khi tách xong với cỡ chữ lớn. */
function renderSplitResults(childBatches, remainingQty) {
  $("split-result-section").hidden = false;
  $("split-result-msg").textContent = `Đã tách thành công ${childBatches.length} lô con. Số dư lô mẹ còn lại: ${formatNumber(remainingQty)} kg.`;

  // Ẩn nút lưu, đổi nút Huỷ thành Hoàn tất
  $("btn-submit-split").hidden = true;
  $("btn-cancel-split").textContent = "Hoàn tất";

  const container = $("child-batches-list");
  container.innerHTML = childBatches
    .map(
      (child, idx) => `
      <div class="child-batch-tag">
        <span class="child-batch-sub">LÔ CON #${idx + 1} · THÙNG HÀNG</span>
        <div class="child-batch-code-large font-mono">${escapeHtml(child.batch_code || `LOT-${child.id}`)}</div>
        <div class="child-batch-weight">${formatNumber(child.quantity)} kg</div>
        <span class="child-batch-sub">${escapeHtml(child.product_name)}</span>
      </div>`
    )
    .join("");

  // Cuộn xuống khu vực kết quả
  $("split-result-section").scrollIntoView({ behavior: "smooth" });
}

/* ---------------------------------- 8c. Gộp nhiều lô nông sản (T-45) --- */
function toggleMergeMode(forceState) {
  isMergeMode = typeof forceState === "boolean" ? forceState : !isMergeMode;
  const btn = $("btn-toggle-merge-mode");
  const textSpan = $("btn-toggle-merge-text");
  const actionBar = $("merge-action-bar");

  if (isMergeMode) {
    if (btn) btn.classList.add("active");
    if (textSpan) textSpan.textContent = "Thoát chế độ gộp lô";
    if (actionBar) actionBar.hidden = false;
    toast("Đã bật chế độ gộp lô. Vui lòng tích chọn các lô cần gộp.", "info");
  } else {
    if (btn) btn.classList.remove("active");
    if (textSpan) textSpan.textContent = "Bật chế độ gộp lô";
    if (actionBar) actionBar.hidden = true;
    selectedBatchIds.clear();
  }
  renderBatches();
}

function updateMergeActionBar() {
  const actionBar = $("merge-action-bar");
  if (!actionBar || !isMergeMode) return;

  const countBadge = $("merge-selected-count");
  const statusBox = $("merge-validation-status");
  const mergeBtn = $("btn-open-merge-modal");

  const selectedBatches = batches.filter((b) => selectedBatchIds.has(b.id));
  const count = selectedBatches.length;

  if (countBadge) countBadge.textContent = `Đã chọn: ${count} lô`;

  if (count === 0) {
    if (statusBox) {
      statusBox.className = "merge-status-box";
      statusBox.textContent = "Tích chọn các lô cần gộp vào cùng một mẻ.";
    }
    if (mergeBtn) mergeBtn.disabled = true;
    return;
  }

  if (count === 1) {
    if (statusBox) {
      statusBox.className = "merge-status-box";
      statusBox.textContent = "Cần chọn ít nhất 2 lô để thực hiện thao tác gộp.";
    }
    if (mergeBtn) mergeBtn.disabled = true;
    return;
  }

  const productNames = Array.from(new Set(selectedBatches.map((b) => b.product_name)));
  if (productNames.length === 1) {
    if (statusBox) {
      statusBox.className = "merge-status-box valid";
      statusBox.innerHTML = `✓ Hợp lệ: ${count} lô cùng nông sản <strong>"${escapeHtml(productNames[0])}"</strong>`;
    }
    if (mergeBtn) mergeBtn.disabled = false;
  } else {
    if (statusBox) {
      statusBox.className = "merge-status-box invalid";
      statusBox.innerHTML = `⚠ Khác loại: Các lô được chọn khác nông sản (${productNames.map((p) => `"${escapeHtml(p)}"`).join(" vs ")}). Chỉ được gộp các lô cùng loại!`;
    }
    if (mergeBtn) mergeBtn.disabled = true;
  }
}

function openMergeModal() {
  const selectedBatches = batches.filter((b) => selectedBatchIds.has(b.id));
  if (selectedBatches.length < 2) return;

  $("merge-modal-product-name").textContent = selectedBatches[0].product_name;

  const farmSelect = $("merge-modal-dest-farm");
  farmSelect.innerHTML = farms.map((f) => `<option value="${f.id}">${escapeHtml(f.name)}</option>`).join("");

  const tbody = $("merge-modal-items-body");
  tbody.innerHTML = selectedBatches
    .map(
      (b) => `
      <tr>
        <td class="font-mono"><strong>${escapeHtml(b.batch_code || ('LOT-' + b.id))}</strong></td>
        <td>${escapeHtml(farmLabel(b.farm_id))}</td>
        <td class="is-right font-mono">${formatNumber(b.quantity)} kg</td>
        <td class="is-right">
          <input type="number" class="input-extract-qty" data-batch-id="${b.id}"
                 min="0.1" max="${b.quantity}" step="0.1" value="${b.quantity}" />
        </td>
      </tr>`
    )
    .join("");

  calculateMergeTotal();
  $("merge-modal").hidden = false;
}

function calculateMergeTotal() {
  const inputs = document.querySelectorAll(".input-extract-qty");
  let total = 0;
  inputs.forEach((input) => {
    const val = Number(input.value) || 0;
    total += val;
  });
  $("merge-modal-total-qty").textContent = formatNumber(Math.round(total * 100) / 100);
}

async function executeBatchMerge() {
  const selectedBatches = batches.filter((b) => selectedBatchIds.has(b.id));
  const inputs = document.querySelectorAll(".input-extract-qty");
  const items = [];
  let hasError = false;

  inputs.forEach((input) => {
    const bId = Number(input.dataset.batchId);
    const qty = Number(input.value);
    const orig = selectedBatches.find((b) => b.id === bId);
    if (!qty || qty <= 0 || (orig && qty > orig.quantity)) {
      hasError = true;
    }
    items.push({ batch_id: bId, quantity: qty });
  });

  if (hasError) {
    toast("Khối lượng trích xuất phải > 0 và không vượt quá khối lượng từng lô.", "error");
    return;
  }

  const destFarmId = Number($("merge-modal-dest-farm").value);
  if (!destFarmId) {
    toast("Vui lòng chọn thửa đất tiếp nhận lô gộp.", "error");
    return;
  }

  const notes = $("merge-modal-notes").value.trim() || undefined;
  const todayStr = new Date().toISOString().split("T")[0];

  const payload = {
    farm_id: destFarmId,
    harvest_date: todayStr,
    items,
    notes,
  };

  try {
    const res = await apiRequest("/batches/merge", {
      method: "POST",
      body: payload,
    });

    $("merge-modal").hidden = true;
    toggleMergeMode(false);

    const newBatch = res.merged_batch || {};
    $("merge-new-batch-code").textContent = newBatch.batch_code || `LOT-${newBatch.id}`;
    $("merge-new-batch-qty").textContent = `${formatNumber(newBatch.quantity)} kg`;
    $("merge-success-modal").hidden = false;

    toast(res.message || "Gộp lô thành công!", "success");
    await loadBatches();
  } catch (error) {
    toast(`Không thể gộp lô: ${error.message}`, "error");
  }
}

/* ---------------------------------- 8d. Danh mục sản phẩm chuẩn (S-16) --- */
function canWriteProducts() {
  return session !== null && session.role === ROLE_ADMIN;
}

async function loadProducts() {
  try {
    const data = await apiRequest("/products");
    products = Array.isArray(data) ? data : [];
    renderProducts();
    updateProductsDatalist();
  } catch (error) {
    // Không ném lỗi
  }
}

function updateProductsDatalist() {
  const dl = $("products-datalist");
  if (!dl) return;
  dl.innerHTML = products
    .map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} (${escapeHtml(p.unit)})</option>`)
    .join("");
}

function renderProducts() {
  const tbody = $("product-table-body");
  if (!tbody) return;
  const canWrite = canWriteProducts();

  tbody.innerHTML = products
    .map(
      (p) => `
      <tr class="${p.id === editingProductId ? "is-editing" : ""}">
        <td class="id-cell font-mono">#${escapeHtml(p.id)}</td>
        <td><strong>${escapeHtml(p.name)}</strong></td>
        <td><code>${escapeHtml(p.unit)}</code></td>
        <td>${escapeHtml(p.description || "—")}</td>
        <td class="is-center">
          ${
            canWrite
              ? `<div class="table__actions">
                  <button class="btn btn--sm btn--primary" type="button"
                          data-action="edit" data-entity="product" data-id="${escapeHtml(p.id)}">Sửa</button>
                  <button class="btn btn--sm btn--danger" type="button"
                          data-action="delete" data-entity="product" data-id="${escapeHtml(p.id)}">Xoá</button>
                </div>`
              : `<span style="font-size: 11px; color: #64748b;">Chỉ xem</span>`
          }
        </td>
      </tr>`
    )
    .join("");

  if ($("product-empty")) {
    $("product-empty").hidden = products.length > 0;
  }
}

function productSubmitLabel() {
  return editingProductId === null ? "Thêm sản phẩm" : "Cập nhật sản phẩm";
}

async function handleProductSubmit(event) {
  event.preventDefault();
  const nameInput = $("product-name");
  const unitSelect = $("product-unit");
  const descInput = $("product-description");
  const submitBtn = $("product-submit");

  const name = nameInput.value.trim();
  const unit = unitSelect.value;
  const description = descInput.value.trim() || null;

  if (!name) {
    toast("Vui lòng nhập tên sản phẩm nông sản.", "error");
    nameInput.focus();
    return;
  }

  setButtonLoading(submitBtn, true, "Đang lưu…", productSubmitLabel());

  try {
    if (editingProductId === null) {
      await apiRequest("/products", {
        method: "POST",
        body: { name, unit, description },
      });
      toast(`Đã thêm sản phẩm "${name}" vào danh mục toàn hệ thống.`, "success");
    } else {
      await apiRequest(`/products/${editingProductId}`, {
        method: "PUT",
        body: { name, unit, description },
      });
      toast(`Đã cập nhật sản phẩm "${name}".`, "success");
    }
    resetProductForm();
    await loadProducts();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setButtonLoading(submitBtn, false, "Đang lưu…", productSubmitLabel());
  }
}

function resetProductForm() {
  editingProductId = null;
  $("product-id").value = "";
  $("product-name").value = "";
  $("product-unit").value = "kg";
  $("product-description").value = "";
  if ($("product-cancel")) $("product-cancel").hidden = true;
  if ($("product-submit-label")) $("product-submit-label").textContent = "Thêm sản phẩm";
  renderProducts();
}

function startEditProduct(id) {
  const p = products.find((item) => item.id === Number(id));
  if (!p) return;
  editingProductId = p.id;
  $("product-id").value = p.id;
  $("product-name").value = p.name;
  $("product-unit").value = p.unit;
  $("product-description").value = p.description || "";
  if ($("product-cancel")) $("product-cancel").hidden = false;
  if ($("product-submit-label")) $("product-submit-label").textContent = "Cập nhật sản phẩm";
  renderProducts();
  $("product-name").focus();
}

async function deleteProduct(id) {
  const p = products.find((item) => item.id === Number(id));
  const confirmMsg = p
    ? `Bạn có chắc muốn xoá sản phẩm "${p.name}" (#${p.id})?`
    : `Xoá sản phẩm #${id}?`;
  if (!window.confirm(confirmMsg)) return;

  try {
    await apiRequest(`/products/${id}`, { method: "DELETE" });
    toast(`Đã xoá sản phẩm #${id}.`, "success");
    if (editingProductId === Number(id)) resetProductForm();
    await loadProducts();
  } catch (error) {
    toast(`Không thể xoá sản phẩm: ${error.message}`, "error");
  }
}

/* ---------------------------------- 8e. Quản lý phiếu bàn giao (S-35) --- */
function populateHandoverOrgs() {
  const select = $("handover-recipient-org");
  if (!select) return;
  const currentOrg = session?.username || "";
  const filtered = PARTNER_ORGANIZATIONS.filter(
    (o) => !o.name.toLowerCase().includes(currentOrg.toLowerCase())
  );
  select.innerHTML =
    '<option value="">-- Chọn tổ chức đối tác nhận --</option>' +
    filtered
      .map((org) => `<option value="${org.id}" data-name="${escapeHtml(org.name)}">${escapeHtml(org.name)}</option>`)
      .join("");
}

function populateHandoverBatches() {
  const select = $("handover-batch-id");
  if (!select) return;
  select.innerHTML =
    '<option value="">-- Chọn lô nông sản bàn giao --</option>' +
    batches
      .map(
        (b) =>
          `<option value="${b.id}">${escapeHtml(b.batch_code || ('LOT-' + b.id))} — ${escapeHtml(b.product_name)} (${formatNumber(b.quantity)} kg)</option>`
      )
      .join("");
}

async function loadHandovers() {
  try {
    const data = await apiRequest("/handovers");
    handovers = Array.isArray(data) ? data : [];
    renderHandovers();
  } catch (error) {
    // Không ném lỗi
  }
}

function renderHandovers() {
  const tbody = $("handover-table-body");
  if (!tbody) return;

  tbody.innerHTML = handovers
    .map((h) => {
      const isPending = h.status === "pending" || h.status === "PENDING";
      const isAccepted = h.status === "accepted" || h.status === "completed";
      const isRejected = h.status === "rejected";

      let statusBadge = "";
      if (isPending) {
        statusBadge = `<span class="handover-badge handover-badge--pending">⏳ Chờ nhận</span>`;
      } else if (isAccepted) {
        statusBadge = `<span class="handover-badge handover-badge--accepted">✓ Đã nhận</span>`;
      } else if (isRejected) {
        statusBadge = `<span class="handover-badge handover-badge--rejected">✕ Đã từ chối</span>`;
      } else {
        statusBadge = `<span class="handover-badge">${escapeHtml(h.status)}</span>`;
      }

      const matchingBatch = batches.find((b) => b.id === h.batch_id);
      const batchCode = matchingBatch ? (matchingBatch.batch_code || `LOT-${matchingBatch.id}`) : `Lô #${h.batch_id}`;

      return `
        <tr>
          <td class="id-cell font-mono">#${escapeHtml(h.id)}</td>
          <td><strong>${escapeHtml(batchCode)}</strong> ${matchingBatch ? `(${escapeHtml(matchingBatch.product_name)})` : ""}</td>
          <td>${escapeHtml(h.sender_name || (h.sender_id ? '#' + h.sender_id : "Bên giao"))}</td>
          <td><strong>${escapeHtml(h.receiver_name || (h.receiver_id ? '#' + h.receiver_id : "Bên nhận"))}</strong></td>
          <td class="font-mono" style="font-size: 0.84rem;">${escapeHtml(h.created_at ? formatDate(h.created_at.split("T")[0]) : "—")}</td>
          <td>${escapeHtml(h.notes || "—")}</td>
          <td class="is-center">${statusBadge}</td>
          <td class="is-center">
            ${
              isPending
                ? `<div style="display: flex; gap: 6px; justify-content: center;">
                    <button class="btn btn--primary btn--sm" style="padding: 4px 10px; font-size: 12px; background: #059669;" type="button"
                            onclick="handleAcceptHandover(${h.id})">Tiếp nhận</button>
                    <button class="btn btn--sm" style="padding: 4px 10px; font-size: 12px; color: #dc2626; border-color: #fca5a5;" type="button"
                            onclick="openRejectModal(${h.id})">Từ chối</button>
                  </div>`
                : `<span style="font-size: 12px; color: #64748b;">Đã hoàn tất</span>`
            }
          </td>
        </tr>`;
    })
    .join("");

  if ($("handover-empty")) {
    $("handover-empty").hidden = handovers.length > 0;
  }
}

async function handleHandoverSubmit(event) {
  event.preventDefault();
  const batchSelect = $("handover-batch-id");
  const orgSelect = $("handover-recipient-org");
  const dateInput = $("handover-date");
  const notesInput = $("handover-notes");

  const batchId = Number(batchSelect.value);
  const orgOption = orgSelect.options[orgSelect.selectedIndex];
  const receiverName = orgOption ? (orgOption.dataset.name || orgOption.textContent) : "";
  const receiverId = Number(orgSelect.value) || null;

  if (!batchId) {
    toast("Vui lòng chọn lô nông sản cần bàn giao.", "error");
    batchSelect.focus();
    return;
  }
  if (!receiverName) {
    toast("Vui lòng chọn tổ chức đối tác nhận.", "error");
    orgSelect.focus();
    return;
  }
  if (!dateInput.value) {
    toast("Vui lòng chọn ngày bàn giao.", "error");
    dateInput.focus();
    return;
  }

  const payload = {
    batch_id: batchId,
    receiver_id: receiverId,
    receiver_name: receiverName,
    notes: notesInput.value.trim() || null,
  };

  try {
    await apiRequest("/handovers", {
      method: "POST",
      body: payload,
    });
    toast(`Đã tạo phiếu bàn giao lô sang "${receiverName}".`, "success");
    batchSelect.value = "";
    orgSelect.value = "";
    notesInput.value = "";
    await loadHandovers();
    await loadBatches();
  } catch (error) {
    toast(`Không tạo được phiếu bàn giao: ${error.message}`, "error");
  }
}

async function handleAcceptHandover(id) {
  if (!window.confirm(`Xác nhận tiếp nhận lô hàng theo phiếu bàn giao #${id}?`)) return;
  try {
    await apiRequest(`/handovers/${id}/accept`, {
      method: "POST",
      body: { notes: "Tiếp nhận lô hàng vào kho thành công." },
    });
    toast(`Đã tiếp nhận lô hàng thành công (quyền sở hữu đã được chuyển giao)!`, "success");
    await loadHandovers();
    await loadBatches();
  } catch (error) {
    toast(`Không thể tiếp nhận bàn giao: ${error.message}`, "error");
  }
}

function openRejectModal(id) {
  $("reject-handover-id").value = id;
  $("reject-handover-reason").value = "";
  $("reject-handover-modal").hidden = false;
  $("reject-handover-reason").focus();
}

function closeRejectModal() {
  $("reject-handover-modal").hidden = true;
}

async function handleRejectHandoverSubmit() {
  const id = Number($("reject-handover-id").value);
  const reason = $("reject-handover-reason").value.trim();
  if (!reason) {
    toast("Bắt buộc phải nhập lý do từ chối bàn giao.", "error");
    $("reject-handover-reason").focus();
    return;
  }

  try {
    await apiRequest(`/handovers/${id}/reject`, {
      method: "POST",
      body: { reason },
    });
    toast(`Đã từ chối tiếp nhận phiếu bàn giao #${id}.`, "info");
    closeRejectModal();
    await loadHandovers();
  } catch (error) {
    toast(`Không thể từ chối: ${error.message}`, "error");
  }
}

/* ---------------------------------- 8f. Điều hướng phân hệ (Tab Navigation) --- */
function initTabs() {
  const pills = document.querySelectorAll(".nav-tab-pill");
  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      switchTab(pill.dataset.tab);
    });
  });
}

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll(".nav-tab-pill").forEach((pill) => {
    pill.classList.toggle("active", pill.dataset.tab === tab);
  });

  const cards = {
    farms: $("farms-card"),
    batches: $("batches-card"),
    products: $("products-card"),
    handovers: $("handovers-card"),
    coldchain: $("coldchain-card"),
    orders: $("orders-card"),
    users: $("users-card"),
  };

  const isAdmin = session !== null && session.role === ROLE_ADMIN;

  Object.entries(cards).forEach(([key, el]) => {
    if (!el) return;
    if (key === "users" && !isAdmin) {
      el.hidden = true;
      return;
    }
    if (tab === "all") {
      el.hidden = false;
    } else {
      el.hidden = key !== tab;
    }
  });
}

/* ---------------------------------- 9. Dòng thời gian & Toàn vẹn chuỗi (Audit Chain) --- */
/** Định dạng chi tiết từng sự kiện trong dòng thời gian (không xuất chuỗi JSON thô). */
function formatEventDetail(ev, payload) {
  const t = ev.event_type || "";
  let badgeColor = "#2563eb";
  let icon = "📝";
  let title = t;
  let bodyHtml = "";

  if (t === "HARVEST" || t === "CREATE" || t === "HARVEST_CREATED") {
    badgeColor = "#16a34a";
    icon = "🌱";
    title = "Thu hoạch & Khởi tạo lô";
    bodyHtml = `
      <div class="event-desc">Khởi tạo mẻ thu hoạch ban đầu từ thửa đất <strong>${escapeHtml(payload.farm_name || (payload.farm_id ? '#' + payload.farm_id : ''))}</strong>.</div>
      <div class="event-chips">
        <span class="event-chip">🌾 Sản phẩm: <strong>${escapeHtml(payload.product_name || '—')}</strong></span>
        <span class="event-chip">⚖️ Sản lượng: <strong>${formatNumber(payload.quantity || payload.initial_quantity)} kg</strong></span>
        ${payload.harvest_date ? `<span class="event-chip">📅 Thu hoạch: <strong>${formatDate(payload.harvest_date)}</strong></span>` : ""}
      </div>`;
  } else if (t === "SPLIT") {
    badgeColor = "#ea580c";
    icon = "✂️";
    title = "Tách lô nông sản";
    const childDetails = payload.children || [];
    bodyHtml = `
      <div class="event-desc">Tách thành <strong>${childDetails.length || (payload.child_batch_codes || []).length}</strong> lô con:</div>
      <div class="event-chips">
        ${childDetails.map((c) => `<span class="event-chip event-chip--child">${escapeHtml(c.batch_code)}: <strong>${formatNumber(c.quantity)} kg</strong></span>`).join("")}
      </div>
      <div class="event-subnote">Số dư còn lại của lô mẹ: <strong>${formatNumber(payload.remaining_quantity)} kg</strong></div>`;
  } else if (t === "BIRTH") {
    badgeColor = "#8b5cf6";
    icon = "🐣";
    title = "Khai sinh từ lô mẹ";
    bodyHtml = `
      <div class="event-desc">Tạo ra từ đợt tách của lô mẹ <code>${escapeHtml(payload.parent_batch_code || ('LOT-' + payload.parent_batch_id))}</code>.</div>
      <div class="event-chips">
        <span class="event-chip">⚖️ Sản lượng cấp: <strong>${formatNumber(payload.initial_quantity)} kg</strong></span>
        <span class="event-chip">🏷️ Mã lô mẹ: <strong>${escapeHtml(payload.parent_batch_code || ('LOT-' + payload.parent_batch_id))}</strong></span>
      </div>`;
  } else if (t === "MERGE") {
    badgeColor = "#0284c7";
    icon = "📦";
    title = "Gộp nhiều lô thành lô mới";
    const pList = payload.parent_batches || [];
    bodyHtml = `
      <div class="event-desc">Hình thành từ việc gộp <strong>${pList.length}</strong> lô thành phần:</div>
      <div class="event-chips">
        ${pList.map((p) => `<span class="event-chip event-chip--parent">${escapeHtml(p.batch_code)}: lấy ${formatNumber(p.quantity)} kg</span>`).join("")}
      </div>
      <div class="event-subnote">Tổng sản lượng thu được: <strong>${formatNumber(payload.total_merged_quantity)} kg</strong></div>`;
  } else if (t === "MERGE_PARENT") {
    badgeColor = "#0284c7";
    icon = "➡️";
    title = "Trích sản lượng vào lô gộp";
    bodyHtml = `
      <div class="event-desc">Đã trích <strong>${formatNumber(payload.contributed_quantity)} kg</strong> vào lô mới <code>${escapeHtml(payload.target_merged_batch_code || ('LOT-' + payload.target_merged_batch_id))}</code>.</div>
      <div class="event-subnote">Sản lượng còn lại sau khi trích: <strong>${formatNumber(payload.remaining_quantity)} kg</strong></div>`;
  } else if (t === "HANDOVER_INITIATED") {
    badgeColor = "#d97706";
    icon = "📤";
    title = "Khởi tạo bàn giao đối tác";
    bodyHtml = `
      <div class="event-desc">Khởi tạo phiếu bàn giao chuyển giao sang đối tác chuỗi cung ứng.</div>
      <div class="event-chips">
        <span class="event-chip">🏢 Bên nhận: <strong>${escapeHtml(payload.receiver_name || payload.recipient_org_name || 'Đối tác')}</strong></span>
        ${payload.notes ? `<span class="event-chip">📝 Ghi chú: ${escapeHtml(payload.notes)}</span>` : ""}
      </div>`;
  } else if (t === "HANDOVER_ACCEPTED" || t === "HANDOVER_CONFIRMED") {
    badgeColor = "#059669";
    icon = "🤝";
    title = "Tiếp nhận bàn giao thành công";
    bodyHtml = `
      <div class="event-desc">Bên nhận đã nghiệm thu và xác nhận tiếp nhận lô hàng vào hệ thống kho.</div>
      <div class="event-chips">
        <span class="event-chip">🏢 Đơn vị tiếp nhận: <strong>${escapeHtml(payload.receiver_name || ev.organization_name || 'Bên nhận')}</strong></span>
        ${payload.notes ? `<span class="event-chip">📝 Ghi chú: ${escapeHtml(payload.notes)}</span>` : ""}
      </div>`;
  } else if (t === "OWNER_CHANGED") {
    badgeColor = "#0891b2";
    icon = "🏢";
    title = "Chuyển giao quyền sở hữu lô";
    bodyHtml = `
      <div class="event-desc">Quyền sở hữu lô hàng đã được cập nhật chính thức.</div>
      <div class="event-chips">
        <span class="event-chip">Từ: <strong>${escapeHtml(payload.previous_owner || 'Chủ cũ')}</strong></span>
        <span class="event-chip">Sang: <strong>${escapeHtml(payload.new_owner || 'Chủ mới')}</strong></span>
      </div>`;
  } else if (t === "HANDOVER_REJECTED") {
    badgeColor = "#dc2626";
    icon = "🚫";
    title = "Từ chối tiếp nhận bàn giao";
    bodyHtml = `
      <div class="event-desc" style="color: #b91c1c;">Lô hàng bị từ chối tiếp nhận. Lô hàng giữ nguyên chủ sở hữu ban đầu.</div>
      <div class="event-chips">
        <span class="event-chip event-chip--danger">⚠️ Lý do: <strong>${escapeHtml(payload.reason || payload.notes || 'Không rõ lý do')}</strong></span>
        <span class="event-chip">🏢 Đơn vị từ chối: <strong>${escapeHtml(payload.rejecter_name || ev.actor || 'Bên nhận')}</strong></span>
      </div>`;
  } else if (t === "TEMPERATURE_EXCURSION" || t === "COLDCHAIN_ALERT") {
    badgeColor = "#ef4444";
    icon = "❄️";
    title = "Cảnh báo vi phạm chuỗi lạnh";
    bodyHtml = `
      <div class="event-desc" style="color: #b91c1c;">Nhiệt độ bảo quản vượt ngưỡng cho phép trong quá trình lưu trữ / vận chuyển.</div>
      <div class="event-chips">
        <span class="event-chip event-chip--danger">🌡️ Nhiệt độ ghi nhận: <strong>${escapeHtml(payload.temperature ?? '—')}°C</strong></span>
        <span class="event-chip">Ngưỡng chuẩn: <strong>${escapeHtml(payload.min_temp ?? '—')}°C - ${escapeHtml(payload.max_temp ?? '—')}°C</strong></span>
        ${payload.sensor_id ? `<span class="event-chip">Cảm biến: <code>${escapeHtml(payload.sensor_id)}</code></span>` : ""}
      </div>`;
  } else if (t === "RECALL_ISSUED") {
    badgeColor = "#b91c1c";
    icon = "🚨";
    title = "Lệnh thu hồi khẩn cấp";
    bodyHtml = `
      <div class="event-desc" style="color: #991b1b; font-weight: 600;">Cơ quan thẩm quyền đã kích hoạt lệnh thu hồi đối với lô hàng này.</div>
      <div class="event-chips">
        <span class="event-chip event-chip--danger">Mã lệnh: <strong>${escapeHtml(payload.order_code || '—')}</strong></span>
        <span class="event-chip">Lý do: <strong>${escapeHtml(payload.reason || '—')}</strong></span>
      </div>`;
  } else {
    const keys = Object.keys(payload);
    badgeColor = "#64748b";
    icon = "📌";
    title = t || "Sự kiện chuỗi";
    bodyHtml = `
      <div class="event-chips">
        ${keys.map((k) => `<span class="event-chip"><strong>${escapeHtml(k)}:</strong> ${escapeHtml(typeof payload[k] === "object" ? JSON.stringify(payload[k]) : String(payload[k]))}</span>`).join("")}
      </div>`;
  }

  return { badgeColor, icon, title, bodyHtml };
}

/** Mở modal dòng thời gian của lô và gọi GET /batches/{id}/events */
async function openTimelineModal(batchId) {
  activeTimelineBatchId = batchId;
  const batch = batches.find((b) => b.id === batchId);
  const backdrop = $("timeline-modal-backdrop");
  if (!backdrop) return;

  $("timeline-batch-code").textContent = batch?.batch_code || `LOT-${batchId}`;
  $("timeline-batch-product").textContent = batch ? `· ${batch.product_name} (${formatNumber(batch.quantity)} kg)` : "";
  $("timeline-integrity-badge").textContent = "Đang kiểm tra chuỗi hash…";
  $("timeline-integrity-badge").style.color = "#4b5563";
  $("timeline-events-container").innerHTML = "";
  $("timeline-empty").hidden = true;
  backdrop.hidden = false;

  try {
    const data = await apiRequest(`/batches/${batchId}/events`);
    const events = data.events || [];

    // Cập nhật trạng thái toàn vẹn (Tamper-proof Cryptographic Verification)
    if (data.is_valid) {
      $("timeline-integrity-badge").textContent = "✓ Toàn vẹn (100% Valid Hash-Chain)";
      $("timeline-integrity-badge").style.color = "#059669";
    } else {
      $("timeline-integrity-badge").textContent = `⚠ Cảnh báo sai lệch (Tại sự kiện #${data.tampered_index})`;
      $("timeline-integrity-badge").style.color = "#dc2626";
    }

    if (events.length === 0) {
      $("timeline-empty").hidden = false;
      return;
    }

    $("timeline-events-container").innerHTML = events
      .map((ev, idx) => {
        let payloadObj = {};
        try {
          payloadObj = typeof ev.payload === "string" ? JSON.parse(ev.payload) : ev.payload;
        } catch (e) {
          payloadObj = { raw: ev.payload };
        }

        const { badgeColor, icon, title, bodyHtml } = formatEventDetail(ev, payloadObj);

        return `
          <div style="position: relative; margin-bottom: 20px;">
            <div style="position: absolute; left: -31px; top: 0; width: 22px; height: 22px; border-radius: 50%; background: #fff; border: 2px solid ${badgeColor}; display: flex; align-items: center; justify-content: center; font-size: 11px;">
              ${icon}
            </div>
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div>
                  <span style="font-weight: 700; color: ${badgeColor}; font-size: 0.9rem; text-transform: uppercase;">${escapeHtml(title)}</span>
                  <span style="font-size: 0.8rem; color: #64748b; margin-left: 8px;">bởi <strong>${escapeHtml(ev.actor)}</strong> (${escapeHtml(ev.organization_name || ev.organization || 'Hệ thống')})</span>
                </div>
                <span style="font-size: 0.78rem; color: #94a3b8; font-family: monospace;">${escapeHtml(new Date(ev.timestamp).toLocaleString("vi-VN"))}</span>
              </div>
              <div style="font-size: 0.88rem; color: #334155;">
                ${bodyHtml}
              </div>
              <div style="margin-top: 8px; padding-top: 6px; border-top: 1px solid #f1f5f9; display: flex; justify-content: space-between; font-size: 0.72rem; color: #94a3b8; font-family: monospace;">
                <span>HASH: ${escapeHtml(ev.hash.slice(0, 16))}…</span>
                <span>PREV: ${escapeHtml(ev.previous_hash.slice(0, 16))}…</span>
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  } catch (error) {
    $("timeline-integrity-badge").textContent = "Lỗi khi tải";
    $("timeline-integrity-badge").style.color = "#dc2626";
    toast(`Không tải được dòng thời gian: ${error.message}`, "error");
  }
}

let activeTimelineBatchId = null;

function closeTimelineModal() {
  const backdrop = $("timeline-modal-backdrop");
  if (backdrop) backdrop.hidden = true;
  activeTimelineBatchId = null;
}

/** Tải xuống tệp PDF hồ sơ truy xuất nguồn gốc */
async function handleExportPdf() {
  if (!activeTimelineBatchId) {
    toast("Vui lòng chọn một lô để xuất hồ sơ!", "error");
    return;
  }

  const btn = $("btn-export-pdf");
  setButtonLoading(btn, true, "Đang tạo PDF…", "Xuất Hồ Sơ PDF");

  try {
    const url = `${API_BASE_URL}/batches/${activeTimelineBatchId}/export-pdf`;
    const headers = authHeader();
    const response = await fetch(url, { headers });

    if (!response.ok) {
      throw new Error(`Máy chủ báo lỗi HTTP ${response.status}`);
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = `Ho-so-truy-xuat-BATCH-${activeTimelineBatchId}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(downloadUrl);

    toast("Đã xuất hồ sơ truy xuất PDF thành công!", "success");
  } catch (error) {
    toast(`Xuất hồ sơ PDF thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang tạo PDF…", "Xuất Hồ Sơ PDF (Đính Kèm Biên Bản)");
  }
}

/** Mở modal Bản Đồ Hành Trình Công Khai (S-06) */
function openPublicMapModal(batchIdentifier = "") {
  const modal = $("action-modal-public-map");
  if (!modal) return;
  modal.hidden = false;
  const input = $("public-map-search-code");
  if (input) {
    if (batchIdentifier) {
      input.value = batchIdentifier;
      loadPublicMap(batchIdentifier);
    } else {
      input.focus();
    }
  }
}

/** Đóng modal Bản Đồ */
function closePublicMapModal() {
  const modal = $("action-modal-public-map");
  if (modal) modal.hidden = true;
}

/** Tải và hiển thị bản đồ hành trình công khai (S-06) */
async function loadPublicMap(batchIdentifier) {
  const container = $("public-map-render-area");
  if (!container || !batchIdentifier) return;

  container.innerHTML = `<div style="text-align: center; color: #0284c7; padding: 30px 0;">Đang dựng hành trình chuỗi cung ứng công khai...</div>`;

  try {
    const mapData = await apiRequest(`/batches/code/${encodeURIComponent(batchIdentifier)}/map`, { auth: false });
    const allStops = [mapData.origin_point, ...(mapData.waypoints || [])];

    let stopsListHtml = `
      <div style="margin-bottom: 16px;">
        <h5 style="margin: 0 0 8px; font-size: 14px; font-weight: 700; color: #0f172a;">Lô: ${escapeHtml(mapData.product_name)} [Mã: ${escapeHtml(mapData.batch_code)}]</h5>
        <div style="font-size: 12px; color: #64748b;">Hành trình bao gồm ${allStops.length} điểm dừng chính qua các cấp hành chính:</div>
      </div>
    `;

    const nodesHtml = allStops.map((stop, idx) => {
      const isOrigin = idx === 0;
      const color = isOrigin ? "#16a34a" : "#0284c7";
      const bg = isOrigin ? "#f0fdf4" : "#f0f9ff";
      const badge = isOrigin ? "ĐIỂM XUẤT XỨ (VÙNG TRỒNG)" : `ĐIỂM DỪNG #${stop.order}`;

      return `
        <div style="display: flex; gap: 14px; margin-bottom: 12px; position: relative;">
          <div style="display: flex; flex-direction: column; align-items: center; width: 32px; flex-shrink: 0;">
            <div style="width: 28px; height: 28px; border-radius: 50%; background: ${color}; color: white; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 12px; z-index: 2;">
              ${stop.order}
            </div>
            ${idx < allStops.length - 1 ? `<div style="width: 2px; flex-grow: 1; background: #cbd5e1; margin: 4px 0;"></div>` : ''}
          </div>

          <div style="flex-grow: 1; background: ${bg}; border: 1px solid ${isOrigin ? '#bbf7d0' : '#bae6fd'}; border-radius: 8px; padding: 12px 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 4px;">
              <div>
                <span style="background: ${color}; color: white; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 10px;">${badge}</span>
                <strong style="margin-left: 6px; color: #0f172a; font-size: 13px;">${escapeHtml(stop.name)}</strong>
              </div>
              <div style="font-family: monospace; font-size: 11px; color: #64748b; background: #ffffff; padding: 2px 8px; border-radius: 4px; border: 1px solid #e2e8f0;">
                📍 Toạ độ cấp Xã/Huyện: ${stop.latitude.toFixed(3)}°N, ${stop.longitude.toFixed(3)}°E
              </div>
            </div>
            <div style="font-size: 12px; color: #334155; margin-bottom: 4px;">
              <strong>Địa bàn hiển thị:</strong> ${escapeHtml(stop.location_level)} | <strong>Đơn vị:</strong> ${escapeHtml(stop.organization)}
            </div>
            <div style="font-size: 12px; color: #047857;">
              ✓ ${escapeHtml(stop.action)}
            </div>
          </div>
        </div>
      `;
    }).join("");

    container.innerHTML = stopsListHtml + nodesHtml;
  } catch (err) {
    container.innerHTML = `<div style="padding: 20px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; color: #dc2626; text-align: center;">Không tìm thấy bản đồ cho mã lô "${escapeHtml(batchIdentifier)}": ${escapeHtml(err.message)}</div>`;
  }
}

/* --------------------------- 9b. Giám Sát Lệnh Kiểm Tra & Thu Hồi (Inspector Orders) --- */
/** GET /orders -> tải danh sách các lệnh kiểm tra / thu hồi */
async function loadOrders() {
  try {
    const data = await apiRequest("/orders");
    orders = Array.isArray(data) ? data : [];
    renderOrders();
  } catch (error) {
    // Không làm gián đoạn nếu là tài khoản chưa được phân quyền
    orders = [];
    renderOrders();
  }
}

/** Vẽ bảng danh sách các lệnh thanh tra / thu hồi */
function renderOrders() {
  const tbody = $("order-table-body");
  if (!tbody) return;

  tbody.innerHTML = orders
    .map((order) => {
      const isCompleted = order.status === "COMPLETED";
      const statusBadge = isCompleted
        ? `<span style="background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 0.8rem;">ĐÃ HOÀN TẤT</span>`
        : `<span style="background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 0.8rem;">ĐANG THỰC THI</span>`;

      const progressColor = isCompleted ? "#059669" : "#2563eb";
      const pendingText = order.pending_organizations && order.pending_organizations.length > 0
        ? `<span style="color: #d97706; font-size: 0.82rem; font-weight: 600;">Chưa xong: ${escapeHtml(order.pending_organizations.join(", "))}</span>`
        : `<span style="color: #059669; font-size: 0.82rem; font-weight: 600;">✓ Tất cả đã xác nhận</span>`;

      return `
        <tr>
          <td class="font-mono" style="font-weight: 700; color: #4338ca;">${escapeHtml(order.order_code)}</td>
          <td>
            <div style="font-weight: 600; color: #1e293b;">${escapeHtml(order.title)}</div>
            <div style="font-size: 0.8rem; color: #64748b; margin-top: 2px;">Lý do: ${escapeHtml(order.reason)} ${order.batch_id ? `(Lô #${order.batch_id})` : ""}</div>
          </td>
          <td class="is-center font-mono" style="font-weight: 800; font-size: 1.05rem; color: ${progressColor};">
            ${escapeHtml(order.progress_ratio)}
          </td>
          <td>${pendingText}</td>
          <td>${statusBadge}</td>
          <td class="is-center">
            <button class="btn btn--sm" style="background: #e0e7ff; color: #4338ca; border: 1px solid #c7d2fe;" type="button"
                    data-action="view-order" data-id="${order.id}">Chi tiết</button>
          </td>
        </tr>
      `;
    })
    .join("");

  if ($("order-empty")) {
    $("order-empty").hidden = orders.length > 0;
  }
}

/** Xử lý tạo lệnh kiểm tra mới */
async function handleOrderSubmit(event) {
  event.preventDefault();
  const title = $("order-title").value.trim();
  const reason = $("order-reason").value.trim();
  const batchIdVal = $("order-batch-id").value.trim();
  const targetsRaw = $("order-targets").value.trim();

  if (!title || !reason || !targetsRaw) {
    toast("Vui lòng điền đầy đủ tiêu đề, lý do và danh sách các bên liên quan.", "error");
    return;
  }

  const targetOrganizations = targetsRaw.split(",").map((s) => s.trim()).filter(Boolean);
  if (targetOrganizations.length === 0) {
    toast("Cần ít nhất một tổ chức liên quan.", "error");
    return;
  }

  const payload = {
    title,
    reason,
    batch_id: batchIdVal ? Number(batchIdVal) : null,
    target_organizations: targetOrganizations,
  };

  const btn = $("order-submit");
  setButtonLoading(btn, true, "Đang ban hành…", "Ban hành lệnh kiểm tra mới");

  try {
    const res = await apiRequest("/orders", {
      method: "POST",
      body: payload,
    });
    toast(`Đã ban hành lệnh ${res.order_code} thành công!`, "success");
    $("order-form").reset();
    $("order-targets").value = "HTX Trồng Trọt, Đơn Vị Vận Tải Lạnh, Kho Trung Chuyển, Nhà Máy Đóng Gói, Siêu Thị Phân Phối";
    await loadOrders();
  } catch (error) {
    toast(`Không thể ban hành lệnh: ${error.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang ban hành…", "Ban hành lệnh kiểm tra mới");
  }
}

/** Mở modal chi tiết lệnh và danh sách xác nhận */
async function openOrderModal(orderId) {
  activeViewingOrderId = orderId;
  const backdrop = $("order-modal-backdrop");
  if (!backdrop) return;

  try {
    const order = await apiRequest(`/orders/${orderId}`);
    $("order-modal-code").textContent = order.order_code;
    $("order-modal-title").textContent = order.title;
    $("order-modal-reason").textContent = `${order.reason} ${order.batch_id ? `(Gắn liền với Lô #${order.batch_id})` : ""}`;
    $("order-modal-progress-badge").textContent = order.progress_ratio;

    const isCompleted = order.status === "COMPLETED";
    const statusBadge = $("order-modal-status-badge");
    statusBadge.textContent = isCompleted ? "HOÀN TẤT & ĐÃ ĐÓNG HỒ SƠ" : "ĐANG THỰC THI";
    statusBadge.style.color = isCompleted ? "#059669" : "#2563eb";

    const pendingBanner = $("order-modal-pending-banner");
    const pendingList = $("order-modal-pending-list");
    if (order.pending_organizations && order.pending_organizations.length > 0) {
      pendingBanner.hidden = false;
      pendingList.textContent = order.pending_organizations.join(" • ");
    } else {
      pendingBanner.hidden = true;
    }

    const tbody = $("order-targets-table-body");
    tbody.innerHTML = (order.targets || [])
      .map((t) => {
        const isTargetDone = t.status === "CONFIRMED";
        const sttTag = isTargetDone
          ? `<span style="color: #059669; font-weight: 700; font-size: 0.8rem; background: #ecfdf5; border: 1px solid #a7f3d0; padding: 2px 8px; border-radius: 4px;">✓ Đã xác nhận</span>`
          : `<span style="color: #d97706; font-weight: 700; font-size: 0.8rem; background: #fffbeb; border: 1px solid #fde68a; padding: 2px 8px; border-radius: 4px;">⏳ Đang chờ</span>`;

        const confirmBtn = isTargetDone
          ? `<span style="color: #94a3b8; font-size: 0.8rem;">Đã xong</span>`
          : `<button class="btn btn--primary btn--sm" type="button" data-action="confirm-target" data-order-id="${order.id}" data-org="${escapeHtml(t.org_name)}">Xác nhận</button>`;

        return `
          <tr>
            <td style="font-weight: 600; color: #1e293b;">${escapeHtml(t.org_name)}</td>
            <td class="is-center">${sttTag}</td>
            <td style="font-size: 0.8rem; font-family: monospace; color: #64748b;">${t.confirmed_at ? new Date(t.confirmed_at).toLocaleString("vi-VN") : "—"}</td>
            <td style="font-size: 0.85rem; color: #475569;">${escapeHtml(t.note || (isTargetDone ? "Đã phản hồi theo yêu cầu" : "Chưa phản hồi"))}</td>
            <td class="is-center">${confirmBtn}</td>
          </tr>
        `;
      })
      .join("");

    backdrop.hidden = false;
  } catch (error) {
    toast(`Không tải được chi tiết lệnh: ${error.message}`, "error");
  }
}

function closeOrderModal() {
  const backdrop = $("order-modal-backdrop");
  if (backdrop) backdrop.hidden = true;
}

/** Xác nhận lệnh cho một bên liên quan */
async function handleTargetConfirm(orderId, orgName) {
  const note = prompt(`Nhập ghi chú phản hồi/xác nhận cho bên "${orgName}":`, `Đã kiểm tra an toàn tại ${orgName}`);
  if (note === null) return; // Bấm Cancel

  try {
    const res = await apiRequest(`/orders/${orderId}/confirm`, {
      method: "POST",
      body: {
        org_name: orgName,
        note: note.trim() || `Xác nhận từ ${orgName}`,
      },
    });

    toast(res.message, "success");
    await loadOrders();
    await openOrderModal(orderId); // Tải lại modal để cập nhật tiến độ
  } catch (error) {
    toast(`Xác nhận thất bại: ${error.message}`, "error");
  }
}

/* ------------------------------------------------ 8b. Chuỗi Lạnh & Ngưỡng --- */
/** Tải danh sách cấu hình ngưỡng cho từng loại sản phẩm */
async function loadThresholds() {
  try {
    const data = await apiRequest("/cold-chain/thresholds");
    thresholds = Array.isArray(data) ? data : [];
    renderThresholds();
  } catch (error) {
    thresholds = [];
    renderThresholds();
  }
}

/** Hiển thị danh sách ngưỡng trên bảng */
function renderThresholds() {
  const tbody = $("threshold-table-body");
  if (!tbody) return;

  tbody.innerHTML = thresholds
    .map(
      (t) => `
      <tr data-threshold-id="${t.id}">
        <td class="id-cell">${t.id}</td>
        <td><strong style="color: #0369a1;">${escapeHtml(t.product_type)}</strong></td>
        <td class="is-center font-mono" style="color: #0284c7; font-weight: 600;">${t.temp_min}°C</td>
        <td class="is-center font-mono" style="color: #e11d48; font-weight: 600;">${t.temp_max}°C</td>
        <td class="is-center font-mono">${t.delay_minutes} phút</td>
        <td style="color: #64748b; font-size: 0.85rem;">${escapeHtml(t.description || "—")}</td>
        <td class="is-center">
          <button class="btn btn--secondary btn--sm" type="button" data-action="edit-threshold" data-id="${t.id}">Sửa</button>
        </td>
      </tr>`
    )
    .join("");
}

/** Xử lý lưu cấu hình ngưỡng */
async function handleThresholdSubmit(e) {
  e.preventDefault();
  const btn = $("threshold-submit");
  const pType = $("threshold-product-type").value.trim();
  const tMin = parseFloat($("threshold-temp-min").value);
  const tMax = parseFloat($("threshold-temp-max").value);
  const delay = parseInt($("threshold-delay").value, 10);
  const desc = $("threshold-desc").value.trim();

  if (tMax <= tMin) {
    toast("Ngưỡng trên phải lớn hơn ngưỡng dưới!", "error");
    return;
  }

  setButtonLoading(btn, true, "Đang lưu…", "Lưu cấu hình ngưỡng");
  try {
    if (editingThresholdId) {
      await apiRequest(`/cold-chain/thresholds/${editingThresholdId}`, {
        method: "PUT",
        body: {
          temp_min: tMin,
          temp_max: tMax,
          delay_minutes: delay,
          description: desc,
        },
      });
      toast(`Đã cập nhật ngưỡng cho loại "${pType}" thành công!`, "success");
      editingThresholdId = null;
    } else {
      await apiRequest("/cold-chain/thresholds", {
        method: "POST",
        body: {
          product_type: pType,
          temp_min: tMin,
          temp_max: tMax,
          delay_minutes: delay,
          description: desc,
        },
      });
      toast(`Đã thêm cấu hình ngưỡng cho loại "${pType}"!`, "success");
    }
    $("threshold-form").reset();
    await loadThresholds();
  } catch (error) {
    toast(`Không thể lưu cấu hình ngưỡng: ${error.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang lưu…", "Lưu cấu hình ngưỡng");
  }
}

/** Tính toán ngưỡng hiệu dụng cho chuyến xe chở nhiều sản phẩm */
async function handleCalcEffectiveThreshold(productInputStr) {
  const inputStr = productInputStr || $("multi-product-input").value;
  const ptypes = inputStr.split(",").map((s) => s.trim()).filter(Boolean);
  if (ptypes.length === 0) {
    toast("Vui lòng nhập ít nhất một loại sản phẩm!", "error");
    return;
  }

  const resContainer = $("effective-threshold-result");
  const summaryEl = $("effective-summary-text");
  const warningEl = $("effective-warning-box");

  try {
    const data = await apiRequest("/cold-chain/effective-threshold", {
      method: "POST",
      body: { product_types: ptypes },
    });

    resContainer.hidden = false;
    summaryEl.innerHTML = `
      <div style="margin-bottom: 6px; color: #0f766e;">✓ ${escapeHtml(data.strictest_rule_summary)}</div>
      <div style="display: flex; gap: 16px; margin-top: 6px; font-family: monospace; font-size: 0.85rem;">
        <span>Ngưỡng dưới hiệu dụng: <strong style="color: #0284c7;">${data.effective_temp_min}°C</strong></span>
        <span>Ngưỡng trên hiệu dụng: <strong style="color: #e11d48;">${data.effective_temp_max}°C</strong></span>
        <span>Độ trễ nghiêm ngặt: <strong style="color: #d97706;">${data.effective_delay_minutes} phút</strong></span>
      </div>
    `;

    if (!data.is_compatible) {
      warningEl.hidden = false;
      warningEl.textContent = data.compatibility_warning;
    } else {
      warningEl.hidden = true;
    }
  } catch (error) {
    toast(`Lỗi tính ngưỡng: ${error.message}`, "error");
  }
}

/** Tải danh sách vi phạm chuỗi lạnh */
async function loadViolations() {
  try {
    const data = await apiRequest("/cold-chain/violations");
    violations = Array.isArray(data) ? data : [];
    renderViolations();
  } catch (error) {
    violations = [];
    renderViolations();
  }
}

/** Render danh sách vi phạm */
function renderViolations() {
  const tbody = $("violation-table-body");
  const empty = $("violation-empty");
  if (!tbody) return;

  if (violations.length === 0) {
    tbody.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }

  if (empty) empty.hidden = true;
  tbody.innerHTML = violations
    .map(
      (v) => `
      <tr>
        <td class="font-mono"><strong>${escapeHtml(v.shipment_code)}</strong></td>
        <td><span class="role-pill" style="background: #f1f5f9; color: #334155;">${escapeHtml(v.product_types.join(", "))}</span></td>
        <td class="is-center font-mono" style="color: #dc2626; font-weight: 700;">${v.recorded_temperature}°C</td>
        <td class="is-center font-mono">${v.duration_minutes} phút</td>
        <td class="is-center font-mono" style="font-size: 0.8rem; color: #475569;">[${v.applied_temp_min}°C, ${v.applied_temp_max}°C] (delay ${v.applied_delay_minutes}m)</td>
        <td style="color: #b91c1c; font-size: 0.85rem;">${escapeHtml(v.violation_reason)}</td>
        <td style="font-size: 0.8rem; font-family: monospace; color: #64748b;">${new Date(v.timestamp).toLocaleString("vi-VN")}</td>
      </tr>`
    )
    .join("");
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

/** Vẽ bảng tài khoản (chỉ username + role; backend không trả mật khẩu). */
function renderUsers() {
  $("user-table-body").innerHTML = users
    .map(
      (user) => `
      <tr>
        <td class="id-cell">${escapeHtml(user.id)}</td>
        <td>${escapeHtml(user.username)}</td>
        <td><code>${escapeHtml(user.role)}</code></td>
      </tr>`
    )
    .join("");

  $("user-empty").hidden = users.length > 0;
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

  // Modal Tách Lô (T-40)
  $("btn-add-split-row").addEventListener("click", addSplitRow);
  $("btn-submit-split").addEventListener("click", handleSplitSubmit);
  $("btn-cancel-split").addEventListener("click", closeSplitModal);
  $("split-modal-close").addEventListener("click", closeSplitModal);

  // Modal Dòng Thời Gian (Audit Chain)
  if ($("timeline-modal-close")) {
    $("timeline-modal-close").addEventListener("click", closeTimelineModal);
  }
  if ($("btn-close-timeline")) {
    $("btn-close-timeline").addEventListener("click", closeTimelineModal);
  }
  if ($("btn-export-pdf")) {
    $("btn-export-pdf").addEventListener("click", handleExportPdf);
  }

  // Lệnh kiểm tra / Thu hồi khẩn cấp (T-56)
  if ($("order-form")) {
    $("order-form").addEventListener("submit", handleOrderSubmit);
  }
  if ($("order-modal-close")) {
    $("order-modal-close").addEventListener("click", closeOrderModal);
  }
  if ($("btn-close-order")) {
    $("btn-close-order").addEventListener("click", closeOrderModal);
  }

  // Event delegation cho danh sách lệnh
  const orderTableBody = $("order-table-body");
  if (orderTableBody) {
    orderTableBody.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action='view-order']");
      if (!btn) return;
      const orderId = Number(btn.dataset.id);
      openOrderModal(orderId);
    });
  }

  // Event delegation cho xác nhận từng bên trong modal lệnh
  const orderTargetsBody = $("order-targets-table-body");
  if (orderTargetsBody) {
    orderTargetsBody.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action='confirm-target']");
      if (!btn) return;
      const orderId = Number(btn.dataset.orderId);
      const org = btn.dataset.org;
      handleTargetConfirm(orderId, org);
    });
  }

  // Cấu hình Ngưỡng Chuỗi Lạnh (Section 5)
  if ($("threshold-form")) {
    $("threshold-form").addEventListener("submit", handleThresholdSubmit);
  }
  if ($("btn-calc-effective")) {
    $("btn-calc-effective").addEventListener("click", () => handleCalcEffectiveThreshold());
  }
  if ($("btn-test-conflict")) {
    $("btn-test-conflict").addEventListener("click", () => {
      $("multi-product-input").value = "Rau lá, Thịt đông lạnh";
      handleCalcEffectiveThreshold("Rau lá, Thịt đông lạnh");
    });
  }

  // Sửa threshold trên bảng
  const thresholdTableBody = $("threshold-table-body");
  if (thresholdTableBody) {
    thresholdTableBody.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action='edit-threshold']");
      if (!btn) return;
      const tId = Number(btn.dataset.id);
      const item = thresholds.find((t) => t.id === tId);
      if (!item) return;
      editingThresholdId = tId;
      $("threshold-product-type").value = item.product_type;
      $("threshold-temp-min").value = item.temp_min;
      $("threshold-temp-max").value = item.temp_max;
      $("threshold-delay").value = item.delay_minutes;
      $("threshold-desc").value = item.description || "";
      toast(`Đang sửa cấu hình ngưỡng cho "${item.product_type}".`, "info");
      $("threshold-temp-min").focus();
    });
  }

  // Lắng nghe sự thay đổi trên bảng dòng tách lô (input khối lượng & ghi chú, nút xoá dòng)
  $("split-rows-body").addEventListener("input", (e) => {
    const input = e.target;
    const rowEl = input.closest("tr[data-row-id]");
    if (!rowEl) return;
    const rowId = Number(rowEl.dataset.rowId);
    const row = splitRows.find((r) => r.id === rowId);
    if (!row) return;

    if (input.classList.contains("split-input-qty")) {
      row.quantity = input.value;
    } else if (input.classList.contains("split-input-note")) {
      row.note = input.value;
    }
    updateSplitCalculation();
  });

  $("split-rows-body").addEventListener("click", (e) => {
    const btnDel = e.target.closest("button[data-action='del-row']");
    if (!btnDel) return;
    const rowId = Number(btnDel.dataset.id);
    removeSplitRow(rowId);
  });

  // Cột "Thao tác" của các bảng dùng event delegation: nội dung bảng được vẽ lại
  // liên tục nên chỉ gắn 1 listener cho mỗi <tbody> thay vì gắn cho từng nút.
  $("farm-table-body").addEventListener("click", handleTableAction);
  $("batch-table-body").addEventListener("click", handleTableAction);
  if ($("product-table-body")) {
    $("product-table-body").addEventListener("click", handleTableAction);
  }

  // Lắng nghe chọn checkbox gộp lô trong bảng lô
  $("batch-table-body").addEventListener("change", (e) => {
    if (e.target.classList.contains("batch-merge-cb")) {
      const bId = Number(e.target.dataset.batchId);
      if (e.target.checked) {
        selectedBatchIds.add(bId);
      } else {
        selectedBatchIds.delete(bId);
      }
      updateMergeActionBar();
    }
  });

  // Lắng nghe thay đổi khối lượng trích xuất trong modal gộp lô
  const mergeItemsBody = $("merge-modal-items-body");
  if (mergeItemsBody) {
    mergeItemsBody.addEventListener("input", (e) => {
      if (e.target.classList.contains("input-extract-qty")) {
        calculateMergeTotal();
      }
    });
  }

  // Sự kiện nút gộp lô & modal gộp lô (T-45)
  if ($("btn-toggle-merge-mode")) {
    $("btn-toggle-merge-mode").addEventListener("click", () => toggleMergeMode());
  }
  if ($("btn-open-merge-modal")) {
    $("btn-open-merge-modal").addEventListener("click", openMergeModal);
  }
  if ($("btn-close-merge-modal")) {
    $("btn-close-merge-modal").addEventListener("click", () => $("merge-modal").hidden = true);
  }
  if ($("btn-cancel-merge-modal")) {
    $("btn-cancel-merge-modal").addEventListener("click", () => $("merge-modal").hidden = true);
  }
  if ($("btn-confirm-merge")) {
    $("btn-confirm-merge").addEventListener("click", executeBatchMerge);
  }
  if ($("btn-close-merge-success")) {
    $("btn-close-merge-success").addEventListener("click", () => $("merge-success-modal").hidden = true);
  }

  // Sự kiện danh mục sản phẩm dùng chung (S-16)
  if ($("product-form")) {
    $("product-form").addEventListener("submit", handleProductSubmit);
  }
  if ($("product-cancel")) {
    $("product-cancel").addEventListener("click", () => cancelEdit("product"));
  }

  // Sự kiện bàn giao chuỗi cung ứng (S-35)
  if ($("handover-form")) {
    $("handover-form").addEventListener("submit", handleHandoverSubmit);
  }
  if ($("btn-close-reject-modal")) {
    $("btn-close-reject-modal").addEventListener("click", closeRejectModal);
  }
  if ($("btn-cancel-reject-modal")) {
    $("btn-cancel-reject-modal").addEventListener("click", closeRejectModal);
  }
  if ($("btn-confirm-reject")) {
    $("btn-confirm-reject").addEventListener("click", handleRejectHandoverSubmit);
  }

  // Tìm kiếm & lọc lô nông sản có nút tìm, nút xóa và debounce
  const batchFilter = $("batch-filter-search");
  if (batchFilter) {
    let debounceTimer;
    batchFilter.addEventListener("input", (e) => {
      const val = e.target.value.trim();
      if ($("btn-batch-search-clear")) {
        $("btn-batch-search-clear").hidden = !val;
      }
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        loadBatches(val);
      }, 350);
    });

    batchFilter.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        loadBatches(e.target.value.trim());
      }
    });
  }

  if ($("btn-batch-search")) {
    $("btn-batch-search").addEventListener("click", () => {
      loadBatches($("batch-filter-search")?.value.trim() || "");
    });
  }

  if ($("btn-batch-search-clear")) {
    $("btn-batch-search-clear").addEventListener("click", () => {
      $("batch-filter-search").value = "";
      $("btn-batch-search-clear").hidden = true;
      loadBatches("");
    });
  }

  // Sự kiện Bản đồ hành trình công khai (S-06)
  if ($("btn-open-public-map")) {
    $("btn-open-public-map").addEventListener("click", () => openPublicMapModal());
  }
  if ($("btn-open-map-from-timeline")) {
    $("btn-open-map-from-timeline").addEventListener("click", () => {
      if (activeTimelineBatchId) {
        openPublicMapModal(String(activeTimelineBatchId));
      } else {
        openPublicMapModal();
      }
    });
  }
  if ($("btn-close-public-map")) {
    $("btn-close-public-map").addEventListener("click", closePublicMapModal);
  }
  if ($("btn-close-public-map-footer")) {
    $("btn-close-public-map-footer").addEventListener("click", closePublicMapModal);
  }
  if ($("btn-search-public-map")) {
    $("btn-search-public-map").addEventListener("click", () => {
      const code = $("public-map-search-code").value.trim();
      if (!code) {
        toast("Vui lòng nhập mã lô hoặc ID lô để xem bản đồ.", "error");
        return;
      }
      loadPublicMap(code);
    });
  }
  if ($("public-map-search-code")) {
    $("public-map-search-code").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const code = $("public-map-search-code").value.trim();
        if (code) loadPublicMap(code);
      }
    });
  }
}

/** Huỷ chế độ sửa của form vùng trồng / lô nông sản / sản phẩm (nút "Huỷ sửa"). */
function cancelEdit(entity) {
  if (entity === "farm") {
    resetFarmForm();
    renderFarms(); // bỏ tô nền dòng đang sửa
    toast("Đã huỷ chế độ sửa vùng trồng.", "info");
    return;
  }

  if (entity === "product") {
    resetProductForm();
    renderProducts();
    toast("Đã huỷ chế độ sửa sản phẩm.", "info");
    return;
  }

  resetBatchForm();
  renderBatches();
  toast("Đã huỷ chế độ sửa lô nông sản.", "info");
}

/**
 * Xử lý click ở cột "Thao tác" của các bảng (nút Sửa / Xoá / Tách / Timeline).
 */
function handleTableAction(event) {
  const button = event.target.closest("button[data-action]");
  if (button === null) {
    return; // bấm ra ngoài nút -> không làm gì
  }

  const id = Number(button.dataset.id);
  const entity = button.dataset.entity;
  const action = button.dataset.action;

  if (action === "timeline") {
    openTimelineModal(id);
    return;
  }

  if (action === "split") {
    openSplitModal(id);
    return;
  }

  if (action === "edit") {
    if (entity === "farm") {
      startEditFarm(id);
    } else if (entity === "batch") {
      startEditBatch(id);
    } else if (entity === "product") {
      startEditProduct(id);
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
    } else if (entity === "batch") {
      deleteBatch(id);
    } else if (entity === "product") {
      deleteProduct(id);
    }
  }
}

/** Tải dữ liệu dùng chung cho giao diện sau khi đăng nhập (theo phân quyền). */
async function loadAllData() {
  await checkHealth();
  await loadFarms(); // phải chạy trước để bảng lô hiển thị được tên vùng trồng
  await loadBatches();
  await loadProducts(); // tải danh mục nông sản dùng chung
  await loadHandovers(); // tải danh sách bàn giao
  await loadOrders(); // tải danh sách lệnh kiểm tra
  await loadThresholds(); // tải danh mục ngưỡng từng loại sản phẩm
  await loadViolations(); // tải nhật ký vi phạm chuỗi lạnh
  if (session !== null && session.role === ROLE_ADMIN) {
    await loadUsers(); // chỉ admin gọi được GET /users
  }
}

/** Tải lại toàn bộ dữ liệu; `silent = true` để bỏ toast tổng kết. */
async function reloadAll({ silent = false } = {}) {
  const button = $("btn-reload");
  setButtonLoading(button, true, "Đang tải…", "Tải lại dữ liệu");

  await loadAllData();

  setButtonLoading(button, false, "Đang tải…", "Tải lại dữ liệu");
  if (!silent) {
    toast(`Đã tải lại: ${farms.length} vùng trồng, ${batches.length} lô, ${products.length} sản phẩm.`, "info");
  }
}

/* -------------------------------------------------------- 12. Khởi động --- */
/**
 * Khởi động ứng dụng:
 * 1. gắn sự kiện + khởi tạo tabs + kiểm tra backend đang chạy;
 * 2. nếu tab còn phiên đăng nhập cũ (sessionStorage) thì xác thực lại với
 *    backend rồi vào thẳng giao diện;
 * 3. ngược lại, hiện màn hình đăng nhập.
 */
async function init() {
  if ($("stat-api")) {
    $("stat-api").textContent = API_BASE_URL;
  }
  initTabs();
  bindEvents();
  resetFarmForm(); // các form luôn khởi động ở chế độ "thêm mới / tạo mới"
  resetBatchForm();
  resetProductForm();
  await checkHealth(); // báo ngay nếu uvicorn chưa chạy

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
