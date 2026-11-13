const STATUS_OPTIONS = [
  ["submitted", "Submitted"],
  ["under_review", "Under review"],
  ["in_progress", "In progress"],
  ["resolved", "Resolved"],
  ["closed", "Closed"],
  ["rejected", "Rejected"],
];

const elements = {
  hero: document.querySelector("#hero"),
  workspace: document.querySelector("#workspace"),
  dialog: document.querySelector("#auth-dialog"),
  notice: document.querySelector("#notice"),
  reportPanel: document.querySelector("#report-panel"),
  departmentPanel: document.querySelector("#department-panel"),
  reports: document.querySelector("#reports-list"),
  categorySelect: document.querySelector("#category-select"),
  notificationsButton: document.querySelector("#notifications-button"),
  notificationsPanel: document.querySelector("#notifications-panel"),
  notificationsList: document.querySelector("#notifications-list"),
};
let currentUser = null;
let departments = [];
let categories = [];
let maps = {};
let reportLocationMarker = null;

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = body?.detail;
    const fieldDetail = Array.isArray(detail)
      ? detail.find((item) => ["description", "latitude", "longitude", "location_address"].includes(item.loc?.at(-1)))
      : detail;
    const field = fieldDetail?.field
      || (["latitude", "longitude", "location_address"].includes(fieldDetail?.loc?.at(-1)) ? "location" : fieldDetail?.loc?.at(-1));
    const message = fieldDetail?.message
      || fieldDetail?.msg
      || (typeof detail === "string" ? detail : `Request failed (${response.status}).`);
    const error = new Error(message);
    error.field = field;
    throw error;
  }
  return body;
}

