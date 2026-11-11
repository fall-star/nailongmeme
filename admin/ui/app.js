(() => {
  const state = {
    images: [],
    total: 0,
    offset: 0,
    limit: 60,
    stats: null,
    categories: {},
    allTags: {},
    current: null,
    currentIndex: -1,
    selectedTags: new Set(),
    scan: null,
    scanItems: new Map(),
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  function toast(message, isError = false) {
    const node = $('#toast');
    node.textContent = message;
    node.classList.toggle('error', isError);
    node.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove('show'), 3200);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) { /* keep status text */ }
      throw new Error(detail);
    }
    return response.json();
  }

  function splitTags(value) {
    return [...new Set(String(value || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean))];
  }

  function setApiState(ok) {
    const node = $('#api-state');
    node.textContent = ok ? 'API 正常' : 'API 不可用';
    node.classList.toggle('bad', !ok);
  }

  async function loadStats() {
    try {
      const data = await api('/api/admin/stats');
      state.stats = data;
      $('#stats').innerHTML = [
        ['全部素材', data.total],
        ['已发布', data.published],
        ['草稿', data.draft, 'accent'],
        ['无标签', data.missing_tag],
        ['最大 ID', data.max_id],
        ['下一个 ID', data.next_id],
      ].map(([label, value, cls]) => `<div class="stat ${cls || ''}"><span>${label}</span><b>${value}</b></div>`).join('');
      setApiState(true);
    } catch (error) {
      setApiState(false);
      toast(error.message, true);
    }
  }

  async function loadTags() {
    try {
      const data = await api('/api/admin/tags');
      state.categories = data.categories || {};
      state.allTags = data.tags || {};
      const select = $('#filter-tag');
      const current = select.value;
      select.innerHTML = '<option value="">全部标签</option>' + Object.keys(state.allTags)
        .sort((a, b) => state.allTags[b] - state.allTags[a] || a.localeCompare(b))
        .map((tag) => `<option value="${escapeHtml(tag)}">${escapeHtml(tag)} (${state.allTags[tag]})</option>`).join('');
      select.value = current;
      renderTagGroups();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function loadImages(reset = true) {
    if (reset) {
      state.offset = 0;
      state.images = [];
    }
    const params = new URLSearchParams({
      q: $('#filter-q').value.trim(),
      status: $('#filter-status').value,
      tag: $('#filter-tag').value,
      missing: $('#filter-missing').value,
      limit: String(state.limit),
      offset: String(state.offset),
    });
    try {
      const data = await api(`/api/admin/images?${params}`);
      state.total = data.total;
      state.images = reset ? data.items : state.images.concat(data.items);
      state.offset = state.images.length;
      renderImages();
      setApiState(true);
    } catch (error) {
      setApiState(false);
      toast(error.message, true);
    }
  }

  function renderImages() {
    $('#result-count').textContent = `共 ${state.total} 张，当前显示 ${state.images.length} 张`;
    const grid = $('#image-grid');
    if (!state.images.length) {
      grid.innerHTML = '<div class="panel empty-panel" style="grid-column:1/-1"><div class="empty-mark">◌</div><h2>没有匹配的图片</h2><p>换个筛选条件，或者去导入新图片。</p></div>';
    } else {
      grid.innerHTML = state.images.map((image) => `
        <article class="image-card" data-id="${escapeHtml(image.id)}" tabindex="0" role="button" aria-label="编辑 ${escapeHtml(image.name)}">
          <span class="card-status ${escapeHtml(image.status)}">${image.status === 'published' ? '已发布' : image.status === 'draft' ? '草稿' : '已屏蔽'}</span>
          <img loading="lazy" src="${escapeHtml(image.thumb_320_url)}" alt="">
          <div class="card-info">
            <div class="card-title">${escapeHtml(image.name || '未命名')}</div>
            <div class="card-sub"><span>#${escapeHtml(image.id)}</span><span>${escapeHtml(image.format || '')}</span></div>
            <div class="card-tags">${escapeHtml((image.tag || []).join(' · ') || '无标签')}</div>
          </div>
        </article>`).join('');
    }
    $('#load-more').hidden = state.images.length >= state.total;
  }

  function findImage(id) {
    return state.images.find((image) => String(image.id) === String(id));
  }

  function openEditor(id) {
    const index = state.images.findIndex((image) => String(image.id) === String(id));
    if (index < 0) return;
    state.currentIndex = index;
    state.current = state.images[index];
    state.selectedTags = new Set(state.current.tag || []);
    renderEditor();
    const dialog = $('#editor');
    if (!dialog.open) dialog.showModal();
  }

  function renderEditor() {
    const image = state.current;
    if (!image) return;
    $('#editor-id').textContent = `#${image.id}`;
    $('#editor-file').textContent = `${image.format || ''} · ${image.width || '?'}×${image.height || '?'}`;
    $('#editor-image').src = image.image_url;
    $('#editor-name').value = image.name || '';
    $('#editor-status').value = image.status || 'draft';
    $('#editor-meta').textContent = `${image.bytes ? Math.round(image.bytes / 1024) + ' KB' : ''} · ${image.source_type || 'manual'}`;
    $('#tag-count').textContent = `${state.selectedTags.size} 个`;
    renderSelectedTags();
    renderTagGroups();
    $('#save-state').textContent = '';
  }

  function renderSelectedTags() {
    const container = $('#selected-tags');
    container.innerHTML = [...state.selectedTags].map((tag) => `
      <span class="tag-chip">${escapeHtml(tag)}<button type="button" data-remove-tag="${escapeHtml(tag)}" aria-label="移除 ${escapeHtml(tag)}">×</button></span>
    `).join('') || '<span style="color:var(--muted);font-size:10px">还没有标签</span>';
  }

  function renderTagGroups() {
    const container = $('#tag-groups');
    if (!container) return;
    const groups = Object.entries(state.categories);
    const categorized = new Set(groups.flatMap(([, tags]) => tags));
    const extras = Object.keys(state.allTags).filter((tag) => !categorized.has(tag)).sort().slice(0, 80);
    if (extras.length) groups.push(['其他已有标签', extras]);
    container.innerHTML = groups.map(([name, tags]) => `
      <div class="tag-group"><span>${escapeHtml(name)}</span><div class="tag-options">
        ${tags.map((tag) => `<button type="button" class="tag-option ${state.selectedTags.has(tag) ? 'selected' : ''}" data-toggle-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`).join('')}
      </div></div>`).join('');
  }

  function toggleTag(tag) {
    if (state.selectedTags.has(tag)) state.selectedTags.delete(tag);
    else state.selectedTags.add(tag);
    renderSelectedTags();
    renderTagGroups();
    $('#tag-count').textContent = `${state.selectedTags.size} 个`;
  }

  async function saveEditor() {
    if (!state.current) return;
    const name = $('#editor-name').value.trim();
    if (!name) { toast('名称不能为空', true); return; }
    const button = $('#save-image');
    button.disabled = true;
    $('#save-state').textContent = '保存中…';
    try {
      const updated = await api(`/api/admin/images/${state.current.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, tag: [...state.selectedTags], status: $('#editor-status').value }),
      });
      state.images[state.currentIndex] = updated;
      state.current = updated;
      renderImages();
      await loadStats();
      await loadTags();
      renderEditor();
      $('#save-state').textContent = '已保存';
      toast('已保存');
    } catch (error) {
      $('#save-state').textContent = '';
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function navigateEditor(step) {
    if (!state.images.length) return;
    state.currentIndex = (state.currentIndex + step + state.images.length) % state.images.length;
    state.current = state.images[state.currentIndex];
    state.selectedTags = new Set(state.current.tag || []);
    renderEditor();
  }

  async function loadSubmissions() {
    const list = $('#submission-list');
    list.innerHTML = '<div class="submission-empty">加载中…</div>';
    const params = new URLSearchParams({ status: $('#submission-status').value });
    try {
      const data = await api(`/api/admin/submissions?${params}`);
      if (!data.items.length) {
        list.innerHTML = '<div class="submission-empty">暂无投稿。公开投稿接口会先把图片放进隔离区，管理员在这里审核。</div>';
        return;
      }
      list.innerHTML = data.items.map((item) => {
        const statusLabels = { pending_review: '待审核', approved: '已通过', rejected: '已拒绝', duplicate: '重复', received: '已接收' };
        const disabled = ['approved', 'rejected'].includes(item.status) ? 'disabled' : '';
        const submitter = item.user_nickname
          ? `<div class="submission-user">${item.user_avatar_url ? `<img src="${escapeHtml(item.user_avatar_url)}" alt="">` : ''}<span><b>${escapeHtml(item.user_nickname)}</b><small>${escapeHtml(item.user_email || '')}</small></span></div>`
          : '<div class="submission-user legacy">旧投稿 · 无账户信息</div>';
        return `<article class="submission-card" data-id="${escapeHtml(item.submission_id)}">
          <img src="${escapeHtml(item.preview_url)}" alt="">
          <div class="submission-body">
            ${submitter}
            <span class="submission-status ${escapeHtml(item.status)}">${statusLabels[item.status] || escapeHtml(item.status)}</span>
            <div class="submission-name">${escapeHtml(item.name || '提交者未命名')}</div>
            <div class="submission-id">${escapeHtml(item.submission_id)}</div>
            <div class="submission-meta">${escapeHtml(item.format || '')} · ${item.width || '?'}×${item.height || '?'} · ${item.bytes ? Math.round(item.bytes / 1024) + ' KB' : ''}<br>建议标签：${escapeHtml((item.tag || []).join(' · ') || '无')}${item.duplicate_id ? '<br>已有 ID：' + escapeHtml(item.duplicate_id) : ''}</div>
            <div class="submission-form">
              <input class="submission-name-input" value="${escapeHtml(item.name || '')}" placeholder="最终名称" ${disabled}>
              <input class="submission-tag-input" value="${escapeHtml((item.tag || []).join(','))}" placeholder="最终标签，逗号分隔" ${disabled}>
            </div>
            <label class="notify-row"><input type="checkbox" class="notify-user" ${disabled || !item.user_email ? 'disabled' : ''}> 通过后发邮件通知${item.user_email ? '' : '（无邮箱）'}</label>
            <div class="submission-actions">
              <button class="btn primary submission-approve" ${disabled}>通过并发布</button>
              <button class="btn submission-reject" ${disabled}>拒绝</button>
            </div>
          </div>
        </article>`;
      }).join('');
    } catch (error) {
      list.innerHTML = `<div class="submission-empty">${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadTagSuggestions() {
    const list = $('#tag-suggestion-list');
    if (!list) return;
    list.innerHTML = '<div class="submission-empty">加载中…</div>';
    try {
      const data = await api('/api/admin/tag-suggestions?status=pending_review');
      if (!data.items.length) {
        list.innerHTML = '<div class="submission-empty">暂无待审核 tag 建议。</div>';
        return;
      }
      list.innerHTML = data.items.map((item) => `
        <article class="tag-suggestion-card" data-suggestion="${escapeHtml(item.suggestion_id)}">
          <img src="${escapeHtml(item.preview_url)}" alt="">
          <div class="tag-suggestion-body">
            ${item.user_nickname ? `<div class="submission-user">${item.user_avatar_url ? `<img src="${escapeHtml(item.user_avatar_url)}" alt="">` : ''}<span><b>${escapeHtml(item.user_nickname)}</b><small>${escapeHtml(item.user_email || '')}</small></span></div>` : '<div class="submission-user legacy">旧建议 · 无账户信息</div>'}
            <div class="submission-id">#${escapeHtml(item.image_id)} · ${escapeHtml(item.image_name || '')}</div>
            <div class="suggested-tags">${(item.tags || []).map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`).join('')}</div>
            ${item.note ? `<p class="suggestion-note">${escapeHtml(item.note)}</p>` : ''}
            <label class="notify-row"><input type="checkbox" class="notify-user" ${!item.user_email ? 'disabled' : ''}> 通过后发邮件通知${item.user_email ? '' : '（无邮箱）'}</label>
            <div class="submission-actions">
              <button class="btn primary tag-approve">通过并加入本地</button>
              <button class="btn tag-reject">拒绝</button>
            </div>
          </div>
        </article>`).join('');
    } catch (error) {
      list.innerHTML = `<div class="submission-empty">${escapeHtml(error.message)}</div>`;
    }
  }

  async function approveTagSuggestion(id) {
    try {
      const card = document.querySelector(`.tag-suggestion-card[data-suggestion="${CSS.escape(id)}"]`);
      const notify = Boolean(card?.querySelector('.notify-user')?.checked);
      const result = await api(`/api/admin/tag-suggestions/${id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notify_user: notify }),
      });
      const mail = result.notification?.ok ? '，通知邮件已发送' : (notify ? '，但通知邮件失败' : '');
      toast(`tag 已加入本地 catalog${mail}，记得点击“同步到服务器”`);
      await loadTagSuggestions();
      await loadStats();
      await loadTags();
      await loadImages();
      console.debug('approved suggestion', result);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function rejectTagSuggestion(id) {
    const reason = window.prompt('拒绝原因（可留空）', '');
    if (reason === null) return;
    try {
      await api(`/api/admin/tag-suggestions/${id}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });
      toast('已拒绝 tag 建议');
      await loadTagSuggestions();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function pullInbox() {
    const button = $('#pull-inbox');
    button.disabled = true;
    button.textContent = '拉取中…';
    try {
      const result = await api('/api/admin/inbox/pull', { method: 'POST' });
      toast(`已拉取：${result.new_submissions} 个投稿，${result.new_tag_suggestions} 个 tag 建议`);
      await loadSubmissions();
      await loadTagSuggestions();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '拉取线上投稿';
    }
  }

  async function approveSubmission(id) {
    const card = document.querySelector(`.submission-card[data-id="${CSS.escape(id)}"]`);
    if (!card) return;
    const name = card.querySelector('.submission-name-input').value.trim();
    const tag = splitTags(card.querySelector('.submission-tag-input').value);
    if (!name) { toast('名称不能为空', true); return; }
    const notify = Boolean(card.querySelector('.notify-user')?.checked);
    try {
      const result = await api(`/api/admin/submissions/${id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, tag, publish: true, notify_user: notify }),
      });
      const mail = result.notification?.ok ? '，通知邮件已发送' : (notify ? '，但通知邮件失败' : '');
      toast(result.status === 'approved' ? `已通过，catalog ID ${result.catalog_id}${mail}` : '发现重复，已标记');
      await loadSubmissions();
      await loadStats();
      await loadTags();
      await loadImages();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function rejectSubmission(id) {
    const reason = window.prompt('拒绝原因（可留空）', '');
    if (reason === null) return;
    try {
      await api(`/api/admin/submissions/${id}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });
      toast('已拒绝');
      await loadSubmissions();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function switchView(name) {
    $$('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.view === name));
    $$('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
    if (name === 'library') { loadStats(); loadImages(); }
    if (name === 'import') { loadStats(); loadTags(); loadSavedScans(); }
    if (name === 'submissions') { loadSubmissions(); loadTagSuggestions(); }
  }

  function showScan(data) {
    state.scan = data;
    state.scanItems = new Map(data.items.map((item) => [item.token, item]));
    const panel = $('#scan-panel');
    panel.hidden = false;
    const ready = data.items.filter((item) => item.status === 'ready').length;
    const duplicates = data.items.filter((item) => item.status === 'duplicate').length;
    const invalid = data.items.filter((item) => item.status === 'invalid').length;
    $('#scan-summary').textContent = `${data.count} 个文件 · ${ready} 可导入 · ${duplicates} 重复 · ${invalid} 无效`;
    $('#scan-body').innerHTML = data.items.map((item) => {
      const disabled = item.status !== 'ready';
      const tags = (item.tag || []).join(',');
      return `<tr data-token="${escapeHtml(item.token)}">
        <td><input type="checkbox" class="scan-select" ${disabled ? 'disabled' : 'checked'}></td>
        <td>${item.status === 'ready' ? `<img src="${escapeHtml(item.preview_url)}" alt="">` : '<span class="badge invalid">—</span>'}</td>
        <td><div class="file-name" title="${escapeHtml(item.relative_path)}">${escapeHtml(item.relative_path)}</div><small>${escapeHtml(item.format || '')} ${item.width || ''}${item.height ? '×' + item.height : ''}</small></td>
        <td><input class="scan-name" value="${escapeHtml(item.name || '')}" ${disabled ? 'disabled' : ''}></td>
        <td><input class="scan-tags" value="${escapeHtml(tags)}" placeholder="奶蛙,大笑" ${disabled ? 'disabled' : ''}></td>
        <td><span class="badge ${escapeHtml(item.status)}">${item.status === 'ready' ? '可导入' : item.status === 'duplicate' ? '重复' : '无效'}</span>${item.reason ? `<small title="${escapeHtml(item.reason)}"> ${escapeHtml(item.reason.slice(0, 18))}</small>` : ''}</td>
        <td><button type="button" class="scan-delete" data-delete-token="${escapeHtml(item.token)}" title="从本次扫描删除">删除</button></td>
      </tr>`;
    }).join('');
    $('#commit-result').hidden = true;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function syncServer() {
    const button = $('#sync-server');
    button.disabled = true;
    button.textContent = '检查中…';
    try {
      const preview = await api('/api/admin/sync/preview');
      const lines = [
        `本地已发布 ${preview.local_public} 张，生产公开 ${preview.production_public} 张。`,
        `新增公开图片：${preview.new_public_count} 张`,
        `元数据变化：${preview.changed_public_count} 条`,
        `草稿：${preview.drafts} 张（只同步元数据，不上传草稿图片）`,
        `SSH：${preview.ssh_ok ? '已连接' : '不可用'}`,
        '',
        '将重新构建并替换生产前端，并重启生产 API。确定同步？',
      ];
      if (!window.confirm(lines.join('\n'))) return;
      button.textContent = '同步中…';
      const result = await api('/api/admin/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      });
      toast(`同步完成：${result.new_public_ids.length} 张新图，${result.changed_public_count} 条元数据变化`);
      await loadStats();
      await loadTags();
      await loadImages();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '同步到服务器';
    }
  }

  async function loadSavedScans() {
    const box = $('#saved-scans');
    try {
      const data = await api('/api/admin/scans');
      if (!data.items.length) { box.hidden = true; box.innerHTML = ''; return; }
      box.hidden = false;
      box.innerHTML = '<b>未完成的导入</b>' + data.items.map((scan) => `
        <button class="saved-scan" data-scan="${escapeHtml(scan.scan_id)}">
          ${escapeHtml(scan.source_dir || '单张上传')} · 可导入 ${scan.ready}/${scan.count}
        </button>`).join('');
    } catch (_) { box.hidden = true; }
  }

  async function uploadSingle(file) {
    if (!file) return;
    try {
      const response = await fetch(`/api/admin/upload?filename=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        body: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || response.statusText);
      }
      const data = await response.json();
      showScan(data);
      toast('已读取图片，确认名称和标签后导入');
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function scanFolder() {
    const path = $('#folder-path').value.trim();
    if (!path) { toast('请填写文件夹路径', true); return; }
    const button = $('#scan-folder');
    button.disabled = true;
    button.textContent = '扫描中…';
    try {
      const data = await api('/api/admin/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, recursive: $('#recursive').checked }),
      });
      showScan(data);
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '扫描';
    }
  }

  async function commitScan() {
    if (!state.scan) return;
    const rows = $$('#scan-body tr');
    const items = [];
    rows.forEach((row) => {
      const checkbox = row.querySelector('.scan-select');
      if (!checkbox || !checkbox.checked || checkbox.disabled) return;
      const token = row.dataset.token;
      const source = state.scanItems.get(token);
      items.push({
        token,
        name: row.querySelector('.scan-name').value.trim() || source.name,
        tag: splitTags(row.querySelector('.scan-tags').value),
      });
    });
    if (!items.length) { toast('先选择至少一张可导入图片', true); return; }
    const button = $('#commit-scan');
    button.disabled = true;
    button.textContent = '导入中…';
    try {
      const result = await api(`/api/admin/scans/${state.scan.scan_id}/commit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      const panel = $('#commit-result');
      panel.hidden = false;
      panel.innerHTML = `已导入 ${result.imported.length} 张草稿${result.failed.length ? `，失败 ${result.failed.length} 张：${result.failed.map((item) => escapeHtml(item.error)).join('；')}` : ''}。
        <br>去素材库补名称和标签，确认后把状态改为「已发布」。`;
      state.scan = null;
      await loadStats();
      await loadTags();
      await loadImages();
      toast(`已导入 ${result.imported.length} 张草稿`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '导入选中项';
    }
  }

  async function deleteScanItem(token) {
    if (!state.scan) return;
    try {
      const scanId = state.scan.scan_id;
      const result = await api(`/api/admin/scans/${scanId}/items/${encodeURIComponent(token)}`, { method: 'DELETE' });
      state.scanItems.delete(token);
      const row = document.querySelector(`#scan-body tr[data-token="${CSS.escape(token)}"]`);
      if (row) row.remove();
      if (!result.remaining) {
        state.scan = null;
        $('#scan-panel').hidden = true;
        $('#saved-scans').hidden = true;
        toast('已删除，本次扫描没有候选图片');
      } else {
        state.scan.items = result.items;
        $('#scan-summary').textContent = `${result.items.length} 个文件 · ${result.items.filter((item) => item.status === 'ready').length} 可导入`;
        toast('已从本次扫描删除');
      }
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function pickFolder() {
    const button = $('#pick-folder');
    button.disabled = true;
    button.textContent = '选择中…';
    try {
      const result = await api('/api/admin/choose-folder', { method: 'POST' });
      if (!result.cancelled && result.path) {
        $('#folder-path').value = result.path;
        await scanFolder();
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '选择文件夹';
    }
  }

  function applyCommonTags() {
    const tags = splitTags($('#common-tags').value);
    if (!tags.length) return;
    $$('#scan-body tr').forEach((row) => {
      const input = row.querySelector('.scan-tags');
      if (!input || input.disabled) return;
      input.value = [...new Set([...splitTags(input.value), ...tags])].join(',');
    });
  }

  function bindEvents() {
    $$('.tab').forEach((tab) => tab.addEventListener('click', () => switchView(tab.dataset.view)));
    $('#jump-import').addEventListener('click', () => switchView('import'));
    $('#reload-library').addEventListener('click', () => { loadStats(); loadTags(); loadImages(); });
    let filterTimer;
    $('#filter-q').addEventListener('input', () => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(() => loadImages(), 240);
    });
    ['#filter-status', '#filter-tag', '#filter-missing'].forEach((selector) => $(selector).addEventListener('change', () => loadImages()));
    $('#load-more').addEventListener('click', () => loadImages(false));
    $('#image-grid').addEventListener('click', (event) => {
      const card = event.target.closest('.image-card');
      if (card) openEditor(card.dataset.id);
    });
    $('#image-grid').addEventListener('keydown', (event) => {
      const card = event.target.closest('.image-card');
      if (card && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        openEditor(card.dataset.id);
      }
    });
    $('#close-editor').addEventListener('click', () => $('#editor').close());
    $('#editor').addEventListener('close', () => { state.current = null; });
    $('#save-image').addEventListener('click', saveEditor);
    $('#prev-image').addEventListener('click', () => navigateEditor(-1));
    $('#next-image').addEventListener('click', () => navigateEditor(1));
    $('#selected-tags').addEventListener('click', (event) => {
      const button = event.target.closest('[data-remove-tag]');
      if (button) toggleTag(button.dataset.removeTag);
    });
    $('#tag-groups').addEventListener('click', (event) => {
      const button = event.target.closest('[data-toggle-tag]');
      if (button) toggleTag(button.dataset.toggleTag);
    });
    function addCustomTag() {
      const input = $('#custom-tag');
      const tag = input.value.trim();
      if (!tag) return;
      toggleTag(tag);
      input.value = '';
    }
    $('#add-tag').addEventListener('click', addCustomTag);
    $('#custom-tag').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); addCustomTag(); }
    });
    $('#saved-scans').addEventListener('click', async (event) => {
      const button = event.target.closest('[data-scan]');
      if (!button) return;
      try {
        const data = await api(`/api/admin/scans/${button.dataset.scan}`);
        showScan(data);
      } catch (error) {
        toast(error.message, true);
      }
    });
    $('#sync-server').addEventListener('click', syncServer);
    $('#single-file').addEventListener('change', (event) => uploadSingle(event.target.files[0]));
    $('#pick-folder').addEventListener('click', pickFolder);
    $('#scan-folder').addEventListener('click', scanFolder);
    $('#scan-body').addEventListener('click', (event) => {
      const button = event.target.closest('[data-delete-token]');
      if (button) deleteScanItem(button.dataset.deleteToken);
    });
    $('#folder-path').addEventListener('keydown', (event) => { if (event.key === 'Enter') scanFolder(); });
    $('#apply-common-tags').addEventListener('click', applyCommonTags);
    $('#commit-scan').addEventListener('click', commitScan);
    $('#refresh-search').addEventListener('click', async () => {
      const button = $('#refresh-search');
      button.disabled = true;
      button.textContent = '刷新中…';
      try {
        const result = await api('/api/admin/search/reload', { method: 'POST' });
        toast(`搜索已刷新，API ${result.version || ''}`);
      } catch (error) {
        toast(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = '刷新搜索';
      }
    });
    $('#sync-data').addEventListener('click', async () => {
      const button = $('#sync-data');
      button.disabled = true;
      button.textContent = '同步中…';
      try {
        await api('/api/admin/sync-data', { method: 'POST' });
        toast('前台 gallery.json 已刷新；开发服务器会自动使用新数据');
      } catch (error) {
        toast(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = '同步前台数据';
      }
    });
    $('#reload-submissions').addEventListener('click', loadSubmissions);
    $('#reload-tag-suggestions').addEventListener('click', loadTagSuggestions);
    $('#pull-inbox').addEventListener('click', pullInbox);
    $('#tag-suggestion-list').addEventListener('click', (event) => {
      const card = event.target.closest('.tag-suggestion-card');
      if (!card) return;
      if (event.target.closest('.tag-approve')) approveTagSuggestion(card.dataset.suggestion);
      if (event.target.closest('.tag-reject')) rejectTagSuggestion(card.dataset.suggestion);
    });
    $('#submission-status').addEventListener('change', loadSubmissions);
    $('#submission-list').addEventListener('click', (event) => {
      const card = event.target.closest('.submission-card');
      if (!card) return;
      if (event.target.closest('.submission-approve')) approveSubmission(card.dataset.id);
      if (event.target.closest('.submission-reject')) rejectSubmission(card.dataset.id);
    });
    document.addEventListener('keydown', (event) => {
      if (!$('#editor').open) return;
      if (event.target.matches('input, textarea, select')) return;
      if (event.key === 'ArrowLeft') navigateEditor(-1);
      if (event.key === 'ArrowRight') navigateEditor(1);
      if (event.key === 'Escape') $('#editor').close();
    });
  }

  bindEvents();
  loadStats();
  loadTags();
  loadImages();
})();
