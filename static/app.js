// ==========================================
// GLOBAL APPLICATION STATE
// ==========================================
const state = {
  currentView: 'dashboard',
  currentReportTab: 'daily',
  settings: {
    business_name: 'Crazinos Daily',
    currency: 'INR ₹',
    currency_symbol: '₹',
    timezone: 'Asia/Kolkata',
    stock_tracking_enabled: '1',
    profit_calculation_enabled: '1',
    require_auth: '0'
  },
  kits: [],
  platforms: [],
  dashboardData: null,
  activeReportData: null,
  eodModalData: null,
  currentKitDetails: null
};

// ==========================================
// UTILITY HELPERS
// ==========================================
function getTodayYMD() {
  const now = new Date();
  const yr = now.getFullYear();
  const mo = String(now.getMonth() + 1).padStart(2, '0');
  const da = String(now.getDate()).padStart(2, '0');
  return `${yr}-${mo}-${da}`;
}

function getCurrentLocalDateTimeInput() {
  const now = new Date();
  const yr = now.getFullYear();
  const mo = String(now.getMonth() + 1).padStart(2, '0');
  const da = String(now.getDate()).padStart(2, '0');
  const hr = String(now.getHours()).padStart(2, '0');
  const mi = String(now.getMinutes()).padStart(2, '0');
  return `${yr}-${mo}-${da}T${hr}:${mi}`;
}

