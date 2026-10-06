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

/**
 * Lớp lỗi mở rộng lưu trữ đầy đủ thông tin lỗi từ máy chủ:
 * - status: mã HTTP (422, 404, 401, 500...)
 * - data: toàn bộ JSON payload máy chủ trả về
 * - detail: danh sách lỗi Pydantic (mảng) hoặc thông điệp chuỗi
 */
class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
    this.detail = data ? data.detail : null;
  }
}

/**
 * Bật/tắt trạng thái "đang gửi" của nút submit:
 * - Vô hiệu hoá nút và chặn tương tác chuột (chống double click)
 * - Hiển thị spinner và loadingText
 * - Phục hồi nhãn và icon ban đầu khi hoàn tất
 */
function setButtonLoading(button, isLoading, loadingText = "Đang xử lý…", idleText = null) {
  if (!button) return;
  button.disabled = isLoading;
  if (isLoading) {
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    if (!button.dataset.originalHtml) {
      button.dataset.originalHtml = button.innerHTML;
    }
    button.innerHTML = `<span class="btn-spinner" aria-hidden="true"></span> <span>${escapeHtml(loadingText)}</span>`;
  } else {
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    if (idleText) {
      const svg = button.querySelector("svg");
      if (svg) {
        button.innerHTML = `${svg.outerHTML} <span>${escapeHtml(idleText)}</span>`;
      } else {
        button.innerHTML = `<span>${escapeHtml(idleText)}</span>`;
      }
      delete button.dataset.originalHtml;
    } else if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
      delete button.dataset.originalHtml;
    }
  }
}

/* -------------------------- 2.1. Cơ chế xử lý lỗi form dùng chung (SCRUM-37) --- */

/**
 * Chuyển đổi thông điệp lỗi của Pydantic / FastAPI sang tiếng Việt rõ ràng, dễ hiểu.
 */
function humanizeValidationError(msg, type, ctx) {
  if (!msg) return "Dữ liệu nhập không hợp lệ.";
  const lower = String(msg).toLowerCase();

  if (lower.includes("greater than 0") || (type === "greater_than" && ctx && ctx.gt === 0)) {
    return "Giá trị phải lớn hơn 0.";
  }
  if (lower.includes("greater than or equal to")) {
    const val = ctx && ctx.ge !== undefined ? ctx.ge : "";
    return `Giá trị phải lớn hơn hoặc bằng ${val}.`.trim();
  }
  if (lower.includes("less than or equal to")) {
    const val = ctx && ctx.le !== undefined ? ctx.le : "";
    return `Giá trị phải nhỏ hơn hoặc bằng ${val}.`.trim();
  }
  if (lower.includes("field required") || type === "missing") {
    return "Vui lòng nhập trường này.";
  }
  if (lower.includes("at least 1 character") || type === "string_too_short") {
    return "Không được để trống trường này.";
  }
  if (lower.includes("at most") || type === "string_too_long") {
    const max = ctx && ctx.max_length !== undefined ? ctx.max_length : "";
    return `Độ dài không được vượt quá ${max} ký tự.`.trim();
  }
  if (lower.includes("valid date") || type === "date_from_datetime_parsing") {
    return "Ngày không đúng định dạng hợp lệ (YYYY-MM-DD).";
  }
  if (lower.includes("valid integer") || type === "int_parsing") {
    return "Vui lòng nhập số nguyên hợp lệ.";
  }
  if (lower.includes("valid number") || type === "float_parsing") {
    return "Vui lòng nhập số hợp lệ.";
  }
  return msg;
}

/**
 * Tìm phần tử nhập liệu tương ứng trong form từ tên trường do máy chủ trả về.
 * Tìm linh hoạt theo [name="..."], direct #id, #id hậu tố, và bí danh trường.
 */
function findFieldElement(form, fieldName) {
  if (!form || !fieldName) return null;
  const nameStr = String(fieldName).trim();
  const kebab = nameStr.replace(/_/g, "-");

  // 1. Khớp theo name attribute
  let el = form.querySelector(`[name="${nameStr}"]`) || form.querySelector(`[name="${kebab}"]`);
  if (el) return el;

  // 2. Khớp theo ID trực tiếp
  el = form.querySelector(`#${nameStr}`) || form.querySelector(`#${kebab}`);
  if (el) return el;

  // 3. Khớp theo ID có hậu tố (ví dụ batch-farm-id khớp farm_id)
  el = form.querySelector(`[id$="-${kebab}"]`);
  if (el) return el;

  // 4. Danh sách bí danh trường dùng chung trong hệ thống
  const aliases = {
    farm_id: ["batch-farm-id", "farm_id", "farm-id"],
    product_name: ["batch-product-name", "product_name", "product-name"],
    quantity: ["batch-quantity", "quantity"],
    harvest_date: ["batch-harvest-date", "harvest_date", "harvest-date"],
    name: ["farm-name", "name"],
    location: ["farm-location", "location"],
    area: ["farm-area", "area"],
    owner: ["farm-owner", "owner"],
    username: ["login-username", "username"],
    password: ["login-password", "password"],
  };

  const list = aliases[nameStr] || [];
  for (const candidate of list) {
    el = form.querySelector(`[name="${candidate}"]`) || form.querySelector(`#${candidate}`);
    if (el) return el;
  }

  return null;
}