function showNotice(message, isError = false) {
  elements.notice.textContent = message;
  elements.notice.classList.toggle("error", isError);
  elements.notice.hidden = false;
  elements.notice.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function reportFieldError(field, message) {
  const errorElement = document.querySelector(`#${field}-error`);
  const target = document.querySelector(field === "description" ? "#issue-description" : "#issue-address");
  errorElement.textContent = message;
  errorElement.hidden = false;
  target.setAttribute("aria-invalid", "true");
}

function clearReportFieldError(field) {
  const errorElement = document.querySelector(`#${field}-error`);
  const target = document.querySelector(field === "description" ? "#issue-description" : "#issue-address");
  errorElement.textContent = "";
  errorElement.hidden = true;
  target.removeAttribute("aria-invalid");
}

function reportLocationError(address, latitude, longitude) {
  if ((latitude === "") !== (longitude === "")) {
    return "Provide both latitude and longitude, or leave both blank.";
  }
  if (latitude !== "" && longitude !== "") {
    const lat = Number(latitude);
    const lon = Number(longitude);
    if (!Number.isFinite(lat) || lat < -90 || lat > 90
      || !Number.isFinite(lon) || lon < -180 || lon > 180) {
      return "The selected coordinates are invalid.";
    }
    return null;
  }
  return address.trim()
    ? null
    : "Please provide an address or select a location on the map.";
}

function clearLocationErrorIfValid() {
  const address = document.querySelector("#issue-address").value;
  const latitude = document.querySelector("#issue-latitude").value;
  const longitude = document.querySelector("#issue-longitude").value;
  if (!reportLocationError(address, latitude, longitude)) {
    clearReportFieldError("location");
  }
}

function showAuthError(message) {
  const error = document.querySelector("#auth-error");
  error.textContent = message;
  error.hidden = false;
}

function openAuth(mode) {
  document.querySelector("#auth-error").hidden = true;
  setAuthMode(mode);
  elements.dialog.showModal();
}

function setAuthMode(mode) {
  document.querySelector("#auth-error").hidden = true;
  const registering = mode === "register";
  document.querySelector("#login-form").hidden = registering;
  document.querySelector("#register-form").hidden = !registering;
  document.querySelector("#auth-title").textContent = registering ? "Create your account" : "Sign in";
  document.querySelector("#show-login").classList.toggle("active", !registering);
  document.querySelector("#show-register").classList.toggle("active", registering);
}

function showWorkspace(user) {
  currentUser = user;
  const admin = user.role === "admin";
  document.body.classList.toggle("admin-mode", admin);
  document.body.classList.toggle("citizen-mode", !admin);
  elements.workspace.hidden = false;
  elements.hero.hidden = true;
  document.querySelector("#header-login").hidden = true;
  document.querySelector("#header-register").hidden = true;
  document.querySelector("#citizen-how-link").hidden = admin;
  document.querySelector("#admin-navigation").hidden = !admin;
  document.querySelector("#admin-dashboard-header").hidden = !admin;
  document.querySelector("#workspace-heading").hidden = admin;
  document.querySelector("#how-it-works").hidden = admin;
  document.querySelector("#account-name").textContent = admin
    ? `${user.full_name.trim().split(/\s+/)[0]} · Admin`
    : user.full_name;
  document.querySelector("#account-name").hidden = false;
  document.querySelector("#logout-button").hidden = false;
  elements.notificationsButton.hidden = false;
  updateAdminNavigation();
  document.querySelector("#workspace-title").textContent = `Hello, ${user.full_name.split(" ")[0]}`;
  document.querySelector("#workspace-description").textContent = "Create a report and keep track of its progress.";
  document.querySelector("#reports-heading").textContent = admin ? "Recent complaints" : "Your complaints";
  document.querySelector("#reports-intro").textContent = admin
    ? "Review new complaints and update their status, category, priority, or department."
    : "Updates appear here when the status changes.";
  elements.reportPanel.hidden = admin;
  elements.departmentPanel.hidden = !admin;
  document.querySelector("#admin-overview").hidden = !admin;
  document.querySelector("#user-management-panel").hidden = !admin;
  document.querySelector("#analytics-panel").hidden = !admin;
  document.querySelector("#sla-panel").hidden = !admin;
  document.querySelector("#admin-notifications-module").hidden = !admin;
  document.querySelector("#department-map-panel").hidden = !admin;
  document.querySelector("#issues-map-panel").hidden = false;
  document.querySelector(".closing-cta").hidden = true;
}

function showLanding() {
  currentUser = null;
  document.body.classList.remove("admin-mode", "citizen-mode");
  elements.workspace.hidden = true;
  elements.hero.hidden = false;
  document.querySelector("#header-login").hidden = false;
  document.querySelector("#header-register").hidden = false;
  document.querySelector("#citizen-how-link").hidden = false;
  document.querySelector("#admin-navigation").hidden = true;
  updateAdminNavigation();
  document.querySelector("#admin-dashboard-header").hidden = true;
  document.querySelector("#workspace-heading").hidden = false;
  document.querySelector("#account-name").hidden = true;
  document.querySelector("#logout-button").hidden = true;
  elements.notificationsButton.hidden = true;
  elements.notificationsPanel.hidden = true;
  document.querySelector("#how-it-works").hidden = false;
  document.querySelector(".closing-cta").hidden = false;
}

function updateAdminNavigation() {
  const navigation = document.querySelector("#admin-navigation");
  const activeHash = window.location.hash || "#admin-overview";
  const links = navigation.querySelectorAll("a");
  links.forEach((link) => {
    const active = document.body.classList.contains("admin-mode") && link.hash === activeHash;
    link.classList.toggle("is-active", active);
    if (active) {
      link.setAttribute("aria-current", "location");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

window.addEventListener("hashchange", updateAdminNavigation);

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function optionsMarkup(options, selected = "") {
  return options.map(([value, label]) => (
    `<option value="${escapeHtml(value)}" ${String(value) === String(selected) ? "selected" : ""}>${escapeHtml(label)}</option>`
  )).join("");
}

async function loadCategories() {
  categories = await api("/api/categories");
  elements.categorySelect.innerHTML = `<option value="">Choose a category</option>${categories.map((category) =>
    `<option value="${category.id}">${escapeHtml(category.name)}</option>`
  ).join("")}`;
  document.querySelector("#sla-category").innerHTML = categories.map((category) =>
    `<option value="${category.id}">${escapeHtml(category.name)}</option>`
  ).join("");
  document.querySelector("#department-category-options").innerHTML = categories.map((category) =>
    `<label class="category-check"><input type="checkbox" name="category_ids" value="${category.id}">${escapeHtml(category.name)}</label>`
  ).join("");
}

async function loadDepartments() {
  departments = await api("/api/departments");
  renderDepartmentList();
  renderDepartmentMap();
}

function renderDepartmentList() {
  const target = document.querySelector("#department-list");
  if (departments.length === 0) {
    target.innerHTML = `<p class="empty-state">No departments have been added yet.</p>`;
    return;
  }
  target.innerHTML = departments.map((department) => `
    <form class="department-edit" data-id="${department.id}">
      <label>Department name<input name="name" type="text" minlength="2" maxlength="120" value="${escapeHtml(department.name)}" required></label>
      <button class="button button-outline" type="submit">Save name</button>
    </form>`).join("");
  target.querySelectorAll(".department-edit").forEach((form) => {
    form.addEventListener("submit", renameDepartment);
  });
}

async function loadAdminUsers() {
  if (!currentUser || currentUser.role !== "admin") return;
  const users = await api("/api/admin/users");
  const target = document.querySelector("#admin-users-list");
  if (!users.length) {
    target.innerHTML = `<p class="empty-state">No users have registered yet.</p>`;
    return;
  }
  target.innerHTML = `
    <div class="admin-user-table-wrap"><table class="admin-user-table">
      <thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Account</th><th scope="col">Joined</th></tr></thead>
      <tbody>${users.map((user) => `<tr>
        <td>${escapeHtml(user.full_name)}</td><td>${escapeHtml(user.email)}</td>
        <td>${escapeHtml(user.role)}</td><td>${user.active ? "Active" : "Inactive"}</td>
        <td>${escapeHtml(CivicTrackDateTime.formatDateTime(user.created_at))}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
}

async function renameDepartment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const name = new FormData(form).get("name").trim();
  if (name.length < 2) {
    showNotice("Department names must contain at least 2 characters.", true);
    form.elements.name.focus();
    return;
  }
  try {
    await api(`/api/departments/${form.dataset.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await loadDepartments();
    await Promise.all([loadReports(), loadAnalytics()]);
    showNotice("Department name updated. Existing assignments are unchanged.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function loadNotifications() {
  if (!currentUser) return;
  const data = await api("/api/notifications");
  const count = document.querySelector("#notification-count");
  count.textContent = data.unread_count;
  count.hidden = data.unread_count === 0;
  const markup = data.items.length
    ? data.items.map((item) => `
      <article class="notification-item ${item.read ? "" : "unread"}">
        <p>${escapeHtml(item.message)}</p>
        <div><time>${escapeHtml(CivicTrackDateTime.formatDateTime(item.created_at))}</time>
        ${item.read ? "" : `<button class="mark-read" type="button" data-id="${item.id}">Mark read</button>`}</div>
      </article>`).join("")
    : `<p class="empty-state">No notifications yet.</p>`;
  const lists = [elements.notificationsList, document.querySelector("#admin-notifications-list")];
  for (const list of lists) {
    list.innerHTML = markup;
    list.querySelectorAll(".mark-read").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/notifications/${button.dataset.id}/read`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ read: true }),
          });
          await loadNotifications();
        } catch (error) {
          showNotice(error.message, true);
        }
      });
    });
  }
}