function formatCurrency(val) {
  const sym = state.settings.currency_symbol || '₹';
  const num = Number(val || 0);
  if (Number.isInteger(num)) {
    return `${sym}${num.toLocaleString('en-IN')}`;
  }
  return `${sym}${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatNumber(val) {
  const num = Number(val || 0);
  return num.toLocaleString('en-IN');
}

function formatSaleTime(dateStr) {
  if (!dateStr) return '';
  const clean = String(dateStr).replace(' ', 'T');
  const dt = new Date(clean);
  if (isNaN(dt.getTime())) {
    return String(dateStr).slice(11, 16) || String(dateStr);
  }
  return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatSaleDateTimeShort(dateStr) {
  if (!dateStr) return '';
  const clean = String(dateStr).replace(' ', 'T');
  const dt = new Date(clean);
  if (isNaN(dt.getTime())) return String(dateStr).slice(0, 16);
  return dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) +
    ' ' +
    dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showToast(message, isError = false) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast-msg${isError ? ' error' : ''}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 3500);
}

async function apiFetch(url, options = {}) {
  const opts = {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  };
  const res = await fetch(url, opts);
  if (res.status === 401) {
    openModal('authLockModal');
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.error || 'Authentication required');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

// ==========================================
// INITIALIZATION
// ==========================================
document.addEventListener('DOMContentLoaded', async () => {
  // Register Service Worker for PWA
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // Initialize date inputs
  const today = getTodayYMD();
  document.getElementById('dashboardDatePicker').value = today;
  document.getElementById('saleDateTimeInput').value = getCurrentLocalDateTimeInput();
  document.getElementById('repDailyDate').value = today;
  document.getElementById('repWeeklyStart').value = today;
  document.getElementById('repMonthlyInput').value = today.slice(0, 7);
  document.getElementById('repCustomStart').value = today;
  document.getElementById('repCustomEnd').value = today;
  document.getElementById('eodModalDatePicker').value = today;
  document.getElementById('historyQuickDateInput').value = today;

  // Initialize comparison defaults (yesterday vs today)
  const yest = new Date();
  yest.setDate(yest.getDate() - 1);
  const yestYMD = `${yest.getFullYear()}-${String(yest.getMonth() + 1).padStart(2, '0')}-${String(yest.getDate()).padStart(2, '0')}`;
  document.getElementById('cmpP1Start').value = yestYMD;
  document.getElementById('cmpP1End').value = yestYMD;
  document.getElementById('cmpP2Start').value = today;
  document.getElementById('cmpP2End').value = today;

  // Populate Year selector for Annual Report
  const currentYear = new Date().getFullYear();
  const yearSelect = document.getElementById('repAnnualYear');
  yearSelect.innerHTML = '';
  for (let y = currentYear + 1; y >= currentYear - 5; y--) {
    const opt = document.createElement('option');
    opt.value = String(y);
    opt.textContent = String(y);
    if (y === currentYear) opt.selected = true;
    yearSelect.appendChild(opt);
  }

  // Close searchable kit dropdown when clicking outside
  document.addEventListener('click', (e) => {
    const searchInput = document.getElementById('saleKitSearch');
    const resultsBox = document.getElementById('saleKitSearchResults');
    if (searchInput && resultsBox && !searchInput.contains(e.target) && !resultsBox.contains(e.target)) {
      resultsBox.style.display = 'none';
    }
  });

  await checkAuthAndBootstrap();
});

async function checkAuthAndBootstrap() {
  try {
    const auth = await fetch('/api/auth/status').then((r) => r.json());
    if (!auth.authenticated) {
      openModal('authLockModal');
      return;
    }
    await loadBootstrapData();
    await loadDashboard();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadBootstrapData() {
  const data = await apiFetch('/api/bootstrap');
  state.settings = data.settings || state.settings;
  state.platforms = data.platforms || [];
  state.kits = data.kits || [];

  applySettingsToUI();
  populateSaleFormOptions();
  populateReportFilterOptions();
  renderKitsGrid();
  renderSettingsPlatformsTable();
}

function applySettingsToUI() {
  const bName = state.settings.business_name || 'Crazinos Daily';
  document.getElementById('headerBusinessName').textContent = bName;
  document.getElementById('headerDateDisplay').textContent = new Date().toLocaleDateString('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  });

  document.getElementById('setBusinessName').value = bName;
  document.getElementById('setCurrency').value = state.settings.currency || 'INR ₹';
  document.getElementById('setTimezone').value = state.settings.timezone || 'Asia/Kolkata';
  document.getElementById('setStockTracking').checked = state.settings.stock_tracking_enabled === '1';
  document.getElementById('setProfitCalc').checked = state.settings.profit_calculation_enabled === '1';
  document.getElementById('setRequireAuth').checked = state.settings.require_auth === '1';

  const profitRow = document.getElementById('calcProfitRow');
  if (profitRow) {
    profitRow.style.display = state.settings.profit_calculation_enabled === '1' ? 'flex' : 'none';
  }
}

// ==========================================
// NAVIGATION & MODAL CONTROLS
// ==========================================
function switchView(viewName) {
  state.currentView = viewName;
  document.querySelectorAll('.view-section').forEach((sec) => {
    sec.classList.toggle('active', sec.id === `view-${viewName}`);
  });
  document.querySelectorAll('.bottom-nav .nav-item').forEach((btn) => {
    btn.classList.toggle('active', btn.getAttribute('data-view') === viewName);
  });

  window.scrollTo({ top: 0, behavior: 'instant' });

  if (viewName === 'dashboard') {
    loadDashboard();
  } else if (viewName === 'add-sale') {
    document.getElementById('saleDateTimeInput').value = getCurrentLocalDateTimeInput();
    populateSaleFormOptions();
    recalcSaleForm();
    loadRecentSalesList();
  } else if (viewName === 'kits') {
    refreshKitsList();
  } else if (viewName === 'reports') {
    if (state.currentReportTab === 'history') {
      loadReportHistoryAndComparison();
    } else {
      loadActiveReport();
    }
  } else if (viewName === 'settings') {
    applySettingsToUI();
    renderSettingsPlatformsTable();
    loadDatabaseStatus();
  }
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add('open');
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove('open');
}

// ==========================================
// 1. DASHBOARD LOGIC
// ==========================================
function resetDashboardToToday() {
  document.getElementById('dashboardDatePicker').value = getTodayYMD();
  loadDashboard();
}

async function loadDashboard() {
  try {
    const selectedDate = document.getElementById('dashboardDatePicker').value || getTodayYMD();
    const data = await apiFetch(`/api/dashboard?date=${encodeURIComponent(selectedDate)}`);
    state.dashboardData = data;

    const s = data.summary;
    const isToday = selectedDate === getTodayYMD();
    document.getElementById('dashboardDateLabel').textContent = isToday
      ? `Today — ${data.pretty_date}`
      : `Viewing Date — ${data.pretty_date}`;

    document.getElementById('dashKitsSold').textContent = formatNumber(s.total_kits_sold);
    document.getElementById('dashRevenue').textContent = formatCurrency(s.total_revenue);
    document.getElementById('dashOrders').textContent = formatNumber(s.total_orders);

    const profitSub = document.getElementById('dashProfitSubtext');
    if (s.profit_calculation_enabled) {
      profitSub.textContent = `Est. Profit: ${formatCurrency(s.total_profit)}`;
    } else {
      profitSub.textContent = 'Gross sales revenue';
    }

    const stockCard = document.getElementById('dashStockCard');
    if (s.stock_tracking_enabled) {
      stockCard.style.display = 'block';
      document.getElementById('dashRemainingStock').textContent = formatNumber(s.total_remaining_stock);
      document.getElementById('dashStockSubtext').textContent =
        s.low_stock_count > 0
          ? `${s.low_stock_count} kit(s) low on stock`
          : `${s.tracked_kits_count} active kit(s) tracked`;
    } else {
      stockCard.style.display = 'none';
    }

    renderDashboardSales(data.platform_groups || []);
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderDashboardSales(platformGroups) {
  const container = document.getElementById('dashboardSalesContainer');
  const pill = document.getElementById('dashPlatformCountPill');
  pill.textContent = `${platformGroups.length} Platform${platformGroups.length === 1 ? '' : 's'}`;

  if (!platformGroups.length) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-title">No sales recorded for this date</div>
        <p>Tap <strong>+ Add Sale</strong> to record a kit sale in seconds.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = platformGroups
    .map(
      (grp) => `
      <div class="platform-group-card">
        <div class="platform-group-header">
          <span class="platform-badge">${escapeHtml(grp.platform_name)}</span>
          <span style="font-size:0.88rem; color:var(--dark-text);">
            ${formatNumber(grp.total_quantity)} Kits • <strong style="color:var(--primary-red);">${formatCurrency(grp.total_amount)}</strong>
          </span>
        </div>
        <div>
          ${grp.sales
            .map(
              (sale) => `
            <div class="sale-item">
              <div class="sale-main">
                <div class="sale-kit-name">${escapeHtml(sale.kit_name)}</div>
                <div class="sale-meta">
                  <span>Platform: <strong>${escapeHtml(sale.platform_name)}</strong></span>
                  <span>Qty: <strong>${formatNumber(sale.quantity)}</strong> x ${formatCurrency(sale.selling_price_at_sale)}</span>
                  <span>${escapeHtml(formatSaleTime(sale.sale_date))}</span>
                </div>
              </div>
              <div class="sale-right">
                <div class="sale-amount">${formatCurrency(sale.total_amount)}</div>
                <div class="sale-actions">
                  <button type="button" class="chip-btn" onclick="openEditSaleModal(${sale.id})">Edit</button>
                  <button type="button" class="chip-btn" style="color:var(--primary-red);" onclick="confirmDeleteSale(${sale.id})">Delete</button>
                </div>
              </div>
            </div>
          `
            )
            .join('')}
        </div>
      </div>
    `
    )
    .join('');
}

// ==========================================
// 2. ADD SALE & EDIT/DELETE SALE LOGIC
// ==========================================
function populateSaleFormOptions() {
  const kitSelect = document.getElementById('saleKitSelect');
  const prevKitVal = kitSelect.value;
  const activeKits = state.kits.filter((k) => k.status === 'Active');

  kitSelect.innerHTML = '<option value="">-- Choose a Kit --</option>';
  activeKits.forEach((k) => {
    const stockTag =
      state.settings.stock_tracking_enabled === '1' && k.stock_quantity !== null
        ? ` (Stock: ${k.stock_quantity})`
        : '';
    const opt = document.createElement('option');
    opt.value = String(k.id);
    opt.textContent = `${k.kit_name} [${k.sku}] — ${formatCurrency(k.default_selling_price)}${stockTag}`;
    kitSelect.appendChild(opt);
  });

  if (prevKitVal && activeKits.some((k) => String(k.id) === String(prevKitVal))) {
    kitSelect.value = prevKitVal;
  } else if (activeKits.length === 1) {
    kitSelect.value = String(activeKits[0].id);
    onSaleKitSelected();
  }

  const platSelect = document.getElementById('salePlatformSelect');
  const prevPlatVal = platSelect.value;
  const activePlats = state.platforms.filter((p) => p.status === 'Active');

  platSelect.innerHTML = '<option value="">-- Choose Selling Platform --</option>';
  activePlats.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = String(p.id);
    opt.textContent = p.platform_name;
    platSelect.appendChild(opt);
  });

  if (prevPlatVal && activePlats.some((p) => String(p.id) === String(prevPlatVal))) {
    platSelect.value = prevPlatVal;
  } else if (activePlats.length > 0 && !platSelect.value) {
    platSelect.value = String(activePlats[0].id);
  }

  renderPlatformQuickChips();
}

function renderPlatformQuickChips() {
  const wrap = document.getElementById('platformQuickChips');
  const selectedId = document.getElementById('salePlatformSelect').value;
  const activePlats = state.platforms.filter((p) => p.status === 'Active');

  wrap.innerHTML = activePlats
    .map(
      (p) => `
      <button type="button" class="chip-btn ${String(p.id) === String(selectedId) ? 'active' : ''}" onclick="selectQuickPlatform(${p.id})">
        ${escapeHtml(p.platform_name)}
      </button>
    `
    )
    .join('');
}

function selectQuickPlatform(platformId) {
  document.getElementById('salePlatformSelect').value = String(platformId);
  renderPlatformQuickChips();
}

function syncPlatformQuickChips() {
  renderPlatformQuickChips();
}

function showSaleKitDropdown() {
  filterSaleKitOptions();
}

function filterSaleKitOptions() {
  const q = (document.getElementById('saleKitSearch').value || '').trim().toLowerCase();
  const resultsBox = document.getElementById('saleKitSearchResults');
  const activeKits = state.kits.filter((k) => k.status === 'Active');

  const matches = activeKits.filter(
    (k) =>
      k.kit_name.toLowerCase().includes(q) ||
      k.sku.toLowerCase().includes(q) ||
      (k.category || '').toLowerCase().includes(q)
  );

  if (!matches.length) {
    resultsBox.innerHTML = `<div style="padding:10px 14px; font-size:0.86rem; color:var(--muted-text);">No matching active kits found.</div>`;
    resultsBox.style.display = 'block';
    return;
  }

  resultsBox.innerHTML = matches
    .map(
      (k) => `
      <div class="kit-option-item" onclick="pickSearchedKit(${k.id})">
        <div>
          <div style="font-weight:700;">${escapeHtml(k.kit_name)}</div>
          <div style="font-size:0.76rem; color:var(--muted-text);">SKU: ${escapeHtml(k.sku)} • ${escapeHtml(k.category || 'General')}</div>
        </div>
        <div style="text-align:right;">
          <div style="font-weight:800; color:var(--primary-blue);">${formatCurrency(k.default_selling_price)}</div>
          ${
            state.settings.stock_tracking_enabled === '1' && k.stock_quantity !== null
              ? `<div style="font-size:0.74rem; color:var(--muted-text);">Stock: ${k.stock_quantity}</div>`
              : ''
          }
        </div>
      </div>
    `
    )
    .join('');
  resultsBox.style.display = 'block';
}

function pickSearchedKit(kitId) {
  const selectEl = document.getElementById('saleKitSelect');
  selectEl.value = String(kitId);
  const kit = state.kits.find((k) => k.id === Number(kitId));
  if (kit) {
    document.getElementById('saleKitSearch').value = `${kit.kit_name} (${kit.sku})`;
  }
  document.getElementById('saleKitSearchResults').style.display = 'none';
  onSaleKitSelected();
}

function onSaleKitSelected() {
  const kitId = Number(document.getElementById('saleKitSelect').value);
  const kit = state.kits.find((k) => k.id === kitId);
  const hint = document.getElementById('selectedKitStockHint');

  if (kit) {
    document.getElementById('salePriceInput').value = kit.default_selling_price;
    document.getElementById('saleKitSearch').value = `${kit.kit_name} (${kit.sku})`;
    if (state.settings.stock_tracking_enabled === '1' && kit.stock_quantity !== null) {
      hint.textContent = `Available Stock: ${kit.stock_quantity} units • Default Price: ${formatCurrency(kit.default_selling_price)}`;
    } else {
      hint.textContent = `Default Price: ${formatCurrency(kit.default_selling_price)}`;
    }
  } else {
    hint.textContent = '';
  }
  recalcSaleForm();
}

function adjustSaleQty(delta) {
  const input = document.getElementById('saleQuantityInput');
  const current = parseInt(input.value || '1', 10) || 1;
  input.value = Math.max(1, current + delta);
  recalcSaleForm();
}

function setSaleQty(val) {
  document.getElementById('saleQuantityInput').value = val;
  recalcSaleForm();
}

function recalcSaleForm() {
  const kitId = Number(document.getElementById('saleKitSelect').value);
  const kit = state.kits.find((k) => k.id === kitId);
  const qty = Math.max(0, parseInt(document.getElementById('saleQuantityInput').value || '0', 10) || 0);
  const price = Math.max(0, parseFloat(document.getElementById('salePriceInput').value || '0') || 0);

  const totalAmount = qty * price;
  document.getElementById('calcTotalSaleAmount').textContent = formatCurrency(totalAmount);

  const profitEl = document.getElementById('calcEstimatedProfit');
  if (kit && kit.cost_price !== null && kit.cost_price !== undefined) {
    const estProfit = (price - Number(kit.cost_price)) * qty;
    profitEl.textContent = formatCurrency(estProfit);
  } else {
    profitEl.textContent = 'N/A (No cost price set)';
  }
}

async function submitAddSale(event) {
  event.preventDefault();
  const kitId = document.getElementById('saleKitSelect').value;
  const platformId = document.getElementById('salePlatformSelect').value;
  const quantity = document.getElementById('saleQuantityInput').value;
  const sellingPrice = document.getElementById('salePriceInput').value;
  const saleDate = document.getElementById('saleDateTimeInput').value;

  if (!kitId) {
    showToast('Please select a Kit', true);
    return;
  }
  if (!platformId) {
    showToast('Please select a Platform', true);
    return;
  }

  try {
    const res = await apiFetch('/api/sales', {
      method: 'POST',
      body: JSON.stringify({
        kit_id: Number(kitId),
        platform_id: Number(platformId),
        quantity: Number(quantity),
        selling_price: Number(sellingPrice),
        sale_date: saleDate
      })
    });

    showToast(`Sale saved: ${res.sale.quantity} x ${res.sale.kit_name} (${formatCurrency(res.sale.total_amount)})`);

    // Reset quantity to 1 and refresh time for next quick entry
    document.getElementById('saleQuantityInput').value = '1';
    document.getElementById('saleDateTimeInput').value = getCurrentLocalDateTimeInput();

    await loadBootstrapData();
    onSaleKitSelected();
    await loadRecentSalesList();
    await loadDashboard();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadRecentSalesList() {
  try {
    const sales = await apiFetch('/api/sales?limit=20');
    const container = document.getElementById('recentSalesListContainer');
    if (!sales.length) {
      container.innerHTML = `<div class="empty-state">No sales recorded yet.</div>`;
      return;
    }

    container.innerHTML = sales
      .map(
        (sale) => `
        <div class="sale-item">
          <div class="sale-main">
            <div class="sale-kit-name">${escapeHtml(sale.kit_name)}</div>
            <div class="sale-meta">
              <span class="platform-badge">${escapeHtml(sale.platform_name)}</span>
              <span>Qty: <strong>${formatNumber(sale.quantity)}</strong> x ${formatCurrency(sale.selling_price_at_sale)}</span>
              <span>${escapeHtml(formatSaleDateTimeShort(sale.sale_date))}</span>
            </div>
          </div>
          <div class="sale-right">
            <div class="sale-amount">${formatCurrency(sale.total_amount)}</div>
            <div class="sale-actions">
              <button type="button" class="chip-btn" onclick="openEditSaleModal(${sale.id})">Edit</button>
              <button type="button" class="chip-btn" style="color:var(--primary-red);" onclick="confirmDeleteSale(${sale.id})">Delete</button>
            </div>
          </div>
        </div>
      `
      )
      .join('');
  } catch (err) {
    showToast(err.message, true);
  }
}

async function openEditSaleModal(saleId) {
  try {
    const sales = await apiFetch('/api/sales?limit=200');
    const sale = sales.find((s) => s.id === Number(saleId));
    if (!sale) {
      showToast('Sale record not found', true);
      return;
    }

    document.getElementById('editSaleId').value = sale.id;

    const kitSel = document.getElementById('editSaleKit');
    kitSel.innerHTML = state.kits
      .map((k) => `<option value="${k.id}">${escapeHtml(k.kit_name)} (${escapeHtml(k.sku)})</option>`)
      .join('');
    kitSel.value = String(sale.kit_id);

    const platSel = document.getElementById('editSalePlatform');
    platSel.innerHTML = state.platforms
      .map((p) => `<option value="${p.id}">${escapeHtml(p.platform_name)}</option>`)
      .join('');
    platSel.value = String(sale.platform_id);

    document.getElementById('editSaleQty').value = sale.quantity;
    document.getElementById('editSalePrice').value = sale.selling_price_at_sale;
    document.getElementById('editSaleDate').value = String(sale.sale_date).replace(' ', 'T').slice(0, 16);

    openModal('editSaleModal');
  } catch (err) {
    showToast(err.message, true);
  }
}

async function submitEditSaleForm(event) {
  event.preventDefault();
  const saleId = document.getElementById('editSaleId').value;
  try {
    await apiFetch(`/api/sales/${saleId}`, {
      method: 'PUT',
      body: JSON.stringify({
        kit_id: Number(document.getElementById('editSaleKit').value),
        platform_id: Number(document.getElementById('editSalePlatform').value),
        quantity: Number(document.getElementById('editSaleQty').value),
        selling_price: Number(document.getElementById('editSalePrice').value),
        sale_date: document.getElementById('editSaleDate').value
      })
    });
    closeModal('editSaleModal');
    showToast('Sale updated successfully');
    await loadBootstrapData();
    await loadDashboard();
    if (state.currentView === 'add-sale') await loadRecentSalesList();
    if (state.currentView === 'reports') await loadActiveReport();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function confirmDeleteSale(saleId) {
  if (!window.confirm('Are you sure you want to delete this sale record? Stock and totals will be updated automatically.')) {
    return;
  }
  try {
    await apiFetch(`/api/sales/${saleId}`, { method: 'DELETE' });
    showToast('Sale entry deleted');
    await loadBootstrapData();
    await loadDashboard();
    if (state.currentView === 'add-sale') await loadRecentSalesList();
    if (state.currentView === 'reports') await loadActiveReport();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function promptQuickAddPlatform() {
  const name = window.prompt('Enter custom platform name (e.g., Blinkit, Exhibitions):');
  if (!name || !name.trim()) return;
  try {
    const res = await apiFetch('/api/platforms', {
      method: 'POST',
      body: JSON.stringify({ platform_name: name.trim() })
    });
    showToast(`Platform "${res.platform.platform_name}" added!`);
    await loadBootstrapData();
    document.getElementById('salePlatformSelect').value = String(res.platform.id);
    renderPlatformQuickChips();
  } catch (err) {
    showToast(err.message, true);
  }
}

// ==========================================
// 3. KIT MANAGEMENT & DETAILS LOGIC
// ==========================================
async function refreshKitsList() {
  try {
    state.kits = await apiFetch('/api/kits');
    renderKitsGrid();
    populateSaleFormOptions();
    populateReportFilterOptions();
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderKitsGrid() {
  const container = document.getElementById('kitsGridContainer');
  if (!container) return;

  const q = (document.getElementById('kitFilterSearch')?.value || '').trim().toLowerCase();
  const statusFilter = document.getElementById('kitFilterStatus')?.value || 'ALL';

  const filtered = state.kits.filter((k) => {
    if (statusFilter !== 'ALL' && k.status !== statusFilter) return false;
    if (!q) return true;
    return (
      k.kit_name.toLowerCase().includes(q) ||
      k.sku.toLowerCase().includes(q) ||
      (k.category || '').toLowerCase().includes(q)
    );
  });

  if (!filtered.length) {
    container.innerHTML = `
      <div class="card" style="grid-column: 1 / -1;">
        <div class="empty-state">
          <div class="empty-state-title">No kits found</div>
          <p>Tap <strong>+ Add New Kit</strong> to add your first kit product.</p>
        </div>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered
    .map((k) => {
      const initials = k.kit_name.slice(0, 2).toUpperCase();
      const thumbHtml = k.image
        ? `<img src="${escapeHtml(k.image)}" alt="${escapeHtml(k.kit_name)}" onerror="this.style.display='none';this.parentElement.textContent='${initials}';" />`
        : initials;

      const stockDisplay =
        k.stock_quantity === null || k.stock_quantity === undefined
          ? 'Untracked'
          : `${formatNumber(k.stock_quantity)} left`;

      return `
        <div class="kit-card ${k.status === 'Inactive' ? 'inactive' : ''}">
          <div>
            <div class="kit-top">
              <div style="display:flex; gap:12px; align-items:center;">
                <div class="kit-thumb">${thumbHtml}</div>
                <div>
                  <div style="font-weight:800; font-size:1.02rem; color:var(--dark-text);">${escapeHtml(k.kit_name)}</div>
                  <div style="font-size:0.78rem; color:var(--muted-text);">SKU: <strong>${escapeHtml(k.sku)}</strong> • ${escapeHtml(k.category || 'General')}</div>
                </div>
              </div>
              <span class="status-pill ${k.status === 'Active' ? 'active' : 'inactive'}">${escapeHtml(k.status)}</span>
            </div>

            ${
              k.description
                ? `<p style="font-size:0.83rem; color:var(--muted-text); margin-top:8px;">${escapeHtml(k.description)}</p>`
                : ''
            }
          </div>

          <div>
            <div class="kit-stats-strip" style="margin-bottom:12px;">
              <div>
                <div class="kit-stat-lbl">Selling Price</div>
                <div class="kit-stat-val" style="color:var(--primary-blue);">${formatCurrency(k.default_selling_price)}</div>
              </div>
              <div>
                <div class="kit-stat-lbl">Sold</div>
                <div class="kit-stat-val">${formatNumber(k.total_quantity_sold)}</div>
              </div>
              <div>
                <div class="kit-stat-lbl">Revenue</div>
                <div class="kit-stat-val" style="color:var(--primary-red);">${formatCurrency(k.total_revenue)}</div>
              </div>
            </div>

            <div style="display:flex; align-items:center; justify-content:space-between; font-size:0.8rem; color:var(--muted-text); margin-bottom:10px;">
              <span>Cost: ${k.cost_price !== null ? formatCurrency(k.cost_price) : 'N/A'}</span>
              <span>Stock: <strong>${stockDisplay}</strong></span>
            </div>

            <div style="display:flex; gap:8px;">
              <button type="button" class="btn btn-sm btn-blue" style="flex:1;" onclick="openKitDetailsModal(${k.id})">
                Details & History
              </button>
              <button type="button" class="btn btn-sm btn-outline-blue" onclick="openKitModal(${k.id})">
                Edit
              </button>
              <button type="button" class="btn btn-sm ${k.status === 'Active' ? 'btn-outline-red' : 'btn-outline-blue'}" onclick="toggleKitActiveStatus(${k.id})">
                ${k.status === 'Active' ? 'Deactivate' : 'Activate'}
              </button>
            </div>
          </div>
        </div>
      `;
    })
    .join('');
}

