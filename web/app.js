let backend = null;
let state = { current: null, backups: [] };
let selectedId = null;
let activeRequests = 0;
let searchQuery = '';
let busyFocus = null;
const AUTO_REFRESH_MS = 300_000;
let lastRefreshAt = 0;
let toastTimer = null;

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function quotaValues(text) {
  const value = String(text || '');
  const usedMatch = value.match(/(\d+)%/);
  const remainingMatch = value.match(/còn\s+(\d+)/i);
  const used = usedMatch ? Math.min(100, Math.max(0, Number(usedMatch[1]))) : 0;
  const remaining = remainingMatch ? Math.min(100, Math.max(0, Number(remainingMatch[1]))) : 100 - used;
  return { used, remaining };
}
function quotaTone(text) {
  const { used, remaining } = quotaValues(text);
  if (remaining <= 0) return 'badge-bad';
  return used >= 90 ? 'badge-bad' : used >= 70 ? 'badge-warn' : 'badge-good';
}
function quotaBar(text, kind) {
  if (!/\d+%/.test(String(text || ''))) return `<span class="quota-unavailable" title="${escapeHtml(text || 'Chưa có dữ liệu')}">Chưa có dữ liệu</span>`;
  const { remaining } = quotaValues(text);
  const tone = quotaTone(text);
  return `<span class="quota-progress ${kind} ${tone}" title="${escapeHtml(text)}">
    <span class="quota-progress-fill" style="width:${remaining}%"></span>
    <span class="quota-progress-label">${escapeHtml(compactQuota(text))}</span>
  </span>`;
}
function displayStatus(text) {
  const labels = { 'HOẠT ĐỘNG':'Còn quota', 'ĐANG HOẠT ĐỘNG':'Còn quota', 'ĐÃ HẾT QUOTA':'Hết quota', 'HẾT QUOTA':'Hết quota', 'LỖI ĐỒNG BỘ':'Chưa đồng bộ', 'LỖI PHIÊN':'Lỗi phiên', 'UNAVAILABLE':'Chưa có dữ liệu', 'ĐANG DÙNG':'Đang dùng', 'ĐÃ LƯU':'Đã lưu' };
  return labels[String(text || '').toUpperCase()] || text || 'Chưa có dữ liệu';
}
function statusTone(text) {
  const value = String(text).toUpperCase();
  return value.includes('HẾT') || value.includes('LỖI') ? 'badge-bad' : (value.includes('HOẠT') || value.includes('DÙNG') || value.includes('LƯU')) ? 'badge-good' : 'badge-warn';
}
function splitDate(text) {
  if (!text || text === '—') return ['—', ''];
  const parts = text.split('·').map(x => x.trim());
  return [parts[0], parts[1] || ''];
}

function compactQuota(text) {
  const value = String(text || '—');
  const match = value.match(/(\d+)%.*?còn\s+(\d+)/i);
  return match ? `${match[1]}% dùng · còn ${match[2]}` : value;
}