function renderChart(elementId, rows) {
  const target = document.querySelector(elementId);
  if (!rows.length) {
    target.innerHTML = `<p class="empty-state">No data available yet.</p>`;
    return;
  }
  const maximum = Math.max(1, ...rows.map((row) => row.count));
  target.innerHTML = rows.map((row) => `
    <div class="chart-row">
      <span class="chart-label" title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</span>
      <span class="chart-track"><span class="chart-bar-fill" style="width:${(row.count / maximum) * 100}%"></span></span>
      <strong>${row.count}</strong>
    </div>`).join("");
}

async function loadAnalytics() {
  if (!currentUser || currentUser.role !== "admin") return;
  const data = await api("/api/admin/analytics");
  document.querySelector("#stat-total").textContent = data.total;
  document.querySelector("#stat-pending").textContent = data.pending;
  document.querySelector("#stat-resolved").textContent = data.resolved;
  document.querySelector("#stat-rejected").textContent = data.rejected;
  document.querySelector("#stat-overdue").textContent = data.overdue;
  renderChart("#category-chart", data.by_category);
  renderChart("#department-chart", data.by_department);
}

async function loadSlaPolicies() {
  if (!currentUser || currentUser.role !== "admin") return;
  const policies = await api("/api/admin/sla-policies");
  const list = document.querySelector("#sla-policy-list");
  list.innerHTML = policies.length ? policies.map((policy) => `
    <div class="sla-policy-row"><strong>${escapeHtml(policy.category)} · priority ${policy.severity_min}+</strong>
      <span>Respond in ${policy.response_minutes} min · resolve in ${policy.resolution_minutes} min</span></div>`).join("")
    : `<p class="empty-state">No policies are configured. Run the database setup command or add a policy above.</p>`;
}