function openKitModal(kitId = null) {
  const form = document.getElementById('kitForm');
  form.reset();
  if (kitId) {
    const kit = state.kits.find((k) => k.id === Number(kitId));
    if (!kit) return;
    document.getElementById('kitModalTitle').textContent = `Edit Kit — ${kit.kit_name}`;
    document.getElementById('kitFormId').value = kit.id;
    document.getElementById('kitFormName').value = kit.kit_name;
    document.getElementById('kitFormSku').value = kit.sku;
    document.getElementById('kitFormCategory').value = kit.category || 'General';
    document.getElementById('kitFormStatus').value = kit.status || 'Active';
    document.getElementById('kitFormSellingPrice').value = kit.default_selling_price;
    document.getElementById('kitFormCostPrice').value = kit.cost_price !== null ? kit.cost_price : '';
    document.getElementById('kitFormStock').value = kit.stock_quantity !== null ? kit.stock_quantity : '';
    document.getElementById('kitFormImage').value = kit.image || '';
    document.getElementById('kitFormDescription').value = kit.description || '';
  } else {
    document.getElementById('kitModalTitle').textContent = 'Add New Kit';
    document.getElementById('kitFormId').value = '';
    document.getElementById('kitFormStatus').value = 'Active';
  }
  openModal('kitModal');
}

