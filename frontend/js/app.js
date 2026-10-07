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

// ID bản ghi đang được SỬA trên form (null = form đang ở chế độ "thêm mới").
// Sprint 5: bấm nút "Sửa" ở bảng -> form phía trên đổ sẵn dữ liệu và nút submit
// gọi PUT thay vì POST.
let editingFarmId = null;
let editingBatchId = null;
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

  $("users-card").hidden = !isAdmin;
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
/** GET /batches -> cập nhật bảng danh sách lô (hỗ trợ tìm kiếm & lọc). */
async function loadBatches(search = "") {
  try {
    const url = search ? `/batches?search=${encodeURIComponent(search)}` : "/batches";
    const data = await apiRequest(url);
    batches = Array.isArray(data) ? data : [];
    renderBatches();
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
  $("batch-table-body").innerHTML = batches
    .map(
      (batch) => `
      <tr class="${batch.id === editingBatchId ? "is-editing" : ""}">
        <td class="id-cell font-mono" style="font-weight: 700; color: #047857;">${escapeHtml(batch.batch_code || `LOT-${batch.id}`)}</td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td>${escapeHtml(batch.product_name)}</td>
        <td class="is-right">${formatNumber(batch.quantity)}</td>
        <td>${escapeHtml(formatDate(batch.harvest_date))}</td>
        <td><span style="display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0;">${escapeHtml(batch.current_holder_org || "HTX Nông Nghiệp Số 4")}</span></td>
        <td>
          <div class="table__actions">
            <button class="btn btn--sm" style="background: #e0e7ff; color: #4338ca; border: 1px solid #c7d2fe;" type="button"
                    data-action="timeline" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}"
                    title="Xem dòng thời gian lịch sử & tính toàn vẹn chuỗi">Lịch sử</button>
            <button class="btn btn--sm btn-table-split" type="button"
                    data-action="split" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}"
                    title="Tách nhập nhiều dòng lô con (T-40)">Tách</button>
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

/* ---------------------------------- 9. Dòng thời gian & Toàn vẹn chuỗi (Audit Chain) --- */
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

        let eventBadgeColor = "#2563eb";
        let eventIcon = "📝";
        let detailHtml = "";

        if (ev.event_type === "HARVEST" || ev.event_type === "CREATE") {
          eventBadgeColor = "#16a34a";
          eventIcon = "🌱";
          detailHtml = `<div>Khởi tạo / Thu hoạch mẻ nông sản ban đầu.</div>`;
        } else if (ev.event_type === "SPLIT") {
          eventBadgeColor = "#ea580c";
          eventIcon = "✂️";
          const childCodes = payloadObj.child_batch_codes || [];
          const childDetails = payloadObj.children || [];
          detailHtml = `
            <div style="font-weight: 600; color: #c2410c;">Tách thành ${childCodes.length} lô con:</div>
            <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px;">
              ${childDetails.map(c => `<span style="background: #fff7ed; border: 1px solid #fdba74; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 0.85rem; font-weight: 700; color: #9a3412;">${escapeHtml(c.batch_code)} (${formatNumber(c.quantity)} kg)</span>`).join("")}
            </div>
            <div style="margin-top: 4px; font-size: 0.8rem; color: #6b7280;">Số dư còn lại: ${formatNumber(payloadObj.remaining_quantity)} kg</div>
          `;
        } else if (ev.event_type === "BIRTH") {
          eventBadgeColor = "#8b5cf6";
          eventIcon = "🐣";
          detailHtml = `
            <div><strong>Khai sinh từ lô mẹ:</strong> <span style="font-family: monospace; font-weight: 700; color: #6d28d9; background: #f5f3ff; border: 1px solid #ddd6fe; padding: 2px 8px; border-radius: 4px;">${escapeHtml(payloadObj.parent_batch_code || `LOT-${payloadObj.parent_batch_id}`)}</span></div>
            <div style="margin-top: 4px; font-size: 0.85rem;">Khối lượng ban đầu: <strong>${formatNumber(payloadObj.initial_quantity)} kg</strong></div>
          `;
        } else if (ev.event_type === "MERGE") {
          eventBadgeColor = "#0284c7";
          eventIcon = "📦";
          const pList = payloadObj.parent_batches || [];
          detailHtml = `
            <div style="font-weight: 600; color: #0369a1;">Gộp từ ${pList.length} lô mẹ:</div>
            <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px;">
              ${pList.map(p => `<span style="background: #f0f9ff; border: 1px solid #bae6fd; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 0.85rem; font-weight: 700; color: #0369a1;">${escapeHtml(p.batch_code)}: lấy ${formatNumber(p.quantity)} kg</span>`).join("")}
            </div>
            <div style="margin-top: 4px; font-size: 0.85rem;">Tổng khối lượng gộp: <strong>${formatNumber(payloadObj.total_merged_quantity)} kg</strong></div>
          `;
        } else if (ev.event_type === "MERGE_PARENT") {
          eventBadgeColor = "#0284c7";
          eventIcon = "➡️";
          detailHtml = `
            <div>Đã trích <strong>${formatNumber(payloadObj.contributed_quantity)} kg</strong> gộp vào lô mới: <span style="font-family: monospace; font-weight: 700; color: #0369a1; background: #f0f9ff; border: 1px solid #bae6fd; padding: 2px 8px; border-radius: 4px;">${escapeHtml(payloadObj.target_merged_batch_code || `LOT-${payloadObj.target_merged_batch_id}`)}</span></div>
            <div style="margin-top: 4px; font-size: 0.8rem; color: #6b7280;">Số dư còn lại: ${formatNumber(payloadObj.remaining_quantity)} kg</div>
          `;
        } else if (ev.event_type === "HANDOVER_CONFIRMED" || ev.event_type === "HANDOVER_INITIATED") {
          eventBadgeColor = "#d97706";
          eventIcon = "🤝";
          detailHtml = `<div>${escapeHtml(ev.event_type)}: ${escapeHtml(JSON.stringify(payloadObj))}</div>`;
        } else {
          detailHtml = `<div>${escapeHtml(JSON.stringify(payloadObj))}</div>`;
        }

        return `
          <div style="position: relative; margin-bottom: 20px;">
            <div style="position: absolute; left: -31px; top: 0; width: 22px; height: 22px; border-radius: 50%; background: #fff; border: 2px solid ${eventBadgeColor}; display: flex; align-items: center; justify-content: center; font-size: 11px;">
              ${eventIcon}
            </div>
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div>
                  <span style="font-weight: 700; color: ${eventBadgeColor}; font-size: 0.9rem; text-transform: uppercase;">${escapeHtml(ev.event_type)}</span>
                  <span style="font-size: 0.8rem; color: #64748b; margin-left: 8px;">bởi <strong>${escapeHtml(ev.actor)}</strong> (${escapeHtml(ev.organization_name || ev.organization)})</span>
                </div>
                <span style="font-size: 0.78rem; color: #94a3b8; font-family: monospace;">${escapeHtml(new Date(ev.timestamp).toLocaleString("vi-VN"))}</span>
              </div>
              <div style="font-size: 0.88rem; color: #334155;">
                ${detailHtml}
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
    }
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

  // Cột "Thao tác" của 2 bảng dùng event delegation: nội dung bảng được vẽ lại
  // liên tục nên chỉ gắn 1 listener cho mỗi <tbody> thay vì gắn cho từng nút.
  $("farm-table-body").addEventListener("click", handleTableAction);
  $("batch-table-body").addEventListener("click", handleTableAction);

  // Tìm kiếm & lọc lô nông sản có debounce 300ms (SCRUM-49 / SCRUM-50)
  const batchFilter = $("batch-filter-search");
  if (batchFilter) {
    let debounceTimer;
    batchFilter.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        loadBatches(e.target.value.trim());
      }, 300);
    });
  }
}