function render(next) {
  const providerChanged = Boolean(state.provider && next?.provider && state.provider !== next.provider);
  state = next || { current: null, backups: [] };
  const isAgy = state.provider === 'antigravity';
  const appName = state.appName || (isAgy ? 'Antigravity' : 'Codex');
  document.body.classList.toggle('provider-antigravity', isAgy);
  document.querySelectorAll('.provider-tab').forEach(tab => {
    const active = tab.dataset.provider === (state.provider || 'codex');
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-pressed', String(active));
  });
  $('brand-mark').textContent = isAgy ? 'A' : 'C';
  $('brand-eyebrow').textContent = appName;
  const current = state.current;
  $('current-name').textContent = current?.name || 'Chưa đăng nhập';
  $('current-avatar').textContent = current?.name ? current.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase() : '—';
  $('current-email').textContent = current?.email || state.currentError || `Không tìm thấy phiên ${appName}`;
  $('current-status').textContent = current ? 'Đang đăng nhập' : 'Chưa đăng nhập';
  $('current-status').classList.toggle('off', !current);
  const currentQuota = state.currentQuota || {};
  $('current-session').textContent = compactQuota(currentQuota.session);
  $('current-weekly').textContent = compactQuota(currentQuota.weekly);
  $('current-session').className = (/\d+%/.test(String(currentQuota.session || '')) ? quotaTone(currentQuota.session) : '');
  $('current-weekly').className = (/\d+%/.test(String(currentQuota.weekly || '')) ? quotaTone(currentQuota.weekly) : '');
  $('current-quota-status').textContent = current ? displayStatus(currentQuota.status) : '—';
  $('current-quota-status').className = statusTone(currentQuota.status);
  $('backup').disabled = !current;
  $('logout').disabled = !current;
  $('backup-count').textContent = state.backups.length;
  $('saved-count').textContent = state.backups.length;
  $('active-count').textContent = isAgy ? (current ? 1 : 0) : state.backups.filter(x => String(x.status).toUpperCase().includes('HOẠT')).length;
  $('limited-count').textContent = isAgy ? Math.max(0, state.backups.length - (current ? 1 : 0)) : state.backups.filter(x => String(x.status).toUpperCase().includes('HẾT')).length;
  $('active-label').textContent = isAgy ? 'Đang đăng nhập' : 'Còn quota';
  $('limited-label').textContent = isAgy ? 'Phiên dự phòng' : 'Hết quota';
  $('cache-caption').textContent = isAgy
    ? 'Chỉ sao lưu đăng nhập, không gồm hội thoại và dự án.'
    : (state.quotaError ? state.quotaError : 'Tự làm mới mỗi 5 phút.');
  $('cache-caption').title = state.quotaError || (isAgy ? '' : `Quota trực tiếp từ phiên Codex lúc ${state.quotaFetchedAt || 'chưa xác định'}`);
  $('cache-caption').classList.toggle('error', Boolean(state.quotaError));
  $('sync-indicator').classList.toggle('error', Boolean(state.quotaError));
  const updatedAt = String(state.quotaFetchedAt || '').split('·').pop().trim();
  $('sync-label').textContent = isAgy ? 'Đã đọc phiên trên máy' : (state.quotaError ? 'Chưa đồng bộ đầy đủ' : (updatedAt ? `Đã cập nhật ${updatedAt}` : 'Sẵn sàng'));

  if (selectedId && !state.backups.some(x => x.id === selectedId)) selectedId = null;
  const body = $('backup-body');
  const focusedRowId = body.contains(document.activeElement) ? document.activeElement?.closest('tr')?.dataset.id : null;
  const query = searchQuery.trim().toLocaleLowerCase('vi');
  const visibleBackups = state.backups.filter(item => !query || `${item.name} ${item.email}`.toLocaleLowerCase('vi').includes(query));
  body.innerHTML = visibleBackups.map(item => {
    const sync = splitDate(item.quotaUpdated), created = splitDate(item.backupAt);
    return `<tr data-id="${escapeHtml(item.id)}" class="${item.id === selectedId ? 'selected' : ''}" tabindex="0" aria-selected="${item.id === selectedId}">
      <td><div class="account-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div><div class="account-email" title="${escapeHtml(item.email)}">${escapeHtml(item.email)}</div></td>
      <td><span class="badge badge-plan">${escapeHtml(item.plan)}</span></td>
      <td><span class="badge ${statusTone(item.status)}">${escapeHtml(displayStatus(item.status))}</span></td>
      <td class="quota-only">${quotaBar(item.session, 'session')}</td>
      <td class="quota-only">${quotaBar(item.weekly, 'weekly')}</td>
      <td class="quota-only"><div class="date-main">${escapeHtml(sync[0])}</div><div class="date-sub">${escapeHtml(sync[1])}</div></td>
      <td><div class="date-main">${escapeHtml(created[0])}</div><div class="date-sub">${escapeHtml(created[1])}</div></td>
    </tr>`;
  }).join('');
  body.querySelectorAll('tr').forEach(row => {
    const select = () => {
      const focused = document.activeElement === row;
      selectedId = row.dataset.id;
      render(state);
      if (focused) [...$('backup-body').rows].find(item => item.dataset.id === selectedId)?.focus({ preventScroll: true });
    };
    row.addEventListener('click', select);
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); }
      if (event.key === 'ArrowDown') { event.preventDefault(); row.nextElementSibling?.focus(); }
      if (event.key === 'ArrowUp') { event.preventDefault(); row.previousElementSibling?.focus(); }
    });
  });
  if (focusedRowId) [...body.rows].find(row => row.dataset.id === focusedRowId)?.focus({ preventScroll: true });
  $('empty-state').classList.toggle('visible', !visibleBackups.length);
  $('empty-title').textContent = state.backups.length ? 'Không tìm thấy phiên phù hợp' : 'Chưa có phiên đã lưu';
  $('empty-description').textContent = state.backups.length ? 'Thử tên hoặc email khác để tìm lại phiên.' : 'Chọn “Lưu phiên hiện tại” để tạo bản sao lưu đầu tiên.';
  const selected = state.backups.find(x => x.id === selectedId);
  $('selection-label').textContent = selected ? `Đã chọn: ${selected.name} · ${selected.email}` : 'Chưa chọn phiên';
  $('delete').disabled = !selected;
  $('restore').disabled = !selected;
  requestAnimationFrame(updateProviderIndicator);
  if (providerChanged && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.querySelector('.workspace main').animate([
      { opacity: .4, transform: 'translateY(5px)' },
      { opacity: 1, transform: 'translateY(0)' }
    ], { duration: 220, easing: 'cubic-bezier(.22,1,.36,1)' });
  }
}