async function submitKitForm(event) {
  event.preventDefault();
  const kitId = document.getElementById('kitFormId').value;
  const payload = {
    kit_name: document.getElementById('kitFormName').value.trim(),
    sku: document.getElementById('kitFormSku').value.trim(),
    category: document.getElementById('kitFormCategory').value.trim() || 'General',
    status: document.getElementById('kitFormStatus').value,
    default_selling_price: document.getElementById('kitFormSellingPrice').value,
    cost_price: document.getElementById('kitFormCostPrice').value,
    stock_quantity: document.getElementById('kitFormStock').value,
    image: document.getElementById('kitFormImage').value.trim(),
    description: document.getElementById('kitFormDescription').value.trim()
  };

  try {
    if (kitId) {
      await apiFetch(`/api/kits/${kitId}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      showToast('Kit updated successfully!');
    } else {
      const res = await apiFetch('/api/kits', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      showToast(`Kit "${res.kit.kit_name}" added!`);
    }
    closeModal('kitModal');
    await loadBootstrapData();
    await loadDashboard();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function toggleKitActiveStatus(kitId) {
  const kit = state.kits.find((k) => k.id === Number(kitId));
  if (!kit) return;
  const nextStatus = kit.status === 'Active' ? 'Inactive' : 'Active';
  try {
    await apiFetch(`/api/kits/${kitId}`, {
      method: 'PUT',
      body: JSON.stringify({ status: nextStatus })
    });
    showToast(`Kit "${kit.kit_name}" marked as ${nextStatus}`);
    await loadBootstrapData();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function openKitDetailsModal(kitId) {
  try {
    const kit = await apiFetch(`/api/kits/${kitId}`);
    state.currentKitDetails = kit;

    document.getElementById('detKitName').textContent = kit.kit_name;
    document.getElementById('detKitSkuCategory').textContent = `SKU: ${kit.sku} • Category: ${kit.category || 'General'}`;
    document.getElementById('detSellingPrice').textContent = formatCurrency(kit.default_selling_price);
    document.getElementById('detCostPrice').textContent =
      kit.cost_price !== null ? `Cost Price: ${formatCurrency(kit.cost_price)}` : 'Cost Price: Not configured';
    document.getElementById('detTotalSold').textContent = `${formatNumber(kit.total_quantity_sold)} Units`;
    document.getElementById('detTotalOrders').textContent = `${formatNumber(kit.total_orders)} Orders`;
    document.getElementById('detTotalRevenue').textContent = formatCurrency(kit.total_revenue);
    document.getElementById('detTotalProfit').textContent =
      state.settings.profit_calculation_enabled === '1'
        ? `Est. Profit: ${formatCurrency(kit.total_profit)}`
        : '';
    document.getElementById('detRemainingStock').textContent =
      kit.stock_quantity !== null ? formatNumber(kit.stock_quantity) : 'Untracked';
    document.getElementById('detStatusText').textContent = `Status: ${kit.status}`;

    document.getElementById('detQuickKitId').value = kit.id;
    document.getElementById('detNewSellingPrice').value = kit.default_selling_price;

    const historyWrap = document.getElementById('detSalesHistoryContainer');
    if (!kit.sales_history || !kit.sales_history.length) {
      historyWrap.innerHTML = `<div class="empty-state">No sales recorded for this kit yet.</div>`;
    } else {
      historyWrap.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Date & Time</th>
              <th>Platform</th>
              <th class="text-right">Qty</th>
              <th class="text-right">Sale Price</th>
              <th class="text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            ${kit.sales_history
              .map(
                (s) => `
              <tr>
                <td>${escapeHtml(formatSaleDateTimeShort(s.sale_date))}</td>
                <td>${escapeHtml(s.platform_name)}</td>
                <td class="text-right">${formatNumber(s.quantity)}</td>
                <td class="text-right">${formatCurrency(s.selling_price_at_sale)}</td>
                <td class="text-right" style="font-weight:700; color:var(--primary-red);">${formatCurrency(s.total_amount)}</td>
              </tr>
            `
              )
              .join('')}
          </tbody>
        </table>
      `;
    }

    openModal('kitDetailsModal');
  } catch (err) {
    showToast(err.message, true);
  }
}

async function submitQuickPriceUpdate(event) {
  event.preventDefault();
  const kitId = document.getElementById('detQuickKitId').value;
  const newPrice = document.getElementById('detNewSellingPrice').value;
  try {
    await apiFetch(`/api/kits/${kitId}`, {
      method: 'PUT',
      body: JSON.stringify({ default_selling_price: Number(newPrice) })
    });
    showToast('Default selling price updated! Historical sales remain unchanged.');
    await loadBootstrapData();
    await openKitDetailsModal(kitId);
  } catch (err) {
    showToast(err.message, true);
  }
}

function editKitFromDetails() {
  if (!state.currentKitDetails) return;
  closeModal('kitDetailsModal');
  openKitModal(state.currentKitDetails.id);
}

// ==========================================
// 4. REPORTS & ANALYTICS LOGIC
// ==========================================
function populateReportFilterOptions() {
  const kitSel = document.getElementById('repFilterKit');
  const prevKit = kitSel.value;
  kitSel.innerHTML = '<option value="">All Kits</option>';
  state.kits.forEach((k) => {
    const opt = document.createElement('option');
    opt.value = String(k.id);
    opt.textContent = `${k.kit_name} (${k.sku})`;
    kitSel.appendChild(opt);
  });
  kitSel.value = prevKit;

  const platSel = document.getElementById('repFilterPlatform');
  const prevPlat = platSel.value;
  platSel.innerHTML = '<option value="">All Platforms</option>';
  state.platforms.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = String(p.id);
    opt.textContent = p.platform_name;
    platSel.appendChild(opt);
  });
  platSel.value = prevPlat;
}