/** Huỷ chế độ sửa của form vùng trồng / lô nông sản (nút "Huỷ sửa"). */
function cancelEdit(entity) {
  if (entity === "farm") {
    resetFarmForm();
    renderFarms(); // bỏ tô nền dòng đang sửa
    toast("Đã huỷ chế độ sửa vùng trồng.", "info");
    return;
  }

  resetBatchForm();
  renderBatches();
  toast("Đã huỷ chế độ sửa lô nông sản.", "info");
}

/**
 * Xử lý click ở cột "Thao tác" của cả 2 bảng (nút Tách / Sửa / Xoá).
 *
 * Đọc dữ liệu từ chính nút được bấm: `data-action` (split|edit|delete),
 * `data-entity` (farm|batch) và `data-id`.
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
    } else {
      startEditBatch(id);
    }
    return;
  }

  if (action === "delete") {
    // Chốt chặn ở giao diện; backend cũng chặn bằng `require_admin` -> 403.
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

/** Tải dữ liệu dùng chung cho giao diện sau khi đăng nhập (theo phân quyền). */
async function loadAllData() {
  await checkHealth();
  await loadFarms(); // phải chạy trước để bảng lô hiển thị được tên vùng trồng
  await loadBatches();
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
    toast(`Đã tải lại: ${farms.length} vùng trồng, ${batches.length} lô, ${thresholds.length} cấu hình ngưỡng.`, "info");
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
