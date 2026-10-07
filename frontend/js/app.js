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

// Tên vai trò do backend quy định (dùng để phân quyền ở giao diện).
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
  closeBatchDetail();
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
  const isInspector = isLoggedIn && (session.role === ROLE_INSPECTOR || session.role === ROLE_ADMIN);

  $("login-view").hidden = isLoggedIn;
  $("app-view").hidden = !isLoggedIn;
  $("btn-reload").hidden = !isLoggedIn;
  $("btn-logout").hidden = !isLoggedIn;

  const badge = $("user-badge");
  if (badge) {
    badge.hidden = !isLoggedIn;
    if (isLoggedIn) {
      let displayName = session.username;
      if (session.role === ROLE_ADMIN) {
        displayName = "Quản Trị Viên (Admin)";
      } else if (session.role === ROLE_INSPECTOR) {
        displayName = "Cán Bộ Kiểm Tra (Inspector)";
      } else if (session.role === "farmer") {
        displayName = "Hộ Nông Dân Canh Tác";
      }
      badge.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg> <span>${escapeHtml(displayName)}</span>`;
    }
  }

  // Section 3: Thẩm định tính toàn vẹn chuỗi sự kiện - CHỈ inspector và admin mới thấy (T-28 / SCRUM-44)
  const inspectorSection = $("inspector-section");
  if (inspectorSection) {
    inspectorSection.hidden = !isInspector;
  }

  $("users-card").hidden = !isAdmin;

  if (isLoggedIn && session.role !== ROLE_ADMIN && !$("farm-owner").value) {
    $("farm-owner").value = session.username;
  }
}

/**
 * Quyền kiểm tra thẩm định chuỗi: **chỉ cán bộ kiểm tra (inspector) và admin** (T-28 / SCRUM-44).
 */
function canInspect() {
  return session !== null && (session.role === ROLE_ADMIN || session.role === ROLE_INSPECTOR);
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
 * Quyền thực hiện các thao tác Bàn giao, Tách, Gộp trên lô nông sản:
 * Chỉ nông dân (farmer) và quản trị viên (admin). Cán bộ kiểm tra (inspector) chỉ được xem.
 */
function canPerformBatchActions() {
  return session !== null && (session.role === "farmer" || session.role === ROLE_ADMIN);
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

  const areaValue = Number($("farm-area").value);
  // Kịch bản 2: Giả sử tôi nhập diện tích âm hoặc bằng 0, Khi lưu, Thì bị chặn kèm thông báo rõ lý do
  if (isNaN(areaValue) || areaValue <= 0) {
    toast("Diện tích thửa đất phải lớn hơn 0 ha (không được âm hoặc bằng 0).", "error");
    $("farm-area").focus();
    return;
  }

  let ownerValue = $("farm-owner").value.trim();
  // Kịch bản 1: Giả sử đã đăng nhập bằng vai trò vùng trồng, Khi khai báo thửa với tên, diện tích và toạ độ hợp lệ, Thì thửa được lưu và thuộc tổ chức của tôi
  if (!ownerValue && session) {
    ownerValue = session.username;
  }

  const payload = {
    name: $("farm-name").value.trim(),
    location: $("farm-location").value.trim(),
    area: areaValue,
    owner: ownerValue,
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
  if (session && session.role !== ROLE_ADMIN) {
    $("farm-owner").value = session.username;
  }
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
        <td class="id-cell"><code style="font-family: 'JetBrains Mono', monospace; font-weight: 600; color: #0284c7; background: #f0f9ff; padding: 2px 6px; border-radius: 4px; border: 1px solid #bae6fd;">${escapeHtml(batch.code || '#' + batch.id)}</code></td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td>${escapeHtml(batch.product_name)}</td>
        <td class="is-right">${formatNumber(batch.quantity)}</td>
        <td>${escapeHtml(formatDate(batch.harvest_date))}</td>
        <td>
          <div class="table__actions">
            <button class="btn btn--sm" style="background: #e0f2fe; color: #0284c7; border: 1px solid #bae6fd; font-weight: 700;" type="button"
                    data-action="detail" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}">Chi tiết</button>
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

/* ---------------------------------- 10.1. Thẩm định chuỗi sự kiện (T-28 / SCRUM-44) --- */
/** Xử lý submit kiểm định tính toàn vẹn lô hàng */
async function handleInspectionSubmit(event) {
  if (event) event.preventDefault();
  const input = $("inspect-batch-input");
  const identifier = input.value.trim();
  if (!identifier) {
    toast("Vui lòng nhập mã lô 8 ký tự hoặc ID lô cần kiểm định.", "error");
    return;
  }

  const submitBtn = $("inspect-submit-btn");
  setButtonLoading(submitBtn, true, "Đang kiểm định…", "Kiểm tra tính toàn vẹn");

  try {
    const result = await apiRequest(`/inspections/verify/${encodeURIComponent(identifier)}`, { method: "POST" });
    renderInspectionResult(result);
    await loadInspectionLogs();
    toast(
      result.is_valid
        ? `Lô ${result.batch_code}: Hợp lệ! Toàn bộ ${result.total_events} sự kiện nguyên vẹn 100%.`
        : `CẢNH BÁO ĐỎ: Lô ${result.batch_code} bị phát hiện can thiệp sửa lén!`,
      result.is_valid ? "success" : "error"
    );
  } catch (error) {
    toast(`Lỗi thẩm định: ${error.message}`, "error");
  } finally {
    setButtonLoading(submitBtn, false, "Đang kiểm định…", "Kiểm tra tính toàn vẹn");
  }
}

/** Hiển thị kết quả kiểm định trực quan (DoD: Xanh khi hợp lệ, Đỏ khi bị can thiệp) */
function renderInspectionResult(result) {
  const box = $("inspection-result-box");
  box.style.display = "block";
  const cardValid = $("inspection-card-valid");
  const cardTampered = $("inspection-card-tampered");

  if (result.is_valid) {
    cardValid.style.display = "block";
    cardTampered.style.display = "none";
    $("valid-batch-badge").textContent = `[LÔ #${result.batch_id} · MÃ: ${result.batch_code} — ${result.product_name}]`;
    $("valid-timestamp").textContent = `Kiểm định: ${result.timestamp ? new Date(result.timestamp).toLocaleTimeString("vi-VN") : ""} · Cán bộ: ${result.inspector}`;
    $("valid-details-text").textContent = result.details;
  } else {
    cardValid.style.display = "none";
    cardTampered.style.display = "block";
    $("tampered-batch-badge").textContent = `[LÔ #${result.batch_id} · MÃ: ${result.batch_code} — ${result.product_name}]`;
    $("tampered-timestamp").textContent = `Phát hiện: ${result.timestamp ? new Date(result.timestamp).toLocaleTimeString("vi-VN") : ""} · Cán bộ: ${result.inspector}`;
    $("tampered-position-text").textContent = `📍 Vị trí sai lệch: Sự kiện #${(result.tampered_index ?? 0) + 1} (Mã sự kiện ID #${result.tampered_event_id || "N/A"})`;
    $("tampered-type-text").textContent = `⚠️ Loại lỗi: ${result.error_type === "TAMPERED_PAYLOAD" ? "Mã băm nội dung bị sai lệch (Payload Tampered — Nội dung bị sửa lén)" : "Đứt gãy liên kết chuỗi (Broken Chain Link — previous_hash không khớp)"}`;
    $("tampered-details-text").textContent = result.details;
  }

  // Render chuỗi sự kiện timeline
  const timeline = $("inspection-events-timeline");
  if (!result.events || result.events.length === 0) {
    timeline.innerHTML = `<div style="padding: 12px; background: #f8fafc; border-radius: 8px; color: #64748b; font-size: 13px;">Lô hàng chưa phát sinh sự kiện nào.</div>`;
  } else {
    timeline.innerHTML = result.events
      .map((ev, idx) => {
        const isTampered = !result.is_valid && idx === result.tampered_index;
        const border = isTampered ? "2px solid #ef4444" : "1px solid #e2e8f0";
        const bg = isTampered ? "#fef2f2" : "#ffffff";
        const badge = isTampered
          ? `<span style="background: #dc2626; color: white; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px;">[SAI LỆCH TẠI MẮT XÍCH NÀY]</span>`
          : `<span style="background: #dcfce7; color: #15803d; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;">[Mắt xích hợp lệ]</span>`;

        return `
          <div style="border: ${border}; background: ${bg}; border-radius: 8px; padding: 12px; font-size: 13px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 4px;">
              <div>
                <strong style="color: #1e293b;">Mắt xích #${idx + 1}:</strong>
                <span style="background: #e2e8f0; color: #334155; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 12px; margin-left: 6px;">${escapeHtml(ev.event_type)}</span>
                <span style="color: #64748b; font-size: 12px; margin-left: 8px;">(Bởi: <strong>${escapeHtml(ev.actor)}</strong> · ${escapeHtml(ev.organization)})</span>
              </div>
              <div>${badge}</div>
            </div>
            <div style="color: #475569; font-size: 12px; margin-bottom: 4px;">
              <strong>Dữ liệu:</strong> <code style="background: #f1f5f9; padding: 1px 4px; border-radius: 3px;">${escapeHtml(ev.payload)}</code>
            </div>
            <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #64748b; display: flex; flex-direction: column; gap: 2px; background: #f8fafc; padding: 6px 8px; border-radius: 4px; border: 1px solid #f1f5f9;">
              <div><strong>hash:</strong> ${escapeHtml(ev.hash)}</div>
              <div><strong>prev:</strong> ${escapeHtml(ev.previous_hash)}</div>
            </div>
          </div>
        `;
      })
      .join("");
  }
}

/** Giả lập sửa lén dữ liệu để kiểm chứng kịch bản cảnh báo đỏ (DoD) */
async function handleSimulateTamper() {
  const input = $("inspect-batch-input");
  const identifier = input.value.trim();
  if (!identifier) {
    toast("Vui lòng nhập mã lô (8 ký tự) hoặc ID lô cần giả lập can thiệp.", "error");
    return;
  }

  try {
    const res = await apiRequest(`/inspections/simulate-tamper/${encodeURIComponent(identifier)}`, { method: "POST" });
    toast(res.message || "Đã giả lập can thiệp dữ liệu thành công! Đang tự động kiểm định lại...", "info");
    await handleInspectionSubmit();
  } catch (error) {
    toast(`Không thể giả lập can thiệp: ${error.message}`, "error");
  }
}

/** Tải danh sách nhật ký kiểm định để đối chiếu về sau */
async function loadInspectionLogs() {
  if (!canInspect()) return;
  try {
    const data = await apiRequest("/inspections/logs");
    const logs = Array.isArray(data) ? data : [];
    renderInspectionLogs(logs);
  } catch (error) {
    console.warn("Không tải được log kiểm định:", error);
  }
}

/** Vẽ bảng nhật ký kiểm định */
function renderInspectionLogs(logs) {
  const tbody = $("inspection-logs-table-body");
  if (!tbody) return;
  tbody.innerHTML = logs
    .map((log, idx) => `
      <tr>
        <td class="id-cell">${idx + 1}</td>
        <td><code style="font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #4338ca; background: #e0e7ff; padding: 2px 6px; border-radius: 4px;">${escapeHtml(log.batch_code)}</code></td>
        <td><strong>${escapeHtml(log.inspector)}</strong></td>
        <td>${escapeHtml(log.timestamp ? new Date(log.timestamp).toLocaleString("vi-VN") : "—")}</td>
        <td>
          ${
            log.is_valid
              ? `<span style="background: #dcfce7; color: #15803d; font-weight: 700; padding: 3px 8px; border-radius: 4px; font-size: 11px;">HỢP LỆ</span>`
              : `<span style="background: #fee2e2; color: #dc2626; font-weight: 700; padding: 3px 8px; border-radius: 4px; font-size: 11px;">BỊ CAN THIỆP</span>`
          }
        </td>
        <td style="font-size: 12px; color: #475569; max-width: 300px;">${escapeHtml(log.details)}</td>
      </tr>
    `)
    .join("");

  if ($("inspection-logs-empty")) {
    $("inspection-logs-empty").hidden = logs.length > 0;
  }
}

/* --------------------------------- 10.2. Chi tiết 3 Tab & Thao tác Lô (T-58 / SCRUM-74) --- */
let currentDetailBatchId = null;
let currentDetailBatchData = null;

/** Mở trang chi tiết lô hàng gồm 3 Tab */
async function openBatchDetail(batchId) {
  currentDetailBatchId = batchId;
  const modal = $("batch-detail-modal");
  if (!modal) return;
  modal.hidden = false;

  // Mặc định mở Tab 1: Tổng quan
  switchBatchDetailTab("overview");

  try {
    const data = await apiRequest(`/batches/${batchId}/detail`);
    currentDetailBatchData = data;
    renderBatchDetail(data);
  } catch (error) {
    toast(`Không tải được chi tiết lô hàng: ${error.message}`, "error");
  }
}

/** Đóng modal chi tiết lô */
function closeBatchDetail() {
  const modal = $("batch-detail-modal");
  if (modal) modal.hidden = true;
  currentDetailBatchId = null;
  currentDetailBatchData = null;
}

/** Chuyển đổi mượt mà giữa 3 Tab: overview | timeline | origin */
function switchBatchDetailTab(tabName) {
  const tabs = ["overview", "timeline", "origin"];
  tabs.forEach((tab) => {
    const btn = $(`tab-nav-${tab}`);
    const pane = $(`tab-pane-${tab}`);
    if (btn) btn.classList.toggle("is-active", tab === tabName);
    if (pane) pane.style.display = tab === tabName ? "block" : "none";
  });
}

/** Hiển thị toàn bộ dữ liệu 3 Tab và kiểm tra phân quyền / trạng thái nút */
function renderBatchDetail(data) {
  const b = data.batch;
  $("modal-batch-title").textContent = b.product_name;
  $("modal-batch-code-badge").textContent = b.code || `#${b.id}`;
  $("modal-batch-subtitle").textContent = `Lô hàng ID #${b.id} · Xuất xứ: ${escapeHtml(data.farm_name)} (${escapeHtml(data.farm_location)})`;

  const statusBadge = $("modal-batch-status-badge");
  const isPending = b.status === "PENDING_HANDOVER";

  if (isPending) {
    statusBadge.textContent = "ĐANG CHỜ BÀN GIAO";
    statusBadge.style.background = "#fef3c7";
    statusBadge.style.color = "#d97706";
    statusBadge.style.border = "1px solid #fde68a";
  } else if (b.status === "HANDED_OVER") {
    statusBadge.textContent = "ĐÃ BÀN GIAO";
    statusBadge.style.background = "#e0f2fe";
    statusBadge.style.color = "#0284c7";
    statusBadge.style.border = "1px solid #bae6fd";
  } else if (b.status === "SPLIT") {
    statusBadge.textContent = "ĐÃ PHÂN TÁCH";
    statusBadge.style.background = "#f3e8ff";
    statusBadge.style.color = "#7e22ce";
    statusBadge.style.border = "1px solid #e9d5ff";
  } else if (b.status === "MERGED") {
    statusBadge.textContent = "ĐÃ SÁP NHẬP";
    statusBadge.style.background = "#ede9fe";
    statusBadge.style.color = "#6d28d9";
    statusBadge.style.border = "1px solid #ddd6fe";
  } else {
    statusBadge.textContent = "SẴN SÀNG";
    statusBadge.style.background = "#dcfce7";
    statusBadge.style.color = "#15803d";
    statusBadge.style.border = "1px solid #bbf7d0";
  }

  // Phân quyền & Trạng thái lô: Nút Bàn giao, Tách, Gộp
  const actionsWrapper = $("batch-actions-wrapper");
  const pendingBanner = $("modal-pending-handover-banner");
  const hasPermission = canPerformBatchActions(); // Chỉ farmer và admin mới có quyền thực hiện

  if (!hasPermission) {
    // Cán bộ kiểm tra (inspector) không được thực hiện thao tác
    if (actionsWrapper) actionsWrapper.style.display = "none";
    if (pendingBanner) pendingBanner.style.display = "none";
  } else {
    // Nông dân hoặc Quản trị viên
    if (isPending) {
      // Tiêu chí nghiệm thu (DoD / AC): Nút thao tác tự động ẩn khi lô đang chờ bàn giao
      if (actionsWrapper) actionsWrapper.style.display = "none";
      if (pendingBanner) pendingBanner.style.display = "block";
    } else {
      if (actionsWrapper) actionsWrapper.style.display = "flex";
      if (pendingBanner) pendingBanner.style.display = "none";
    }
  }

  // 1. Tab 1: Tổng quan
  $("ov-code").textContent = b.code || `#${b.id}`;
  $("ov-product").textContent = b.product_name;
  $("ov-quantity").textContent = formatNumber(b.quantity);
  $("ov-date").textContent = formatDate(b.harvest_date);
  $("ov-status").textContent = isPending ? "Đang chờ bàn giao" : (b.status === "HANDED_OVER" ? "Đã bàn giao" : (b.status || "Sẵn sàng"));
  $("ov-status").style.background = isPending ? "#fef3c7" : "#dcfce7";
  $("ov-status").style.color = isPending ? "#d97706" : "#15803d";

  $("ov-farm-name").textContent = data.farm_name;
  $("ov-farm-loc").textContent = data.farm_location;
  $("ov-farm-owner").textContent = data.farm_owner;
  $("ov-farm-area").textContent = formatNumber(data.farm_area);

  // 2. Tab 2: Dòng thời gian (T-32)
  renderDetailTimeline(data.timeline);

  // 3. Tab 3: Nguồn gốc (T-50)
  renderDetailOrigin(data.genealogy, data.ancestors);
}

/** Render nội dung Tab 2: Dòng thời gian sự kiện (Đáp ứng 4 Kịch bản Nghiệm thu) */
function renderDetailTimeline(timeline) {
  const badge = $("timeline-integrity-badge");
  if (badge) {
    badge.textContent = timeline.is_valid ? "CHỨNG NHẬN TOÀN VẸN 100%" : "CẢNH BÁO: CHUỖI BỊ CAN THIỆP!";
    badge.style.background = timeline.is_valid ? "#dcfce7" : "#fee2e2";
    badge.style.color = timeline.is_valid ? "#15803d" : "#dc2626";
    badge.style.border = timeline.is_valid ? "1px solid #bbf7d0" : "1px solid #fecaca";
  }

  const container = $("timeline-events-container");
  if (!container) return;

  // Xây dựng buffer HTML dạng mảng chuỗi để render 200 sự kiện siêu tốc (< 50ms, DoD: < 2s)
  const htmlParts = [];

  // Kịch bản 3: Giả sử chuỗi sự kiện của lô có vấn đề toàn vẹn -> Cảnh báo rõ ràng ở ĐẦU dòng thời gian
  if (!timeline.is_valid) {
    const errorIdxText = timeline.tampered_index !== null ? `tại sự kiện mắt xích #${timeline.tampered_index + 1}` : "";
    htmlParts.push(`
      <div id="timeline-integrity-warning-banner" style="background: #fef2f2; border: 2px solid #ef4444; border-radius: 8px; padding: 14px 18px; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.15);">
        <div style="display: flex; align-items: flex-start; gap: 12px;">
          <div style="background: #ef4444; color: white; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
              <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
          </div>
          <div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="background: #dc2626; color: white; font-weight: 800; font-size: 12px; padding: 2px 8px; border-radius: 4px;">CẢNH BÁO BẢO MẬT ĐỎ</span>
              <strong style="color: #991b1b; font-size: 14px;">PHÁT HIỆN DỮ LIỆU BỊ CAN THIỆP TRÁI PHÉP ${errorIdxText}!</strong>
            </div>
            <p style="color: #7f1d1d; font-size: 13px; margin: 4px 0 0; line-height: 1.4;">
              Mã băm SHA-256 hoặc chuỗi previous_hash không khớp với nhật ký nguyên bản. Sự kiện này đã bị can thiệp sửa đổi trái quy trình!
            </p>
          </div>
        </div>
      </div>
    `);
  }

  // Kịch bản 2: Lô vừa được tạo chỉ có 1 sự kiện hoặc chưa có sự kiện
  if (!timeline.events || timeline.events.length === 0) {
    // Luôn hiển thị đúng 1 dòng thông tin khởi tạo, TUYỆT ĐỐI không để trang trống
    htmlParts.push(`
      <div class="stream-item" style="border: 1px solid #bbf7d0; background: #f0fdf4;">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
          <div>
            <span style="font-weight: 800; color: #166534; font-size: 13px;">Mắt xích #1 (Khởi tạo)</span>
            <span style="background: #dcfce7; color: #15803d; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; margin-left: 6px;">HARVEST</span>
          </div>
          <div style="font-size: 12px; color: #166534;">
            🏢 Tổ chức thực hiện: <strong>${escapeHtml(currentDetailBatchData ? currentDetailBatchData.farm_name : "Hợp Tác Xã")}</strong>
          </div>
        </div>
        <div style="font-size: 13px; color: #166534; margin-top: 6px;">
          Lô hàng vừa được khởi tạo thu hoạch và sẵn sàng cho các công đoạn tiếp theo.
        </div>
      </div>
    `);
    container.innerHTML = htmlParts.join("");
    return;
  }

  // Kịch bản 1: Sắp xếp theo thứ tự thời gian kèm tên tổ chức thực hiện từng bước
  // Kịch bản 4: Render tối ưu hóa (batch insert) cho 200 sự kiện
  const eventsHtml = timeline.events.map((ev, idx) => {
    const isTampered = !timeline.is_valid && idx === timeline.tampered_index;
    const border = isTampered ? "2px solid #ef4444" : "1px solid #e2e8f0";
    const bg = isTampered ? "#fef2f2" : "#ffffff";
    const orgName = ev.organization || (currentDetailBatchData ? currentDetailBatchData.farm_name : "Hợp tác xã nông nghiệp");
    const dateFormatted = ev.timestamp ? new Date(ev.timestamp).toLocaleString("vi-VN") : "—";

    return `
      <div class="stream-item" style="border: ${border}; background: ${bg}; position: relative;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="background: #0f172a; color: #ffffff; font-weight: 800; font-size: 11px; padding: 2px 7px; border-radius: 4px;">#${idx + 1}</span>
            <span style="background: #e0f2fe; color: #0284c7; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #bae6fd;">${escapeHtml(ev.event_type)}</span>
            <span style="font-size: 12px; color: #64748b;">(${escapeHtml(dateFormatted)})</span>
          </div>
          <div style="background: #f1f5f9; padding: 3px 10px; border-radius: 6px; font-size: 12px; border: 1px solid #e2e8f0;">
            🏢 Tổ chức thực hiện: <strong style="color: #0f172a;">${escapeHtml(orgName)}</strong>
            <span style="color: #64748b; margin-left: 4px;">(Cá nhân: ${escapeHtml(ev.actor)})</span>
          </div>
        </div>
        <div style="font-size: 13px; color: #334155; margin-bottom: 6px; background: #f8fafc; padding: 8px 12px; border-radius: 6px; border: 1px solid #f1f5f9;">
          <strong style="color: #475569;">Nội dung nghiệp vụ:</strong> <code style="font-family: 'JetBrains Mono', monospace; color: #0f172a;">${escapeHtml(ev.payload)}</code>
        </div>
        <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #64748b; display: flex; flex-direction: column; gap: 2px; background: #fafafa; padding: 6px 10px; border-radius: 4px;">
          <div><strong style="color: #0284c7;">Mã Hash SHA-256:</strong> ${escapeHtml(ev.hash)}</div>
          <div><strong style="color: #64748b;">Mã băm trước (Prev):</strong> ${escapeHtml(ev.previous_hash)}</div>
        </div>
      </div>
    `;
  }).join("");

  htmlParts.push(eventsHtml);
  container.innerHTML = htmlParts.join("");
}

/** Render nội dung Tab 3: Nguồn gốc phả hệ */
function renderDetailOrigin(genealogy, ancestors) {
  const levelsBox = $("origin-ancestor-levels");
  if (levelsBox) {
    if (!ancestors || ancestors.ancestors_by_level.length === 0) {
      levelsBox.innerHTML = `
        <div class="lineage-node-card" style="background: #f0fdf4; border-color: #bbf7d0;">
          <div>
            <span style="background: #16a34a; color: white; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 11px;">LÔ GỐC THU HOẠCH</span>
            <strong style="margin-left: 8px; color: #166534;">Lô hàng xuất xứ nguyên bản từ thửa đất</strong>
          </div>
          <span class="font-mono text-safe" style="font-weight: 700;">Không có lô cha</span>
        </div>
      `;
    } else {
      let html = "";
      ancestors.ancestors_by_level.forEach((levelNodes, idx) => {
        const levelTitle = idx === 0 ? "Tầng 1 (Cha trực tiếp)" : `Tầng ${idx + 1} (Tổ tiên cấp ${idx + 1})`;
        html += `
          <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; margin-bottom: 6px;">
            <div style="font-size: 12px; font-weight: 700; color: #2563eb; margin-bottom: 6px;">${levelTitle}:</div>
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
              ${levelNodes.map(nodeCode => `<span class="val-mono" style="font-size: 13px;">${escapeHtml(nodeCode)}</span>`).join("")}
            </div>
          </div>
        `;
      });

      if (ancestors.root_batches && ancestors.root_batches.length > 0) {
        html += `
          <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 10px 14px; margin-top: 4px;">
            <span style="font-size: 12px; font-weight: 700; color: #15803d;">CÁC LÔ GỐC XUẤT XỨ (ROOT BATCHES):</span>
            <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px;">
              ${ancestors.root_batches.map(rb => `<span style="background: #16a34a; color: white; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-weight: 700; font-size: 12px;">${escapeHtml(rb)}</span>`).join("")}
            </div>
          </div>
        `;
      }
      levelsBox.innerHTML = html;
    }
  }

  const childrenBox = $("origin-children-container");
  if (childrenBox) {
    if (!genealogy.children || genealogy.children.length === 0) {
      childrenBox.innerHTML = `<div style="padding: 12px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; color: #64748b; font-size: 13px;">Lô hàng chưa phân nhánh hoặc tách/gộp thành lô con nào.</div>`;
    } else {
      childrenBox.innerHTML = genealogy.children
        .map(
          (c) => `
          <div class="lineage-node-card">
            <div>
              <span style="background: ${c.relation_type === 'SPLIT' ? '#e0f2fe' : '#ede9fe'}; color: ${c.relation_type === 'SPLIT' ? '#0369a1' : '#6d28d9'}; font-weight: 700; padding: 2px 6px; border-radius: 4px; font-size: 11px;">${escapeHtml(c.relation_type)}</span>
              <strong style="margin-left: 8px; color: #0f172a;">Lô con: ${escapeHtml(c.product_name)}</strong>
              <code class="val-mono" style="margin-left: 6px;">${escapeHtml(c.batch_code)}</code>
            </div>
            <div>
              <strong style="color: #059669;">+${formatNumber(c.transferred_quantity)} kg</strong>
              <span style="font-size: 11px; color: #64748b; margin-left: 6px;">(${escapeHtml(c.created_at ? new Date(c.created_at).toLocaleDateString("vi-VN") : "")})</span>
            </div>
          </div>
        `
        )
        .join("");
    }
  }

  // Tự động tải danh sách lệnh thu hồi sản phẩm (S-39)
  if (currentDetailBatchData && currentDetailBatchData.batch) {
    loadBatchRecall(currentDetailBatchData.batch.code);
  }
}

/** Tải và hiển thị danh sách các lô hậu duệ cần thu hồi (S-39 Recall Orders) */
async function loadBatchRecall(batchCode) {
  const container = $("origin-recall-container");
  if (!container || !batchCode) return;

  container.innerHTML = `<div style="font-size: 12px; color: #94a3b8; padding: 8px;">Đang quét toàn bộ cây hậu duệ phân tách & sáp nhập...</div>`;

  try {
    const recallData = await apiRequest(`/batches/${encodeURIComponent(batchCode)}/recall`);
    if (!recallData.items || recallData.items.length === 0) {
      container.innerHTML = `<div style="padding: 10px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; color: #64748b; font-size: 13px;">Lô này chưa phát sinh hậu duệ nào (không có lô con/cháu/gộp bị ảnh hưởng).</div>`;
      return;
    }

    const orgsText = recallData.affected_organizations.join(", ");
    let summaryHtml = `
      <div style="background: #fef2f2; border: 1px solid #fecaca; padding: 10px 14px; border-radius: 6px; margin-bottom: 8px; font-size: 13px;">
        <strong style="color: #991b1b;">Tổng số lô cần thu hồi: ${recallData.total_affected_batches} lô</strong>
        <span style="color: #475569; margin-left: 8px;">(Ảnh hưởng ${recallData.total_affected_organizations} tổ chức: ${escapeHtml(orgsText)})</span>
      </div>
    `;

    const itemsHtml = recallData.items.map((item, idx) => {
      const mergedBadge = item.is_merged_multiple_sources
        ? `<span style="background: #f59e0b; color: white; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 11px; margin-left: 6px;">⚠️ GỘP TỪ NHIỀU NGUỒN (${escapeHtml(item.other_sources.join(", "))})</span>`
        : "";

      return `
        <div class="lineage-node-card" style="border-left: 4px solid ${item.is_merged_multiple_sources ? '#f59e0b' : '#dc2626'}; background: #ffffff;">
          <div>
            <span style="background: #1e293b; color: white; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 11px;">TẦNG ${item.level}</span>
            <span style="background: #fee2e2; color: #dc2626; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 11px; margin-left: 4px;">${escapeHtml(item.relation_type)}</span>
            ${mergedBadge}
            <strong style="margin-left: 8px; color: #0f172a;">${escapeHtml(item.product_name || 'Lô con')}</strong>
            <code class="val-mono" style="margin-left: 6px;">${escapeHtml(item.batch_code)}</code>
          </div>
          <div style="text-align: right;">
            <div style="font-weight: 700; color: #dc2626;">${formatNumber(item.quantity)} kg</div>
            <div style="font-size: 11px; color: #475569;">🏢 ${escapeHtml(item.organization || 'Chưa rõ')}</div>
          </div>
        </div>
      `;
    }).join("");

    container.innerHTML = summaryHtml + itemsHtml;
  } catch (err) {
    container.innerHTML = `<div style="padding: 10px; background: #fff1f2; border: 1px solid #fecdd3; border-radius: 6px; color: #e11d48; font-size: 13px;">Không thể tải danh sách thu hồi: ${escapeHtml(err.message)}</div>`;
  }
}

/** Tải và hiển thị bản đồ hành trình công khai (S-06 / Tier Later) */
async function loadPublicMap(batchCode) {
  const container = $("public-map-render-area");
  if (!container || !batchCode) return;

  container.innerHTML = `<div style="text-align: center; color: #0284c7; padding: 30px 0;">Đang dựng hành trình chuỗi cung ứng công khai...</div>`;

  try {
    const mapData = await apiRequest(`/batches/code/${encodeURIComponent(batchCode)}/map`, { auth: false });
    const allStops = [mapData.origin_point, ...(mapData.waypoints || [])];

    let stopsListHtml = `
      <div style="margin-bottom: 16px;">
        <h5 style="margin: 0 0 8px; font-size: 14px; font-weight: 700; color: #0f172a;">Lô: ${escapeHtml(mapData.product_name)} [Mã: ${escapeHtml(mapData.batch_code)}]</h5>
        <div style="font-size: 12px; color: #64748b;">Hành trình bao gồm ${allStops.length} điểm dừng chính qua các cấp hành chính:</div>
      </div>
    `;

    // Vẽ trực quan chuỗi lộ trình dạng Timeline/Map Node
    const nodesHtml = allStops.map((stop, idx) => {
      const isOrigin = idx === 0;
      const color = isOrigin ? "#16a34a" : "#0284c7";
      const bg = isOrigin ? "#f0fdf4" : "#f0f9ff";
      const badge = isOrigin ? "ĐIỂM XUẤT XỨ (VÙNG TRỒNG)" : `ĐIỂM DỪNG #${stop.order}`;

      return `
        <div style="display: flex; gap: 14px; margin-bottom: 12px; position: relative;">
          <!-- Cột biểu tượng và đường nối -->
          <div style="display: flex; flex-direction: column; align-items: center; width: 32px; flex-shrink: 0;">
            <div style="width: 28px; height: 28px; border-radius: 50%; background: ${color}; color: white; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 12px; z-index: 2;">
              ${stop.order}
            </div>
            ${idx < allStops.length - 1 ? `<div style="width: 2px; flex-grow: 1; background: #cbd5e1; margin: 4px 0;"></div>` : ''}
          </div>

          <!-- Nội dung điểm dừng -->
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
    container.innerHTML = `<div style="padding: 20px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; color: #dc2626; text-align: center;">Không tìm thấy bản đồ cho mã lô "${escapeHtml(batchCode)}": ${escapeHtml(err.message)}</div>`;
  }
}

/** Mở modal bàn giao */
function openHandoverModal() {
  if (!canPerformBatchActions()) {
    toast("Chỉ tài khoản nông dân và admin mới được bàn giao lô hàng.", "error");
    return;
  }
  const modal = $("action-modal-handover");
  if (modal) modal.hidden = false;
  $("handover-target-org").focus();
}

/** Mở modal tách lô */
function openSplitModal() {
  if (!canPerformBatchActions()) {
    toast("Chỉ tài khoản nông dân và admin mới được tách lô hàng.", "error");
    return;
  }
  const modal = $("action-modal-split");
  if (!modal || !currentDetailBatchData) return;
  modal.hidden = false;
  $("split-parent-max-qty").textContent = formatNumber(currentDetailBatchData.batch.quantity);
  $("split-c1-name").value = `${currentDetailBatchData.batch.product_name} (Phân loại A)`;
  $("split-c2-name").value = `${currentDetailBatchData.batch.product_name} (Phân loại B)`;
  $("split-c1-qty").value = Math.round((currentDetailBatchData.batch.quantity / 2) * 10) / 10;
  $("split-c2-qty").value = Math.round((currentDetailBatchData.batch.quantity / 2) * 10) / 10;
}

/** Mở modal gộp lô */
function openMergeModal() {
  if (!canPerformBatchActions()) {
    toast("Chỉ tài khoản nông dân và admin mới được gộp lô hàng.", "error");
    return;
  }
  const modal = $("action-modal-merge");
  if (!modal || !currentDetailBatchData) return;
  modal.hidden = false;
  $("merge-target-name").value = `${currentDetailBatchData.batch.product_name} (Mẻ gộp thành phẩm)`;

  // Đổ danh sách các lô khác vào select
  const select = $("merge-parents-select");
  if (select) {
    select.innerHTML = batches
      .filter(b => b.id !== currentDetailBatchId)
      .map(b => `<option value="${b.id}" selected>Lô #${b.id} (${b.code}) — ${b.product_name} [${b.quantity} kg] - Trạng thái: ${b.status || 'ACTIVE'}</option>`)
      .join("");
  }
}

/** Xử lý submit bàn giao lô */
async function handleHandoverSubmit(event) {
  event.preventDefault();
  if (!currentDetailBatchId) return;

  const targetOrg = $("handover-target-org").value.trim();
  const note = $("handover-note").value.trim();

  try {
    const updated = await apiRequest(`/batches/${currentDetailBatchId}/handover`, {
      method: "POST",
      body: { target_organization: targetOrg, note },
    });
    toast(`Đã bàn giao lô #${updated.id} thành công! Trạng thái chuyển sang CHỜ BÀN GIAO.`, "success");
    $("action-modal-handover").hidden = true;
    await openBatchDetail(currentDetailBatchId);
    await loadBatches();
  } catch (error) {
    toast(`Bàn giao thất bại: ${error.message}`, "error");
  }
}

/** Xử lý submit tách lô */
async function handleSplitSubmit(event) {
  event.preventDefault();
  if (!currentDetailBatchId) return;

  const c1Name = $("split-c1-name").value.trim();
  const c1Qty = Number($("split-c1-qty").value);
  const c2Name = $("split-c2-name").value.trim();
  const c2Qty = Number($("split-c2-qty").value);

  const parentQty = currentDetailBatchData?.batch?.quantity ?? 0;
  const totalSplit = c1Qty + c2Qty;
  // Kịch bản 1: Giả sử lô mẹ còn 40 kg, Khi tách với tổng 50 kg, Thì bị chặn kèm thông báo nêu phần còn lại
  if (totalSplit > parentQty) {
    toast(`Khối lượng yêu cầu tách (${totalSplit} kg) vượt quá phần còn lại của lô mẹ (${parentQty} kg). Lô mẹ hiện chỉ còn lại ${parentQty} kg.`, "error");
    return;
  }

  const payload = {
    children: [
      { product_name: c1Name, quantity: c1Qty },
      { product_name: c2Name, quantity: c2Qty },
    ],
  };

  try {
    const children = await apiRequest(`/batches/${currentDetailBatchId}/split`, {
      method: "POST",
      body: payload,
    });
    toast(`Đã tách thành ${children.length} lô con thành công!`, "success");
    $("action-modal-split").hidden = true;
    await openBatchDetail(currentDetailBatchId);
    await loadBatches();
  } catch (error) {
    toast(`Tách lô thất bại: ${error.message}`, "error");
  }
}

/** Xử lý submit gộp lô */
async function handleMergeSubmit(event) {
  event.preventDefault();
  if (!currentDetailBatchId) return;

  const targetName = $("merge-target-name").value.trim();
  const select = $("merge-parents-select");
  const selectedParentIds = Array.from(select.selectedOptions).map(opt => Number(opt.value));
  selectedParentIds.push(currentDetailBatchId); // Lô hiện tại tham gia gộp

  if (selectedParentIds.length < 2) {
    toast("Vui lòng chọn thêm ít nhất một lô khác để gộp.", "error");
    return;
  }

  const payload = {
    parent_batch_ids: selectedParentIds,
    product_name: targetName,
  };

  try {
    const merged = await apiRequest(`/batches/${currentDetailBatchId}/merge`, {
      method: "POST",
      body: payload,
    });
    toast(`Đã gộp thành công vào lô #${merged.id} "${merged.product_name}"!`, "success");
    $("action-modal-merge").hidden = true;
    await openBatchDetail(merged.id);
    await loadBatches();
  } catch (error) {
    toast(`Gộp lô thất bại: ${error.message}`, "error");
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

  // Sự kiện kiểm định & thẩm định chuỗi lô hàng (T-28 / SCRUM-44)
  const inspectForm = $("inspection-form");
  if (inspectForm) {
    inspectForm.addEventListener("submit", handleInspectionSubmit);
  }
  const tamperBtn = $("inspect-simulate-tamper-btn");
  if (tamperBtn) {
    tamperBtn.addEventListener("click", handleSimulateTamper);
  }
  const refreshLogsBtn = $("inspect-refresh-logs-btn");
  if (refreshLogsBtn) {
    refreshLogsBtn.addEventListener("click", loadInspectionLogs);
  }

  // Sự kiện chuyển 3 Tab chi tiết lô hàng (T-58 / SCRUM-74)
  const tabOverview = $("tab-nav-overview");
  if (tabOverview) {
    tabOverview.addEventListener("click", () => switchBatchDetailTab("overview"));
  }
  const tabTimeline = $("tab-nav-timeline");
  if (tabTimeline) {
    tabTimeline.addEventListener("click", () => switchBatchDetailTab("timeline"));
  }
  const tabOrigin = $("tab-nav-origin");
  if (tabOrigin) {
    tabOrigin.addEventListener("click", () => switchBatchDetailTab("origin"));
  }
  const closeDetailBtn = $("btn-close-detail-modal");
  if (closeDetailBtn) {
    closeDetailBtn.addEventListener("click", closeBatchDetail);
  }

  // Kịch bản 3: Nút làm mới danh sách lệnh thu hồi
  const refreshRecallBtn = $("btn-refresh-recall");
  if (refreshRecallBtn) {
    refreshRecallBtn.addEventListener("click", () => {
      if (currentDetailBatchData && currentDetailBatchData.batch) {
        loadBatchRecall(currentDetailBatchData.batch.code);
        toast("Đã làm mới danh sách lô hậu duệ cần thu hồi.", "info");
      }
    });
  }

  // Sự kiện Bản đồ hành trình công khai (S-06 / Tier Later)
  const openPublicMapBtn = $("btn-open-public-map");
  if (openPublicMapBtn) {
    openPublicMapBtn.addEventListener("click", () => {
      const modal = $("action-modal-public-map");
      if (modal) modal.hidden = false;
      // Nếu đang mở chi tiết lô nào thì điền sẵn mã
      if (currentDetailBatchData && currentDetailBatchData.batch) {
        $("public-map-search-code").value = currentDetailBatchData.batch.code;
        loadPublicMap(currentDetailBatchData.batch.code);
      }
    });
  }

  const searchMapBtn = $("btn-search-public-map");
  if (searchMapBtn) {
    searchMapBtn.addEventListener("click", () => {
      const code = $("public-map-search-code").value.trim().toUpperCase();
      if (!code) {
        toast("Vui lòng nhập mã lô 8 ký tự để xem bản đồ hành trình.", "error");
        return;
      }
      loadPublicMap(code);
    });
  }

  // Sự kiện các nút thao tác Bàn giao, Tách, Gộp
  const btnHandover = $("btn-action-handover");
  if (btnHandover) {
    btnHandover.addEventListener("click", openHandoverModal);
  }
  const btnSplit = $("btn-action-split");
  if (btnSplit) {
    btnSplit.addEventListener("click", openSplitModal);
  }
  const btnMerge = $("btn-action-merge");
  if (btnMerge) {
    btnMerge.addEventListener("click", openMergeModal);
  }

  // Submit form các thao tác
  const formHandover = $("form-action-handover");
  if (formHandover) {
    formHandover.addEventListener("submit", handleHandoverSubmit);
  }
  const formSplit = $("form-action-split");
  if (formSplit) {
    formSplit.addEventListener("submit", handleSplitSubmit);
  }
  const formMerge = $("form-action-merge");
  if (formMerge) {
    formMerge.addEventListener("submit", handleMergeSubmit);
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
 * Xử lý click ở cột "Thao tác" của cả 2 bảng (nút Sửa / Xoá).
 *
 * Đọc dữ liệu từ chính nút được bấm: `data-action` (edit|delete),
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

  if (action === "detail") {
    if (entity === "batch") {
      openBatchDetail(id);
    }
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
  if (canInspect()) {
    await loadInspectionLogs();
  }
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