function ensureMap(elementId, center = [20.5937, 78.9629], zoom = 5) {
  if (typeof L === "undefined") {
    const mapElement = document.getElementById(elementId);
    if (mapElement) mapElement.innerHTML = `<p class="map-fallback">Map library could not be loaded. Coordinate fields remain available.</p>`;
    return null;
  }
  if (!maps[elementId]) {
    const map = L.map(elementId).setView(center, zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }).addTo(map);
    maps[elementId] = { map, markers: L.layerGroup().addTo(map) };
  }
  requestAnimationFrame(() => maps[elementId].map.invalidateSize());
  return maps[elementId];
}

function setReportLocation(latitude, longitude, pan = true) {
  const lat = Number(latitude);
  const lon = Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    showNotice("The selected coordinates are invalid.", true);
    return;
  }
  document.querySelector("#issue-latitude").value = lat.toFixed(6);
  document.querySelector("#issue-longitude").value = lon.toFixed(6);
  clearLocationErrorIfValid();
  document.querySelector("#location-coordinates").textContent = `Selected: ${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  const mapState = ensureMap("report-location-map", [lat, lon], 13);
  if (!mapState) return;
  if (reportLocationMarker) reportLocationMarker.remove();
  reportLocationMarker = L.marker([lat, lon]).addTo(mapState.map).bindPopup("Complaint location");
  if (pan) mapState.map.setView([lat, lon], Math.max(mapState.map.getZoom(), 13));
}

function initializeReportMap() {
  const mapState = ensureMap("report-location-map");
  if (!mapState || mapState.map._civicTrackClickHandler) return;
  mapState.map.on("click", (event) => {
    setReportLocation(event.latlng.lat, event.latlng.lng, false);
  });
  mapState.map._civicTrackClickHandler = true;
}

function renderIssueMap(issues) {
  const state = ensureMap("issues-map");
  if (!state) return;
  state.markers.clearLayers();
  const points = [];
  for (const issue of issues) {
    if (issue.latitude === null || issue.longitude === null) continue;
    points.push([issue.latitude, issue.longitude]);
    L.marker([issue.latitude, issue.longitude])
      .bindPopup(`<strong>${escapeHtml(issue.complaint_code)} · ${escapeHtml(issue.title)}</strong><br>${escapeHtml(issue.status_label)}${issue.location_address ? `<br>${escapeHtml(issue.location_address)}` : ""}<br>${issue.latitude}, ${issue.longitude}`)
      .addTo(state.markers);
  }
  if (currentUser?.role === "admin") {
    for (const department of departments) {
      if (department.latitude === null || department.longitude === null) continue;
      points.push([department.latitude, department.longitude]);
      L.circleMarker([department.latitude, department.longitude], { color: "#b86f54" })
        .bindPopup(`<strong>${escapeHtml(department.name)}</strong><br>Department`)
        .addTo(state.markers);
    }
  }
  if (points.length) state.map.fitBounds(points, { padding: [24, 24], maxZoom: 13 });
}

function renderDepartmentMap() {
  if (!currentUser || currentUser.role !== "admin") return;
  const state = ensureMap("department-map");
  if (!state) return;
  state.markers.clearLayers();
  const points = [];
  for (const department of departments) {
    if (department.latitude === null || department.longitude === null) continue;
    points.push([department.latitude, department.longitude]);
    L.marker([department.latitude, department.longitude])
      .bindPopup(`<strong>${escapeHtml(department.name)}</strong>`)
      .addTo(state.markers);
  }
  if (points.length) state.map.fitBounds(points, { padding: [24, 24], maxZoom: 13 });
  renderIssueMapCache();
}

let latestIssues = [];

function renderIssueMapCache() {
  if (currentUser) renderIssueMap(latestIssues);
}

async function loadReports() {
  const reports = await api("/api/issues");
  latestIssues = reports;
  if (reports.length === 0) {
    elements.reports.innerHTML = `<p class="empty-state">${currentUser.role === "admin" ? "No complaints have been submitted yet." : "You have not submitted a complaint yet."}</p>`;
    renderIssueMap(reports);
    return;
  }
  elements.reports.innerHTML = reports.map((issue) => {
    const adminControls = currentUser.role === "admin" ? `
      <form class="admin-controls" data-code="${escapeHtml(issue.complaint_code)}">
        <select name="status" aria-label="Complaint status">${optionsMarkup(STATUS_OPTIONS, issue.status)}</select>
        <select name="department_id" aria-label="Assigned department">
          <option value="">Unassigned</option>
          ${departments.map((department) => `<option value="${department.id}" ${department.id === issue.department_id ? "selected" : ""}>${escapeHtml(department.name)}</option>`).join("")}
        </select>
        <select name="category_id" aria-label="Verified complaint category" data-original="${issue.category_id}">
          ${categories.map((category) => `<option value="${category.id}" ${category.id === issue.category_id ? "selected" : ""}>${escapeHtml(category.name)}</option>`).join("")}
        </select>
        <select name="severity" aria-label="Complaint priority" data-original="${issue.severity}">
          ${[1, 2, 3, 4, 5].map((priority) => `<option value="${priority}" ${priority === issue.severity ? "selected" : ""}>Priority ${priority}</option>`).join("")}
        </select>
        <button class="button button-primary" type="submit">Save updates</button>
      </form>` : "";
    const overdueLabel = issue.overdue
      ? `<span class="overdue-badge">${issue.response_overdue ? "Response overdue" : "Resolution overdue"} · escalate</span>`
      : "";
    return `<article class="report-card">
      <div class="report-card-top"><div><h4>${escapeHtml(issue.title)}</h4><span class="report-code">${escapeHtml(issue.complaint_code)} · ${escapeHtml(issue.category)}</span></div>
      <span class="status-badge status-${escapeHtml(issue.status)}">${escapeHtml(issue.status_label)}</span></div>
      <p class="report-description">${escapeHtml(issue.description)}</p>
      <div class="report-meta"><span>Reported by ${escapeHtml(issue.reporter_name)}</span><span>${currentUser.role === "admin" ? "Submitted " : ""}${escapeHtml(CivicTrackDateTime.formatDateTime(issue.created_at))}</span>
      ${issue.location_address ? `<span>Location: ${escapeHtml(issue.location_address)}</span>` : ""}
      ${issue.latitude !== null && issue.longitude !== null ? `<span>Coordinates: ${escapeHtml(issue.latitude)}, ${escapeHtml(issue.longitude)}</span>` : ""}
      <span>${issue.department_name ? `Assigned to ${escapeHtml(issue.department_name)}` : "Not assigned"}</span>
      <span>Priority ${escapeHtml(issue.severity)}/5</span>
      ${issue.sla_response_due_at ? `<span>Respond by ${escapeHtml(CivicTrackDateTime.formatDateTime(issue.sla_response_due_at))}</span>` : ""}
      ${issue.sla_resolution_due_at ? `<span>Resolve by ${escapeHtml(CivicTrackDateTime.formatDateTime(issue.sla_resolution_due_at))}</span>` : ""}
      ${overdueLabel}</div>
      ${issue.image_url ? `<img class="report-photo" src="${escapeHtml(issue.image_url)}" alt="Photo for ${escapeHtml(issue.title)}" loading="lazy">` : ""}
      ${adminControls}</article>`;
  }).join("");
  elements.reports.querySelectorAll(".admin-controls").forEach((form) => {
    form.addEventListener("submit", updateComplaint);
  });
  renderIssueMap(reports);
}

async function updateComplaint(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const update = { status: formData.get("status") };
  const departmentId = formData.get("department_id");
  update.assigned_department_id = departmentId ? Number(departmentId) : null;
  const categorySelect = form.querySelector('[name="category_id"]');
  if (categorySelect.value !== categorySelect.dataset.original) {
    update.category_id = Number(categorySelect.value);
  }
  const severitySelect = form.querySelector('[name="severity"]');
  if (severitySelect.value !== severitySelect.dataset.original) {
    update.severity = Number(severitySelect.value);
  }
  try {
    await api(`/api/issues/${encodeURIComponent(form.dataset.code)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    });
    showNotice("Complaint updated.");
    await loadReports();
    await loadNotifications();
    await loadAnalytics();
  } catch (error) {
    showNotice(error.message, true);
  }
}

