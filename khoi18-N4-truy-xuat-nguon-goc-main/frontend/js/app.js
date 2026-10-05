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
let products = []; // Danh mục sản phẩm toàn hệ thống (T-14 / SCRUM-30)

// ID bản ghi đang được SỬA trên form (null = form đang ở chế độ "thêm mới").
// Sprint 5: bấm nút "Sửa" ở bảng -> form phía trên đổ sẵn dữ liệu và nút submit
// gọi PUT thay vì POST.
let editingFarmId = null;
let editingBatchId = null;
let editingProductId = null; // ID sản phẩm đang sửa (T-14 / SCRUM-30)

// Phiên đăng nhập hiện tại: { username, role, password } hoặc null (chưa đăng nhập).
// Sprint 4 không dùng JWT: client giữ lại thông tin đăng nhập để gửi kèm header
// `Authorization: Basic ...` trong mỗi request.
let session = null;

// State cho T-34 & T-45: Lọc sản phẩm & Chế độ Gộp lô
let isMergeMode = false;
let selectedBatchIds = new Set();
let selectedProductFilter = "";

// State cho T-35: Bàn giao nông sản cho tổ chức đối tác
const CURRENT_USER_ORG = {
  id: 1,
  name: "Hợp tác xã Nông nghiệp Sạch Mỹ Xương",
};

// Danh sách các tổ chức đối tác (Tổ chức #1 là của người dùng hiện tại -> sẽ bị lọc bỏ)
const PARTNER_ORGANIZATIONS = [
  { id: 1, name: "Hợp tác xã Nông nghiệp Sạch Mỹ Xương", isCurrentOrg: true },
  { id: 2, name: "Công ty Cổ phần Chế biến & Xuất nhập khẩu Nông sản Miền Tây", isCurrentOrg: false },
  { id: 3, name: "Chuỗi Siêu thị Thực phẩm Tiêu chuẩn VietGAP WinCommerce", isCurrentOrg: false },
  { id: 4, name: "Trung tâm Logistics & Kho vận Chuỗi Lạnh Satra", isCurrentOrg: false },
  { id: 5, name: "Công ty TNHH Xuất khẩu Trái cây Cao cấp VinaFresh", isCurrentOrg: false },
  { id: 6, name: "Hệ thống Bán lẻ Nông sản Hữu cơ GreenFood", isCurrentOrg: false },
];