/**
 * Hiển thị lỗi ngay cạnh ô nhập liệu tương ứng:
 * - Tô viền đỏ và đổi nền ô nhập (.is-invalid)
 * - Gắn cờ accessibility aria-invalid
 * - Chèn phần tử .field-error ngay dưới/cạnh ô nhập kèm icon cảnh báo
 */
function showFieldError(inputEl, message) {
  if (!inputEl) return;

  const formGroup = inputEl.closest(".form-group") || inputEl.parentElement;
  inputEl.classList.add("is-invalid");
  inputEl.setAttribute("aria-invalid", "true");

  const fieldKey = inputEl.name || inputEl.id || "field";
  const errorId = `${inputEl.id || fieldKey}-error`;
  inputEl.setAttribute("aria-describedby", errorId);

  if (formGroup) {
    formGroup.classList.add("has-error");
  }

  let errorEl = formGroup ? formGroup.querySelector(`.field-error[data-for="${fieldKey}"]`) : null;
  if (!errorEl) {
    errorEl = document.createElement("div");
    errorEl.className = "field-error";
    errorEl.id = errorId;
    errorEl.setAttribute("data-for", fieldKey);
    errorEl.setAttribute("role", "alert");

    // Chèn sau .input-wrapper (nếu có wrapper) hoặc ngay sau input
    const target = inputEl.closest(".input-wrapper") || inputEl;
    if (target.nextSibling) {
      target.parentNode.insertBefore(errorEl, target.nextSibling);
    } else {
      target.parentNode.appendChild(errorEl);
    }
  }

  errorEl.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="8" x2="12" y2="12"></line>
      <line x1="12" y1="16" x2="12.01" y2="16"></line>
    </svg>
    <span class="field-error-text">${escapeHtml(message)}</span>
  `;
}

/** Xoá lỗi của một ô nhập cụ thể khi người dùng chỉnh sửa. */
function clearFieldError(inputEl) {
  if (!inputEl) return;
  inputEl.classList.remove("is-invalid");
  inputEl.removeAttribute("aria-invalid");
  inputEl.removeAttribute("aria-describedby");

  const formGroup = inputEl.closest(".form-group") || inputEl.parentElement;
  if (formGroup) {
    const fieldKey = inputEl.name || inputEl.id || "field";
    const errorEl = formGroup.querySelector(`.field-error[data-for="${fieldKey}"]`);
    if (errorEl) {
      errorEl.remove();
    }
    if (!formGroup.querySelector(".field-error")) {
      formGroup.classList.remove("has-error");
    }
  }
}

/** Xoá toàn bộ lỗi hiển thị của một biểu mẫu. */
function clearFormErrors(form) {
  if (!form) return;
  form.querySelectorAll(".is-invalid").forEach((el) => {
    el.classList.remove("is-invalid");
    el.removeAttribute("aria-invalid");
    el.removeAttribute("aria-describedby");
  });
  form.querySelectorAll(".has-error").forEach((el) => {
    el.classList.remove("has-error");
  });
  form.querySelectorAll(".field-error").forEach((el) => {
    el.remove();
  });
  const alertEl = form.querySelector(".form-alert-error");
  if (alertEl) {
    alertEl.remove();
  }
}

/** Hiển thị banner lỗi chung của biểu mẫu khi không gắn riêng vào ô nào. */
function showFormAlert(form, message) {
  if (!form || !message) return;
  let alertEl = form.querySelector(".form-alert-error");
  if (!alertEl) {
    alertEl = document.createElement("div");
    alertEl.className = "form-alert-error";
    alertEl.setAttribute("role", "alert");
    form.insertBefore(alertEl, form.firstChild);
  }
  alertEl.innerHTML = `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="8" x2="12" y2="12"></line>
      <line x1="12" y1="16" x2="12.01" y2="16"></line>
    </svg>
    <span>${escapeHtml(message)}</span>
  `;
}

/**
 * Đọc danh sách lỗi trả về từ máy chủ và hiển thị lỗi ngay cạnh ô nhập liệu tương ứng.
 * Xử lý cả danh sách lỗi Pydantic (HTTP 422) lẫn thông điệp lỗi chuỗi (404, 401...).
 */
function applyServerErrors(form, error) {
  if (!form) return;
  clearFormErrors(form);

  if (!error) return;

  let firstInvalidEl = null;

  // Trường hợp 1: Danh sách lỗi mảng từ Pydantic (HTTP 422)
  if (Array.isArray(error.detail)) {
    for (const item of error.detail) {
      const loc = item.loc || [];
      const fieldName = loc.length > 0 ? loc[loc.length - 1] : null;
      if (fieldName) {
        const el = findFieldElement(form, fieldName);
        const friendlyMsg = humanizeValidationError(item.msg, item.type, item.ctx);
        if (el) {
          showFieldError(el, friendlyMsg);
          if (!firstInvalidEl) firstInvalidEl = el;
        }
      }
    }
  }
  // Trường hợp 2: Lỗi chi tiết dạng chuỗi từ FastAPI HTTPException
  else if (typeof error.detail === "string") {
    const detail = error.detail.trim();
    const lower = detail.toLowerCase();
    let matchedEl = null;

    if (lower.includes("vùng trồng") || lower.includes("farm")) {
      matchedEl = findFieldElement(form, "farm_id") || findFieldElement(form, "name");
    } else if (lower.includes("tên đăng nhập") || lower.includes("tài khoản")) {
      matchedEl = findFieldElement(form, "username");
    } else if (lower.includes("mật khẩu")) {
      matchedEl = findFieldElement(form, "password");
    }

    if (matchedEl) {
      showFieldError(matchedEl, detail);
      firstInvalidEl = matchedEl;
    } else {
      showFormAlert(form, detail);
    }
  } else if (error.message) {
    showFormAlert(form, error.message);
  }

  // Tự động focus vào ô bị lỗi đầu tiên
  if (firstInvalidEl) {
    firstInvalidEl.focus();
  }
}

/**
 * Tự động xoá lỗi ngay khi người dùng gõ phím hoặc thay đổi giá trị trong ô.
 */
function setupFormErrorAutoClear(form) {
  if (!form || form.dataset.errorListenerAttached) return;
  form.dataset.errorListenerAttached = "true";

  const clearHandler = (event) => {
    const target = event.target;
    if (target && target.matches("input, select, textarea")) {
      clearFieldError(target);
      const alertEl = form.querySelector(".form-alert-error");
      if (alertEl) {
        alertEl.remove();
      }
    }
  };

  form.addEventListener("input", clearHandler);
  form.addEventListener("change", clearHandler);
}

/* -------------------------------------------------------- 3. Gọi API --- */
/**
 * Gọi API backend và trả về dữ liệu JSON.
 * Ném ApiError kèm đầy đủ status và detail lỗi từ máy chủ nếu thất bại.
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
    throw new ApiError(
      `Không kết nối được backend (${API_BASE_URL}). Hãy chắc chắn uvicorn đang chạy.`,
      0,
      null
    );
  }

  const data = await readJson(response);
  if (!response.ok) {
    const message = describeError(data, response.status);
    throw new ApiError(message, response.status, data);
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
      .map((item) => {
        const field = (item.loc || []).filter((x) => x !== "body").join(".");
        const friendlyMsg = humanizeValidationError(item.msg, item.type, item.ctx);
        return field ? `${field}: ${friendlyMsg}` : friendlyMsg;
      })
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
  const button = $("login-submit");

  // Chống double-click: chặn bấm gửi hai lần trong lúc chờ phản hồi
  if (form.dataset.submitting === "true") {
    console.warn("Chặn bấm gửi nhiều lần (double-click prevention)");
    return;
  }
  form.dataset.submitting = "true";

  clearFormErrors(form);

  const username = $("login-username").value.trim();
  const password = $("login-password").value; // không trim mật khẩu

  setButtonLoading(button, true, "Đang kiểm tra…", "Đăng nhập hệ thống");

  try {
    const data = await requestLogin(username, password);
    startSession({ username: data.username, role: data.role, password });
    form.reset();
    clearFormErrors(form);
    toast(`Xin chào ${data.username} (role: ${data.role}).`, "success");
    await reloadAll({ silent: true });
  } catch (error) {
    applyServerErrors(form, error);
    toast(`Đăng nhập thất bại: ${error.message}`, "error");
    $("login-password").select();
  } finally {
    delete form.dataset.submitting;
    setButtonLoading(button, false, "Đang kiểm tra…", "Đăng nhập hệ thống");
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
  const button = $("farm-submit");

  // Chống double-click: chặn bấm gửi hai lần liên tiếp trong lúc chờ phản hồi
  if (form.dataset.submitting === "true") {
    console.warn("Chặn bấm gửi nhiều lần (double-click prevention)");
    return;
  }
  form.dataset.submitting = "true";

  clearFormErrors(form);

  const areaStr = $("farm-area").value.trim();
  const payload = {
    name: $("farm-name").value.trim(),
    location: $("farm-location").value.trim(),
    area: areaStr !== "" ? Number(areaStr) : 0,
    owner: $("farm-owner").value.trim(),
  };

  const isEditing = editingFarmId !== null;
  const submitLabel = farmSubmitLabel();
  setButtonLoading(button, true, "Đang lưu…", submitLabel);

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
    applyServerErrors(form, error);
    toast(`${isEditing ? "Cập nhật" : "Thêm"} vùng trồng thất bại: ${error.message}`, "error");
  } finally {
    delete form.dataset.submitting;
    setButtonLoading(button, false, "Đang lưu…", farmSubmitLabel());
  }
}

/** Đưa form vùng trồng về chế độ "thêm mới" (bỏ dữ liệu đang sửa). */
function resetFarmForm() {
  editingFarmId = null;
  const form = $("farm-form");
  form.reset();
  clearFormErrors(form);
  delete form.dataset.submitting;
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
            <button class="btn btn--timeline btn--sm" type="button"
                    data-action="timeline" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}" title="Xem dòng thời gian sự kiện & kiểm tra chuỗi băm T-28">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              <span>Dòng thời gian</span>
            </button>
            <button class="btn btn--split btn--sm" type="button"
                    data-action="split" data-entity="batch"
                    data-id="${escapeHtml(batch.id)}" title="Tách lô nông sản có khóa dòng lô mẹ (T-41 / SCRUM-57)">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" y1="9" x2="6" y2="21"/></svg>
              <span>Tách lô</span>
            </button>
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
  const button = $("batch-submit");

  // Chống double-click: chặn bấm gửi hai lần liên tiếp (chỉ tạo đúng 1 lô duy nhất)
  if (form.dataset.submitting === "true") {
    console.warn("Chặn bấm gửi nhiều lần (double-click prevention)");
    return;
  }
  form.dataset.submitting = "true";

  clearFormErrors(form);

  const farmSelect = $("batch-farm-id");
  const qtyStr = $("batch-quantity").value.trim();

  const payload = {
    farm_id: farmSelect.value ? Number(farmSelect.value) : 0,
    product_name: $("batch-product-name").value.trim(),
    quantity: qtyStr !== "" ? Number(qtyStr) : 0,
    harvest_date: $("batch-harvest-date").value,
  };

  const isEditing = editingBatchId !== null;
  const submitLabel = batchSubmitLabel();
  setButtonLoading(button, true, "Đang lưu…", submitLabel);

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
    applyServerErrors(form, error);
    toast(`${isEditing ? "Cập nhật" : "Tạo"} lô nông sản thất bại: ${error.message}`, "error");
  } finally {
    delete form.dataset.submitting;
    setButtonLoading(button, false, "Đang lưu…", batchSubmitLabel());
  }
}

/** Đưa form lô nông sản về chế độ "tạo mới" (bỏ dữ liệu đang sửa). */
function resetBatchForm() {
  editingBatchId = null;
  const form = $("batch-form");
  form.reset();
  clearFormErrors(form);
  delete form.dataset.submitting;
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

/* ------------------ 10. Dòng thời gian sự kiện & Kiểm tra toàn vẹn T-28 (T-31) --- */
let activeTimelineBatchId = null;
let activeTimelineEvents = [];
let activeIntegrityReport = null;

/** Bảng phân loại loại sự kiện chuỗi cung ứng */
function getEventTypeMeta(eventType) {
  const map = {
    HARVEST: {
      name: "Thu hoạch",
      badgeClass: "event-type-badge--harvest",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M12 2a10 10 0 0 1 10 10c0 5.523-4.477 10-10 10S2 17.523 2 12c0-2.5 1-5 3-7"/><circle cx="12" cy="12" r="2.5" fill="#10b981"/></svg>`,
    },
    QUALITY_INSPECTION: {
      name: "Kiểm định chất lượng",
      badgeClass: "event-type-badge--quality",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>`,
    },
    WASH_AND_SORT: {
      name: "Sơ chế & Phân loại",
      badgeClass: "event-type-badge--processing",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>`,
    },
    PACKAGING: {
      name: "Đóng gói & Dán nhãn",
      badgeClass: "event-type-badge--package",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>`,
    },
    COLD_STORAGE_IN: {
      name: "Nhập kho lạnh",
      badgeClass: "event-type-badge--cold",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M2 12h20M12 2v20M4.93 4.93l14.14 14.14M4.93 19.07l14.14-14.14"/></svg>`,
    },
    TEMPERATURE_LOG: {
      name: "Giám sát chuỗi lạnh IoT",
      badgeClass: "event-type-badge--iot",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/></svg>`,
    },
    TRANSPORT_DISPATCH: {
      name: "Xuất kho vận chuyển",
      badgeClass: "event-type-badge--transport",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>`,
    },
    TRANSIT_TELEMETRY: {
      name: "Hành trình xe lạnh",
      badgeClass: "event-type-badge--transport",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>`,
    },
    DISTRIBUTION_CENTER: {
      name: "Nhập TT Phân phối",
      badgeClass: "event-type-badge--distribution",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
    },
    RETAIL_HANDOVER: {
      name: "Bàn giao siêu thị",
      badgeClass: "event-type-badge--retail",
      icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>`,
    },
  };

  return map[eventType] || {
    name: eventType,
    badgeClass: "event-type-badge--quality",
    icon: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  };
}

