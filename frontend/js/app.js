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
let products = [];
let users = [];

// ID bản ghi đang được SỬA trên form (null = form đang ở chế độ "thêm mới").
// Sprint 5: bấm nút "Sửa" ở bảng -> form phía trên đổ sẵn dữ liệu và nút submit
// gọi PUT thay vì POST.
let editingFarmId = null;
let editingBatchId = null;
let editingProductId = null;

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

  // Danh mục sản phẩm dùng chung: farmer **vẫn xem được danh sách** để chọn sản
  // phẩm, nhưng form thêm/sửa và cột "Thao tác" chỉ hiện với admin. Quyền ghi do
  // backend quyết định (`require_admin`) - farmer gọi POST/PUT sẽ nhận 403.
  $("product-form").hidden = !isAdmin;
  $("product-actions-th").hidden = !isAdmin;
}

/**
 * Quyền xoá dữ liệu ở giao diện: **chỉ admin** (Sprint 5).
 * Farmer dùng giao diện sẽ không thấy nút Xoá; nếu cố gọi API xoá thì backend
 * trả `403 Forbidden` (`require_admin`) - đây chỉ là lớp bảo vệ ở UI.
 */
function canDelete() {
  return session !== null && session.role === ROLE_ADMIN;
}

/**
 * Quyền **ghi danh mục sản phẩm** ở giao diện: **chỉ admin** (Sprint 6).
 *
 * Người dùng vùng trồng (farmer) chỉ thấy danh sách sản phẩm để chọn; nút
 * thêm/sửa bị ẩn. Nếu cố gọi API ghi (`POST`/`PUT /products`) thì backend trả
 * `403 Forbidden` (`require_admin`) - đây chỉ là lớp tiện ích ở UI.
 */