document.querySelector("#notifications-button").addEventListener("click", async () => {
  const isOpening = elements.notificationsPanel.hidden;
  elements.notificationsPanel.hidden = !isOpening;
  elements.notificationsButton.setAttribute("aria-expanded", String(isOpening));
  if (isOpening) {
    try {
      await loadNotifications();
    } catch (error) {
      showNotice(error.message, true);
    }
  }
});

document.querySelectorAll("[data-open-notifications]").forEach((button) => {
  button.addEventListener("click", () => document.querySelector("#notifications-button").click());
});

document.querySelector("#close-notifications").addEventListener("click", () => {
  elements.notificationsPanel.hidden = true;
  elements.notificationsButton.setAttribute("aria-expanded", "false");
});

document.querySelector("#suggest-category").addEventListener("click", async () => {
  const data = new FormData(document.querySelector("#report-form"));
  try {
    const result = await api("/api/category-suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: data.get("title"),
        description: data.get("description"),
      }),
    });
    const target = document.querySelector("#category-suggestions");
    if (result.suggestions.length === 0) {
      target.innerHTML = `<p class="field-hint">No category matched these keywords. Please choose manually.</p>`;
      return;
    }
    target.innerHTML = result.suggestions.map((suggestion) => `
      <button class="suggestion-choice" type="button" data-category="${suggestion.category_id}">
        <strong>${escapeHtml(suggestion.category)}</strong>
        <span>Matched: ${escapeHtml(suggestion.matched_terms.join(", "))}</span>
      </button>`).join("");
    target.querySelectorAll(".suggestion-choice").forEach((button) => {
      button.addEventListener("click", () => {
        elements.categorySelect.value = button.dataset.category;
        showNotice("Suggested category selected. Please review it before submitting.");
      });
    });
  } catch (error) {
    showNotice(error.message, true);
  }
});