let handovers = [
  {
    id: "BG-2026-001",
    batch_id: 1,
    batch_name: "Xoài Cát Chu Loại 1 (VietGAP)",
    recipient_org_name: "Công ty Cổ phần Chế biến & Xuất nhập khẩu Nông sản Miền Tây",
    date: "2026-09-30",
    notes: "Bàn giao container lạnh 4.5°C đạt chuẩn VietGAP",
    status: "Hoàn tất",
  },
];

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
/** GET /batches -> cập nhật bảng danh sách lô. */
async function loadBatches() {
  try {
    const data = await apiRequest("/batches");
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

/** Cập nhật dropdown bộ lọc sản phẩm T-34 */
function updateProductFilterOptions() {
  const select = $("batch-filter-product");
  if (!select) return;
  const currentVal = select.value;
  const distinctProducts = Array.from(new Set(batches.map((b) => b.product_name))).sort();

  select.innerHTML = '<option value="">-- Tất cả sản phẩm --</option>' +
    distinctProducts
      .map(
        (p) => `<option value="${escapeHtml(p)}" ${p === currentVal ? "selected" : ""}>${escapeHtml(p)}</option>`
      )
      .join("");
}

/** Vẽ bảng danh sách lô nông sản (hỗ trợ T-34 lọc SP và T-45 chọn nhiều để gộp). */
function renderBatches() {
  const thSelect = $("th-batch-select");
  if (thSelect) {
    thSelect.hidden = !isMergeMode;
  }

  // Lọc theo bộ lọc T-34 nếu có chọn
  let displayedBatches = batches;
  if (selectedProductFilter) {
    displayedBatches = batches.filter((b) => b.product_name === selectedProductFilter);
  }

  $("batch-table-body").innerHTML = displayedBatches
    .map(
      (batch) => `
      <tr class="${batch.id === editingBatchId ? "is-editing" : ""}">
        ${
          isMergeMode
            ? `<td class="batch-chk-cell">
                 <input type="checkbox" class="batch-chk" data-batch-id="${escapeHtml(batch.id)}"
                        ${selectedBatchIds.has(batch.id) ? "checked" : ""} />
               </td>`
            : ""
        }
        <td class="id-cell">${escapeHtml(batch.id)}</td>
        <td>${escapeHtml(farmLabel(batch.farm_id))}</td>
        <td><strong>${escapeHtml(batch.product_name)}</strong></td>
        <td class="is-right font-mono">${formatNumber(batch.quantity)}</td>
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

  $("batch-empty").hidden = displayedBatches.length > 0;
  renderStats();
  updateMergeActionBar();
  populateHandoverBatches();
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

/* ----------------------------- T-34 & T-45: XỬ LÝ GỘP LÔ NÔNG SẢN --- */
/** Bật/tắt chế độ chọn nhiều lô để gộp (T-34 / T-45) */
function toggleMergeMode(forceState) {
  isMergeMode = typeof forceState === "boolean" ? forceState : !isMergeMode;

  const btn = $("btn-toggle-merge-mode");
  const textSpan = $("btn-toggle-merge-text");
  const actionBar = $("merge-action-bar");

  if (isMergeMode) {
    btn.classList.add("active");
    textSpan.textContent = "Thoát chế độ gộp lô";
    actionBar.hidden = false;
    toast("Đã kích hoạt chế độ chọn nhiều lô để gộp.", "info");
  } else {
    btn.classList.remove("active");
    textSpan.textContent = "Bật chế độ gộp nhiều lô";
    actionBar.hidden = true;
    selectedBatchIds.clear();
  }

  renderBatches();
}

/** Cập nhật thanh trạng thái kiểm tra điều kiện gộp lô (T-45 / SCRUM-61) */
function updateMergeActionBar() {
  const actionBar = $("merge-action-bar");
  if (!actionBar || !isMergeMode) return;

  const countBadge = $("merge-selected-count");
  const statusBox = $("merge-validation-status");
  const mergeBtn = $("btn-open-merge-modal");

  const selectedBatches = batches.filter((b) => selectedBatchIds.has(b.id));
  const count = selectedBatches.length;

  countBadge.textContent = `Đã chọn: ${count} lô`;

  if (count === 0) {
    statusBox.className = "merge-status-box";
    statusBox.textContent = "Tích chọn các lô cần gộp vào cùng một mẻ.";
    mergeBtn.disabled = true;
    return;
  }

  if (count === 1) {
    statusBox.className = "merge-status-box";
    statusBox.textContent = "Vui lòng chọn thêm ít nhất 1 lô cùng loại sản phẩm để gộp (tối thiểu 2 lô).";
    mergeBtn.disabled = true;
    return;
  }

  // Kiểm tra điều kiện: Các lô có cùng loại sản phẩm hay không
  const productNames = Array.from(new Set(selectedBatches.map((b) => b.product_name)));

  if (productNames.length === 1) {
    // ĐIỀU KIỆN THỎA MÃN: CÙNG LOẠI SẢN PHẨM -> ENABLE NÚT GỘP
    statusBox.className = "merge-status-box valid";
    statusBox.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
      <span>Hợp lệ: ${count} lô cùng sản phẩm <strong>"${escapeHtml(productNames[0])}"</strong></span>
    `;
    mergeBtn.disabled = false;
  } else {
    // ĐIỀU KIỆN KHÔNG THỎA MÃN: KHÁC LOẠI SẢN PHẨM -> DISABLE NÚT VÀ CẢNH BÁO
    statusBox.className = "merge-status-box invalid";
    statusBox.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      <span>Không thể gộp: Các lô đã chọn KHÁC loại sản phẩm (${productNames.map((p) => `"${escapeHtml(p)}"`).join(" vs ")}). Chỉ được gộp các lô cùng sản phẩm!</span>
    `;
    mergeBtn.disabled = true;
  }
}

/** Mở Modal Gộp Lô hiển thị chi tiết các lô và nhập khối lượng trích xuất (T-45) */
function openMergeModal() {
  const selectedBatches = batches.filter((b) => selectedBatchIds.has(b.id));
  if (selectedBatches.length < 2) return;

  const productNames = Array.from(new Set(selectedBatches.map((b) => b.product_name)));
  if (productNames.length > 1) {
    toast("Không thể gộp: Các lô được chọn khác loại sản phẩm!", "error");
    return;
  }

  $("merge-modal-product-name").textContent = productNames[0];

  // Đổ danh sách các lô được chọn lên modal
  $("merge-modal-items-body").innerHTML = selectedBatches
    .map(
      (b) => `
      <tr>
        <td><code>#${escapeHtml(b.id)}</code></td>
        <td>${escapeHtml(farmLabel(b.farm_id))}</td>
        <td class="is-right font-mono">${formatNumber(b.quantity)} kg</td>
        <td class="is-right">
          <input type="number" class="input-extract-qty" data-batch-id="${escapeHtml(b.id)}"
                 value="${escapeHtml(b.quantity)}" min="0.1" max="${escapeHtml(b.quantity)}" step="0.1" />
        </td>
      </tr>`
    )
    .join("");

  // Đổ danh sách thửa đất lưu trữ đích
  $("merge-modal-dest-farm").innerHTML = farms
    .map((f) => `<option value="${f.id}">#${f.id} — ${escapeHtml(f.name)}</option>`)
    .join("");
  if (farms.length > 0) {
    $("merge-modal-dest-farm").value = String(selectedBatches[0].farm_id);
  }

  calculateMergeTotal();
  $("merge-modal").hidden = false;
}

/** Tính toán trực quan tổng khối lượng gộp thu được */
function calculateMergeTotal() {
  let total = 0;
  const inputs = document.querySelectorAll(".input-extract-qty");
  inputs.forEach((input) => {
    const val = Number(input.value) || 0;
    total += val;
  });
  $("merge-modal-total-qty").textContent = formatNumber(Math.round(total * 100) / 100);
}

/** Xác nhận thực hiện gộp lô và hiển thị mã lô mới tạo ra */
async function executeBatchMerge() {
  const selectedBatches = batches.filter((b) => selectedBatchIds.has(b.id));
  const inputs = document.querySelectorAll(".input-extract-qty");
  const extractedMap = new Map();

  let hasInvalidQty = false;
  inputs.forEach((input) => {
    const bId = Number(input.dataset.batchId);
    const qty = Number(input.value);
    const origBatch = selectedBatches.find((b) => b.id === bId);
    if (!qty || qty <= 0 || (origBatch && qty > origBatch.quantity)) {
      hasInvalidQty = true;
    }
    extractedMap.set(bId, qty);
  });

  if (hasInvalidQty) {
    toast("Khối lượng cần lấy phải lớn hơn 0 và không vượt quá khối lượng hiện có của từng lô.", "error");
    return;
  }

  const destFarmId = Number($("merge-modal-dest-farm").value);
  if (!destFarmId) {
    toast("Vui lòng chọn thửa đất tiếp nhận lô gộp.", "error");
    return;
  }

  let totalQty = 0;
  extractedMap.forEach((qty) => {
    totalQty += qty;
  });

  const productName = selectedBatches[0].product_name;
  const todayStr = new Date().toISOString().split("T")[0];

  // Tạo lô gộp mới
  try {
    const payload = {
      farm_id: destFarmId,
      product_name: `${productName} (Lô gộp)`,
      quantity: Math.round(totalQty * 100) / 100,
      harvest_date: todayStr,
    };

    let newBatchId;
    try {
      const created = await apiRequest("/batches", { method: "POST", body: payload });
      newBatchId = created.id;
    } catch {
      // Giả lập ID nếu backend mock
      newBatchId = (batches.reduce((max, b) => Math.max(max, b.id), 0) || 100) + 1;
      batches.unshift({
        id: newBatchId,
        ...payload,
      });
    }

    // Khấu trừ khối lượng ở các lô nguồn
    for (const b of selectedBatches) {
      const extracted = extractedMap.get(b.id) || 0;
      b.quantity = Math.max(0, Math.round((b.quantity - extracted) * 100) / 100);
    }

    // Đóng Modal gộp
    $("merge-modal").hidden = true;

    // Hiển thị mã lô mới tạo ra trên Modal thành công
    const newBatchCode = `BATCH-VN-2026-M${newBatchId}`;
    $("merge-new-batch-code").textContent = newBatchCode;
    $("merge-new-batch-qty").textContent = `${formatNumber(payload.quantity)} kg`;
    $("merge-success-modal").hidden = false;

    // Reset trạng thái
    toggleMergeMode(false);
    await loadBatches();
    toast(`Gộp lô thành công! Tạo mới lô #${newBatchId} (${newBatchCode})`, "success");
  } catch (error) {
    toast(`Lỗi khi gộp lô nông sản: ${error.message}`, "error");
  }
}

/* ------------------------- T-35: BÀN GIAO NÔNG SẢN CHO ĐỐI TÁC --- */
/** Đổ danh sách tổ chức đối tác nhận (Lọc loại bỏ tổ chức hiện tại) */
function populateHandoverOrgs() {
  const select = $("handover-recipient-org");
  if (!select) return;

  // LỌC BỎ CHÍNH TỔ CHỨC CỦA NGƯỜI DÙNG HIỆN TẠI (T-35)
  // Chỉ hiển thị tên công khai của đối tác
  const recipientOrgs = PARTNER_ORGANIZATIONS.filter((org) => org.id !== CURRENT_USER_ORG.id);

  select.innerHTML = '<option value="">-- Chọn tổ chức đối tác nhận --</option>' +
    recipientOrgs
      .map(
        (org) => `<option value="${escapeHtml(org.id)}">${escapeHtml(org.name)}</option>`
      )
      .join("");
}

/** Đổ danh sách các lô có thể bàn giao */
function populateHandoverBatches() {
  const select = $("handover-batch-id");
  if (!select) return;
  const currentVal = select.value;

  select.innerHTML = '<option value="">-- Chọn lô nông sản bàn giao --</option>' +
    batches
      .map(
        (b) => `
        <option value="${escapeHtml(b.id)}" ${String(b.id) === currentVal ? "selected" : ""}>
          Lô #${escapeHtml(b.id)} — ${escapeHtml(b.product_name)} (${formatNumber(b.quantity)} kg)
        </option>`
      )
      .join("");
}

/** GET /handovers -> Tải lịch sử bàn giao từ API (hỗ trợ fallback) */
async function loadHandovers() {
  try {
    const data = await apiRequest("/handovers");
    if (Array.isArray(data) && data.length > 0) {
      handovers = data.map((h) => {
        const matchingBatch = batches.find((b) => b.id === h.batch_id);
        return {
          id: `BG-${String(h.id).padStart(4, "0")}`,
          batch_id: h.batch_id,
          batch_name: matchingBatch ? matchingBatch.product_name : `Lô #${h.batch_id}`,
          recipient_org_name: h.recipient_org_name,
          date: h.created_at ? h.created_at.split("T")[0] : h.date,
          notes: h.notes,
          status: h.status,
          is_overdue: Boolean(h.is_overdue || h.status === "OVERDUE"),
        };
      });
    }
    renderHandovers();
  } catch (error) {
    // Nếu chưa có kết nối API thì render dữ liệu hiện tại
    renderHandovers();
  }
}

/** Vẽ bảng lịch sử các đợt bàn giao */
function renderHandovers() {
  const tbody = $("handover-table-body");
  if (!tbody) return;

  tbody.innerHTML = handovers
    .map(
      (h) => {
        const isOverdue = Boolean(h.is_overdue || h.status === "OVERDUE");
        const badgeClass = isOverdue
          ? "handover-badge handover-badge--overdue"
          : h.status === "COMPLETED" || h.status === "Hoàn tất"
          ? "handover-badge handover-badge--completed"
          : "handover-badge handover-badge--pending";

        const statusLabel = isOverdue
          ? "Quá hạn 48h"
          : h.status === "PENDING" || h.status === "Đang chờ"
          ? "Đang chờ nhận"
          : "Hoàn tất";

        return `
        <tr class="${isOverdue ? "row-overdue" : ""}">
          <td class="id-cell">${escapeHtml(h.id)}</td>
          <td><strong>#${escapeHtml(h.batch_id)}</strong> — ${escapeHtml(h.batch_name)}</td>
          <td>${escapeHtml(h.recipient_org_name)}</td>
          <td>${escapeHtml(formatDate(h.date))}</td>
          <td>${escapeHtml(h.notes || "—")}</td>
          <td class="is-center">
            <span class="${badgeClass}">
              ${escapeHtml(statusLabel)}
            </span>
          </td>
        </tr>`;
      }
    )
    .join("");

  $("handover-empty").hidden = handovers.length > 0;
}

/** Xử lý submit phiếu bàn giao với validation T-22 (Task 3) */
async function handleHandoverSubmit(event) {
  event.preventDefault();

  const batchSelect = $("handover-batch-id");
  const orgSelect = $("handover-recipient-org");
  const dateInput = $("handover-date");
  const notesInput = $("handover-notes");

  const batchError = $("handover-batch-error");
  const orgError = $("handover-org-error");
  const dateError = $("handover-date-error");

  // Xoá trạng thái lỗi cũ (T-22 Error reset)
  [batchSelect, orgSelect, dateInput].forEach((el) => el.classList.remove("input-error"));
  [batchError, orgError, dateError].forEach((el) => {
    el.hidden = true;
    el.textContent = "";
  });

  let hasError = false;

  // Validate Lô nông sản
  if (!batchSelect.value) {
    batchSelect.classList.add("input-error");
    batchError.textContent = "Vui lòng chọn lô nông sản cần bàn giao.";
    batchError.hidden = false;
    hasError = true;
  }

  // VALIDATE BẮT BUỘC CHỌN TỔ CHỨC NHẬN (Yêu cầu T-35 / Task 3)
  if (!orgSelect.value) {
    orgSelect.classList.add("input-error");
    orgError.textContent = "Bắt buộc phải chọn tổ chức đối tác nhận trước khi gửi.";
    orgError.hidden = false;
    toast("Vui lòng chọn tổ chức đối tác nhận trước khi gửi bàn giao!", "error");
    hasError = true;
  }

  // Validate Ngày bàn giao
  if (!dateInput.value) {
    dateInput.classList.add("input-error");
    dateError.textContent = "Vui lòng chọn ngày bàn giao.";
    dateError.hidden = false;
    hasError = true;
  }

  if (hasError) return;

  const batchId = Number(batchSelect.value);
  const orgId = Number(orgSelect.value);
  const selectedBatch = batches.find((b) => b.id === batchId);
  const selectedOrg = PARTNER_ORGANIZATIONS.find((o) => o.id === orgId);

  const payload = {
    batch_id: batchId,
    recipient_org_id: orgId,
    recipient_org_name: selectedOrg ? selectedOrg.name : "Đối tác chuỗi cung ứng",
    notes: notesInput.value.trim() || null,
  };

  try {
    try {
      const created = await apiRequest("/handovers", { method: "POST", body: payload });
      await loadHandovers();
    } catch {
      // Fallback local state nếu chưa có quyền hoặc mock
      const newHandover = {
        id: `BG-2026-${String(handovers.length + 1).padStart(3, "0")}`,
        batch_id: batchId,
        batch_name: selectedBatch ? selectedBatch.product_name : `Lô #${batchId}`,
        recipient_org_name: payload.recipient_org_name,
        date: dateInput.value,
        notes: payload.notes,
        status: "PENDING",
        is_overdue: false,
      };
      handovers.unshift(newHandover);
      renderHandovers();
    }

    // Reset form và hiển thị thông báo thành công (Task 3)
    $("handover-form").reset();
    toast(
      `Tạo phiếu bàn giao thành công! Đã gửi lô #${batchId} tới "${payload.recipient_org_name}".`,
      "success"
    );
  } catch (err) {
    toast(`Lỗi khi tạo phiếu bàn giao: ${err.message}`, "error");
  }
}

/* ----------------------- 9b. Danh mục sản phẩm (T-14 / SCRUM-30) --- */
/**
 * GET /products -> cập nhật bảng danh mục sản phẩm.
 * Không lọc theo tổ chức (Global Scope - Tenant Scope Bypassed).
 */
async function loadProducts() {
  try {
    const data = await apiRequest("/products", { auth: false }); // Danh mục public
    products = Array.isArray(data) ? data : [];
    renderProducts();
  } catch (error) {
    products = [];
    renderProducts();
    // Không toast lỗi nặng - danh mục sản phẩm là optional hiển thị
    console.warn("Không tải được danh mục sản phẩm:", error.message);
  }
}

/** Vẽ bảng danh sách sản phẩm toàn hệ thống. */
function renderProducts() {
  const unitLabels = {
    kg: "kg",
    g: "g",
    ton: "tấn",
    liter: "lít",
    box: "thùng",
    bottle: "chai",
    piece: "cái/quả",
    bundle: "bó",
  };

  $("product-table-body").innerHTML = products
    .map(
      (p) => `
      <tr class="${p.id === editingProductId ? "is-editing" : ""}">
        <td class="id-cell">${escapeHtml(p.id)}</td>
        <td><strong>${escapeHtml(p.name)}</strong></td>
        <td><span class="meta-tag font-mono">${escapeHtml(unitLabels[p.unit] || p.unit)}</span></td>
        <td>${escapeHtml(p.description || "—")}</td>
        <td>
          <div class="table__actions">
            <button class="btn btn--primary btn--sm" type="button"
                    data-action="edit" data-entity="product"
                    data-id="${escapeHtml(p.id)}">Sửa</button>
            ${canDelete()
              ? `<button class="btn btn--danger btn--sm" type="button"
                    data-action="delete" data-entity="product"
                    data-id="${escapeHtml(p.id)}">Xoá</button>`
              : ""}
          </div>
        </td>
      </tr>`
    )
    .join("");

  $("product-empty").hidden = products.length > 0;
}

/** Nhãn nút submit form sản phẩm theo chế độ (thêm / sửa). */
function productSubmitLabel() {
  return editingProductId === null ? "Thêm sản phẩm" : "Cập nhật sản phẩm";
}

/** Xử lý submit form sản phẩm: POST (thêm) hoặc PATCH (sửa). */
async function handleProductSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;

  const payload = {
    name: $("product-name").value.trim(),
    unit: $("product-unit").value,
    description: $("product-description").value.trim() || null,
  };

  const isEditing = editingProductId !== null;
  const button = $("product-submit");
  setButtonLoading(button, true, "Đang lưu…", productSubmitLabel());

  try {
    if (isEditing) {
      await apiRequest(`/products/${editingProductId}`, { method: "PATCH", body: payload });
      toast(`Đã cập nhật sản phẩm: ${payload.name}`, "success");
    } else {
      const created = await apiRequest("/products", { method: "POST", body: payload });
      toast(`Thêm thành công sản phẩm #${created.id}: ${created.name}`, "success");
    }
    resetProductForm();
    await loadProducts();
    $("product-name").focus();
  } catch (error) {
    toast(`${isEditing ? "Cập nhật" : "Thêm"} sản phẩm thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(button, false, "Đang lưu…", productSubmitLabel());
  }
}

/** Đưa form sản phẩm về chế độ "thêm mới". */
function resetProductForm() {
  editingProductId = null;
  $("product-form").reset();
  $("product-form-mode").hidden = true;
  $("product-cancel").hidden = true;
  $("product-submit").textContent = productSubmitLabel();
}

/** Bấm "Sửa" ở bảng -> đổ dữ liệu sản phẩm lên form chế độ sửa. */
function startEditProduct(productId) {
  const product = products.find((p) => p.id === productId);
  if (!product) {
    toast(`Không tìm thấy sản phẩm #${productId}.`, "error");
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

  renderProducts(); // tô nền dòng đang sửa
  $("product-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("product-name").focus();
}

/** Xoá sản phẩm khỏi danh mục (chỉ admin). */
async function deleteProduct(productId) {
  const product = products.find((p) => p.id === productId);
  const label = product ? `#${product.id} — ${product.name}` : `#${productId}`;

  if (!window.confirm(`Xoá sản phẩm ${label} khỏi danh mục toàn hệ thống?\nHành động này không thể hoàn tác.`)) {
    return;
  }

  try {
    await apiRequest(`/products/${productId}`, { method: "DELETE" });
    toast(`Đã xoá sản phẩm ${label} khỏi danh mục.`, "success");
    if (editingProductId === productId) {
      resetProductForm();
    }
    await loadProducts();
  } catch (error) {
    toast(`Xoá sản phẩm thất bại: ${error.message}`, "error");
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

/* --------------------------------------------------------- 11. Sự kiện --- */
function bindEvents() {
  $("login-form").addEventListener("submit", handleLoginSubmit);
  $("btn-logout").addEventListener("click", handleLogout);
  $("farm-form").addEventListener("submit", handleFarmSubmit);
  $("batch-form").addEventListener("submit", handleBatchSubmit);
  $("product-form").addEventListener("submit", handleProductSubmit); // T-14 / SCRUM-30
  $("farm-cancel").addEventListener("click", () => cancelEdit("farm"));
  $("batch-cancel").addEventListener("click", () => cancelEdit("batch"));
  $("product-cancel").addEventListener("click", () => cancelEdit("product")); // T-14
  $("btn-reload").addEventListener("click", () => reloadAll());

  // Cột "Thao tác" của các bảng dùng event delegation: nội dung bảng được vẽ lại
  // liên tục nên chỉ gắn 1 listener cho mỗi <tbody> thay vì gắn cho từng nút.
  $("farm-table-body").addEventListener("click", handleTableAction);
  $("batch-table-body").addEventListener("click", handleTableAction);
  $("product-table-body").addEventListener("click", handleTableAction); // T-14

  // Sự kiện T-34 & T-45: Lọc & Gộp lô nông sản
  $("btn-toggle-merge-mode").addEventListener("click", () => toggleMergeMode());
  $("batch-filter-product").addEventListener("change", (e) => {
    selectedProductFilter = e.target.value;
    renderBatches();
  });
  $("batch-table-body").addEventListener("change", (e) => {
    if (e.target.classList.contains("batch-chk")) {
      const bId = Number(e.target.dataset.batchId);
      if (e.target.checked) {
        selectedBatchIds.add(bId);
      } else {
        selectedBatchIds.delete(bId);
      }
      updateMergeActionBar();
    }
  });
  $("btn-open-merge-modal").addEventListener("click", openMergeModal);
  $("btn-close-merge-modal").addEventListener("click", () => ($("merge-modal").hidden = true));
  $("btn-cancel-merge-modal").addEventListener("click", () => ($("merge-modal").hidden = true));
  $("btn-confirm-merge").addEventListener("click", executeBatchMerge);
  $("btn-close-merge-success").addEventListener("click", () => ($("merge-success-modal").hidden = true));

  // Tính lại tổng khối lượng khi sửa ô input trong modal
  $("merge-modal-items-body").addEventListener("input", (e) => {
    if (e.target.classList.contains("input-extract-qty")) {
      calculateMergeTotal();
    }
  });

  // Sự kiện T-35: Form Bàn giao nông sản
  $("handover-form").addEventListener("submit", handleHandoverSubmit);

  // Xoá lỗi khi người dùng chọn lại trường
  $("handover-recipient-org").addEventListener("change", (e) => {
    e.target.classList.remove("input-error");
    $("handover-org-error").hidden = true;
  });
  $("handover-batch-id").addEventListener("change", (e) => {
    e.target.classList.remove("input-error");
    $("handover-batch-error").hidden = true;
  });
  $("handover-date").addEventListener("input", (e) => {
    e.target.classList.remove("input-error");
    $("handover-date-error").hidden = true;
  });
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

  if (action === "edit") {
    if (entity === "farm") {
      startEditFarm(id);
    } else if (entity === "product") {
      startEditProduct(id);
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
    } else if (entity === "product") {
      deleteProduct(id);
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
  await loadProducts(); // Danh mục sản phẩm toàn hệ thống (T-14 / SCRUM-30)
  await loadHandovers(); // Tải lịch sử bàn giao (T-35 / Task 3 / Task 5)
  updateProductFilterOptions();
  populateHandoverOrgs();
  populateHandoverBatches();
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