function canWriteProducts() {
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
  products = [];
  users = [];
  resetFarmForm(); // bỏ chế độ sửa (nếu đang sửa) trước khi vẽ lại bảng rỗng
  resetBatchForm();
  resetProductForm();
  renderFarms();
  renderFarmOptions();
  renderBatches();
  renderProducts();
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

/** Vẽ bảng danh sách lô nông sản (kèm cột "Thao tác": Sửa/Xoá). */
function renderBatches() {
  $("batch-table-body").innerHTML = batches
    .map(
      (batch) => `
      <tr class="${batch.id === editingBatchId ? "is-editing" : ""}">
        <td class="id-cell">${escapeHtml(batch.id)}</td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td>${escapeHtml(batch.product_name)}</td>
        <td class="is-right">${formatNumber(batch.quantity)}</td>
        <td>${escapeHtml(formatDate(batch.harvest_date))}</td>
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

/* -------------------------------------- 10. Danh mục sản phẩm dùng chung --- */
// Danh mục sản phẩm dùng chung cho **mọi tổ chức** (backend không lọc theo tổ
// chức). Cả farmer và admin đều đọc được; **chỉ admin** được thêm/sửa.
/** GET /products -> cập nhật bảng danh mục sản phẩm. */
async function loadProducts() {
  try {
    const data = await apiRequest("/products");
    products = Array.isArray(data) ? data : [];
    renderProducts();
  } catch (error) {
    toast(`Không tải được danh mục sản phẩm: ${error.message}`, "error");
  }
}

/**
 * Vẽ bảng danh mục sản phẩm.
 *
 * Cột "Thao tác" (nút Sửa) chỉ render với admin: người dùng vùng trồng chỉ thấy
 * danh sách để chọn sản phẩm.
 */
function renderProducts() {
  const canWrite = canWriteProducts();

  $("product-table-body").innerHTML = products
    .map(
      (product) => `
      <tr class="${product.id === editingProductId ? "is-editing" : ""}">
        <td class="id-cell">${escapeHtml(product.id)}</td>
        <td>${escapeHtml(product.name)}</td>
        <td><code>${escapeHtml(product.unit)}</code></td>
        <td>${escapeHtml(product.description || "—")}</td>
        ${
          canWrite
            ? `<td>
          <div class="table__actions">
            <button class="btn btn--primary btn--sm" type="button"
                    data-action="edit" data-entity="product"
                    data-id="${escapeHtml(product.id)}">Sửa</button>
          </div>
        </td>`
            : ""
        }
      </tr>`
    )
    .join("");

  $("product-empty").hidden = products.length > 0;
}

/** Nhãn nút submit form sản phẩm theo chế độ hiện tại (thêm mới / sửa). */
function productSubmitLabel() {
  return editingProductId === null ? "Thêm sản phẩm" : "Cập nhật sản phẩm";
}

/**
 * Xử lý submit form sản phẩm (form chỉ hiện với admin):
 * - chế độ thêm mới (`editingProductId === null`) -> POST /products;
 * - chế độ sửa (đã bấm nút "Sửa" ở bảng)         -> PUT /products/{id}.
 */
async function handleProductSubmit(event) {
  event.preventDefault();

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const description = $("product-description").value.trim();
  const payload = {
    name: $("product-name").value.trim(),
    unit: $("product-unit").value,
    // Mô tả bỏ trống -> gửi null để backend lưu NULL thay vì chuỗi rỗng.
    description: description === "" ? null : description,
  };

  const isEditing = editingProductId !== null;
  const button = $("product-submit");
  setButtonLoading(button, true, "Đang lưu…", productSubmitLabel());

  try {
    if (isEditing) {
      const updated = await apiRequest(`/products/${editingProductId}`, {
        method: "PUT",
        body: payload,
      });
      toast(`Đã cập nhật sản phẩm #${updated.id}: ${updated.name}`, "success");
    } else {
      const created = await apiRequest("/products", { method: "POST", body: payload });
      toast(`Thêm thành công sản phẩm #${created.id}: ${created.name}`, "success");
    }
    resetProductForm(); // về lại chế độ "thêm mới"
    await loadProducts();
    $("product-name").focus();
  } catch (error) {
    toast(`${isEditing ? "Cập nhật" : "Thêm"} sản phẩm thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(button, false, "Đang lưu…", productSubmitLabel());
  }
}

/** Đưa form sản phẩm về chế độ "thêm mới" (bỏ dữ liệu đang sửa). */
function resetProductForm() {
  editingProductId = null;
  $("product-form").reset();
  $("product-form-mode").hidden = true;
  $("product-cancel").hidden = true;
  $("product-submit").textContent = productSubmitLabel();
}

/**
 * Bấm nút "Sửa" ở bảng sản phẩm -> đổ dữ liệu lên form và chuyển sang chế độ sửa
 * (nút submit sẽ gọi ``PUT /products/{id}``).
 */
function startEditProduct(productId) {
  const product = products.find((item) => item.id === productId);
  if (!product) {
    toast(`Không tìm thấy sản phẩm #${productId} trong danh mục đang hiển thị.`, "error");
    return;
  }

  editingProductId = product.id;
  $("product-name").value = product.name;
  $("product-unit").value = product.unit;
  $("product-description").value = product.description || "";

  const mode = $("product-form-mode");
  mode.textContent = `Đang sửa sản phẩm #${product.id} — ${product.name}. Bấm "Cập nhật sản phẩm" để lưu.`;
  mode.hidden = false;
  $("product-cancel").hidden = false;
  $("product-submit").textContent = productSubmitLabel();

  renderProducts(); // tô nền dòng đang sửa trong bảng
  $("product-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("product-name").focus();
}

/* ------------------------------------------ 11. Tài khoản (chỉ admin) --- */
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

/* --------------------------------------------------------- 12. Sự kiện --- */
function bindEvents() {
  $("login-form").addEventListener("submit", handleLoginSubmit);
  $("btn-logout").addEventListener("click", handleLogout);
  $("farm-form").addEventListener("submit", handleFarmSubmit);
  $("batch-form").addEventListener("submit", handleBatchSubmit);
  $("product-form").addEventListener("submit", handleProductSubmit);
  $("farm-cancel").addEventListener("click", () => cancelEdit("farm"));
  $("batch-cancel").addEventListener("click", () => cancelEdit("batch"));
  $("product-cancel").addEventListener("click", () => cancelEdit("product"));
  $("btn-reload").addEventListener("click", () => reloadAll());

  // Cột "Thao tác" của các bảng dùng event delegation: nội dung bảng được vẽ lại
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
  $("product-table-body").addEventListener("click", handleTableAction);
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
 * Xử lý click ở cột "Thao tác" của các bảng (nút Sửa / Xoá).
 *
 * Đọc dữ liệu từ chính nút được bấm: `data-action` (edit|delete),
 * `data-entity` (farm|batch|product) và `data-id`.
 */
function handleTableAction(event) {
  const button = event.target.closest("button[data-action]");
  if (button === null) {
    return; // bấm ra ngoài nút -> không làm gì
  }

  const id = Number(button.dataset.id);
  const entity = button.dataset.entity;
  const action = button.dataset.action;

  if (action === "edit") {
    if (entity === "farm") {
      startEditFarm(id);
    } else if (entity === "batch") {
      startEditBatch(id);
    } else {
      startEditProduct(id);
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
  await loadProducts(); // danh mục dùng chung: cả farmer và admin đều đọc được
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
    toast(
      `Đã tải lại: ${farms.length} vùng trồng, ${batches.length} lô nông sản, ` +
        `${products.length} sản phẩm.`,
      "info"
    );
  }
}

/* -------------------------------------------------------- 13. Khởi động --- */
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
  resetFarmForm(); // 3 form luôn khởi động ở chế độ "thêm mới / tạo mới"
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