/** Định dạng thời điểm ghi nhận: HH:mm · DD/MM/YYYY */
function formatEventTime(isoString) {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return String(isoString);
    const hours = String(d.getHours()).padStart(2, "0");
    const minutes = String(d.getMinutes()).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const year = d.getFullYear();
    return `${hours}:${minutes} · ${day}/${month}/${year}`;
  } catch {
    return String(isoString);
  }
}

/** Chuyển đổi dữ liệu sự kiện (JSON) thành các thẻ tag rõ ràng */
function parseDataToPills(dataStr) {
  if (!dataStr) return "";
  try {
    const obj = JSON.parse(dataStr);
    if (typeof obj === "object" && obj !== null) {
      const keys = Object.keys(obj);
      if (keys.length > 0) {
        return keys
          .map((k) => {
            const val = obj[k];
            let label = k;
            if (k === "stage") label = "Giai đoạn";
            else if (k === "temp_c") label = "Nhiệt độ";
            else if (k === "facility") label = "Cơ sở / Kho";
            else if (k === "weight_kg") label = "Khối lượng";
            else if (k === "inspector") label = "Kỹ thuật viên";
            else if (k === "result") label = "Kết quả";
            else if (k === "method") label = "Phương pháp";
            else if (k === "vehicle_plate") label = "Biển số xe";
            else if (k === "driver") label = "Tài xế";
            else if (k === "status") label = "Trạng thái";
            else if (k === "humidity_pct" || k === "humidity") label = "Độ ẩm";
            else if (k === "retailer") label = "Điểm bán";
            else if (k === "received_by") label = "Người nhận";
            else if (k === "location") label = "Địa điểm";
            else if (k === "door_opened") label = "Mở cửa xe";
            return `<div class="event-data-pill"><span>${escapeHtml(label)}:</span> <strong>${escapeHtml(val)}</strong></div>`;
          })
          .join("");
      }
    }
  } catch {
    // Nếu không phải JSON, giữ nguyên văn bản
  }
  return `<div class="event-data-pill"><strong>${escapeHtml(dataStr)}</strong></div>`;
}