function switchReportTab(tabName) {
  state.currentReportTab = tabName;
  document.querySelectorAll('[data-report-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.getAttribute('data-report-tab') === tabName);
  });

  const isHistory = tabName === 'history';
  document.getElementById('reportFiltersCard').style.display = isHistory ? 'none' : 'block';
  document.getElementById('reportMainAnalyticsWrap').style.display = isHistory ? 'none' : 'block';
  document.getElementById('reportHistoryAndCompareWrap').style.display = isHistory ? 'block' : 'none';

  document.getElementById('filterDailyWrap').style.display = tabName === 'daily' ? 'block' : 'none';
  document.getElementById('filterWeeklyWrap').style.display = tabName === 'weekly' ? 'block' : 'none';
  document.getElementById('filterMonthlyWrap').style.display = tabName === 'monthly' ? 'block' : 'none';
  document.getElementById('filterAnnualWrap').style.display = tabName === 'annual' ? 'block' : 'none';
  document.getElementById('filterCustomWrap').style.display = tabName === 'custom' ? 'block' : 'none';

  if (isHistory) {
    loadReportHistoryAndComparison();
  } else {
    loadActiveReport();
  }
}

function buildReportQueryParams() {
  const tab = state.currentReportTab;
  const params = new URLSearchParams();
  params.set('type', tab);

  if (tab === 'daily') {
    params.set('date', document.getElementById('repDailyDate').value || getTodayYMD());
  } else if (tab === 'weekly') {
    params.set('start_date', document.getElementById('repWeeklyStart').value || getTodayYMD());
  } else if (tab === 'monthly') {
    params.set('month', document.getElementById('repMonthlyInput').value || getTodayYMD().slice(0, 7));
  } else if (tab === 'annual') {
    params.set('year', document.getElementById('repAnnualYear').value || String(new Date().getFullYear()));
  } else if (tab === 'custom') {
    params.set('start_date', document.getElementById('repCustomStart').value || getTodayYMD());
    params.set('end_date', document.getElementById('repCustomEnd').value || getTodayYMD());
  }

  const kitId = document.getElementById('repFilterKit').value;
  const platId = document.getElementById('repFilterPlatform').value;
  if (kitId) params.set('kit_id', kitId);
  if (platId) params.set('platform_id', platId);

  return params;
}

