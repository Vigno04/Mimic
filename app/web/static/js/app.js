let currentTab = 'dashboard';
let charts = {};
let allEndpoints = [];
let allBots = [];

function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

function jsEsc(s) {
  if (!s) return '';
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, '\\\'').replace(/"/g, '&quot;').replace(/\n/g, '\\n').replace(/\r/g, '');
}

function switchTab(tabId) {
  currentTab = tabId;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-content').forEach(el => {
    el.style.display = el.id === `tab-${tabId}` ? 'block' : 'none';
  });
  if (tabId === 'dashboard') loadDashboardStats();
  if (tabId === 'bots') loadBots();
  if (tabId === 'endpoints') loadEndpoints();
  if (tabId === 'memories') loadMemories();
  if (tabId === 'playground') initPlayground();
  if (tabId === 'analytics') loadAnalytics();
  if (tabId === 'tools') loadToolSettings();
  if (tabId === 'logs') { loadLogs(); connectLogsSSE(); } else { disconnectLogsSSE(); }
}

function openModal(id) { document.getElementById(id)?.classList.add('active'); }
function closeModal(id) { document.getElementById(id)?.classList.remove('active'); }

// ── Dashboard ──

async function loadDashboardStats() {
  try {
    const data = await (await fetch('/api/stats')).json();
    const s = data.summary;
    document.getElementById('stat-active-bots').textContent = `${s.active_bots}/${s.total_bots}`;
    document.getElementById('stat-total-tokens').textContent = Number(s.total_tokens).toLocaleString();
    document.getElementById('stat-total-messages').textContent = Number(s.total_messages).toLocaleString();
    document.getElementById('stat-total-memories').textContent = s.total_memories;
    renderCharts(data);
    renderAudits(data.recent_audits || []);
  } catch (e) { console.error(e); }
}