/** Render banner cảnh báo toàn vẹn ở đầu trang theo kết quả hàm T-28 */
function renderIntegrityBanner(report) {
  const container = $("integrity-banner-container");
  if (!container) return;

  if (!report) {
    container.innerHTML = "";
    return;
  }

  if (report.is_valid === false || report.status === "TAMPERED") {
    // ⚠️ PHÁT HIỆN LÔ CÓ VẤN ĐỀ TOÀN VẸN: BANNER ĐỎ NỔI BẬT Ở ĐẦU TRANG
    container.innerHTML = `
      <div class="integrity-banner integrity-banner--tampered" role="alert" aria-live="assertive">
        <div class="banner-icon" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        </div>
        <div style="flex: 1; min-width: 0;">
          <div class="banner-title">
            <span>⚠️ CẢNH BÁO TOÀN VẸN: PHÁT HIỆN DỮ LIỆU BỊ CAN THIỆP TRÁI PHÉP!</span>
            <span class="banner-badge-violation">${escapeHtml(report.tamper_type || "TAMPERED")}</span>
          </div>
          <div class="banner-desc">
            Hàm kiểm định <strong>T-28</strong> phát hiện sai lệch tính toàn vẹn trong chuỗi băm SHA-256 của lô nông sản này. Bản ghi đã bị can thiệp trái phép trực tiếp ngoài hệ thống (dấu hiệu sửa lén SQL hoặc xoá bản ghi)!
          </div>
          <div class="banner-detail-box">
            <div><strong>Chi tiết vi phạm:</strong> ${escapeHtml(report.detail)}</div>
            ${
              report.tampered_event_id
                ? `<div><strong>Mốc sự kiện vi phạm:</strong> Sự kiện #${escapeHtml(report.tampered_event_id)} (Thứ tự sequence: #${escapeHtml(report.tampered_sequence)})</div>`
                : ""
            }
            ${
              report.recorded_hash
                ? `<div><strong>Mã băm lưu trữ:</strong> <span class="font-mono">${escapeHtml(report.recorded_hash.slice(0, 24))}…</span> | <strong>Mã băm tính toán lại:</strong> <span class="font-mono">${escapeHtml((report.expected_hash || "").slice(0, 24))}…</span></div>`
                : ""
            }
            ${
              report.recorded_prev_hash
                ? `<div><strong>Mã băm mắt xích trước ghi nhận:</strong> <span class="font-mono">${escapeHtml(report.recorded_prev_hash.slice(0, 24))}…</span> | <strong>Mong đợi:</strong> <span class="font-mono">${escapeHtml((report.expected_prev_hash || "").slice(0, 24))}…</span></div>`
                : ""
            }
          </div>
        </div>
      </div>
    `;
  } else if (report.total_events > 0) {
    // ✅ CHUỖI NGUYÊN VẸN 100%: BANNER XANH LỤC NGỌC
    container.innerHTML = `
      <div class="integrity-banner integrity-banner--verified" role="status">
        <div class="banner-icon" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>
        </div>
        <div style="flex: 1; min-width: 0;">
          <div class="banner-title">
            <span>✅ XÁC THỰC TOÀN VẸN (T-28): CHUỖI MẬT MÃ BẤT BIẾN HỢP LỆ 100%</span>
          </div>
          <div class="banner-desc">
            Toàn bộ <strong>${escapeHtml(report.verified_count)}/${escapeHtml(report.total_events)}</strong> mốc sự kiện liên kết nối tiếp mã băm SHA-256 từ Genesis đến hiện tại hoàn toàn nguyên vẹn, không có dấu hiệu can thiệp hay đứt gãy dữ liệu.
          </div>
        </div>
      </div>
    `;
  } else {
    // CHƯA CÓ SỰ KIỆN
    container.innerHTML = `
      <div class="integrity-banner integrity-banner--verified" style="background: var(--slate-100); border-color: var(--slate-300); color: var(--slate-700);">
        <div class="banner-icon" style="background: var(--slate-400);">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        </div>
        <div style="flex: 1; min-width: 0;">
          <div class="banner-title" style="color: var(--slate-800);">Lô nông sản chưa có sự kiện nào trong chuỗi</div>
          <div class="banner-desc" style="color: var(--slate-600);">Hãy nhấn "Khôi phục chuỗi chuẩn" để nạp 10 sự kiện mẫu hoặc thêm sự kiện mới.</div>
        </div>
      </div>
    `;
  }
}

/** Render dòng thời gian dọc 1 cột tối ưu cho mobile kho bãi */
function renderVerticalTimeline(events, report) {
  const container = $("vertical-timeline");
  const empty = $("timeline-empty");
  if (!container) return;

  if (!events || events.length === 0) {
    container.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }

  if (empty) empty.hidden = true;

  container.innerHTML = events
    .map((event) => {
      const typeMeta = getEventTypeMeta(event.event_type);
      const isTampered =
        report &&
        !report.is_valid &&
        (report.tampered_event_id === event.id || report.tampered_sequence === event.sequence);

      return `
        <div class="timeline-item" id="event-node-${escapeHtml(event.id)}">
          <div class="timeline-node" style="${isTampered ? "border-color: #ef4444; color: #dc2626; box-shadow: 0 0 12px rgba(239,68,68,0.4);" : ""}">
            #${escapeHtml(event.sequence)}
          </div>
          <div class="timeline-card-content" style="${isTampered ? "border: 2px solid #ef4444; background: #fff5f5;" : ""}">
            <div class="event-card-header">
              <span class="event-type-badge ${typeMeta.badgeClass}">
                ${typeMeta.icon}
                <span>${escapeHtml(typeMeta.name)}</span>
              </span>
              <span class="event-time-badge" title="Thời điểm ghi nhận">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                <span>${escapeHtml(formatEventTime(event.timestamp))}</span>
              </span>
            </div>

            <div class="event-org-row">
              <svg class="event-org-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><line x1="9" y1="22" x2="9" y2="22.01"/><line x1="15" y1="22" x2="15" y2="22.01"/><line x1="9" y1="18" x2="9" y2="18.01"/><line x1="15" y1="18" x2="15" y2="18.01"/><line x1="9" y1="14" x2="9" y2="14.01"/><line x1="15" y1="14" x2="15" y2="14.01"/><line x1="9" y1="10" x2="9" y2="10.01"/><line x1="15" y1="10" x2="15" y2="10.01"/><line x1="9" y1="6" x2="9" y2="6.01"/><line x1="15" y1="6" x2="15" y2="6.01"/></svg>
              <span>${escapeHtml(event.organization || "Hợp tác xã Nông nghiệp Cao Lãnh")}</span>
            </div>

            <div class="event-data-pills">
              ${parseDataToPills(event.data)}
            </div>

            <div class="event-hash-row">
              <span class="hash-label" title="Mã băm SHA-256 của sự kiện này">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                <span>Hash:</span>
                <span class="hash-val">${escapeHtml(event.hash.slice(0, 10))}…${escapeHtml(event.hash.slice(-6))}</span>
              </span>
              <span class="hash-label" title="Mã băm mắt xích trước">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                <span>Prev:</span>
                <span class="hash-val">${event.prev_hash === "0".repeat(64) ? "GENESIS (0x0)" : `${escapeHtml(event.prev_hash.slice(0, 8))}…`}</span>
              </span>
            </div>
          </div>
        </div>
      `;
    })
    .join("");
}

/** Tải dữ liệu dòng thời gian và gọi hàm kiểm tra T-28 */
async function loadTimelineData(batchId, showToast = false) {
  try {
    // 1. Tải danh sách sự kiện
    const events = await apiRequest(`/batches/${batchId}/events`);
    activeTimelineEvents = Array.isArray(events) ? events : [];

    // Nếu chưa có sự kiện nào thì tự động tạo 10 sự kiện chuẩn cho tiện nghiệm thu
    if (activeTimelineEvents.length === 0 && session !== null) {
      try {
        const seeded = await apiRequest(`/batches/${batchId}/seed-events`, { method: "POST" });
        if (Array.isArray(seeded) && seeded.length > 0) {
          activeTimelineEvents = seeded;
        }
      } catch {
        // bỏ qua nếu không đủ quyền seed
      }
    }

    // 2. Gọi hàm kiểm tra toàn vẹn ở T-28
    const report = await apiRequest(`/batches/${batchId}/verify-chain`);
    activeIntegrityReport = report;

    // 3. Render giao diện
    renderIntegrityBanner(report);
    renderVerticalTimeline(activeTimelineEvents, report);

    if (showToast) {
      if (!report.is_valid) {
        toast("⚠️ Phát hiện vi phạm tính toàn vẹn chuỗi sự kiện!", "error");
      } else {
        toast("✅ Chuỗi băm SHA-256 hoàn toàn hợp lệ 100% (Hàm T-28).", "success");
      }
    }
  } catch (error) {
    toast(`Không tải được dòng thời gian lô #${batchId}: ${error.message}`, "error");
  }
}