document.querySelector("#use-gps").addEventListener("click", () => {
  if (!navigator.geolocation) {
    showNotice("This browser does not support location access. Select a point on the map instead.", true);
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (position) => setReportLocation(position.coords.latitude, position.coords.longitude),
    (error) => showNotice(`Could not get your location: ${error.message}`, true),
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 },
  );
});

document.querySelector("#clear-location").addEventListener("click", () => {
  document.querySelector("#issue-latitude").value = "";
  document.querySelector("#issue-longitude").value = "";
  document.querySelector("#location-coordinates").textContent = "No location selected.";
  if (reportLocationMarker) {
    reportLocationMarker.remove();
    reportLocationMarker = null;
  }
});

document.querySelector("#refresh-analytics").addEventListener("click", async () => {
  try {
    await loadAnalytics();
    showNotice("Analytics refreshed from stored complaint data.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

document.querySelector("#sla-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  for (const key of ["category_id", "severity_min", "response_minutes", "resolution_minutes"]) {
    values[key] = Number(values[key]);
  }
  try {
    await api("/api/admin/sla-policies", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    await loadSlaPolicies();
    showNotice("SLA policy saved. It will apply to new reports and future priority/category updates.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

function goToReport() {
  if (!currentUser) {
    openAuth("register");
    return;
  }
  if (currentUser.role === "admin") {
    showNotice("Administrator accounts manage complaints; citizen accounts can submit reports.", true);
    return;
  }
  document.querySelector("#report-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

document.querySelector("#header-login").addEventListener("click", () => openAuth("login"));
document.querySelector("#header-register").addEventListener("click", () => openAuth("register"));
document.querySelector("#hero-report").addEventListener("click", goToReport);
document.querySelector("#closing-report").addEventListener("click", goToReport);
document.querySelector("#close-dialog").addEventListener("click", () => elements.dialog.close());
document.querySelector("#show-login").addEventListener("click", () => setAuthMode("login"));
document.querySelector("#show-register").addEventListener("click", () => setAuthMode("register"));

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  let user;
  try {
    user = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch (error) {
    showAuthError(error.message);
    return;
  }
  elements.dialog.close();
  showWorkspace(user);
  try {
    if (user.role !== "admin") initializeReportMap();
    await loadCategories();
    if (user.role === "admin") await loadDepartments();
    await loadReports();
    await loadNotifications();
    if (user.role === "admin") {
      await Promise.all([
        loadAdminUsers(),
        loadAnalytics(),
        loadSlaPolicies(),
      ]);
    }
  } catch (error) {
    showNotice(error.message, true);
  }
});

document.querySelector("#register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  let user;
  try {
    user = await api("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch (error) {
    showAuthError(error.message);
    return;
  }
  elements.dialog.close();
  showWorkspace(user);
  try {
    initializeReportMap();
    await loadCategories();
    await loadReports();
    await loadNotifications();
    showNotice("Your account is ready. Welcome to CivicTrack!");
  } catch (error) {
    showNotice(error.message, true);
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
    showLanding();
  } catch (error) {
    showNotice(error.message, true);
  }
});

const reportForm = document.querySelector("#report-form");
const descriptionField = document.querySelector("#issue-description");
const addressField = document.querySelector("#issue-address");

descriptionField.addEventListener("input", () => {
  if (descriptionField.value.trim().length >= 10) {
    clearReportFieldError("description");
  }
});

addressField.addEventListener("input", clearLocationErrorIfValid);

reportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = new FormData(form);
  const address = payload.get("location_address").trim();
  const latitudeValue = payload.get("latitude").trim();
  const longitudeValue = payload.get("longitude").trim();
  const description = payload.get("description").trim();
  const locationError = reportLocationError(address, latitudeValue, longitudeValue);
  const descriptionError = !description
    ? "Please enter a complaint description."
    : description.length < 10
      ? "Please provide a little more detail."
      : null;

  clearReportFieldError("description");
  clearReportFieldError("location");
  if (descriptionError) {
    reportFieldError("description", descriptionError);
  }
  if (locationError) {
    reportFieldError("location", locationError);
  }
  if (descriptionError || locationError) {
    const target = descriptionError ? descriptionField : addressField;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
    return;
  }
  if (!form.reportValidity()) {
    return;
  }
  if (!latitudeValue && !longitudeValue) {
    payload.delete("latitude");
    payload.delete("longitude");
  }
  try {
    const issue = await api("/api/issues", { method: "POST", body: payload });
    form.reset();
    document.querySelector("#location-coordinates").textContent = "No location selected.";
    if (reportLocationMarker) {
      reportLocationMarker.remove();
      reportLocationMarker = null;
    }
    document.querySelector("#category-suggestions").replaceChildren();
    showNotice(`Complaint submitted. Your reference ID is ${issue.complaint_code}.`);
    await loadReports();
    await loadNotifications();
    await loadAnalytics();
  } catch (error) {
    if (error.field === "description" || error.field === "location") {
      reportFieldError(error.field, error.message);
      const target = error.field === "description" ? descriptionField : addressField;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.focus({ preventScroll: true });
    } else {
      showNotice(error.message, true);
    }
  }
});

document.querySelector("#department-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const latitude = formData.get("latitude");
  const longitude = formData.get("longitude");
  if (Boolean(latitude) !== Boolean(longitude)) {
    showNotice("Provide both department coordinates, or leave both blank.", true);
    return;
  }
  const data = {
    name: formData.get("name").trim(),
    latitude: latitude ? Number(latitude) : null,
    longitude: longitude ? Number(longitude) : null,
    category_ids: formData.getAll("category_ids").map(Number),
  };
  try {
    await api("/api/departments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    form.reset();
    await loadDepartments();
    await loadReports();
    showNotice("Department added.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

async function start() {
  try {
    await loadCategories();
    const user = await api("/api/auth/me");
    showWorkspace(user);
    if (user.role !== "admin") initializeReportMap();
    if (user.role === "admin") await loadDepartments();
    await loadReports();
    await loadNotifications();
    if (user.role === "admin") {
      await Promise.all([
        loadAdminUsers(),
        loadAnalytics(),
        loadSlaPolicies(),
      ]);
    }
  } catch (error) {
    showLanding();
    if (!error.message.includes("sign in") && !error.message.includes("Session is invalid")) {
      showNotice(error.message, true);
    }
  }
}

start();