function renderCharts(data) {
  const palette = ['#3b82f6','#8b5cf6','#ec4899','#22c55e','#eab308','#ef4444'];

  const ctx1 = document.getElementById('chart-tokens-model')?.getContext('2d');
  if (ctx1) {
    if (charts.m) charts.m.destroy();
    const labels = data.tokens_by_model.map(m => m.model);
    const values = data.tokens_by_model.map(m => m.tokens);
    charts.m = new Chart(ctx1, {
      type: 'doughnut',
      data: {
        labels: labels.length ? labels : ['No data'],
        datasets: [{ data: values.length ? values : [1], backgroundColor: palette, borderWidth: 0 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '70%',
        plugins: { legend: { position: 'bottom', labels: { color: '#71717a', font: { size: 11, family: 'Inter' }, padding: 12, usePointStyle: true, pointStyleWidth: 8 } } }
      }
    });
  }

  const ctx2 = document.getElementById('chart-tool-calls')?.getContext('2d');
  if (ctx2) {
    if (charts.t) charts.t.destroy();
    const names = Object.keys(data.tool_counts || {});
    const counts = Object.values(data.tool_counts || {});
    charts.t = new Chart(ctx2, {
      type: 'bar',
      data: {
        labels: names.length ? names : ['None'],
        datasets: [{ data: counts.length ? counts : [0], backgroundColor: '#3b82f6', borderRadius: 4, barPercentage: 0.6 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#52525b', font: { size: 10, family: 'JetBrains Mono' } }, grid: { display: false }, border: { color: '#27272a' } },
          y: { ticks: { color: '#52525b', font: { size: 10 } }, grid: { color: '#1f1f23' }, border: { display: false }, beginAtZero: true }
        }
      }
    });
  }
}

function renderAudits(audits) {
  const el = document.getElementById('recent-audits-list');
  if (!el) return;
  if (!audits.length) { el.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-3);">No activity yet.</td></tr>'; return; }
  el.innerHTML = audits.map(a => `<tr>
    <td><code>${(a.timestamp||'').split('T')[1]?.slice(0,8)||''}</code></td>
    <td>${a.model_used||'—'}</td>
    <td><code>${a.total_tokens||0}</code></td>
    <td>${a.refused ? '<span class="badge badge-error">refuse</span>' : '<span class="badge badge-online">ok</span>'}</td>
    <td><code>${(a.tools_called||[]).map(t=>t.name).join(', ')||'—'}</code></td>
    <td><code>${a.user_id||'—'}</code></td>
  </tr>`).join('');
}

// ── Endpoints ──

async function loadEndpoints() {
  try {
    allEndpoints = await (await fetch('/api/endpoints')).json();
    const c = document.getElementById('endpoints-cards-container');
    if (!c) return;
    if (!allEndpoints.length) { c.innerHTML = '<p style="color:var(--text-3);">No endpoints configured.</p>'; return; }
    c.innerHTML = allEndpoints.map(ep => `<div class="endpoint-card">
      <div class="card-header">
        <div>
          <div class="card-title">${ep.name}</div>
          <div style="font-size:12px;color:var(--text-2);margin-top:2px;font-family:var(--mono);">${ep.model_name}</div>
        </div>
        <div style="display:flex;gap:4px;">
          <span class="badge badge-provider">${ep.provider}</span>
          ${ep.is_global_fallback ? '<span class="badge badge-global">fallback</span>' : ''}
        </div>
      </div>
      <div class="card-details">
        <div class="card-detail-row"><span>Base URL</span><code style="font-size:12px;">${ep.base_url||'default'}</code></div>
        <div class="card-detail-row"><span>API Key</span><span>${ep.api_key?'••••••••':'none'}</span></div>
        <div class="card-detail-row"><span>Status</span><span id="ep-st-${ep.id}"><span class="badge badge-offline">untested</span></span></div>
      </div>
      <div class="card-actions">
        <button class="btn btn-sm btn-secondary" onclick="testEndpoint('${ep.id}')">Test</button>
        <button class="btn btn-sm btn-secondary" onclick="editEndpoint('${ep.id}')">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteEndpoint('${ep.id}')">Delete</button>
      </div>
    </div>`).join('');
  } catch (e) { console.error(e); }
}

async function testEndpoint(id) {
  const el = document.getElementById(`ep-st-${id}`);
  if (el) el.innerHTML = '<span class="badge badge-offline">testing…</span>';
  try {
    const d = await (await fetch(`/api/endpoints/${id}/test`,{method:'POST'})).json();
    if (d.status==='online') { el.innerHTML=`<span class="badge badge-online">${d.latency_ms}ms</span>`; showToast(`Online · ${d.latency_ms}ms`); }
    else { el.innerHTML=`<span class="badge badge-error">error</span>`; showToast(d.message,'error'); }
  } catch(e) { if(el) el.innerHTML='<span class="badge badge-error">offline</span>'; showToast('Connection error','error'); }
}

function openCreateEndpointModal() {
  document.getElementById('form-endpoint').reset();
  document.getElementById('endpoint-id').value='';
  document.getElementById('modal-endpoint-title').textContent='New Endpoint';
  openModal('modal-endpoint');
}

function editEndpoint(id) {
  const ep=allEndpoints.find(e=>e.id===id); if(!ep)return;
  document.getElementById('endpoint-id').value=ep.id;
  document.getElementById('endpoint-name').value=ep.name;
  document.getElementById('endpoint-provider').value=ep.provider;
  document.getElementById('endpoint-base-url').value=ep.base_url||'';
  document.getElementById('endpoint-api-key').value=ep.api_key||'';
  document.getElementById('endpoint-model-name').value=ep.model_name;
  document.getElementById('endpoint-global-fallback').checked=ep.is_global_fallback;
  document.getElementById('modal-endpoint-title').textContent='Edit Endpoint';
  openModal('modal-endpoint');
}

async function saveEndpoint(e) {
  e.preventDefault();
  const id=document.getElementById('endpoint-id').value;
  const p={name:document.getElementById('endpoint-name').value,provider:document.getElementById('endpoint-provider').value,base_url:document.getElementById('endpoint-base-url').value||null,api_key:document.getElementById('endpoint-api-key').value||null,model_name:document.getElementById('endpoint-model-name').value,is_global_fallback:document.getElementById('endpoint-global-fallback').checked};
  try {
    const r=await fetch(id?`/api/endpoints/${id}`:'/api/endpoints/',{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    if(!r.ok)throw new Error(await r.text());
    showToast('Endpoint saved'); closeModal('modal-endpoint'); loadEndpoints();
  }catch(e){showToast(e.message,'error');}
}

async function deleteEndpoint(id) {
  if(!confirm('Delete this endpoint?'))return;
  try{await fetch(`/api/endpoints/${id}`,{method:'DELETE'}); showToast('Deleted'); loadEndpoints();}catch(e){showToast(e.message,'error');}
}

// ── Bots ──

async function loadBots() {
  try {
    [allBots, allEndpoints] = await Promise.all([(await fetch('/api/bots')).json(), (await fetch('/api/endpoints')).json()]);
    const c = document.getElementById('bots-cards-container');
    if (!c) return;
    if (!allBots.length) {
      c.innerHTML = '<p style="color:var(--text-3);">No bots configured.</p>';
      return;
    }
    c.innerHTML = allBots.map(b => {
      const on = b.live_status && b.live_status.online;
      const st = on ? '<span class="badge badge-online">online</span>' : (b.live_status?.last_error ? '<span class="badge badge-error">error</span>' : '<span class="badge badge-offline">offline</span>');
      const chain = (b.endpoint_chain || []).map(id => {
        const e = allEndpoints.find(x => x.id === id);
        return e ? e.name : id.slice(0, 8);
      }).join(' → ') || '—';

      const triggers = b.triggers || [];
      const triggersHtml = triggers.length ? triggers.map(t => {
        let label = t.type;
        if (t.type === 'command') label = t.pattern || `/${b.name.toLowerCase()}`;
        else if (t.type === 'keywords') label = t.pattern ? `"${t.pattern}"` : 'keywords';
        else if (t.type === 'mention') label = `@${b.name}`;
        else if (t.type === 'reply_to_bot' || t.type === 'reply') label = `Reply to ${b.name}`;
        else if (t.type === 'always') label = `always`;

        const polBadge = t.reply_policy === 'mandatory'
          ? '<span class="badge badge-online">always</span>'
          : (t.reply_policy === 'passive' ? '<span class="badge badge-offline">passive</span>' : '<span class="badge badge-provider">ai_choice</span>');

        return `<div style="display:flex; justify-content:space-between; align-items:center; padding:2px 0;">
          <span style="font-size:12px;"><code style="font-size:11px;">${esc(label)}</code></span>
          ${polBadge}
        </div>`;
      }).join('') : '<span style="color:var(--text-3); font-size:12px;">No triggers</span>';

      return `<div class="bot-card">
        <div class="card-header">
          <div>
            <div class="card-title">${esc(b.name)}</div>
            <div style="font-size:12px; color:var(--text-2); margin-top:2px;">${triggers.length} trigger rule(s)</div>
          </div>
          ${st}
        </div>
        <div class="card-details">
          <div class="card-detail-row"><span>Chain</span><span style="max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px;">${chain}</span></div>
          <div style="padding:6px 0; border-bottom:1px solid var(--border);">
            <div style="font-size:11px; color:var(--text-2); margin-bottom:4px; text-transform:uppercase; letter-spacing:0.4px;">Triggers & Policies</div>
            ${triggersHtml}
          </div>
          <div class="card-detail-row"><span>Memory</span><span>${b.memory_mode}</span></div>
          <div class="card-detail-row"><span>Cooldown</span><span>${b.cooldown_seconds}s</span></div>
          ${b.live_status?.last_error ? `
            <div class="bot-error-banner">
              <div style="font-size:11px; font-weight:600; color:var(--red); display:flex; align-items:center; gap:4px; margin-bottom:2px;">
                <span>⚠️</span><span>Error</span>
              </div>
              <div style="font-size:11px; color:var(--text-1); line-height:1.4;">
                ${esc(b.live_status.last_error)}
              </div>
              ${b.live_status.last_error.toLowerCase().includes('intent') ? `
                <a href="https://discord.com/developers/applications" target="_blank" rel="noreferrer" class="btn btn-sm btn-secondary" style="margin-top:6px; font-size:11px; padding:3px 8px; display:inline-flex; align-items:center; gap:4px;">
                  Open Discord Portal ↗
                </a>
              ` : ''}
            </div>
          ` : ''}
        </div>
        <div class="card-actions">
          ${on ? `<button class="btn btn-sm btn-danger" onclick="stopBot('${b.id}')">Stop</button><button class="btn btn-sm btn-secondary" onclick="restartBot('${b.id}')">Restart</button>` : `<button class="btn btn-sm btn-success" onclick="startBot('${b.id}')">Start</button>`}
          <button class="btn btn-sm btn-secondary" onclick="editBot('${b.id}')">Edit</button>
          <button class="btn btn-sm btn-danger" onclick="deleteBot('${b.id}')">Delete</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) { console.error(e); }
}

async function startBot(id) {
  try {
    const d = await (await fetch(`/api/bots/${id}/start`, { method: 'POST' })).json();
    showToast(d.status === 'started' || d.status === 'already_running' ? 'Bot started' : d.message, d.status === 'error' ? 'error' : 'success');
    setTimeout(loadBots, 1000);
  } catch (e) { showToast(e.message, 'error'); }
}

async function stopBot(id) {
  try {
    await fetch(`/api/bots/${id}/stop`, { method: 'POST' });
    showToast('Bot stopped');
    loadBots();
  } catch (e) { showToast(e.message, 'error'); }
}

async function restartBot(id) {
  showToast('Restarting…');
  try {
    await fetch(`/api/bots/${id}/restart`, { method: 'POST' });
    setTimeout(loadBots, 1500);
  } catch (e) { showToast(e.message, 'error'); }
}

function getActiveTriggerContainerId() {
  return currentBotFormMode === 'classic' ? 'bot-triggers-container-classic' : 'bot-triggers-container';
}

function getTriggersFromContainer(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return [];
  const rows = container.querySelectorAll('.trigger-rule-row');
  return Array.from(rows).map(row => ({
    type: row.querySelector('.trigger-rule-type')?.value || 'keywords',
    pattern: row.querySelector('.trigger-rule-pattern')?.value?.trim() || '',
    case_sensitive: row.querySelector('.trigger-rule-case')?.checked || false,
    reply_policy: row.querySelector('.trigger-rule-policy')?.value || 'ai_choice'
  }));
}

function renderTriggersToContainer(containerId, triggers) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  triggers.forEach(t => addTriggerRuleRow(t, containerId));
}

function addTriggerRuleRow(rule = {}, containerOrId = null) {
  let targetContainer = null;
  if (typeof containerOrId === 'string') {
    targetContainer = document.getElementById(containerOrId);
  } else if (containerOrId && containerOrId.nodeType) {
    targetContainer = containerOrId;
  }
  if (!targetContainer) {
    targetContainer = document.getElementById(getActiveTriggerContainerId()) || document.getElementById('bot-triggers-container');
  }
  if (!targetContainer) return;

  const row = document.createElement('div');
  row.className = 'trigger-rule-row';

  const typeVal = rule.type || 'keywords';
  const patternVal = rule.pattern !== undefined ? rule.pattern : '';
  const caseVal = !!rule.case_sensitive;
  const policyVal = rule.reply_policy || 'ai_choice';

  row.innerHTML = `
    <div class="trigger-rule-header">
      <select class="trigger-rule-type" style="width:160px;" onchange="onTriggerRuleTypeChange(this)">
        <option value="command" ${typeVal === 'command' ? 'selected' : ''}>Command (/)</option>
        <option value="reply_to_bot" ${typeVal === 'reply_to_bot' || typeVal === 'reply' ? 'selected' : ''}>Reply to Bot</option>
        <option value="keywords" ${typeVal === 'keywords' ? 'selected' : ''}>Keywords</option>
        <option value="mention" ${typeVal === 'mention' ? 'selected' : ''}>@Mention</option>
        <option value="follow_up" ${typeVal === 'follow_up' ? 'selected' : ''}>Follow Up (Conversation)</option>
        <option value="always" ${typeVal === 'always' ? 'selected' : ''}>Always</option>
      </select>
      <input type="text" class="trigger-rule-pattern" placeholder="${typeVal === 'command' ? 'e.g. /jarvis or !ask' : typeVal === 'follow_up' ? 'e.g. 3' : 'e.g. jarvis, ai'}" value="${esc(patternVal)}" style="flex:1; display:${(typeVal === 'command' || typeVal === 'keywords' || typeVal === 'follow_up') ? 'block' : 'none'}; font-size:12px;">
      <button type="button" class="btn btn-sm btn-danger" onclick="this.closest('.trigger-rule-row').remove()" style="padding:4px 8px;">✕</button>
    </div>
    <div class="trigger-rule-footer">
      <label class="checkbox-label trigger-rule-case-label" style="display:${typeVal === 'keywords' ? 'flex' : 'none'};">
        <input type="checkbox" class="trigger-rule-case" ${caseVal ? 'checked' : ''}>
        <span style="font-size:11px;">Case sensitive</span>
      </label>
      <div style="display:flex; align-items:center; gap:6px; margin-left:auto;">
        <span style="font-size:11px; color:var(--text-2);">Reply policy:</span>
        <select class="trigger-rule-policy" style="font-size:12px; padding:3px 6px;">
          <option value="mandatory" ${policyVal === 'mandatory' ? 'selected' : ''}>Mandatory (Risponde sempre)</option>
          <option value="ai_choice" ${policyVal === 'ai_choice' ? 'selected' : ''}>AI Choice (Può rifiutare [REFUSE])</option>
          <option value="passive" ${policyVal === 'passive' ? 'selected' : ''}>Passive (Solo log)</option>
        </select>
      </div>
    </div>
  `;
  targetContainer.appendChild(row);
}

function onTriggerRuleTypeChange(selectEl) {
  const row = selectEl.closest('.trigger-rule-row');
  const type = selectEl.value;
  const patternInput = row.querySelector('.trigger-rule-pattern');
  const caseLabel = row.querySelector('.trigger-rule-case-label');
  if (type === 'command') {
    patternInput.style.display = 'block';
    patternInput.placeholder = 'e.g. /jarvis or !ask';
    caseLabel.style.display = 'none';
  } else if (type === 'keywords') {
    patternInput.style.display = 'block';
    patternInput.placeholder = 'e.g. jarvis, ai';
    caseLabel.style.display = 'flex';
  } else if (type === 'follow_up') {
    patternInput.style.display = 'block';
    patternInput.placeholder = 'e.g. 3 (number of messages)';
    caseLabel.style.display = 'none';
  } else {
    patternInput.style.display = 'none';
    caseLabel.style.display = 'none';
  }
}

let currentWizardStep = 1;
let currentBotFormMode = 'wizard';

function openGuideModal() {
  openModal('modal-guide');
}

function toggleBotFormMode(targetMode) {
  if (targetMode) {
    currentBotFormMode = targetMode;
  } else {
    currentBotFormMode = currentBotFormMode === 'wizard' ? 'classic' : 'wizard';
  }

  const wizView = document.getElementById('bot-wizard-view');
  const classicView = document.getElementById('bot-classic-view');
  const btnToggle = document.getElementById('btn-toggle-bot-mode');
  const badge = document.getElementById('modal-bot-mode-badge');

  if (currentBotFormMode === 'wizard') {
    syncClassicToWizardAll();
    if (wizView) wizView.style.display = 'block';
    if (classicView) classicView.style.display = 'none';
    if (btnToggle) btnToggle.textContent = '⚡ Skip Tutorial (Classic Form)';
    if (badge) { badge.textContent = 'Guided Tutorial'; badge.className = 'badge badge-provider'; }
  } else {
    syncWizardToClassicAll();
    if (wizView) wizView.style.display = 'none';
    if (classicView) classicView.style.display = 'block';
    if (btnToggle) btnToggle.textContent = '✨ Guided Setup (Tutorial)';
    if (badge) { badge.textContent = 'Classic Form'; badge.className = 'badge badge-offline'; }
  }
}

function syncWizardToClassicAll() {
  const name = document.getElementById('bot-name')?.value || '';
  const token = document.getElementById('bot-token')?.value || '';
  const prompt = document.getElementById('bot-system-prompt')?.value || '';
  const memMode = document.getElementById('bot-memory-mode')?.value || 'recent_active';
  const activeUsers = document.getElementById('bot-active-users-count')?.value || 5;
  const recentMsgs = document.getElementById('bot-recent-messages-count')?.value || 15;
  const cooldown = document.getElementById('bot-cooldown')?.value || 3;
  const ignoreBots = document.getElementById('bot-ignore-bots')?.checked ?? true;
  const maxReplies = document.getElementById('bot-max-bot-replies')?.value || 1;
  const enabledCh = document.getElementById('bot-enabled-channels')?.value || '';
  const blackCh = document.getElementById('bot-blacklisted-channels')?.value || '';
  const blackUsers = document.getElementById('bot-blacklisted-users')?.value || '';

  if (document.getElementById('bot-name-classic')) document.getElementById('bot-name-classic').value = name;
  if (document.getElementById('bot-token-classic')) document.getElementById('bot-token-classic').value = token;
  if (document.getElementById('bot-system-prompt-classic')) document.getElementById('bot-system-prompt-classic').value = prompt;
  if (document.getElementById('bot-memory-mode-classic')) document.getElementById('bot-memory-mode-classic').value = memMode;
  if (document.getElementById('bot-active-users-count-classic')) document.getElementById('bot-active-users-count-classic').value = activeUsers;
  if (document.getElementById('bot-recent-messages-count-classic')) document.getElementById('bot-recent-messages-count-classic').value = recentMsgs;
  if (document.getElementById('bot-cooldown-classic')) document.getElementById('bot-cooldown-classic').value = cooldown;
  if (document.getElementById('bot-ignore-bots-classic')) document.getElementById('bot-ignore-bots-classic').checked = ignoreBots;
  if (document.getElementById('bot-max-bot-replies-classic')) document.getElementById('bot-max-bot-replies-classic').value = maxReplies;
  if (document.getElementById('bot-enabled-channels-classic')) document.getElementById('bot-enabled-channels-classic').value = enabledCh;
  if (document.getElementById('bot-blacklisted-channels-classic')) document.getElementById('bot-blacklisted-channels-classic').value = blackCh;
  if (document.getElementById('bot-blacklisted-users-classic')) document.getElementById('bot-blacklisted-users-classic').value = blackUsers;

  // Sync triggers
  const triggers = getTriggersFromContainer('bot-triggers-container');
  renderTriggersToContainer('bot-triggers-container-classic', triggers);
}

function syncClassicToWizardAll() {
  const name = document.getElementById('bot-name-classic')?.value || '';
  const token = document.getElementById('bot-token-classic')?.value || '';
  const prompt = document.getElementById('bot-system-prompt-classic')?.value || '';
  const memMode = document.getElementById('bot-memory-mode-classic')?.value || 'recent_active';
  const activeUsers = document.getElementById('bot-active-users-count-classic')?.value || 5;
  const recentMsgs = document.getElementById('bot-recent-messages-count-classic')?.value || 15;
  const cooldown = document.getElementById('bot-cooldown-classic')?.value || 3;
  const ignoreBots = document.getElementById('bot-ignore-bots-classic')?.checked ?? true;
  const maxReplies = document.getElementById('bot-max-bot-replies-classic')?.value || 1;
  const enabledCh = document.getElementById('bot-enabled-channels-classic')?.value || '';
  const blackCh = document.getElementById('bot-blacklisted-channels-classic')?.value || '';
  const blackUsers = document.getElementById('bot-blacklisted-users-classic')?.value || '';

  if (document.getElementById('bot-name')) document.getElementById('bot-name').value = name;
  if (document.getElementById('bot-token')) document.getElementById('bot-token').value = token;
  if (document.getElementById('bot-system-prompt')) document.getElementById('bot-system-prompt').value = prompt;
  if (document.getElementById('bot-memory-mode')) document.getElementById('bot-memory-mode').value = memMode;
  if (document.getElementById('bot-active-users-count')) document.getElementById('bot-active-users-count').value = activeUsers;
  if (document.getElementById('bot-recent-messages-count')) document.getElementById('bot-recent-messages-count').value = recentMsgs;
  if (document.getElementById('bot-cooldown')) document.getElementById('bot-cooldown').value = cooldown;
  if (document.getElementById('bot-ignore-bots')) document.getElementById('bot-ignore-bots').checked = ignoreBots;
  if (document.getElementById('bot-max-bot-replies')) document.getElementById('bot-max-bot-replies').value = maxReplies;
  if (document.getElementById('bot-enabled-channels')) document.getElementById('bot-enabled-channels').value = enabledCh;
  if (document.getElementById('bot-blacklisted-channels')) document.getElementById('bot-blacklisted-channels').value = blackCh;
  if (document.getElementById('bot-blacklisted-users')) document.getElementById('bot-blacklisted-users').value = blackUsers;

  // Sync triggers
  const triggers = getTriggersFromContainer('bot-triggers-container-classic');
  renderTriggersToContainer('bot-triggers-container', triggers);
}

function syncClassicToWizard(type) {
  if (type === 'name') {
    const val = document.getElementById('bot-name-classic')?.value || '';
    if (document.getElementById('bot-name')) document.getElementById('bot-name').value = val;
  } else if (type === 'token') {
    const val = document.getElementById('bot-token-classic')?.value || '';
    if (document.getElementById('bot-token')) document.getElementById('bot-token').value = val;
  } else if (type === 'prompt') {
    const val = document.getElementById('bot-system-prompt-classic')?.value || '';
    if (document.getElementById('bot-system-prompt')) document.getElementById('bot-system-prompt').value = val;
  } else if (type === 'memory_mode') {
    const val = document.getElementById('bot-memory-mode-classic')?.value || 'recent_active';
    if (document.getElementById('bot-memory-mode')) document.getElementById('bot-memory-mode').value = val;
  } else if (type === 'cooldown') {
    const val = document.getElementById('bot-cooldown-classic')?.value || 3;
    if (document.getElementById('bot-cooldown')) document.getElementById('bot-cooldown').value = val;
  }
}

function setWizardStep(step) {
  currentWizardStep = Math.max(1, Math.min(5, step));

  // Update step panes
  for (let i = 1; i <= 5; i++) {
    const pane = document.getElementById(`wiz-step-pane-${i}`);
    if (pane) pane.classList.toggle('active', i === currentWizardStep);
  }

  // Update stepper icons & lines
  document.querySelectorAll('.wizard-stepper .stepper-step').forEach(stepEl => {
    const s = parseInt(stepEl.dataset.step, 10);
    stepEl.classList.toggle('active', s === currentWizardStep);
    stepEl.classList.toggle('completed', s < currentWizardStep);
    const circle = stepEl.querySelector('.step-circle');
    if (circle) {
      if (s < currentWizardStep) {
        circle.innerHTML = '✓';
      } else {
        circle.innerHTML = `<span class="step-num">${s}</span>`;
      }
    }
  });

  const lines = document.querySelectorAll('.wizard-stepper .stepper-line');
  lines.forEach((line, idx) => {
    line.classList.toggle('completed', idx + 1 < currentWizardStep);
  });

  // Update navigation buttons
  const prevBtn = document.getElementById('wiz-btn-prev');
  const nextBtn = document.getElementById('wiz-btn-next');
  const saveBtn = document.getElementById('wiz-btn-save');

  if (prevBtn) prevBtn.style.display = currentWizardStep > 1 ? 'inline-flex' : 'none';

  if (currentWizardStep < 5) {
    if (nextBtn) {
      nextBtn.style.display = 'inline-flex';
      const labels = [
        'Next: Gateway Intents →',
        'Next: Server Invite →',
        'Next: AI & Personality →',
        'Next: Triggers & Finish →'
      ];
      nextBtn.textContent = labels[currentWizardStep - 1] || 'Next →';
    }
    if (saveBtn) saveBtn.style.display = 'none';
  } else {
    if (nextBtn) nextBtn.style.display = 'none';
    if (saveBtn) saveBtn.style.display = 'inline-flex';
  }
}

function nextWizardStep() {
  if (currentWizardStep === 1) {
    const name = document.getElementById('bot-name')?.value?.trim();
    const token = document.getElementById('bot-token')?.value?.trim();
    if (!name) {
      showToast('Please enter a Bot Name', 'error');
      document.getElementById('bot-name')?.focus();
      return;
    }
    if (!token) {
      showToast('Please enter your Discord Bot Token', 'error');
      document.getElementById('bot-token')?.focus();
      return;
    }

    // Try auto-extracting client id from token if empty
    const clientIdInput = document.getElementById('wiz-client-id');
    if (clientIdInput && !clientIdInput.value.trim()) {
      try {
        const part = token.split('.')[0];
        if (part) {
          const decoded = atob(part);
          if (/^\d{16,21}$/.test(decoded)) {
            clientIdInput.value = decoded;
            onWizClientIdChange();
          }
        }
      } catch (e) {}
    }
    if (!document.getElementById('wiz-invite-url-input')?.value) {
      onWizClientIdChange();
    }
  }

  if (currentWizardStep === 4) {
    const selected = getSelectedBotEndpointChain();
    if (!selected.length && botEndpointChainState.length > 0) {
      // Auto-enable first endpoint if none selected
      botEndpointChainState[0].enabled = true;
      renderBotEndpointsChainHTML();
    }
  }

  setWizardStep(currentWizardStep + 1);
}

function prevWizardStep() {
  setWizardStep(currentWizardStep - 1);
}

function jumpToWizardStep(step) {
  if (step > currentWizardStep) {
    // Validate current step before skipping ahead
    if (currentWizardStep === 1) {
      const name = document.getElementById('bot-name')?.value?.trim();
      const token = document.getElementById('bot-token')?.value?.trim();
      if (!name || !token) {
        showToast('Please enter Bot Name and Token first', 'error');
        return;
      }
    }
  }
  setWizardStep(step);
}

function toggleTokenVisibility(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (btn) btn.textContent = '🔒';
  } else {
    input.type = 'password';
    if (btn) btn.textContent = '👁';
  }
}

function onWizClientIdChange() {
  const clientId = document.getElementById('wiz-client-id')?.value?.trim() || '';
  const inviteInput = document.getElementById('wiz-invite-url-input');
  if (!inviteInput) return;

  if (clientId && /^\d+$/.test(clientId)) {
    inviteInput.value = `https://discord.com/oauth2/authorize?client_id=${clientId}&permissions=412317273152&scope=bot%20applications.commands`;
  } else {
    inviteInput.value = '';
    inviteInput.placeholder = clientId ? 'Invalid Client ID (must be numbers)' : 'Enter Client ID in Step 1 to auto-generate';
  }
}

function copyInviteUrl() {
  const input = document.getElementById('wiz-invite-url-input');
  if (!input || !input.value) {
    showToast('Enter your Discord Client ID in Step 1 to generate the invite link', 'error');
    return;
  }
  navigator.clipboard.writeText(input.value).then(() => {
    showToast('Invite link copied to clipboard!');
  }).catch(() => {
    input.select();
    document.execCommand('copy');
    showToast('Invite link copied!');
  });
}

function openInviteUrl() {
  const input = document.getElementById('wiz-invite-url-input');
  if (!input || !input.value) {
    const manualId = prompt('Please enter your Discord Client / Application ID:');
    if (manualId && /^\d+$/.test(manualId.trim())) {
      const url = `https://discord.com/oauth2/authorize?client_id=${manualId.trim()}&permissions=412317273152&scope=bot%20applications.commands`;
      window.open(url, '_blank');
    }
    return;
  }
  window.open(input.value, '_blank');
}

const PROMPT_PRESETS = {
  assistant: 'You are a helpful, knowledgeable, and polite Discord AI assistant. Answer questions clearly, accurately, and concisely.',
  companion: 'You are a friendly, witty, and casual companion in this Discord server. Use a natural, conversational tone with occasional emojis.',
  witty: 'You are a clever and mildly sarcastic AI with a playful sense of humor. Provide sharp, witty, and entertaining answers without being genuinely mean.',
  moderator: 'You are a dedicated Discord server assistant focused on providing factual rules, helpful summaries, and answering member questions neutrally and accurately.',
  coder: 'You are an expert software engineer and programming assistant. When asked about code, provide clean, concise examples with best practices and brief explanations.',
  rpg: 'You are an immersive Tabletop RPG Game Master and fantasy lore-keeper. Speak in character when appropriate, describe scenes vividly, and keep responses engaging.'
};

function applyPromptPreset(presetKey) {
  const text = PROMPT_PRESETS[presetKey];
  if (!text) return;
  const p1 = document.getElementById('bot-system-prompt');
  const p2 = document.getElementById('bot-system-prompt-classic');
  if (p1) p1.value = text;
  if (p2) p2.value = text;
  showToast(`Loaded ${presetKey} preset prompt!`);
}

function loadRecommendedTriggers() {
  const botName = document.getElementById('bot-name')?.value?.trim() || document.getElementById('bot-name-classic')?.value?.trim() || 'bot';
  const defaultRules = [
    { type: 'command', pattern: `/${botName.toLowerCase()}`, reply_policy: 'mandatory' },
    { type: 'reply_to_bot', pattern: '', reply_policy: 'mandatory' },
    { type: 'keywords', pattern: botName.toLowerCase(), case_sensitive: false, reply_policy: 'ai_choice' },
    { type: 'mention', pattern: '', reply_policy: 'mandatory' }
  ];
  renderTriggersToContainer('bot-triggers-container', defaultRules);
  renderTriggersToContainer('bot-triggers-container-classic', defaultRules);
  showToast('Reset to recommended trigger rules');
}

function toggleAccordion(headerEl) {
  const body = headerEl.nextElementSibling;
  if (!body) return;
  const isHidden = body.style.display === 'none' || !body.style.display;
  body.style.display = isHidden ? (body.classList.contains('accordion-body') ? 'flex' : 'block') : 'none';
  const arrow = headerEl.querySelector('span:last-child');
  if (arrow) arrow.textContent = isHidden ? '▲' : '▼';
}

function openCreateBotModal() {
  document.getElementById('form-bot').reset();
  document.getElementById('bot-id').value = '';
  document.getElementById('modal-bot-title').textContent = 'New Bot Setup';
  
  // Set default mode to wizard
  toggleBotFormMode('wizard');
  setWizardStep(1);

  // Clear client id & invite link
  if (document.getElementById('wiz-client-id')) document.getElementById('wiz-client-id').value = '';
  if (document.getElementById('wiz-invite-url-input')) document.getElementById('wiz-invite-url-input').value = '';
  if (document.getElementById('wiz-check-intents')) document.getElementById('wiz-check-intents').checked = false;
  if (document.getElementById('wiz-check-invited')) document.getElementById('wiz-check-invited').checked = false;
  if (document.getElementById('bot-autostart')) document.getElementById('bot-autostart').checked = true;

  populateBotEndpointsChain([]);

  // Default trigger rules in both containers
  const defaultRules = [
    { type: 'command', pattern: '', reply_policy: 'mandatory' },
    { type: 'reply_to_bot', pattern: '', reply_policy: 'mandatory' },
    { type: 'keywords', pattern: 'jarvis', case_sensitive: false, reply_policy: 'ai_choice' },
    { type: 'mention', pattern: '', reply_policy: 'mandatory' }
  ];
  renderTriggersToContainer('bot-triggers-container', defaultRules);
  renderTriggersToContainer('bot-triggers-container-classic', defaultRules);

  // Default prompt
  applyPromptPreset('assistant');

  openModal('modal-bot');
}

function editBot(id) {
  const b = allBots.find(x => x.id === id);
  if (!b) return;

  document.getElementById('bot-id').value = b.id;
  document.getElementById('modal-bot-title').textContent = `Edit Bot: ${b.name}`;

  // Populate wizard fields
  if (document.getElementById('bot-name')) document.getElementById('bot-name').value = b.name || '';
  if (document.getElementById('bot-token')) document.getElementById('bot-token').value = b.discord_token || '';
  if (document.getElementById('bot-system-prompt')) document.getElementById('bot-system-prompt').value = b.system_prompt || '';
  if (document.getElementById('bot-memory-mode')) document.getElementById('bot-memory-mode').value = b.memory_mode || 'recent_active';
  if (document.getElementById('bot-active-users-count')) document.getElementById('bot-active-users-count').value = b.active_users_count ?? 5;
  if (document.getElementById('bot-recent-messages-count')) document.getElementById('bot-recent-messages-count').value = b.recent_messages_count ?? 15;
  if (document.getElementById('bot-cooldown')) document.getElementById('bot-cooldown').value = b.cooldown_seconds ?? 3;
  if (document.getElementById('bot-ignore-bots')) document.getElementById('bot-ignore-bots').checked = b.ignore_bots ?? true;
  if (document.getElementById('bot-max-bot-replies')) document.getElementById('bot-max-bot-replies').value = b.max_consecutive_bot_replies ?? 1;
  if (document.getElementById('bot-enabled-channels')) document.getElementById('bot-enabled-channels').value = (b.enabled_channels || []).join(', ');
  if (document.getElementById('bot-blacklisted-channels')) document.getElementById('bot-blacklisted-channels').value = (b.blacklisted_channels || []).join(', ');
  if (document.getElementById('bot-blacklisted-users')) document.getElementById('bot-blacklisted-users').value = (b.blacklisted_users || []).join(', ');

  // Auto-extract client id if possible
  if (b.discord_token) {
    try {
      const decoded = atob(b.discord_token.split('.')[0]);
      if (/^\d{16,21}$/.test(decoded)) {
        if (document.getElementById('wiz-client-id')) document.getElementById('wiz-client-id').value = decoded;
        onWizClientIdChange();
      }
    } catch(e) {}
  }

  // Populate triggers in both wizard and classic containers
  const triggers = (b.triggers && b.triggers.length) ? b.triggers : [
    { type: 'keywords', pattern: (b.name || 'bot').toLowerCase(), reply_policy: 'ai_choice' }
  ];
  renderTriggersToContainer('bot-triggers-container', triggers);
  renderTriggersToContainer('bot-triggers-container-classic', triggers);

  populateBotEndpointsChain(b.endpoint_chain || []);

  // Sync to classic view and open classic mode by default on edit
  syncWizardToClassicAll();
  toggleBotFormMode('classic');

  openModal('modal-bot');
}

// ── Bot Endpoint Chain State Manager (Priority Fallback & Reordering) ──
let botEndpointChainState = []; // Array of { id, name, provider, model_name, is_global_fallback, enabled }

async function populateBotEndpointsChain(selectedIds = []) {
  if (!allEndpoints || !allEndpoints.length) {
    try {
      allEndpoints = await (await fetch('/api/endpoints')).json();
    } catch(e) {}
  }

  const sel = Array.isArray(selectedIds) ? selectedIds : [];
  const orderedList = [];
  const addedIds = new Set();

  // 1. First add endpoints present in selectedIds in their exact specified priority order
  sel.forEach(id => {
    const ep = (allEndpoints || []).find(e => e.id === id);
    if (ep && !addedIds.has(ep.id)) {
      orderedList.push({ ...ep, enabled: true });
      addedIds.add(ep.id);
    }
  });

  // 2. Append any remaining endpoints not in selectedIds (as disabled)
  (allEndpoints || []).forEach(ep => {
    if (!addedIds.has(ep.id)) {
      // For a brand-new bot with no selection, enable the first non-fallback endpoint by default
      const shouldEnable = sel.length === 0 && orderedList.length === 0 && !ep.is_global_fallback;
      orderedList.push({ ...ep, enabled: shouldEnable });
      addedIds.add(ep.id);
    }
  });

  botEndpointChainState = orderedList;
  renderBotEndpointsChainHTML();
}

function renderBotEndpointsChainHTML() {
  const c1 = document.getElementById('bot-endpoints-chain-list');
  const c2 = document.getElementById('bot-endpoints-chain-list-classic');

  if (!botEndpointChainState.length) {
    const emptyHtml = '<p style="font-size:12px; color:var(--yellow); padding:8px 0;">⚠️ No endpoints configured. Please create an AI endpoint first.</p>';
    if (c1) c1.innerHTML = emptyHtml;
    if (c2) c2.innerHTML = emptyHtml;
    return;
  }

  let activeIndex = 0;
  const itemsHtml = botEndpointChainState.map((ep, idx) => {
    let priorityBadge = '';
    if (ep.enabled) {
      activeIndex++;
      const isPrimary = activeIndex === 1;
      priorityBadge = `<span class="badge ${isPrimary ? 'badge-primary' : 'badge-provider'}" style="font-size:10px; font-weight:600;">#${activeIndex} ${isPrimary ? 'Primary' : 'Fallback'}</span>`;
    } else {
      priorityBadge = `<span class="badge badge-offline" style="font-size:10px;">Off</span>`;
    }

    return `
      <div class="endpoint-chain-item" data-id="${ep.id}" style="padding:6px 10px; background:var(--bg-0); border:1px solid ${ep.enabled ? 'var(--primary)' : 'var(--border)'}; border-radius:6px; margin-bottom:5px; display:flex; align-items:center; gap:8px; opacity:${ep.enabled ? '1' : '0.55'}; transition:all 0.15s ease;">
        <input type="checkbox" ${ep.enabled ? 'checked' : ''} onchange="toggleBotEndpointChainItem('${ep.id}', this.checked)" title="Enable / Disable in fallback chain" style="cursor:pointer; width:15px; height:15px;">
        
        <div style="flex:1; min-width:0;">
          <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
            <strong style="font-size:12px; color:var(--text-0);">${esc(ep.name)}</strong>
            ${priorityBadge}
            ${ep.is_global_fallback ? '<span class="badge badge-global" style="font-size:10px;">Global Fallback</span>' : ''}
          </div>
          <div style="font-size:11px; color:var(--text-2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            ${esc(ep.provider)} · <code>${esc(ep.model_name)}</code>
          </div>
        </div>
        
        <div style="display:flex; gap:3px; margin-left:auto;">
          <button type="button" class="btn btn-sm btn-secondary" onclick="moveBotEndpointChainItem(${idx}, -1)" ${idx === 0 ? 'disabled' : ''} title="Move Up (Higher Priority)" style="padding:2px 7px; font-size:11px; line-height:1.2;">▲</button>
          <button type="button" class="btn btn-sm btn-secondary" onclick="moveBotEndpointChainItem(${idx}, 1)" ${idx === botEndpointChainState.length - 1 ? 'disabled' : ''} title="Move Down (Lower Priority)" style="padding:2px 7px; font-size:11px; line-height:1.2;">▼</button>
        </div>
      </div>
    `;
  }).join('');

  if (c1) c1.innerHTML = itemsHtml;
  if (c2) c2.innerHTML = itemsHtml;
}

function toggleBotEndpointChainItem(id, enabled) {
  const item = botEndpointChainState.find(e => e.id === id);
  if (item) {
    item.enabled = enabled;
    renderBotEndpointsChainHTML();
  }
}

function moveBotEndpointChainItem(index, direction) {
  const newIndex = index + direction;
  if (newIndex < 0 || newIndex >= botEndpointChainState.length) return;
  const temp = botEndpointChainState[index];
  botEndpointChainState[index] = botEndpointChainState[newIndex];
  botEndpointChainState[newIndex] = temp;
  renderBotEndpointsChainHTML();
}

function getSelectedBotEndpointChain() {
  return botEndpointChainState.filter(e => e.enabled).map(e => e.id);
}

async function saveBot(e) {
  e.preventDefault();
  const id = document.getElementById('bot-id').value;

  // Sync values to make sure active view is saved
  if (currentBotFormMode === 'classic') {
    syncClassicToWizardAll();
  } else {
    syncWizardToClassicAll();
  }

  const name = document.getElementById('bot-name')?.value?.trim();
  const token = document.getElementById('bot-token')?.value?.trim();

  if (!name) {
    showToast('Bot name is required', 'error');
    return;
  }
  if (!token) {
    showToast('Discord token is required', 'error');
    return;
  }

  const chain = getSelectedBotEndpointChain();
  if (!chain.length && allEndpoints.length > 0) {
    showToast('Please select at least one AI endpoint for this bot', 'error');
    return;
  }

  const csv = v => (v ? v.split(',').map(s => s.trim()).filter(Boolean) : []);

  // Collect trigger rules
  const activeContainerId = currentBotFormMode === 'classic' ? 'bot-triggers-container-classic' : 'bot-triggers-container';
  let triggers = getTriggersFromContainer(activeContainerId);
  if (!triggers.length) {
    triggers = getTriggersFromContainer(currentBotFormMode === 'classic' ? 'bot-triggers-container' : 'bot-triggers-container-classic');
  }

  const p = {
    name: name,
    discord_token: token,
    endpoint_chain: chain,
    system_prompt: document.getElementById('bot-system-prompt')?.value || '',
    triggers: triggers,
    memory_mode: document.getElementById('bot-memory-mode')?.value || 'recent_active',
    active_users_count: +document.getElementById('bot-active-users-count')?.value || 5,
    recent_messages_count: +document.getElementById('bot-recent-messages-count')?.value || 15,
    cooldown_seconds: +document.getElementById('bot-cooldown')?.value || 3,
    ignore_bots: document.getElementById('bot-ignore-bots')?.checked ?? true,
    max_consecutive_bot_replies: +document.getElementById('bot-max-bot-replies')?.value || 1,
    enabled_channels: csv(document.getElementById('bot-enabled-channels')?.value),
    blacklisted_channels: csv(document.getElementById('bot-blacklisted-channels')?.value),
    blacklisted_users: csv(document.getElementById('bot-blacklisted-users')?.value)
  };

  const autoStart = !id && document.getElementById('bot-autostart')?.checked;

  try {
    const r = await fetch(id ? `/api/bots/${id}` : '/api/bots/', {
      method: id ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p)
    });
    if (!r.ok) throw new Error(await r.text());
    const savedBotData = await r.json();

    showToast(id ? 'Bot updated successfully' : 'Bot created successfully!');
    closeModal('modal-bot');
    await loadBots();

    // Auto-start if requested
    if (autoStart && savedBotData && savedBotData.id) {
      showToast('Starting bot on Discord…');
      await startBot(savedBotData.id);
    }
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function deleteBot(id) {
  if (!confirm('Delete this bot?')) return;
  try {
    await fetch(`/api/bots/${id}`, { method: 'DELETE' });
    showToast('Deleted');
    loadBots();
  } catch (e) { showToast(e.message, 'error'); }
}

// ── Memory ──

// ── Memory ──

let memSub = 'server';
let userMemoryViewMode = 'cards'; // 'cards' | 'table'

function setUserMemoryViewMode(mode) {
  userMemoryViewMode = mode;
  const btnCards = document.getElementById('btn-view-cards');
  const btnTable = document.getElementById('btn-view-table');
  const cardsContainer = document.getElementById('user-memories-cards-container');
  const tableWrap = document.getElementById('memories-table-wrapper');

  if (btnCards) btnCards.style.background = mode === 'cards' ? 'var(--bg-3)' : 'transparent';
  if (btnTable) btnTable.style.background = mode === 'table' ? 'var(--bg-3)' : 'transparent';

  if (memSub === 'user') {
    if (cardsContainer) cardsContainer.style.display = mode === 'cards' ? 'grid' : 'none';
    if (tableWrap) tableWrap.style.display = mode === 'table' ? 'block' : 'none';
    loadUserMem();
  }
}

function switchMemorySubtab(sub) {
  memSub = sub;
  document.getElementById('btn-mem-server').style.background = sub === 'server' ? 'var(--bg-3)' : 'transparent';
  document.getElementById('btn-mem-user').style.background = sub === 'user' ? 'var(--bg-3)' : 'transparent';
  const btnChat = document.getElementById('btn-mem-chat');
  if (btnChat) btnChat.style.background = sub === 'chat' ? 'var(--bg-3)' : 'transparent';
  
  const addBtn = document.getElementById('btn-add-memory');
  const searchBar = document.getElementById('memory-search-bar');
  const tableWrap = document.getElementById('memories-table-wrapper');
  const cardsContainer = document.getElementById('user-memories-cards-container');
  const viewToggle = document.getElementById('user-memory-view-toggle');
  const chatView = document.getElementById('chat-history-view');
  
  if (sub === 'chat') {
    if (addBtn) addBtn.style.display = 'none';
    if (searchBar) searchBar.style.display = 'none';
    if (tableWrap) tableWrap.style.display = 'none';
    if (cardsContainer) cardsContainer.style.display = 'none';
    if (viewToggle) viewToggle.style.display = 'none';
    if (chatView) chatView.style.display = 'block';
    loadChatHistory();
  } else if (sub === 'user') {
    if (addBtn) addBtn.style.display = 'inline-flex';
    if (searchBar) searchBar.style.display = 'flex';
    if (viewToggle) viewToggle.style.display = 'flex';
    if (chatView) chatView.style.display = 'none';
    if (userMemoryViewMode === 'cards') {
      if (cardsContainer) cardsContainer.style.display = 'grid';
      if (tableWrap) tableWrap.style.display = 'none';
    } else {
      if (cardsContainer) cardsContainer.style.display = 'none';
      if (tableWrap) tableWrap.style.display = 'block';
    }
    loadMemories();
  } else {
    // server
    if (addBtn) addBtn.style.display = 'inline-flex';
    if (searchBar) searchBar.style.display = 'flex';
    if (viewToggle) viewToggle.style.display = 'none';
    if (cardsContainer) cardsContainer.style.display = 'none';
    if (tableWrap) tableWrap.style.display = 'block';
    if (chatView) chatView.style.display = 'none';
    loadMemories();
  }
}

function _resolveBotName(botId) {
  if (!botId) return '<span style="color:var(--text-3);">Global</span>';
  const b = allBots.find(x => x.id === botId);
  return b ? `<span>${esc(b.name)}</span>` : `<code>${esc(botId.slice(0, 8))}</code>`;
}

function _populateMemoryBotSelectors() {
  const filter = document.getElementById('memory-bot-filter');
  if (filter) {
    const cur = filter.value;
    filter.innerHTML = '<option value="">All bots</option>' + (allBots || []).map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    filter.value = cur;
  }
  const modalSel = document.getElementById('mem-bot-id');
  if (modalSel) {
    const cur = modalSel.value;
    modalSel.innerHTML = '<option value="">(Global / All Bots)</option>' + (allBots || []).map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    modalSel.value = cur;
  }
  const chatFilter = document.getElementById('chat-history-bot-filter');
  if (chatFilter && chatFilter.options.length <= 1) {
    chatFilter.innerHTML = '<option value="">All bots</option>' + (allBots || []).map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  }
}

async function loadMemories() {
  if (!allBots.length) {
    try { allBots = await (await fetch('/api/bots')).json(); } catch(e){}
  }
  _populateMemoryBotSelectors();

  const s = document.getElementById('memory-search')?.value || '';
  const botId = document.getElementById('memory-bot-filter')?.value || '';

  if (memSub === 'server') await loadServerMem(s, botId);
  else if (memSub === 'user') await loadUserMem(s, botId);
}

async function loadChatHistory() {
  const channelFilter = document.getElementById('chat-history-channel-filter');
  if (channelFilter && channelFilter.options.length <= 1) {
    try {
      const channels = await (await fetch('/api/chat/channels')).json();
      channelFilter.innerHTML = '<option value="">All channels</option>' + 
        channels.map(c => `<option value="${c.channel_id}">${esc(c.channel_name || c.channel_id)}</option>`).join('');
    } catch(e) {}
  }
  
  const botId = document.getElementById('chat-history-bot-filter')?.value || '';
  const channelId = document.getElementById('chat-history-channel-filter')?.value || '';
  
  const params = new URLSearchParams();
  if (botId) params.set('bot_id', botId);
  if (channelId) params.set('channel_id', channelId);
  params.set('limit', '100');
  
  try {
    const url = `/api/chat?${params.toString()}`;
    const msgs = await (await fetch(url)).json();
    const c = document.getElementById('chat-history-table-body');
    if (!c) return;
    
    if (!msgs.length) {
      c.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-3);">No chat history found.</td></tr>';
      return;
    }
    
    c.innerHTML = msgs.map(m => `<tr>
      <td class="mono" style="color:var(--text-2); font-size:12px;">${esc(m.timestamp).substring(0, 19).replace('T', ' ')}</td>
      <td><code>${esc(m.channel_id)}</code></td>
      <td><strong>${esc(m.author_name)}</strong></td>
      <td style="max-width:400px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${esc(m.content)}">${esc(m.content)}${m.has_attachments ? ' <em>[attachments]</em>' : ''}</td>
      <td>
        <div class="action-btns">
          <button class="btn btn-sm" style="color:var(--error);" onclick="deleteChatHistory('${m.id}')" title="Delete">D</button>
        </div>
      </td>
    </tr>`).join('');
  } catch (e) {
    showToast('Failed to load chat history', 'error');
  }
}

async function syncChatHistory() {
  const botId = document.getElementById('chat-history-bot-filter')?.value;
  const channelId = document.getElementById('chat-history-channel-filter')?.value;
  
  if (!botId) return showToast('Please select a bot to sync', 'error');
  if (!channelId) return showToast('Please enter a Channel ID to sync', 'error');
  
  try {
    const res = await fetch('/api/chat/sync', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ bot_id: botId, channel_id: channelId, limit: 100 })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Sync failed');
    showToast(`Synced ${json.synced_count} messages from Discord!`);
    loadChatHistory();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function deleteChatHistory(msgId) {
  if (!confirm('Delete this message from local history?')) return;
  try {
    const res = await fetch(`/api/chat/${msgId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');
    showToast('Message deleted');
    loadChatHistory();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function loadServerMem(search = '', botId = '') {
  try {
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (botId) params.set('bot_id', botId);
    const url = `/api/memories/server${params.toString() ? '?' + params.toString() : ''}`;
    const mems = await (await fetch(url)).json();
    const c = document.getElementById('memories-table-body');
    if (!c) return;
    if (!mems.length) {
      c.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-3);">No server memories.</td></tr>';
      return;
    }
    c.innerHTML = mems.map(m => `<tr>
      <td>${_resolveBotName(m.bot_id)}</td>
      <td><strong>${esc(m.key_phrase)}</strong></td>
      <td>${esc(m.fact)}</td>
      <td><span class="badge badge-provider">${esc(m.category)}</span></td>
      <td><code>${(m.updated_at || '').split('T')[0] || '—'}</code></td>
      <td style="white-space:nowrap;">
        <button class="btn btn-sm btn-secondary" onclick="editServerMemory('${m.id}','${jsEsc(m.key_phrase)}','${jsEsc(m.fact)}','${jsEsc(m.category)}','${m.bot_id || ''}')">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteServerMemory('${m.id}')">Del</button>
      </td>
    </tr>`).join('');
  } catch (e) { console.error(e); }
}

async function loadUserMem(search = '', botId = '') {
  if (!search) search = document.getElementById('memory-search')?.value || '';
  if (!botId) botId = document.getElementById('memory-bot-filter')?.value || '';

  try {
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (botId) params.set('bot_id', botId);

    if (userMemoryViewMode === 'cards') {
      const url = `/api/memories/user/grouped${params.toString() ? '?' + params.toString() : ''}`;
      const groupedUsers = await (await fetch(url)).json();
      const container = document.getElementById('user-memories-cards-container');
      if (!container) return;

      if (!groupedUsers.length) {
        container.innerHTML = '<div style="grid-column: 1/-1; text-align:center; color:var(--text-3); padding:40px 0;">No user memories found. Click "+ Add memory" or "↻ Sync Server Members".</div>';
        return;
      }

      container.innerHTML = groupedUsers.map(user => {
        const initial = (user.display_name || user.username || 'U')[0].toUpperCase();
        const avatarHtml = user.avatar_url 
          ? `<img src="${esc(user.avatar_url)}" alt="avatar" style="width:40px; height:40px; border-radius:50%; object-fit:cover; border:1px solid var(--border);">`
          : `<div style="width:40px; height:40px; border-radius:50%; background:linear-gradient(135deg, var(--primary), #8b5cf6); display:flex; align-items:center; justify-content:center; color:#fff; font-weight:bold; font-size:16px;">${initial}</div>`;

        const factsHtml = user.memories.map(m => `
          <div style="background:var(--bg-0); border:1px solid var(--border); border-radius:6px; padding:8px 10px; margin-bottom:6px; display:flex; flex-direction:column; gap:4px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="badge badge-provider" style="font-size:10px; text-transform:uppercase;">${esc(m.category || 'general')}</span>
              <div style="display:flex; gap:4px; align-items:center;">
                <span style="font-size:11px; color:var(--text-3); margin-right:4px;">${_resolveBotName(m.bot_id)}</span>
                <button class="btn btn-sm btn-secondary" onclick="editUserMemory('${m.id}','${jsEsc(user.user_id)}','${jsEsc(user.display_name || user.username)}','${jsEsc(m.fact)}','${jsEsc(m.category)}','${m.bot_id || ''}')" title="Edit fact" style="padding:1px 6px; font-size:11px;">✎</button>
                <button class="btn btn-sm btn-danger" onclick="deleteUserMemory('${m.id}')" title="Delete fact" style="padding:1px 6px; font-size:11px;">✕</button>
              </div>
            </div>
            <div style="font-size:12px; color:var(--text-1); line-height:1.4; word-break:break-word;">${esc(m.fact)}</div>
          </div>
        `).join('');

        return `
          <div class="bot-card" style="display:flex; flex-direction:column;">
            <div class="card-header" style="align-items:flex-start; padding-bottom:8px; border-bottom:1px solid var(--border);">
              <div style="display:flex; gap:10px; align-items:center; flex:1; min-width:0;">
                ${avatarHtml}
                <div style="flex:1; min-width:0;">
                  <div style="font-weight:600; font-size:14px; color:var(--text-0); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${esc(user.display_name)}">${esc(user.display_name)}</div>
                  <div style="font-size:11px; color:var(--text-2); display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-top:2px;">
                    <span>@${esc(user.username)}${user.global_name && user.global_name !== user.username && user.global_name !== user.display_name ? ' (' + esc(user.global_name) + ')' : ''}</span>
                    <span style="cursor:pointer; background:var(--bg-3); padding:1px 5px; border-radius:3px;" onclick="navigator.clipboard.writeText('${user.user_id}'); showToast('Copied User ID!');" title="Click to copy ID">ID: ${esc(user.user_id)} 📋</span>
                  </div>
                </div>
              </div>
              <span class="badge badge-online" style="font-size:11px;">${user.memories_count} fact(s)</span>
            </div>

            <div style="padding:12px 14px; flex:1; overflow-y:auto; max-height:220px;">
              ${factsHtml}
            </div>

            <div style="padding:8px 14px; border-top:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; background:var(--bg-1);">
              <button class="btn btn-sm btn-secondary" onclick="openAddMemoryForUser('${jsEsc(user.user_id)}', '${jsEsc(user.display_name || user.username)}', '${jsEsc(botId)}')" style="font-size:11px;">+ Add Fact</button>
              <button class="btn btn-sm btn-danger" onclick="wipeUser('${user.user_id}', '${botId}')" style="font-size:11px;">Wipe All</button>
            </div>
          </div>
        `;
      }).join('');
    } else {
      // Table view
      const url = `/api/memories/user${params.toString() ? '?' + params.toString() : ''}`;
      const mems = await (await fetch(url)).json();
      const c = document.getElementById('memories-table-body');
      if (!c) return;
      if (!mems.length) {
        c.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-3);">No user memories.</td></tr>';
        return;
      }
      c.innerHTML = mems.map(m => {
        const initial = (m.display_name || m.username || 'U')[0].toUpperCase();
        const avatar = m.avatar_url
          ? `<img src="${esc(m.avatar_url)}" style="width:22px; height:22px; border-radius:50%; vertical-align:middle; margin-right:6px;">`
          : `<span style="display:inline-flex; width:22px; height:22px; border-radius:50%; background:var(--primary); color:#fff; font-size:11px; align-items:center; justify-content:center; margin-right:6px; vertical-align:middle;">${initial}</span>`;

        return `<tr>
          <td>${_resolveBotName(m.bot_id)}</td>
          <td>
            <div style="display:flex; align-items:center;">
              ${avatar}
              <div>
                <strong>${esc(m.display_name || m.username)}</strong>
                <div style="font-size:10px; color:var(--text-3);">@${esc(m.user_handle || m.username)} · ID: ${esc(m.user_id)}</div>
              </div>
            </div>
          </td>
          <td>${esc(m.fact)}</td>
          <td><span class="badge badge-provider">${esc(m.category)}</span></td>
          <td><code>${(m.updated_at || '').split('T')[0] || '—'}</code></td>
          <td style="white-space:nowrap;">
            <button class="btn btn-sm btn-secondary" onclick="editUserMemory('${m.id}','${jsEsc(m.user_id)}','${jsEsc(m.display_name || m.username)}','${jsEsc(m.fact)}','${jsEsc(m.category)}','${m.bot_id || ''}')">Edit</button>
            <button class="btn btn-sm btn-danger" onclick="deleteUserMemory('${m.id}')">Del</button>
            <button class="btn btn-sm btn-danger" onclick="wipeUser('${m.user_id}','${m.bot_id || ''}')" title="Wipe all for this user">Wipe</button>
          </td>
        </tr>`;
      }).join('');
    }
  } catch (e) { console.error(e); }
}

function openAddMemoryForUser(userId, userName, botId = '') {
  openAddMemoryModal();
  document.getElementById('memory-type').value = 'user';
  if (botId) document.getElementById('mem-bot-id').value = botId;
  document.getElementById('mem-user-id').value = userId;
  document.getElementById('mem-user-name').value = userName;
  toggleMemoryFormFields();
  document.getElementById('mem-fact')?.focus();
}

function openAddMemoryModal() {
  document.getElementById('form-memory').reset();
  document.getElementById('memory-id').value = '';
  document.getElementById('memory-type').value = memSub === 'chat' ? 'server' : memSub;
  const currentFilter = document.getElementById('memory-bot-filter')?.value || '';
  document.getElementById('mem-bot-id').value = currentFilter;
  toggleMemoryFormFields();
  openModal('modal-memory');
}

function toggleMemoryFormFields() {
  const t = document.getElementById('memory-type').value;
  document.getElementById('mem-field-server').style.display = t === 'server' ? 'block' : 'none';
  document.getElementById('mem-field-user').style.display = t === 'user' ? 'block' : 'none';
}

function editServerMemory(id, k, f, c, botId = '') {
  document.getElementById('memory-id').value = id;
  document.getElementById('memory-type').value = 'server';
  document.getElementById('mem-bot-id').value = botId;
  document.getElementById('mem-server-key').value = k;
  document.getElementById('mem-fact').value = f;
  document.getElementById('mem-category').value = c;
  toggleMemoryFormFields();
  openModal('modal-memory');
}

function editUserMemory(id, uid, un, f, c, botId = '') {
  document.getElementById('memory-id').value = id;
  document.getElementById('memory-type').value = 'user';
  document.getElementById('mem-bot-id').value = botId;
  document.getElementById('mem-user-id').value = uid;
  document.getElementById('mem-user-name').value = un;
  document.getElementById('mem-fact').value = f;
  document.getElementById('mem-category').value = c;
  toggleMemoryFormFields();
  openModal('modal-memory');
}

async function saveMemory(e) {
  e.preventDefault();
  const id = document.getElementById('memory-id').value;
  const type = document.getElementById('memory-type').value;
  const botId = document.getElementById('mem-bot-id').value || null;
  const fact = document.getElementById('mem-fact').value;
  const cat = document.getElementById('mem-category').value;

  try {
    if (type === 'server') {
      const p = {
        bot_id: botId,
        key_phrase: document.getElementById('mem-server-key').value,
        fact,
        category: cat
      };
      await fetch(id ? `/api/memories/server/${id}` : '/api/memories/server', {
        method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p)
      });
    } else {
      const p = {
        bot_id: botId,
        user_id: document.getElementById('mem-user-id').value,
        username: document.getElementById('mem-user-name').value,
        fact,
        category: cat
      };
      await fetch(id ? `/api/memories/user/${id}` : '/api/memories/user', {
        method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p)
      });
    }
    showToast('Memory saved');
    closeModal('modal-memory');
    loadMemories();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function deleteServerMemory(id) {
  if (!confirm('Delete?')) return;
  await fetch(`/api/memories/server/${id}`, { method: 'DELETE' });
  showToast('Deleted');
  loadMemories();
}

async function deleteUserMemory(id) {
  if (!confirm('Delete?')) return;
  await fetch(`/api/memories/user/${id}`, { method: 'DELETE' });
  showToast('Deleted');
  loadMemories();
}

async function wipeUser(uid, botId = '') {
  const url = `/api/memories/user/wipe/${uid}${botId ? '?bot_id=' + encodeURIComponent(botId) : ''}`;
  if (!confirm(`Wipe memories for user ${uid}?`)) return;
  const d = await (await fetch(url, { method: 'DELETE' })).json();
  showToast(`Wiped ${d.deleted_count} memories`);
  loadMemories();
}

// ── Playground ──

async function initPlayground(){try{allBots=await(await fetch('/api/bots')).json();const s=document.getElementById('sim-bot-select');if(s)s.innerHTML=allBots.map(b=>`<option value="${b.id}">${b.name}</option>`).join('');}catch(e){console.error(e);}}

async function sendPlaygroundMessage() {
  const botId=document.getElementById('sim-bot-select')?.value;
  const text=document.getElementById('sim-input-text')?.value.trim();
  const username=document.getElementById('sim-username')?.value.trim()||'Tester';
  const imageUrl=document.getElementById('sim-image-url')?.value.trim()||null;
  const replyPolicy=document.getElementById('sim-reply-policy')?.value||null;
  if(!botId){showToast('Select a bot first','error');return;}
  if(!text&&!imageUrl)return;

  const thread=document.getElementById('sim-messages-thread');
  const uMsg=document.createElement('div');uMsg.className='chat-msg user';
  uMsg.innerHTML=`<div class="msg-author">${username}</div><div>${esc(text)}</div>${imageUrl?`<img src="${imageUrl}" style="max-width:180px;border-radius:6px;margin-top:4px;">`:''}`; 
  thread.appendChild(uMsg);thread.scrollTop=thread.scrollHeight;
  document.getElementById('sim-input-text').value='';

  const bMsg=document.createElement('div');bMsg.className='chat-msg bot';
  bMsg.innerHTML=`<div class="msg-author">Bot</div><div style="color:var(--text-2);">Processing…</div>`;
  thread.appendChild(bMsg);thread.scrollTop=thread.scrollHeight;

  try{
    const d=await(await fetch('/api/playground/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_id:botId,message:text,username,image_url:imageUrl,override_reply_policy:replyPolicy})})).json();
    bMsg.innerHTML=d.status==='success'?`<div class="msg-author">Bot ${d.refused?'<span class="badge badge-error">refuse</span>':''}</div><div>${esc(d.reply)}</div>`:`<div class="msg-author">Error</div><div style="color:var(--red);">${esc(d.error||'Error')}</div>`;
    thread.scrollTop=thread.scrollHeight;
    renderInspector(d);
  } catch(e){bMsg.innerHTML=`<div class="msg-author">Error</div><div style="color:var(--red);">${e.message}</div>`;}
}

// ── Tools Settings ──

function toggleSearxngInput() {
  const provider = document.getElementById('tools-search-provider').value;
  const group = document.getElementById('tools-searxng-url-group');
  if (provider === 'searxng') {
    group.style.display = 'block';
  } else {
    group.style.display = 'none';
  }
}

async function loadToolSettings() {
  try {
    const res = await fetch('/api/settings/');
    const settings = await res.json();
    document.getElementById('tools-search-provider').value = settings.web_search_provider || 'duckduckgo';
    document.getElementById('tools-searxng-url').value = settings.searxng_url || '';
    document.getElementById('tools-max-results').value = settings.max_search_results || 5;
    document.getElementById('tools-safesearch').value = settings.search_safesearch || 'moderate';
    document.getElementById('tools-max-iterations').value = settings.max_tool_iterations || 6;
    toggleSearxngInput();
  } catch (e) {
    console.error('Failed to load settings', e);
    showToast('Failed to load tool settings', 'error');
  }
}

async function saveToolSettings() {
  const payload = {
    web_search_provider: document.getElementById('tools-search-provider').value,
    searxng_url: document.getElementById('tools-searxng-url').value,
    max_search_results: parseInt(document.getElementById('tools-max-results').value) || 5,
    search_safesearch: document.getElementById('tools-safesearch').value,
    max_tool_iterations: parseInt(document.getElementById('tools-max-iterations').value) || 6
  };
  
  try {
    const res = await fetch('/api/settings/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    
    if (res.ok) {
      showToast('Settings saved successfully');
    } else {
      const err = await res.json();
      showToast('Error saving settings', 'error');
    }
  } catch (e) {
    console.error(e);
    showToast('Network error saving settings', 'error');
  }
}

function renderInspector(data) {
  const c=document.getElementById('inspector-content');if(!c||!data.debug_inspector)return;const d=data.debug_inspector;
  c.innerHTML=`
    <div class="inspector-section"><h4>Execution</h4>
      <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;"><span style="color:var(--text-2);">Model</span><code>${d.model_used}</code></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;"><span style="color:var(--text-2);">Latency</span><code>${data.elapsed_ms}ms</code></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;"><span style="color:var(--text-2);">Tokens</span><code>${d.tokens.total} (p:${d.tokens.prompt} c:${d.tokens.completion})</code></div>
      <div style="display:flex;justify-content:space-between;font-size:12px;"><span style="color:var(--text-2);">Status</span>${data.refused?'<span class="badge badge-error">refuse</span>':'<span class="badge badge-online">ok</span>'}</div>
    </div>
    <div class="inspector-section"><h4>Tools (${(d.tools_called||[]).length})</h4><div class="code-view">${JSON.stringify(d.tools_called,null,2)}</div></div>
    <div class="inspector-section"><h4>Injected memories</h4>
      <div style="font-size:11px;color:var(--text-2);margin-bottom:6px;">Server: ${d.injected_server_memories.length} · Users: ${d.injected_user_memories.length}</div>
      <div class="code-view">${JSON.stringify({server:d.injected_server_memories,users:d.injected_user_memories},null,2)}</div>
    </div>
    <div class="inspector-section"><h4>System prompt</h4><div class="code-view">${esc(d.system_prompt)}</div></div>`;
}

// ── Analytics ──

let currentAnalyticsRange = '30d';
const AN = {}; // chart instances
const AN_PALETTE = ['#3b82f6','#8b5cf6','#06b6d4','#22c55e','#eab308','#ec4899','#f97316','#a855f7'];
const AN_GRID = { color: '#1f1f23', drawBorder: false };
const AN_TICK = { color: '#71717a', font: { size: 10, family: 'JetBrains Mono' } };

const AN_TOOLTIP = {
  backgroundColor: 'rgba(15, 15, 17, 0.95)',
  titleColor: '#fafafa',
  bodyColor: '#a1a1aa',
  borderColor: '#27272a',
  borderWidth: 1,
  padding: 10,
  cornerRadius: 6,
  boxPadding: 4,
  usePointStyle: true,
  titleFont: { size: 12, family: 'Inter', weight: '600' },
  bodyFont: { size: 11, family: 'JetBrains Mono' }
};

function _anAxis(overrides = {}) {
  return { ticks: AN_TICK, grid: AN_GRID, border: { display: false }, ...overrides };
}

function _anDestroy(...keys) {
  keys.forEach(k => {
    if (AN[k]) {
      try { AN[k].destroy(); } catch (e) {}
      delete AN[k];
    }
  });
}

function _anGradient(ctx, hexColor, alphaStart = 0.3, alphaEnd = 0.0) {
  try {
    const grad = ctx.createLinearGradient(0, 0, 0, 220);
    grad.addColorStop(0, hexColor + Math.round(alphaStart * 255).toString(16).padStart(2, '0'));
    grad.addColorStop(1, hexColor + Math.round(alphaEnd * 255).toString(16).padStart(2, '0'));
    return grad;
  } catch (e) {
    return hexColor + '20';
  }
}

function _anSetEmptyState(containerId, message = 'No activity recorded for this period', icon = '📊') {
  const container = document.getElementById(containerId);
  if (!container) return;
  let overlay = container.querySelector('.chart-empty-state');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'chart-empty-state';
    container.appendChild(overlay);
  }
  overlay.innerHTML = `<div class="chart-empty-icon">${icon}</div><div>${message}</div>`;
  overlay.style.display = 'flex';
  const canvas = container.querySelector('canvas');
  if (canvas) canvas.style.display = 'none';
}

function _anClearEmptyState(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const overlay = container.querySelector('.chart-empty-state');
  if (overlay) overlay.style.display = 'none';
  const canvas = container.querySelector('canvas');
  if (canvas) canvas.style.display = 'block';
}

function setAnalyticsRange(range) {
  currentAnalyticsRange = range;
  document.querySelectorAll('#an-range-selector .range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  loadAnalytics();
}

async function loadAnalytics() {
  const range = currentAnalyticsRange || '30d';
  const botSel = document.getElementById('an-bot');
  const botId = botSel?.value || '';
  const params = new URLSearchParams({ range });
  if (botId) params.set('bot_id', botId);

  try {
    const data = await (await fetch(`/api/stats/timeseries?${params}`)).json();

    // Populate bot selector (preserve current selection without flickering)
    if (botSel) {
      const prev = botSel.value;
      const bots = data.bots || [];
      botSel.innerHTML = '<option value="">All Bots</option>' + bots.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
      botSel.value = prev;
    }

    // ── KPI Summary Cards ──
    const summary = data.summary || {};
    const totalReqs = summary.total_requests || 0;
    const totalTokens = summary.total_tokens || 0;
    const promptTok = summary.prompt_tokens || 0;
    const compTok = summary.completion_tokens || 0;
    const refusedCount = summary.refused_count || 0;
    const successRate = summary.success_rate_pct ?? 100;
    const totalMsgs = summary.total_messages || 0;
    const avgTok = summary.avg_tokens_per_req || (totalReqs ? Math.round(totalTokens / totalReqs) : 0);
    const toolsCalled = summary.total_tools_called || 0;

    if (document.getElementById('an-total-requests')) document.getElementById('an-total-requests').textContent = totalReqs.toLocaleString();
    if (document.getElementById('an-total-tokens')) document.getElementById('an-total-tokens').textContent = totalTokens.toLocaleString();
    if (document.getElementById('an-avg-tokens')) document.getElementById('an-avg-tokens').textContent = avgTok.toLocaleString();
    if (document.getElementById('an-success-rate')) document.getElementById('an-success-rate').textContent = `${successRate}%`;
    if (document.getElementById('an-total-messages')) document.getElementById('an-total-messages').textContent = totalMsgs.toLocaleString();

    if (document.getElementById('an-sub-tokens')) {
      document.getElementById('an-sub-tokens').textContent = `${promptTok.toLocaleString()} prompt · ${compTok.toLocaleString()} compl`;
    }
    if (document.getElementById('an-sub-refused')) {
      document.getElementById('an-sub-refused').textContent = refusedCount > 0 ? `${refusedCount.toLocaleString()} refused / error` : '0 errors or refusals';
    }
    if (document.getElementById('an-sub-tools')) {
      document.getElementById('an-sub-tools').textContent = `${toolsCalled.toLocaleString()} tool calls executed`;
    }

    // ── 1. Requests & Activity Timeline ──
    _anDestroy('req');
    const rpd = data.requests_per_day || [];
    const hasReqs = rpd.some(d => d.requests > 0 || d.refused > 0);
    if (!hasReqs) {
      _anSetEmptyState('container-an-chart-requests', 'No request activity in this time window', '📈');
    } else {
      _anClearEmptyState('container-an-chart-requests');
      const ctx1 = document.getElementById('an-chart-requests')?.getContext('2d');
      if (ctx1) {
        const labels = rpd.map(d => range === '24h' ? (d.day.split(' ')[1] || d.day) : d.day.slice(5));
        AN.req = new Chart(ctx1, {
          type: 'bar',
          data: {
            labels: labels,
            datasets: [
              {
                label: 'Successful Requests',
                data: rpd.map(d => Math.max(0, d.requests - d.refused)),
                backgroundColor: '#3b82f6',
                borderRadius: 4,
                barPercentage: 0.65,
                order: 2
              },
              {
                label: 'Refusals / Errors',
                data: rpd.map(d => d.refused),
                type: 'line',
                borderColor: '#ef4444',
                backgroundColor: 'rgba(239, 68, 68, 0.1)',
                fill: true,
                pointRadius: rpd.some(d => d.refused > 0) ? 2 : 0,
                pointBackgroundColor: '#ef4444',
                borderWidth: 1.5,
                tension: 0.3,
                order: 1
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'top', align: 'end', labels: { color: '#a1a1aa', font: { size: 11 }, usePointStyle: true, pointStyleWidth: 7, padding: 12 } },
              tooltip: AN_TOOLTIP
            },
            scales: {
              x: _anAxis({ grid: { display: false }, stacked: true }),
              y: _anAxis({ beginAtZero: true, stacked: true })
            }
          }
        });
      }
    }

    // ── 2. Tokens Consumption Breakdown ──
    _anDestroy('tok');
    const hasTokens = rpd.some(d => (d.tokens || 0) > 0);
    if (!hasTokens) {
      _anSetEmptyState('container-an-chart-tokens', 'No tokens consumed in this time window', '🪙');
    } else {
      _anClearEmptyState('container-an-chart-tokens');
      const ctx2 = document.getElementById('an-chart-tokens')?.getContext('2d');
      if (ctx2) {
        const labels = rpd.map(d => range === '24h' ? (d.day.split(' ')[1] || d.day) : d.day.slice(5));
        AN.tok = new Chart(ctx2, {
          type: 'bar',
          data: {
            labels: labels,
            datasets: [
              {
                label: 'Prompt Tokens',
                data: rpd.map(d => d.prompt_tokens || 0),
                backgroundColor: '#3b82f6',
                borderRadius: 2,
                barPercentage: 0.65
              },
              {
                label: 'Completion Tokens',
                data: rpd.map(d => d.completion_tokens || 0),
                backgroundColor: '#8b5cf6',
                borderRadius: 2,
                barPercentage: 0.65
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'top', align: 'end', labels: { color: '#a1a1aa', font: { size: 11 }, usePointStyle: true, pointStyleWidth: 7, padding: 12 } },
              tooltip: AN_TOOLTIP
            },
            scales: {
              x: _anAxis({ grid: { display: false }, stacked: true }),
              y: _anAxis({ beginAtZero: true, stacked: true })
            }
          }
        });
      }
    }

    // ── 3. Tokens by Model (Stacked Area) ──
    _anDestroy('model');
    const modelData = data.tokens_by_model_day || {};
    const modelNames = Object.keys(modelData);
    const hasModelTokens = modelNames.some(m => modelData[m].some(d => (d.tokens || 0) > 0));
    if (!hasModelTokens) {
      _anSetEmptyState('container-an-chart-model', 'No model usage recorded in this time window', '🧠');
    } else {
      _anClearEmptyState('container-an-chart-model');
      const ctx3 = document.getElementById('an-chart-model')?.getContext('2d');
      if (ctx3 && modelNames.length) {
        const allDays = [...new Set(modelNames.flatMap(m => modelData[m].map(d => d.day)))].sort();
        const labels = allDays.map(d => range === '24h' ? (d.split(' ')[1] || d) : d.slice(5));
        AN.model = new Chart(ctx3, {
          type: 'line',
          data: {
            labels: labels,
            datasets: modelNames.map((name, i) => {
              const lookup = Object.fromEntries(modelData[name].map(d => [d.day, d.tokens]));
              const color = AN_PALETTE[i % AN_PALETTE.length];
              return {
                label: name,
                data: allDays.map(d => lookup[d] || 0),
                borderColor: color,
                backgroundColor: _anGradient(ctx3, color, 0.25, 0.0),
                fill: true,
                tension: 0.35,
                pointRadius: 0,
                borderWidth: 1.8
              };
            })
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'top', align: 'end', labels: { color: '#a1a1aa', font: { size: 11 }, usePointStyle: true, pointStyleWidth: 7, padding: 12 } },
              tooltip: AN_TOOLTIP
            },
            scales: {
              x: _anAxis({ grid: { display: false } }),
              y: _anAxis({ beginAtZero: true, stacked: true })
            }
          }
        });
      }
    }

    // ── 4. AI Tool Invocations ──
    _anDestroy('tools');
    const toolTotals = data.tool_totals || [];
    if (!toolTotals.length) {
      _anSetEmptyState('container-an-chart-tools', 'No tool invocations recorded in this time window', '⚒');
    } else {
      _anClearEmptyState('container-an-chart-tools');
      const ctx4 = document.getElementById('an-chart-tools')?.getContext('2d');
      if (ctx4) {
        AN.tools = new Chart(ctx4, {
          type: 'bar',
          data: {
            labels: toolTotals.map(t => t.tool),
            datasets: [{
              label: 'Calls',
              data: toolTotals.map(t => t.count),
              backgroundColor: toolTotals.map((_, i) => AN_PALETTE[i % AN_PALETTE.length]),
              borderRadius: 4,
              barPercentage: 0.55
            }]
          },
          options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: AN_TOOLTIP
            },
            scales: {
              x: _anAxis({ beginAtZero: true, grid: { color: '#1f1f23' } }),
              y: _anAxis({ grid: { display: false } })
            }
          }
        });
      }
    }

    // ── 5. Hourly Activity Distribution ──
    _anDestroy('hourly');
    const hourly = data.hourly_distribution || [];
    const hasHourly = hourly.some(h => h.requests > 0);
    if (!hasHourly) {
      _anSetEmptyState('container-an-chart-hourly', 'No requests to analyze hourly distribution', '⏱');
    } else {
      _anClearEmptyState('container-an-chart-hourly');
      const ctx5 = document.getElementById('an-chart-hourly')?.getContext('2d');
      if (ctx5) {
        AN.hourly = new Chart(ctx5, {
          type: 'bar',
          data: {
            labels: hourly.map(h => h.label),
            datasets: [{
              label: 'Requests',
              data: hourly.map(h => h.requests),
              backgroundColor: '#06b6d4',
              borderRadius: 3,
              barPercentage: 0.6
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: AN_TOOLTIP
            },
            scales: {
              x: _anAxis({ grid: { display: false } }),
              y: _anAxis({ beginAtZero: true })
            }
          }
        });
      }
    }

    // ── 6. Per-bot Breakdown ──
    _anDestroy('bots');
    const perBot = data.per_bot || [];
    const hasBotsData = perBot.some(b => b.requests > 0 || b.tokens > 0);
    if (!hasBotsData) {
      _anSetEmptyState('container-an-chart-bots', 'No bot instance telemetry recorded', '🤖');
    } else {
      _anClearEmptyState('container-an-chart-bots');
      const ctx6 = document.getElementById('an-chart-bots')?.getContext('2d');
      if (ctx6 && perBot.length) {
        AN.bots = new Chart(ctx6, {
          type: 'bar',
          data: {
            labels: perBot.map(b => b.bot_name),
            datasets: [
              {
                label: 'Requests',
                data: perBot.map(b => b.requests),
                backgroundColor: '#3b82f6',
                borderRadius: 3,
                barPercentage: 0.5
              },
              {
                label: 'Tokens (k)',
                data: perBot.map(b => Math.round(b.tokens / 1000)),
                backgroundColor: '#8b5cf6',
                borderRadius: 3,
                barPercentage: 0.5
              }
            ]
          },
          options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'top', align: 'end', labels: { color: '#a1a1aa', font: { size: 11 }, usePointStyle: true, pointStyleWidth: 7, padding: 10 } },
              tooltip: AN_TOOLTIP
            },
            scales: {
              x: _anAxis({ beginAtZero: true, grid: { color: '#1f1f23' } }),
              y: _anAxis({ grid: { display: false } })
            }
          }
        });
      }
    }

    // ── Model Efficiency Table ──
    const mt = document.getElementById('an-model-table');
    if (mt) {
      const models = data.model_totals || [];
      const sumTokens = models.reduce((s, m) => s + (m.tokens || 0), 0) || 1;
      mt.innerHTML = models.length ? models.map(m => {
        const pct = Math.round((m.tokens / sumTokens) * 100);
        return `
          <tr>
            <td>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="badge badge-provider">${esc(m.model)}</span>
              </div>
            </td>
            <td><code>${m.requests.toLocaleString()}</code></td>
            <td><code>${m.tokens.toLocaleString()}</code></td>
            <td><code>${(m.avg_tokens || 0).toLocaleString()}</code></td>
            <td>
              <div style="display:flex; align-items:center;">
                <div class="progress-share-track">
                  <div class="progress-share-bar" style="width:${pct}%; background:var(--accent);"></div>
                </div>
                <span style="font-size:11px; color:var(--text-2);">${pct}%</span>
              </div>
            </td>
          </tr>
        `;
      }).join('') : '<tr><td colspan="5" style="text-align:center;color:var(--text-3);padding:20px;">No model activity in this time window</td></tr>';
    }

    // ── Top Users Leaderboard ──
    const ut = document.getElementById('an-users-table');
    if (ut) {
      const users = data.top_users || [];
      const maxMsgs = Math.max(1, ...users.map(u => u.messages));
      ut.innerHTML = users.length ? users.map((u, idx) => {
        const pct = Math.round((u.messages / maxMsgs) * 100);
        const initial = (u.display_name || u.author || 'U')[0].toUpperCase();
        const avatarHtml = u.avatar_url
          ? `<img src="${esc(u.avatar_url)}" class="user-avatar-small" alt="">`
          : `<div class="user-avatar-initial">${initial}</div>`;

        return `
          <tr>
            <td>
              <div class="user-leaderboard-cell">
                ${avatarHtml}
                <div>
                  <div style="font-weight:600; color:var(--text-0);">${esc(u.display_name || u.author)}</div>
                  <div style="font-size:10px; color:var(--text-3);">@${esc(u.username || u.author)}</div>
                </div>
              </div>
            </td>
            <td><code>${u.messages.toLocaleString()}</code></td>
            <td>
              <div style="display:flex; align-items:center;">
                <div class="progress-share-track">
                  <div class="progress-share-bar" style="width:${pct}%; background:var(--green);"></div>
                </div>
                <span style="font-size:11px; color:var(--text-2);">${pct}%</span>
              </div>
            </td>
          </tr>
        `;
      }).join('') : '<tr><td colspan="3" style="text-align:center;color:var(--text-3);padding:20px;">No user activity recorded</td></tr>';
    }

  } catch (e) {
    console.error('Analytics load error:', e);
    showToast('Failed to load analytics telemetry', 'error');
  }
}

// ── Backup ──
function downloadBackup(){window.location.href='/api/backup/export';showToast('Download started');}
async function uploadBackup(e){const f=e.target.files[0];if(!f)return;const fd=new FormData();fd.append('file',f);try{const r=await fetch('/api/backup/import',{method:'POST',body:fd});const d=await r.json();if(r.ok)showToast(`Restored: ${d.imported.bots} bots, ${d.imported.endpoints} endpoints`);else showToast(d.detail,'error');}catch(e){showToast(e.message,'error');}}

// ── Logs ──
let loadedLogs = [];
let logsEventSource = null;
let liveStreamState = {}; // Track current streaming request

function connectLogsSSE() {
  if (logsEventSource) return; // Already connected
  
  logsEventSource = new EventSource('/api/logs/stream');
  
  logsEventSource.onopen = () => {
    console.log('SSE connected to /api/logs/stream');
    const indicator = document.getElementById('live-feed-indicator');
    if (indicator) indicator.style.display = 'flex';
  };
  
  logsEventSource.onerror = (e) => {
    console.warn('SSE connection error, will auto-reconnect', e);
  };
  
  logsEventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleStreamEvent(data);
    } catch (e) {
      console.warn('Failed to parse SSE event:', e);
    }
  };
}

function disconnectLogsSSE() {
  if (logsEventSource) {
    logsEventSource.close();
    logsEventSource = null;
    console.log('SSE disconnected');
    const indicator = document.getElementById('live-feed-indicator');
    if (indicator) indicator.style.display = 'none';
  }
  // Hide live preview
  const container = document.getElementById('live-stream-container');
  if (container) container.style.display = 'none';
}

function handleStreamEvent(data) {
  const type = data.type;
  const requestId = data.request_id;
  
  if (type === 'stream_start') {
    // Initialize live stream state
    liveStreamState = {
      request_id: requestId,
      bot_name: data.bot_name || 'Unknown',
      bot_id: data.bot_id || '',
      user_name: data.user_name || '',
      user_id: data.user_id || '',
      channel_name: data.channel_name || '',
      channel_id: data.channel_id || '',
      accumulated_text: '',
      accumulated_reasoning: '',
      tools: [],
      steps: [],
      start_time: Date.now()
    };
    
    // Show live preview
    const container = document.getElementById('live-stream-container');
    if (container) container.style.display = 'block';
    
    const botName = document.getElementById('live-stream-bot-name');
    if (botName) botName.textContent = data.bot_name || 'Unknown';
    
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      statusBadge.textContent = '⏳ Initializing';
      statusBadge.style.background = 'rgba(59,130,246,0.2)';
      statusBadge.style.color = '#60a5fa';
    }
    
    const model = document.getElementById('live-stream-model');
    if (model) model.textContent = 'connecting…';
    
    const meta = document.getElementById('live-stream-meta');
    if (meta) meta.textContent = `User: ${data.user_name || '?'} · Channel: #${data.channel_name || '?'}`;
    
    const currentStep = document.getElementById('live-stream-current-step');
    if (currentStep) currentStep.innerHTML = `<span>⏳</span> <span>Starting request pipeline for <strong>${esc(data.bot_name || 'Bot')}</strong>...</span>`;
    
    const toolsWrapper = document.getElementById('live-stream-tools-wrapper');
    if (toolsWrapper) toolsWrapper.style.display = 'none';
    const toolsEl = document.getElementById('live-stream-tools');
    if (toolsEl) toolsEl.innerHTML = '';
    
    const reasoningWrap = document.getElementById('live-stream-reasoning-wrapper');
    if (reasoningWrap) reasoningWrap.style.display = 'none';
    const reasoningText = document.getElementById('live-stream-reasoning-text');
    if (reasoningText) reasoningText.textContent = '';
    
    const textEl = document.getElementById('live-stream-text');
    if (textEl) textEl.textContent = '▌';
    
  } else if (type === 'endpoint_attempt' && requestId === liveStreamState.request_id) {
    const model = document.getElementById('live-stream-model');
    if (model) model.textContent = `${data.endpoint_name} (${data.model})`;
    
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      statusBadge.textContent = `🌐 Connecting [${data.chain_index}/${data.chain_total}]`;
      statusBadge.style.background = 'rgba(99,102,241,0.2)';
      statusBadge.style.color = '#818cf8';
    }
    
    const currentStep = document.getElementById('live-stream-current-step');
    if (currentStep) {
      currentStep.innerHTML = `<span>🌐</span> <span>Sending prompt to endpoint <strong>${esc(data.endpoint_name)}</strong> (<em>${esc(data.model)}</em>) [Chain #${data.chain_index} of ${data.chain_total}]...</span>`;
    }
    
  } else if (type === 'endpoint_waiting' && requestId === liveStreamState.request_id) {
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      statusBadge.textContent = '⏳ Waiting for response...';
      statusBadge.style.background = 'rgba(234,179,8,0.2)';
      statusBadge.style.color = '#fbbf24';
    }
    
    const currentStep = document.getElementById('live-stream-current-step');
    if (currentStep) {
      currentStep.innerHTML = `<span>⏳</span> <span>Waiting for first token from <strong>${esc(data.endpoint_name || 'LLM')}</strong>${data.iteration > 1 ? ` (Iteration ${data.iteration})` : ''}...</span>`;
    }
    
  } else if (type === 'endpoint_failover' && requestId === liveStreamState.request_id) {
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      statusBadge.textContent = `🔄 Failover [${data.next_index}/${data.chain_total}]`;
      statusBadge.style.background = 'rgba(249,115,22,0.2)';
      statusBadge.style.color = '#fb923c';
    }
    
    const currentStep = document.getElementById('live-stream-current-step');
    if (currentStep) {
      currentStep.innerHTML = `<span style="color:#ef4444;">⚠️</span> <span style="color:#f87171;">Endpoint <strong>${esc(data.failed_endpoint)}</strong> failed (<em>${esc(data.error.substring(0, 80))}</em>). Failover to <strong>${esc(data.next_endpoint)}</strong> (<em>${esc(data.next_model)}</em>)...</span>`;
    }
    
  } else if (type === 'reasoning_delta' && requestId === liveStreamState.request_id) {
    liveStreamState.accumulated_reasoning = (liveStreamState.accumulated_reasoning || '') + (data.text || '');
    
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      statusBadge.textContent = '🧠 Thinking...';
      statusBadge.style.background = 'rgba(234,179,8,0.2)';
      statusBadge.style.color = '#fbbf24';
    }
    
    const reasoningWrap = document.getElementById('live-stream-reasoning-wrapper');
    if (reasoningWrap) reasoningWrap.style.display = 'block';
    
    const reasoningCount = document.getElementById('live-stream-reasoning-count');
    if (reasoningCount) reasoningCount.textContent = liveStreamState.accumulated_reasoning.length;
    
    const reasoningText = document.getElementById('live-stream-reasoning-text');
    if (reasoningText) {
      reasoningText.textContent = liveStreamState.accumulated_reasoning;
      reasoningText.scrollTop = reasoningText.scrollHeight;
    }
    
  } else if (type === 'text_delta' && requestId === liveStreamState.request_id) {
    liveStreamState.accumulated_text += (data.text || '');
    
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge && statusBadge.textContent !== '💬 Streaming...') {
      statusBadge.textContent = '💬 Streaming...';
      statusBadge.style.background = 'rgba(16,185,129,0.2)';
      statusBadge.style.color = '#34d399';
    }
    
    const textEl = document.getElementById('live-stream-text');
    if (textEl) {
      textEl.textContent = liveStreamState.accumulated_text + '▌';
      textEl.scrollTop = textEl.scrollHeight;
    }
    
  } else if (type === 'tool_call_start' && requestId === liveStreamState.request_id) {
    const toolsWrapper = document.getElementById('live-stream-tools-wrapper');
    if (toolsWrapper) toolsWrapper.style.display = 'block';
    
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      statusBadge.textContent = `🛠️ Calling ${data.tool_name}...`;
      statusBadge.style.background = 'rgba(168,85,247,0.2)';
      statusBadge.style.color = '#c084fc';
    }
    
    const currentStep = document.getElementById('live-stream-current-step');
    if (currentStep) {
      currentStep.innerHTML = `<span>🛠️</span> <span>Executing tool: <strong>${esc(data.tool_name)}</strong>...</span>`;
    }
    
    const toolsEl = document.getElementById('live-stream-tools');
    if (toolsEl) {
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.style.cssText = 'background:var(--bg-3); margin-right:4px; margin-bottom:4px; display:inline-flex; align-items:center; gap:6px; padding:4px 8px; font-size:11px;';
      badge.innerHTML = `<span style="animation:pulse-dot 1s ease-in-out infinite;width:6px;height:6px;border-radius:50%;background:var(--yellow);display:inline-block;"></span> <span>${esc(data.tool_name || 'tool')}</span>`;
      badge.id = `tool-badge-${data.tool_name}`;
      badge.title = JSON.stringify(data.arguments || {}, null, 2);
      toolsEl.appendChild(badge);
    }
    
  } else if (type === 'tool_call_result' && requestId === liveStreamState.request_id) {
    const badge = document.getElementById(`tool-badge-${data.tool_name}`);
    const isErr = !data.success || (data.status === 'error') || (data.result && (data.result.status === 'error' || data.result.error));
    
    if (badge) {
      if (isErr) {
        badge.innerHTML = `❌ <strong>${esc(data.tool_name || 'tool')}</strong> <span style="font-size:10px;">(failed)</span>`;
        badge.style.background = 'rgba(239,68,68,0.2)';
        badge.style.color = 'var(--red)';
        badge.style.border = '1px solid rgba(239,68,68,0.4)';
        badge.title = `Error: ${JSON.stringify(data.result || data.message || 'Unknown error')}`;
      } else {
        badge.innerHTML = `✅ <strong>${esc(data.tool_name || 'tool')}</strong> <span style="font-size:10px; color:#86efac;">(success)</span>`;
        badge.style.background = 'rgba(34,197,94,0.15)';
        badge.style.color = 'var(--green)';
        badge.style.border = '1px solid rgba(34,197,94,0.3)';
        badge.title = `Result: ${JSON.stringify(data.result || 'Success')}`;
      }
    }
    liveStreamState.tools.push({ name: data.tool_name, result: data.result, success: !isErr });
    
  } else if (type === 'stream_end' && requestId === liveStreamState.request_id) {
    // Finalize the live preview
    const textEl = document.getElementById('live-stream-text');
    if (textEl) {
      textEl.textContent = liveStreamState.accumulated_text || (data.refused ? '[REFUSED]' : (data.error ? `Error: ${data.error}` : 'No output'));
    }
    
    const model = document.getElementById('live-stream-model');
    if (model) model.textContent = data.model_used || '?';
    
    const statusBadge = document.getElementById('live-stream-status-badge');
    if (statusBadge) {
      if (data.error) {
        statusBadge.textContent = '❌ Failed';
        statusBadge.style.background = 'rgba(239,68,68,0.2)';
        statusBadge.style.color = '#f87171';
      } else if (data.refused) {
        statusBadge.textContent = '🚫 Refused';
        statusBadge.style.background = 'rgba(234,179,8,0.2)';
        statusBadge.style.color = '#fbbf24';
      } else {
        statusBadge.textContent = '✅ Completed';
        statusBadge.style.background = 'rgba(34,197,94,0.2)';
        statusBadge.style.color = '#4ade80';
      }
    }
    
    const elapsed = Date.now() - (liveStreamState.start_time || Date.now());
    const currentStep = document.getElementById('live-stream-current-step');
    if (currentStep) {
      if (data.error) {
        currentStep.innerHTML = `<span style="color:#ef4444;">❌</span> <span style="color:#f87171;">Pipeline finished with error: ${esc(data.error)}</span>`;
      } else {
        currentStep.innerHTML = `<span style="color:#22c55e;">✅</span> <span>Pipeline completed in <strong>${elapsed}ms</strong> · ${data.total_tokens || 0} tokens · ${data.tools_count || 0} tool call(s).</span>`;
      }
    }
    
    // Update meta with final stats
    const meta = document.getElementById('live-stream-meta');
    if (meta) {
      meta.textContent = `${data.total_tokens || 0} tokens (${data.prompt_tokens || 0} prompt + ${data.completion_tokens || 0} completion) · ${elapsed}ms`;
    }
    
    // Auto-hide after 15 seconds and refresh the table
    setTimeout(() => {
      const container = document.getElementById('live-stream-container');
      if (container && liveStreamState.request_id === requestId) {
        container.style.display = 'none';
        liveStreamState = {};
      }
    }, 15000);
    
    // Refresh the logs table to include the new entry
    setTimeout(() => loadLogs(), 1500);
  }
}

async function loadLogs() {
  try {
    const filterSelect = document.getElementById('logs-bot-filter');
    const limitInput = document.getElementById('logs-limit');
    
    // Populate bot filter if empty
    if (filterSelect && filterSelect.options.length <= 1) {
      const botsReq = await fetch('/api/bots');
      if (botsReq.ok) {
        const bots = await botsReq.json();
        bots.forEach(b => {
          const opt = document.createElement('option');
          opt.value = b.id;
          opt.textContent = b.name;
          filterSelect.appendChild(opt);
        });
      }
    }

    const botId = filterSelect ? filterSelect.value : '';
    const limit = limitInput ? limitInput.value : 10;
    
    const url = new URL(window.location.origin + '/api/logs');
    url.searchParams.append('limit', limit);
    if (botId) url.searchParams.append('bot_id', botId);

    const req = await fetch(url.toString());
    if (!req.ok) throw new Error('Failed to fetch logs');
    loadedLogs = await req.json();

    const tbody = document.getElementById('logs-table-body');
    if (!tbody) return;

    if (!loadedLogs.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-3);">No logs found.</td></tr>';
      return;
    }

    // Resolve bot names from allBots
    const botNameMap = {};
    allBots.forEach(b => { botNameMap[b.id] = b.name; });

    tbody.innerHTML = loadedLogs.map(log => {
      const time = (log.timestamp || '').replace('T', ' ').substring(0, 19);
      const isError = log.model_used === 'error' || log.error_message;
      
      const toolsCalled = Array.isArray(log.tools_called) ? log.tools_called : [];
      const failedTools = toolsCalled.filter(t => t.result && (t.result.status === 'error' || t.result.error));
      
      let toolBadge = '';
      if (failedTools.length > 0) {
        toolBadge = `<div style="margin-top:4px;"><span class="badge" style="background:rgba(239,68,68,0.2);color:var(--red);border:1px solid rgba(239,68,68,0.4);font-size:11px;">⚠️ Tool Error: ${esc(failedTools.map(t => t.name).join(', '))}</span></div>`;
      } else if (toolsCalled.length > 0) {
        toolBadge = `<div style="margin-top:4px;"><span class="badge" style="background:rgba(34,197,94,0.15);color:var(--green);font-size:11px;">🛠️ ${toolsCalled.length} tool${toolsCalled.length > 1 ? 's' : ''} OK</span></div>`;
      }
      
      const statusBadge = isError 
        ? '<span class="badge badge-global" style="background:var(--red);">Error</span>' 
        : (log.refused ? '<span class="badge badge-global" style="background:var(--yellow);color:#000;">Refused</span>' : '<span class="badge badge-provider">Success</span>');
      
      const botDisplay = botNameMap[log.bot_id] || (log.bot_id ? log.bot_id.substring(0,8)+'…' : 'Unknown');
        
      return `<tr>
        <td><code style="font-size:11px;">${time}</code></td>
        <td>
          <div style="font-size:12px;"><strong>Bot:</strong> ${esc(botDisplay)}</div>
          <div style="font-size:11px;color:var(--text-3);"><strong>User:</strong> ${log.user_id || '-'}</div>
        </td>
        <td><span class="badge" style="background:var(--bg-3);">${log.model_used || '-'}</span></td>
        <td><code>${log.total_tokens || 0}</code></td>
        <td>
          ${statusBadge}
          ${toolBadge}
        </td>
        <td>
          <button class="btn btn-sm btn-secondary" onclick="openLogDetails('${log.id}')">View Details</button>
        </td>
      </tr>`;
    }).join('');

  } catch (e) {
    console.error('Logs load error:', e);
    showToast('Failed to load logs', 'error');
  }
}

function openLogDetails(id) {
  const log = loadedLogs.find(l => l.id === id);
  if (!log) return;
  
  document.getElementById('log-detail-system').textContent = log.system_prompt || 'N/A';
  document.getElementById('log-detail-input').textContent = log.input_text || 'N/A';
  document.getElementById('log-detail-output').textContent = log.output_text || 'N/A';
  
  const errContainer = document.getElementById('log-detail-error-container');
  if (log.error_message) {
    errContainer.style.display = 'block';
    document.getElementById('log-detail-error').textContent = log.error_message;
  } else {
    errContainer.style.display = 'none';
  }
  
  const toolsContainer = document.getElementById('log-detail-tools');
  const toolsCalled = Array.isArray(log.tools_called) ? log.tools_called : [];
  if (toolsCalled.length > 0) {
    toolsContainer.innerHTML = toolsCalled.map((t, idx) => {
      const isErr = t.result && (t.result.status === 'error' || t.result.error);
      const errMsg = isErr ? (t.result.message || t.result.error || JSON.stringify(t.result)) : null;
      return `<div style="background:var(--bg-1); border:1px solid ${isErr ? 'rgba(239,68,68,0.5)' : 'var(--border)'}; border-radius:6px; padding:10px; margin-bottom:8px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <strong style="color:${isErr ? 'var(--red)' : 'var(--text-0)'}; font-size:13px;">${isErr ? '❌' : '✓'} ${esc(t.name || 'tool')}</strong>
          <span class="badge" style="background:${isErr ? 'rgba(239,68,68,0.2)' : 'rgba(34,197,94,0.2)'}; color:${isErr ? 'var(--red)' : 'var(--green)'};">
            ${isErr ? 'Failed' : 'Success'}
          </span>
        </div>
        ${isErr ? `<div style="background:rgba(239,68,68,0.1); border-left:3px solid var(--red); padding:6px 10px; font-size:12px; color:var(--red); margin-bottom:6px; border-radius:3px;"><strong>Error:</strong> ${esc(errMsg)}</div>` : ''}
        <div style="font-size:11px; color:var(--text-2); margin-bottom:4px;"><strong>Arguments:</strong></div>
        <pre style="background:var(--bg-0); padding:6px; border-radius:4px; font-size:11px; margin:0 0 6px 0; overflow-x:auto;">${esc(JSON.stringify(t.arguments || {}, null, 2))}</pre>
        <div style="font-size:11px; color:var(--text-2); margin-bottom:4px;"><strong>Result:</strong></div>
        <pre style="background:var(--bg-0); padding:6px; border-radius:4px; font-size:11px; margin:0; overflow-x:auto;">${esc(JSON.stringify(t.result || {}, null, 2))}</pre>
      </div>`;
    }).join('');
  } else {
    toolsContainer.innerHTML = '<div style="color:var(--text-3); font-size:12px;">None</div>';
  }
  
  openModal('modal-log-details');
}

// ── Utils ──
function esc(s){return s?String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'):''}

// ── Init ──
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
  document.getElementById('form-endpoint')?.addEventListener('submit',saveEndpoint);
  document.getElementById('form-bot')?.addEventListener('submit',saveBot);
  document.getElementById('form-memory')?.addEventListener('submit',saveMemory);
  document.getElementById('sim-input-text')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendPlaygroundMessage();}});
  switchTab('dashboard');
});