/** Mở dòng thời gian cho một lô nông sản */
async function openBatchTimeline(batchId) {
  activeTimelineBatchId = batchId;
  const batch = batches.find((b) => b.id === batchId);

  const card = $("timeline-card");
  if (!card) return;

  if (batch) {
    $("timeline-batch-badge").textContent = `Lô #${batch.id} — ${batch.product_name}`;
    $("timeline-batch-desc").textContent =
      `Vùng trồng: ${farmLabel(batch.farm_id)} | Ngày thu hoạch: ${formatDate(batch.harvest_date)} | Sản lượng: ${formatNumber(batch.quantity)} kg`;
  } else {
    $("timeline-batch-badge").textContent = `Lô #${batchId}`;
  }

  card.hidden = false;
  card.scrollIntoView({ behavior: "smooth", block: "start" });

  await loadTimelineData(batchId, false);
}

/** Đóng dòng thời gian */
function closeBatchTimeline() {
  const card = $("timeline-card");
  if (card) card.hidden = true;
  activeTimelineBatchId = null;
  activeTimelineEvents = [];
  activeIntegrityReport = null;
}

/** Gọi hàm kiểm tra T-28 chủ động từ nút bấm */
async function verifyActiveBatchChain(showToast = true) {
  if (!activeTimelineBatchId) return;
  const btn = $("btn-verify-chain");
  setButtonLoading(btn, true, "Đang kiểm tra T-28…", "Kiểm tra toàn vẹn chuỗi (T-28)");
  await loadTimelineData(activeTimelineBatchId, showToast);
  setButtonLoading(btn, false, "Đang kiểm tra T-28…", "Kiểm tra toàn vẹn chuỗi (T-28)");
}