function updateProviderIndicator() {
  const tab = document.querySelector('.provider-tab.active');
  if (!tab) return;
  const menu = tab.parentElement;
  menu.style.setProperty('--tab-x', `${tab.offsetLeft}px`);
  menu.style.setProperty('--tab-y', `${tab.offsetTop}px`);
  menu.style.setProperty('--tab-width', `${tab.offsetWidth}px`);
  menu.style.setProperty('--tab-height', `${tab.offsetHeight}px`);
}
new ResizeObserver(updateProviderIndicator).observe(document.querySelector('.provider-switch'));
requestAnimationFrame(updateProviderIndicator);

async function confirmAction(title, description, label, destructive = false) {
  const dialog = $('confirmation-dialog');
  if (dialog.open) return false;
  $('confirmation-title').textContent = title;
  $('confirmation-description').textContent = description;
  $('confirmation-accept').textContent = label;
  dialog.classList.toggle('destructive', destructive);
  dialog.querySelector('.confirmation-icon use').setAttribute('href', destructive ? '#i-trash' : '#i-restore');
  dialog.returnValue = 'cancel';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'accept'), { once: true });
    dialog.showModal();
  });
} 

function setBusy(busy, text) {
  if (busy && !document.querySelector('.shell').inert) { busyFocus = document.activeElement; }
  document.querySelector('.shell').inert = busy;
  document.querySelector('.shell').setAttribute('aria-busy', String(busy));
  $('busy-layer').classList.toggle('hidden', !busy);
  if (!busy && busyFocus?.isConnected && !busyFocus.disabled) { busyFocus.focus({ preventScroll: true }); busyFocus = null; }
  $('busy-text').textContent = text || 'Vui lòng chờ…';
  if (busy) $('busy-layer').focus({ preventScroll: true });
}
function showToast(kind, message) {
  clearTimeout(toastTimer);
  const item = document.createElement('div');
  item.className = `toast ${kind}`;
  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.innerHTML = kind === 'error'
    ? '<svg class="icon" viewBox="0 0 24 24"><path d="M12 5v9m0 4v1"/></svg>'
    : '<svg class="icon" viewBox="0 0 24 24"><path d="m5 12 4 4 10-10"/></svg>';
  const content = document.createElement('span');
  content.className = 'toast-message';
  content.textContent = message;
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'toast-dismiss';
  dismiss.setAttribute('aria-label', 'Đóng thông báo');
  dismiss.textContent = '×';
  dismiss.addEventListener('click', () => item.remove());
  item.append(icon, content, dismiss);
  $('toast-stack').replaceChildren(item);
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    item.animate([{ opacity: 0, transform: 'translateY(7px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 180, easing: 'cubic-bezier(.22,1,.36,1)' });
  }
  toastTimer = setTimeout(() => item.remove(), kind === 'error' ? 6000 : 3500);
}

async function callApi(method, args = [], busyText = '', options = {}) {
  if (!backend?.[method] || activeRequests > 0) return;
  activeRequests += 1;
  if (!options.quiet) setBusy(true, busyText || 'Đang xử lý…');
  try {
    const result = await backend[method](...args);
    if (['get_state', 'reload', 'select_provider'].includes(method)) lastRefreshAt = Date.now();
    if (result?.state) render(result.state);
    else if (result?.backups) render(result);
    if (result?.message && !options.quiet) showToast('success', result.message);
    return result;
  } catch (error) {
    showToast('error', error?.message || String(error));
    if (method === 'get_state') {
      $('sync-label').textContent = 'Chưa kết nối được';
      $('sync-indicator').classList.add('error');
      $('current-name').textContent = 'Chưa tải được phiên';
      $('current-email').textContent = 'Chọn Làm mới để thử lại.';
    }
  } finally {
    activeRequests -= 1;
    if (!options.quiet) setBusy(activeRequests > 0, '');
  }
}

async function autoRefresh() {
  if (!backend?.reload || activeRequests > 0 || document.hidden || $('confirmation-dialog').open) return;
  if (Date.now() - lastRefreshAt < AUTO_REFRESH_MS) return;
  activeRequests += 1;
  try {
    const result = await backend.reload();
    lastRefreshAt = Date.now();
    if (result?.state) render(result.state);
    else if (result?.backups) render(result);
  } catch (error) {
    console.warn('Không thể tự làm mới trạng thái:', error);
  } finally {
    activeRequests -= 1;
  }
}

window.addEventListener('pywebviewready', async () => {
  backend = window.pywebview.api;
  await callApi('get_state', [], 'Đang tải trạng thái…');
  window.setInterval(autoRefresh, AUTO_REFRESH_MS);
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) autoRefresh();
});

$('session-search').addEventListener('input', event => {
  searchQuery = event.target.value;
  const selected = state.backups.find(item => item.id === selectedId);
  if (selected && !`${selected.name} ${selected.email}`.toLocaleLowerCase('vi').includes(searchQuery.trim().toLocaleLowerCase('vi'))) selectedId = null;
  render(state);
});

$('reload').addEventListener('click', () => callApi('reload', [], state.provider === 'antigravity' ? 'Đang đọc phiên Antigravity…' : 'Đang lấy quota trực tiếp từ Codex…'));
$('backup').addEventListener('click', () => callApi('backup', [], `Đang đóng ${state.appName || 'ứng dụng'} và tạo backup…`));
$('logout').addEventListener('click', async () => {
  if (await confirmAction('Đăng xuất khỏi ứng dụng?', `Phiên hiện tại sẽ được sao lưu trước khi đăng xuất. ${state.appName || 'Ứng dụng'} sẽ đóng và mở lại.`, 'Đăng xuất'))
    callApi('logout', [], 'Đang đăng xuất an toàn…');
});
$('restore').addEventListener('click', async () => {
  const item = state.backups.find(x => x.id === selectedId);
  if (item && await confirmAction('Khôi phục phiên đăng nhập?', `${item.name}\n${item.email}\n\nPhiên đang dùng sẽ được sao lưu trước khi chuyển.`, 'Khôi phục phiên'))
    callApi('restore', [item.id], 'Đang khôi phục phiên…');
});
$('delete').addEventListener('click', async () => {
  const item = state.backups.find(x => x.id === selectedId);
  if (item && await confirmAction('Xóa phiên đã lưu?', `${item.name}\n${item.email}\n\nBản sao lưu này sẽ bị xóa vĩnh viễn.`, 'Xóa phiên', true))
    callApi('delete_backup', [item.id], 'Đang xóa backup…');
});
$('open-folder').addEventListener('click', () => callApi('open_backup_folder'));
document.querySelectorAll('.provider-tab').forEach(tab => tab.addEventListener('click', async () => {
  if (activeRequests > 0 || tab.dataset.provider === state.provider) return;
  const previousSelection = selectedId;
  const previousSearch = searchQuery;
  selectedId = null;
  searchQuery = '';
  $('session-search').value = '';
  const menu = document.querySelector('.provider-switch');
  menu.setAttribute('aria-busy', 'true');
  document.querySelector('.workspace').inert = true;
  $('open-folder').disabled = true;
  document.querySelectorAll('.provider-tab').forEach(button => { button.disabled = true; });
  try {
    const result = await callApi('select_provider', [tab.dataset.provider], '', { quiet: true });
    if (!result) {
      selectedId = previousSelection;
      searchQuery = previousSearch;
      $('session-search').value = searchQuery;
      render(state);
    }
  } finally {
    menu.setAttribute('aria-busy', 'false');
    document.querySelector('.workspace').inert = false;
    $('open-folder').disabled = false;
    document.querySelectorAll('.provider-tab').forEach(button => { button.disabled = false; });
  }
}));