async function loadActiveReport() {
  try {
    const params = buildReportQueryParams();
    const data = await apiFetch(`/api/reports/analytics?${params.toString()}`);
    state.activeReportData = data;
    renderActiveReport(data);
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderActiveReport(data) {
  const s = data.summary;
  document.getElementById('reportPeriodTitleDisplay').textContent = `${data.label}`;

  // 1. Summary Grid
  const sumGrid = document.getElementById('reportSummaryGrid');
  sumGrid.innerHTML = `
    <div class="summary-card">
      <div class="summary-label">Total Kits Sold</div>
      <div class="summary-value blue">${formatNumber(s.total_kits_sold)}</div>
      <div class="summary-subtext">Avg ${formatNumber(s.avg_daily_kits)} units/day</div>
    </div>
    <div class="summary-card highlight-red">
      <div class="summary-label">Total Revenue</div>
      <div class="summary-value red">${formatCurrency(s.total_revenue)}</div>
      <div class="summary-subtext">Avg ${formatCurrency(s.avg_daily_revenue)}/day</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">Total Orders</div>
      <div class="summary-value">${formatNumber(s.total_orders)}</div>
      <div class="summary-subtext">${data.start_date} to ${data.end_date}</div>
    </div>
    ${
      data.profit_calculation_enabled
        ? `
      <div class="summary-card">
        <div class="summary-label">Estimated Profit</div>
        <div class="summary-value blue">${formatCurrency(s.total_profit)}</div>
        <div class="summary-subtext">Based on recorded cost price</div>
      </div>
    `
        : ''
    }
  `;

  // 2. Highlights Row (Best-Selling Kit & Best-Performing Platform)
  const hlGrid = document.getElementById('reportHighlightsGrid');
  const bestKitText = s.best_selling_kit
    ? `${escapeHtml(s.best_selling_kit.kit_name)} (${formatNumber(s.best_selling_kit.quantity_sold)} units)`
    : 'No sales yet';
  const bestPlatText = s.best_performing_platform
    ? `${escapeHtml(s.best_performing_platform.platform_name)} (${formatCurrency(s.best_performing_platform.revenue)})`
    : 'No sales yet';

  hlGrid.innerHTML = `
    <div class="card" style="margin-bottom:0;">
      <div style="font-size:0.78rem; color:var(--muted-text); font-weight:700; text-transform:uppercase;">Best-Selling Kit</div>
      <div style="font-size:1.05rem; font-weight:800; color:var(--primary-blue); margin-top:4px;">${bestKitText}</div>
    </div>
    <div class="card" style="margin-bottom:0;">
      <div style="font-size:0.78rem; color:var(--muted-text); font-weight:700; text-transform:uppercase;">Best-Performing Platform</div>
      <div style="font-size:1.05rem; font-weight:800; color:var(--primary-red); margin-top:4px;">${bestPlatText}</div>
    </div>
    <div class="card" style="margin-bottom:0;">
      <div style="font-size:0.78rem; color:var(--muted-text); font-weight:700; text-transform:uppercase;">Average Daily Sales</div>
      <div style="font-size:1.05rem; font-weight:800; color:var(--dark-text); margin-top:4px;">${formatCurrency(s.avg_daily_revenue)} (${formatNumber(s.avg_daily_kits)} units)</div>
    </div>
  `;

  // 3. Platform Breakdown Table
  const platWrap = document.getElementById('reportPlatformTableWrap');
  if (!data.platform_breakdown.length) {
    platWrap.innerHTML = `<div class="empty-state">No platform sales in this period.</div>`;
  } else {
    platWrap.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Platform</th>
            <th class="text-right">Kits Sold</th>
            <th class="text-right">Orders</th>
            <th class="text-right">Revenue</th>
          </tr>
        </thead>
        <tbody>
          ${data.platform_breakdown
            .map(
              (p) => `
            <tr>
              <td>
                <div style="font-weight:700;">${escapeHtml(p.platform_name)}</div>
                <div class="bar-track"><div class="bar-fill" style="width:${p.share_percent}%;"></div></div>
              </td>
              <td class="text-right">${formatNumber(p.quantity_sold)}</td>
              <td class="text-right">${formatNumber(p.orders)}</td>
              <td class="text-right" style="font-weight:700; color:var(--primary-red);">
                ${formatCurrency(p.revenue)}
                <div style="font-size:0.74rem; color:var(--muted-text); font-weight:400;">${p.share_percent}%</div>
              </td>
            </tr>
          `
            )
            .join('')}
        </tbody>
      </table>
    `;
  }

  // 4. Kit Breakdown Table (Top 5 highlighted for Annual, full list otherwise)
  const kitWrap = document.getElementById('reportKitTableWrap');
  document.getElementById('reportKitCardTitle').textContent =
    data.report_type === 'annual' ? 'Top Best-Selling Kits (Annual)' : 'Kit-Wise Sales Breakdown';
  const kitsToShow = data.report_type === 'annual' ? data.top_5_kits : data.kit_breakdown;

  if (!kitsToShow.length) {
    kitWrap.innerHTML = `<div class="empty-state">No kit sales in this period.</div>`;
  } else {
    kitWrap.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Kit Name</th>
            <th class="text-right">Units Sold</th>
            <th class="text-right">Revenue</th>
          </tr>
        </thead>
        <tbody>
          ${kitsToShow
            .map(
              (k, idx) => `
            <tr>
              <td>
                <div style="font-weight:700;">${idx === 0 ? '#1 ' : ''}${escapeHtml(k.kit_name)}</div>
                <div style="font-size:0.75rem; color:var(--muted-text);">SKU: ${escapeHtml(k.sku)}</div>
                <div class="bar-track"><div class="bar-fill red" style="width:${k.share_percent}%;"></div></div>
              </td>
              <td class="text-right" style="font-weight:700;">${formatNumber(k.quantity_sold)}</td>
              <td class="text-right" style="font-weight:700; color:var(--primary-blue);">${formatCurrency(k.revenue)}</td>
            </tr>
          `
            )
            .join('')}
        </tbody>
      </table>
    `;
  }

  // 5. Trend Breakdown Card (Day-wise for Weekly/Monthly/Custom, Monthly for Annual)
  const trendCard = document.getElementById('reportTrendCard');
  const trendTitle = document.getElementById('reportTrendCardTitle');
  const trendWrap = document.getElementById('reportTrendTableWrap');

  if (data.report_type === 'annual') {
    trendCard.style.display = 'block';
    trendTitle.textContent = 'Monthly Revenue Breakdown & Sales Trends';
    const maxRev = Math.max(...data.monthly_breakdown.map((m) => m.revenue), 1);
    trendWrap.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Month</th>
            <th class="text-right">Kits Sold</th>
            <th class="text-right">Orders</th>
            <th class="text-right">Revenue</th>
            ${data.profit_calculation_enabled ? '<th class="text-right">Est. Profit</th>' : ''}
          </tr>
        </thead>
        <tbody>
          ${data.monthly_breakdown
            .map(
              (m) => `
            <tr>
              <td>
                <div style="font-weight:700;">${escapeHtml(m.month_name)} (${escapeHtml(m.month)})</div>
                <div class="bar-track"><div class="bar-fill" style="width:${Math.round((m.revenue / maxRev) * 100)}%;"></div></div>
              </td>
              <td class="text-right">${formatNumber(m.quantity_sold)}</td>
              <td class="text-right">${formatNumber(m.orders)}</td>
              <td class="text-right" style="font-weight:700; color:var(--primary-red);">${formatCurrency(m.revenue)}</td>
              ${data.profit_calculation_enabled ? `<td class="text-right">${formatCurrency(m.profit)}</td>` : ''}
            </tr>
          `
            )
            .join('')}
        </tbody>
      </table>
    `;
  } else if (data.report_type === 'weekly' || data.report_type === 'monthly' || data.report_type === 'custom') {
    trendCard.style.display = 'block';
    trendTitle.textContent = data.report_type === 'weekly' ? 'Day-Wise Sales Summary (7 Days)' : 'Daily Revenue Trend';
    const maxDayRev = Math.max(...data.daily_trend.map((d) => d.revenue), 1);
    trendWrap.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Date</th>
            <th class="text-right">Units Sold</th>
            <th class="text-right">Orders</th>
            <th class="text-right">Revenue</th>
          </tr>
        </thead>
        <tbody>
          ${data.daily_trend
            .map(
              (d) => `
            <tr>
              <td>
                <div style="font-weight:600;">${escapeHtml(d.label)} ${d.weekday ? `(${escapeHtml(d.weekday)})` : ''}</div>
                <div class="bar-track"><div class="bar-fill" style="width:${Math.round((d.revenue / maxDayRev) * 100)}%;"></div></div>
              </td>
              <td class="text-right">${formatNumber(d.quantity_sold)}</td>
              <td class="text-right">${formatNumber(d.orders)}</td>
              <td class="text-right" style="font-weight:700; color:var(--primary-red);">${formatCurrency(d.revenue)}</td>
            </tr>
          `
            )
            .join('')}
        </tbody>
      </table>
    `;
  } else {
    trendCard.style.display = 'none';
  }
}

function downloadCurrentReport(format) {
  const params = buildReportQueryParams();
  window.location.href = `/api/reports/export/${format}?${params.toString()}`;
}

function openCurrentReportTextModal() {
  if (!state.activeReportData) return;
  state.eodModalData = state.activeReportData;
  document.getElementById('eodModalDatePicker').value = state.activeReportData.start_date;
  document.getElementById('eodReportTextOutput').textContent = state.activeReportData.shareable_text;
  openModal('eodReportModal');
}

// ==========================================
// 8. END-OF-DAY SHAREABLE REPORT MODAL
// ==========================================
async function openEodReportModal(customDate = null) {
  const dateToUse = customDate || document.getElementById('dashboardDatePicker').value || getTodayYMD();
  document.getElementById('eodModalDatePicker').value = dateToUse;
  openModal('eodReportModal');
  await generateEodReportForDate(dateToUse);
}

async function generateEodReportForDate(dateStr) {
  try {
    document.getElementById('eodReportTextOutput').textContent = 'Generating report...';
    const data = await apiFetch('/api/reports/generate-eod', {
      method: 'POST',
      body: JSON.stringify({
        date: dateStr,
        start_date: dateStr,
        end_date: dateStr,
        report_type: 'daily'
      })
    });
    state.eodModalData = data;
    document.getElementById('eodReportTextOutput').textContent = data.shareable_text;
  } catch (err) {
    showToast(err.message, true);
  }
}

async function copyEodReportText() {
  const text = document.getElementById('eodReportTextOutput').textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast('Report summary copied to clipboard.');
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    showToast('Report summary copied to clipboard.');
  }
}

function downloadEodPdf() {
  const dt = document.getElementById('eodModalDatePicker').value || getTodayYMD();
  window.location.href = `/api/reports/export/pdf?type=daily&date=${encodeURIComponent(dt)}`;
}

function downloadEodExcel() {
  const dt = document.getElementById('eodModalDatePicker').value || getTodayYMD();
  window.location.href = `/api/reports/export/excel?type=daily&date=${encodeURIComponent(dt)}`;
}

// ==========================================
// 9. REPORT HISTORY & PERIOD COMPARISON
// ==========================================
async function loadReportHistoryAndComparison() {
  try {
    const hist = await apiFetch('/api/reports/history');
    renderActiveSalesDatesHistory(hist.active_sales_dates || []);
    renderGeneratedReportsLog(hist.generated_reports || []);
    await runPeriodComparison();
  } catch (err) {
    showToast(err.message, true);
  }
}

function openHistoricalDateReport() {
  const dt = document.getElementById('historyQuickDateInput').value || getTodayYMD();
  openEodReportModal(dt);
}

function renderActiveSalesDatesHistory(dates) {
  const wrap = document.getElementById('activeSalesDatesContainer');
  if (!dates.length) {
    wrap.innerHTML = `<div class="empty-state">No sales dates recorded yet.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Date</th>
          <th class="text-right">Kits Sold</th>
          <th class="text-right">Orders</th>
          <th class="text-right">Revenue</th>
          <th class="text-right">Actions</th>
        </tr>
      </thead>
      <tbody>
        ${dates
          .map(
            (d) => `
          <tr>
            <td style="font-weight:700;">${escapeHtml(d.pretty_date)}</td>
            <td class="text-right">${formatNumber(d.total_kits_sold)}</td>
            <td class="text-right">${formatNumber(d.total_orders)}</td>
            <td class="text-right" style="font-weight:700; color:var(--primary-red);">${formatCurrency(d.total_revenue)}</td>
            <td class="text-right">
              <button type="button" class="chip-btn" onclick="openEodReportModal('${d.date}')">View / Share</button>
              <a href="/api/reports/export/pdf?type=daily&date=${encodeURIComponent(d.date)}" class="chip-btn" style="text-decoration:none;">PDF</a>
              <a href="/api/reports/export/excel?type=daily&date=${encodeURIComponent(d.date)}" class="chip-btn" style="text-decoration:none;">Excel</a>
            </td>
          </tr>
        `
          )
          .join('')}
      </tbody>
    </table>
  `;
}

function renderGeneratedReportsLog(reports) {
  const wrap = document.getElementById('generatedReportsLogContainer');
  if (!reports.length) {
    wrap.innerHTML = `<div class="empty-state">No generated reports logged yet. Click "GENERATE TODAY'S REPORT" to create one.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Type</th>
          <th>Period</th>
          <th class="text-right">Kits Sold</th>
          <th class="text-right">Revenue</th>
          <th>Generated At</th>
          <th class="text-right">Actions</th>
        </tr>
      </thead>
      <tbody>
        ${reports
          .map(
            (r) => `
          <tr>
            <td><span class="status-pill active">${escapeHtml(r.report_type.toUpperCase())}</span></td>
            <td style="font-weight:600;">${escapeHtml(r.pretty_period)}</td>
            <td class="text-right">${formatNumber(r.total_kits_sold)}</td>
            <td class="text-right" style="font-weight:700; color:var(--primary-red);">${formatCurrency(r.total_revenue)}</td>
            <td style="font-size:0.8rem; color:var(--muted-text);">${escapeHtml(r.generated_at)}</td>
            <td class="text-right">
              <button type="button" class="chip-btn" onclick="openEodReportModal('${r.start_date}')">Re-Open</button>
              <a href="/api/reports/export/pdf?type=custom&start_date=${encodeURIComponent(r.start_date)}&end_date=${encodeURIComponent(r.end_date)}" class="chip-btn" style="text-decoration:none;">PDF</a>
            </td>
          </tr>
        `
          )
          .join('')}
      </tbody>
    </table>
  `;
}

async function runPeriodComparison() {
  const p1s = document.getElementById('cmpP1Start').value;
  const p1e = document.getElementById('cmpP1End').value;
  const p2s = document.getElementById('cmpP2Start').value;
  const p2e = document.getElementById('cmpP2End').value;

  const params = new URLSearchParams({
    p1_start: p1s,
    p1_end: p1e,
    p2_start: p2s,
    p2_end: p2e
  });

  try {
    const res = await apiFetch(`/api/reports/compare?${params.toString()}`);
    const sa = res.period_a.summary;
    const sb = res.period_b.summary;
    const d = res.deltas;

    const fmtDelta = (deltaObj, isCurr = false) => {
      const sign = deltaObj.diff > 0 ? '+' : '';
      const valStr = isCurr ? formatCurrency(deltaObj.diff) : formatNumber(deltaObj.diff);
      return `${sign}${valStr} (${sign}${deltaObj.percent}%)`;
    };

    document.getElementById('comparisonResultContainer').innerHTML = `
      <div class="table-responsive">
        <table class="data-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th class="text-right">Period A (${escapeHtml(p1s)} to ${escapeHtml(p1e)})</th>
              <th class="text-right">Period B (${escapeHtml(p2s)} to ${escapeHtml(p2e)})</th>
              <th class="text-right">Difference (B vs A)</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style="font-weight:700;">Total Kits Sold</td>
              <td class="text-right">${formatNumber(sa.total_kits_sold)}</td>
              <td class="text-right" style="font-weight:700;">${formatNumber(sb.total_kits_sold)}</td>
              <td class="text-right" style="font-weight:700; color:${d.kits_sold.diff >= 0 ? 'var(--primary-blue)' : 'var(--primary-red)'};">
                ${fmtDelta(d.kits_sold)}
              </td>
            </tr>
            <tr>
              <td style="font-weight:700;">Total Orders</td>
              <td class="text-right">${formatNumber(sa.total_orders)}</td>
              <td class="text-right" style="font-weight:700;">${formatNumber(sb.total_orders)}</td>
              <td class="text-right" style="font-weight:700; color:${d.orders.diff >= 0 ? 'var(--primary-blue)' : 'var(--primary-red)'};">
                ${fmtDelta(d.orders)}
              </td>
            </tr>
            <tr>
              <td style="font-weight:700;">Total Revenue</td>
              <td class="text-right">${formatCurrency(sa.total_revenue)}</td>
              <td class="text-right" style="font-weight:700;">${formatCurrency(sb.total_revenue)}</td>
              <td class="text-right" style="font-weight:700; color:${d.revenue.diff >= 0 ? 'var(--primary-blue)' : 'var(--primary-red)'};">
                ${fmtDelta(d.revenue, true)}
              </td>
            </tr>
            <tr>
              <td style="font-weight:700;">Estimated Profit</td>
              <td class="text-right">${formatCurrency(sa.total_profit)}</td>
              <td class="text-right" style="font-weight:700;">${formatCurrency(sb.total_profit)}</td>
              <td class="text-right" style="font-weight:700; color:${d.profit.diff >= 0 ? 'var(--primary-blue)' : 'var(--primary-red)'};">
                ${fmtDelta(d.profit, true)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    `;
  } catch (err) {
    showToast(err.message, true);
  }
}

// ==========================================
// 10. SETTINGS, PLATFORMS, BACKUP & SECURITY
// ==========================================
async function saveGeneralSettings(event) {
  event.preventDefault();
  const currencyVal = document.getElementById('setCurrency').value;
  const parts = currencyVal.split(' ');
  const symbol = parts[parts.length - 1] || '₹';

  const payload = {
    business_name: document.getElementById('setBusinessName').value.trim(),
    currency: currencyVal,
    currency_symbol: symbol,
    timezone: document.getElementById('setTimezone').value,
    stock_tracking_enabled: document.getElementById('setStockTracking').checked ? '1' : '0',
    profit_calculation_enabled: document.getElementById('setProfitCalc').checked ? '1' : '0',
    require_auth: document.getElementById('setRequireAuth').checked ? '1' : '0'
  };

  try {
    const res = await apiFetch('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
    state.settings = res.settings;
    applySettingsToUI();
    showToast('Settings saved successfully!');
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderSettingsPlatformsTable() {
  const wrap = document.getElementById('settingsPlatformsTableWrap');
  if (!wrap) return;

  wrap.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Platform Name</th>
          <th class="text-right">Total Kits Sold</th>
          <th class="text-right">Total Revenue</th>
          <th>Status</th>
          <th class="text-right">Action</th>
        </tr>
      </thead>
      <tbody>
        ${state.platforms
          .map(
            (p) => `
          <tr>
            <td style="font-weight:700;">${escapeHtml(p.platform_name)}</td>
            <td class="text-right">${formatNumber(p.total_kits_sold)}</td>
            <td class="text-right" style="font-weight:700; color:var(--primary-red);">${formatCurrency(p.total_revenue)}</td>
            <td><span class="status-pill ${p.status === 'Active' ? 'active' : 'inactive'}">${escapeHtml(p.status)}</span></td>
            <td class="text-right">
              <button type="button" class="chip-btn" onclick="togglePlatformStatus(${p.id}, '${p.status}')">
                ${p.status === 'Active' ? 'Disable' : 'Enable'}
              </button>
            </td>
          </tr>
        `
          )
          .join('')}
      </tbody>
    </table>
  `;
}

async function addCustomPlatformFromSettings(event) {
  event.preventDefault();
  const input = document.getElementById('newPlatformNameInput');
  const name = input.value.trim();
  if (!name) return;

  try {
    await apiFetch('/api/platforms', {
      method: 'POST',
      body: JSON.stringify({ platform_name: name })
    });
    input.value = '';
    showToast(`Platform "${name}" added!`);
    await loadBootstrapData();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function togglePlatformStatus(platformId, currentStatus) {
  const nextStatus = currentStatus === 'Active' ? 'Inactive' : 'Active';
  try {
    await apiFetch(`/api/platforms/${platformId}`, {
      method: 'PUT',
      body: JSON.stringify({ status: nextStatus })
    });
    showToast(`Platform status updated to ${nextStatus}`);
    await loadBootstrapData();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function handleRestoreBackupFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;

  if (!window.confirm('Restoring a backup will replace current kits, platforms, and sales with the backup data. Continue?')) {
    event.target.value = '';
    return;
  }

  try {
    const text = await file.text();
    const jsonData = JSON.parse(text);
    await apiFetch('/api/restore', {
      method: 'POST',
      body: JSON.stringify(jsonData)
    });
    showToast('Database backup restored successfully!');
    event.target.value = '';
    await loadBootstrapData();
    await loadDashboard();
  } catch (err) {
    showToast(`Restore failed: ${err.message}`, true);
  }
}

async function lockDashboard() {
  try {
    await fetch('/api/auth/lock', { method: 'POST' });
    openModal('authLockModal');
  } catch (err) {
    showToast(err.message, true);
  }
}

async function submitAdminLogin(event) {
  event.preventDefault();
  const pw = document.getElementById('loginPasswordInput').value;
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || 'Invalid password', true);
      return;
    }
    document.getElementById('loginPasswordInput').value = '';
    closeModal('authLockModal');
    showToast('Dashboard unlocked');
    await loadBootstrapData();
    await loadDashboard();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function changeAdminPassword(event) {
  event.preventDefault();
  const cur = document.getElementById('adminCurrentPassword').value;
  const nw = document.getElementById('adminNewPassword').value;
  try {
    await apiFetch('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: cur, new_password: nw })
    });
    document.getElementById('adminCurrentPassword').value = '';
    document.getElementById('adminNewPassword').value = '';
    showToast('Administrator password updated!');
  } catch (err) {
    showToast(err.message, true);
  }
}

// ==========================================
// 11. SUPABASE POSTGRESQL STATUS & MIGRATION UI
// ==========================================
async function loadDatabaseStatus() {
  try {
    const status = await apiFetch('/api/database/status');
    const badge = document.getElementById('dbEngineBadge');
    const statusText = document.getElementById('dbConnectionStatusText');
    const migrateBtn = document.getElementById('btnMigrateLocalToSupabase');

    if (status.engine === 'postgresql') {
      badge.textContent = status.connected ? 'Supabase PostgreSQL (Connected)' : 'Connection Error';
      badge.className = `status-pill ${status.connected ? 'active' : 'alert'}`;
      statusText.textContent = status.connected
        ? `Connected to ${status.provider} (schema: ${status.schema}, role: ${status.user}, SSL: ${status.ssl_mode})`
        : `PostgreSQL connection error: ${status.error}`;
      if (migrateBtn) migrateBtn.style.display = status.connected ? 'inline-flex' : 'none';
    } else {
      badge.textContent = 'Local SQLite (Offline)';
      badge.className = 'status-pill inactive';
      statusText.textContent =
        'Running on Local SQLite. Set DATABASE_URL in .env to connect to Supabase PostgreSQL.';
      if (migrateBtn) migrateBtn.style.display = 'none';
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

async function migrateLocalDataToSupabase() {
  if (!window.confirm('Copy any existing local SQLite kits, platforms, and sales into Supabase?')) {
    return;
  }
  try {
    const res = await apiFetch('/api/database/migrate-local', { method: 'POST' });
    showToast(res.message);
    await loadBootstrapData();
    await loadDashboard();
  } catch (err) {
    showToast(err.message, true);
  }
}