/** Giả lập hành vi sửa lén SQL để kiểm tra cảnh báo T-31 */
async function handleSimulateTamper() {
  if (!activeTimelineBatchId) return;
  const btn = $("btn-simulate-tamper");
  setButtonLoading(btn, true, "Đang sửa lén SQL…", "Giả lập sửa lén SQL");

  try {
    const report = await apiRequest(`/batches/${activeTimelineBatchId}/simulate-tamper`, {
      method: "POST",
    });
    activeIntegrityReport = report;

    // Tải lại sự kiện đã bị sửa lén
    const events = await apiRequest(`/batches/${activeTimelineBatchId}/events`);
    activeTimelineEvents = Array.isArray(events) ? events : [];

    renderIntegrityBanner(report);
    renderVerticalTimeline(activeTimelineEvents, report);

    toast("⚠️ Đã giả lập can thiệp SQL! Banner cảnh báo đỏ đã xuất hiện ở đầu trang.", "error");

    // Cuộn lên đầu dòng thời gian để nhìn banner ngay lập tức
    $("timeline-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast(`Không thể giả lập sửa lén: ${error.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang sửa lén SQL…", "Giả lập sửa lén SQL");
  }
}

/** Khôi phục lại 10 sự kiện chuẩn toàn vẹn */
async function handleResetChain() {
  if (!activeTimelineBatchId) return;
  const btn = $("btn-reset-chain");
  setButtonLoading(btn, true, "Đang khôi phục…", "Khôi phục chuỗi chuẩn");

  try {
    const report = await apiRequest(`/batches/${activeTimelineBatchId}/reset-events`, {
      method: "POST",
    });
    activeIntegrityReport = report;

    // Tải lại sự kiện chuẩn
    const events = await apiRequest(`/batches/${activeTimelineBatchId}/events`);
    activeTimelineEvents = Array.isArray(events) ? events : [];

    renderIntegrityBanner(report);
    renderVerticalTimeline(activeTimelineEvents, report);

    toast("✅ Đã khôi phục 10 sự kiện chuẩn! Chuỗi băm toàn vẹn 100%.", "success");
  } catch (error) {
    toast(`Khôi phục chuỗi thất bại: ${error.message}`, "error");
  } finally {
    setButtonLoading(btn, false, "Đang khôi phục…", "Khôi phục chuỗi chuẩn");
  }
}

/** Ẩn/hiện form thêm sự kiện mới */
function toggleAddEventForm() {
  const form = $("add-event-form");
  if (!form) return;
  form.hidden = !form.hidden;
  if (!form.hidden) {
    $("event-type-input").focus();
  }
}

/** Xử lý submit thêm sự kiện mới */
async function handleAddEventSubmit(event) {
  event.preventDefault();
  if (!activeTimelineBatchId) return;

  const form = event.currentTarget;
  const button = $("btn-submit-event");

  if (form.dataset.submitting === "true") return;
  form.dataset.submitting = "true";

  clearFormErrors(form);

  const payload = {
    event_type: $("event-type-input").value.trim(),
    organization: $("event-org-input").value.trim(),
    data: $("event-data-input").value.trim(),
  };

  setButtonLoading(button, true, "Đang ghi nhận…", "Lưu sự kiện vào chuỗi");

  try {
    const newEvent = await apiRequest(`/batches/${activeTimelineBatchId}/events`, {
      method: "POST",
      body: payload,
    });
    toast(`Đã ghi nhận sự kiện #${newEvent.sequence} (${newEvent.event_type}) vào chuỗi băm.`, "success");
    form.reset();
    form.hidden = true;
    await loadTimelineData(activeTimelineBatchId, false);
  } catch (error) {
    applyServerErrors(form, error);
    toast(`Không thể thêm sự kiện: ${error.message}`, "error");
  } finally {
    delete form.dataset.submitting;
    setButtonLoading(button, false, "Đang ghi nhận…", "Lưu sự kiện vào chuỗi");
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

  // Kích hoạt cơ chế tự động xoá lỗi tại ô khi người dùng nhập/sửa
  setupFormErrorAutoClear($("login-form"));
  setupFormErrorAutoClear($("farm-form"));
  setupFormErrorAutoClear($("batch-form"));

  // Cột "Thao tác" của 2 bảng dùng event delegation: nội dung bảng được vẽ lại
  // liên tục nên chỉ gắn 1 listener cho mỗi <tbody> thay vì gắn cho từng nút.
  $("farm-table-body").addEventListener("click", handleTableAction);
  $("batch-table-body").addEventListener("click", handleTableAction);

  // Sự kiện cho Dòng thời gian sự kiện & Kiểm tra toàn vẹn T-28 (T-31)
  if ($("btn-timeline-close")) {
    $("btn-timeline-close").addEventListener("click", closeBatchTimeline);
  }
  if ($("btn-verify-chain")) {
    $("btn-verify-chain").addEventListener("click", () => verifyActiveBatchChain(true));
  }
  if ($("btn-simulate-tamper")) {
    $("btn-simulate-tamper").addEventListener("click", handleSimulateTamper);
  }
  if ($("btn-reset-chain")) {
    $("btn-reset-chain").addEventListener("click", handleResetChain);
  }
  if ($("btn-toggle-add-event")) {
    $("btn-toggle-add-event").addEventListener("click", toggleAddEventForm);
  }
  if ($("btn-cancel-add-event")) {
    $("btn-cancel-add-event").addEventListener("click", toggleAddEventForm);
  }
  if ($("add-event-form")) {
    $("add-event-form").addEventListener("submit", handleAddEventSubmit);
    setupFormErrorAutoClear($("add-event-form"));
  }

  // Sự kiện cho Modal Tách Lô Nông Sản (T-40 / T-41 / SCRUM-57)
  if ($("modal-split-close")) {
    $("modal-split-close").addEventListener("click", closeSplitModal);
  }
  if ($("split-cancel")) {
    $("split-cancel").addEventListener("click", closeSplitModal);
  }
  if ($("split-child-quantities")) {
    $("split-child-quantities").addEventListener("input", handleSplitInput);
  }
  if ($("split-form")) {
    $("split-form").addEventListener("submit", handleSplitSubmit);
    setupFormErrorAutoClear($("split-form"));
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

/* ------------------------------------------- Xử lý Modal Tách Lô (T-41) --- */
let currentSplitBatchId = null;

function openSplitModal(batchId) {
  const batch = batches.find((b) => b.id === batchId);
  if (!batch) {
    toast(`Không tìm thấy lô #${batchId}`, "error");
    return;
  }
  currentSplitBatchId = batchId;
  const dateStr = batch.harvest_date ? String(batch.harvest_date).replace(/-/g, "") : "20261006";
  const defCode = `LOT-${String(batch.farm_id).padStart(2, "0")}-${dateStr}-${String(batch.id).padStart(2, "0")}`;
  $("split-parent-code").textContent = batch.batch_code || defCode;
  $("split-parent-product").textContent = batch.product_name;
  $("split-parent-qty").textContent = `${formatNumber(batch.quantity)} kg`;

  const form = $("split-form");
  clearFormErrors(form);
  form.reset();
  $("split-calc-summary").hidden = true;
  $("modal-split").hidden = false;
  $("split-child-quantities").focus();
}

function closeSplitModal() {
  if ($("modal-split")) {
    $("modal-split").hidden = true;
  }
  currentSplitBatchId = null;
}

function handleSplitInput() {
  const batch = batches.find((b) => b.id === currentSplitBatchId);
  if (!batch) return;

  const raw = $("split-child-quantities").value.trim();
  const summaryBox = $("split-calc-summary");
  if (!raw) {
    summaryBox.hidden = true;
    return;
  }

  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  let total = 0;
  let hasInvalid = false;

  for (const p of parts) {
    const val = Number(p);
    if (isNaN(val) || val <= 0) {
      hasInvalid = true;
      break;
    }
    total += val;
  }

  if (hasInvalid || parts.length === 0) {
    summaryBox.hidden = true;
    return;
  }

  summaryBox.hidden = false;
  $("split-calc-total").textContent = `${formatNumber(total)} kg`;
  const remaining = Number(batch.quantity) - total;
  const remEl = $("split-calc-remaining");
  remEl.textContent = `${formatNumber(remaining)} kg`;

  if (remaining < 0) {
    remEl.style.color = "#dc2626";
    showFieldError($("split-child-quantities"), `Tổng khối lượng tách (${formatNumber(total)} kg) vượt quá khối lượng khả dụng (${formatNumber(batch.quantity)} kg).`);
  } else {
    remEl.style.color = "#166534";
    clearFieldError($("split-child-quantities"));
  }
}

async function handleSplitSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.dataset.submitting === "true") return;

  const batch = batches.find((b) => b.id === currentSplitBatchId);
  if (!batch) return;

  clearFormErrors(form);
  const raw = $("split-child-quantities").value.trim();
  if (!raw) {
    showFieldError($("split-child-quantities"), "Vui lòng nhập khối lượng các lô con cần tách.");
    return;
  }

  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  const quantities = [];
  for (let i = 0; i < parts.length; i++) {
    const val = Number(parts[i]);
    if (isNaN(val) || val <= 0) {
      showFieldError($("split-child-quantities"), `Khối lượng lô con thứ ${i + 1} không hợp lệ (> 0).`);
      return;
    }
    quantities.push(val);
  }

  const totalSplit = quantities.reduce((a, b) => a + b, 0);
  if (totalSplit > Number(batch.quantity)) {
    showFieldError($("split-child-quantities"), `Tổng khối lượng tách (${formatNumber(totalSplit)} kg) vượt quá khối lượng khả dụng (${formatNumber(batch.quantity)} kg). Thao tác bị từ chối ngay lập tức.`);
    return;
  }

  form.dataset.submitting = "true";
  const button = $("split-submit");
  setButtonLoading(button, true, "Đang xử lý…", "Xác nhận tách lô");

  try {
    const payload = {
      child_quantities: quantities,
      note: $("split-note").value.trim() || undefined,
    };
    const result = await apiRequest(`/batches/${currentSplitBatchId}/split`, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    toast(result.message || "Tách lô nông sản thành công!", "success");
    closeSplitModal();
    await loadBatches();
  } catch (error) {
    handleApiErrors(error, form);
  } finally {
    form.dataset.submitting = "false";
    setButtonLoading(button, false, "Đang xử lý…", "Xác nhận tách lô");
  }
}

/**
 * Xử lý click ở cột "Thao tác" của cả 2 bảng (nút Sửa / Xoá / Dòng thời gian / Tách lô).
 *
 * Đọc dữ liệu từ chính nút được bấm: `data-action` (edit|delete|timeline|split),
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
    openBatchTimeline(id);
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
