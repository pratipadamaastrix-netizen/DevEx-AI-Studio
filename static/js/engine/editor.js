
/* Minimal FM MVP Editor
   - Loads latest payload
   - Loads context options
   - Allows saving new versions (meta + context only)
*/
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const state = {
    artefactId: null,
    latestVersionNo: null,
    payload: null,
    contextOptions: null
  };

  async function api(url, options) {
    const res = await fetch(url, Object.assign({
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin'
    }, options || {}));
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = j.error || msg; } catch(e) {}
      throw new Error(msg);
    }
    return res.json();
  }

  async function loadContextOptions() {
    // Served by /api/context-options (added in app.py patch)
    const data = await api('/api/context-options', { method: 'GET' });
    state.contextOptions = data;

    // Populate selects
    const journeySel = $('context-journey');
    const incidentSel = $('context-incident-type');
    const audienceSel = $('context-audience');
    const fwSel = $('context-framework');
    const fwVerSel = $('context-framework-version');

    function fillSelect(sel, items, placeholder) {
      sel.innerHTML = '';
      const opt0 = document.createElement('option');
      opt0.value = '';
      opt0.textContent = placeholder;
      sel.appendChild(opt0);
      items.forEach((it) => {
        const opt = document.createElement('option');
        opt.value = (typeof it === 'string') ? it : it.key;
        opt.textContent = (typeof it === 'string') ? it : it.name;
        sel.appendChild(opt);
      });
    }

    fillSelect(journeySel, data.journeys || [], 'Select journey...');
    fillSelect(incidentSel, data.incident_types || [], 'Select incident type...');
    fillSelect(audienceSel, data.audiences || [], 'Select audience...');
    fillSelect(fwSel, data.analysis_frameworks || [], 'Select framework...');

    fwSel.addEventListener('change', () => {
      const key = fwSel.value || '';
      const fw = (data.analysis_frameworks || []).find(x => x.key === key);
      fillSelect(fwVerSel, fw ? (fw.versions || []) : [], 'Select version...');
    });
  }

  function applyPayloadToForm(payload) {
    const meta = (payload && payload.meta) ? payload.meta : {};
    const ctx = meta.context || {};
    const fw = ctx.analysis_framework || {};

    $('meta-title').value = meta.title || '';
    $('meta-status').value = meta.status || 'Draft';
    $('meta-date').value = meta.date || '';
    $('meta-analyst').value = meta.analyst || '';

    $('context-journey').value = ctx.journey || '';
    $('context-incident-type').value = ctx.incident_type || '';
    $('context-audience').value = ctx.audience || '';
    $('context-framework').value = fw.key || '';

    // Trigger version select fill then set
    const fwSel = $('context-framework');
    fwSel.dispatchEvent(new Event('change'));
    $('context-framework-version').value = fw.version || '';
  }

  function buildPayloadFromForm() {
    const p = JSON.parse(JSON.stringify(state.payload || {}));
    if (!p.meta) p.meta = {};
    if (!p.meta.context) p.meta.context = {};
    if (!p.meta.context.analysis_framework) p.meta.context.analysis_framework = { key: '', version: '' };

    p.meta.title = $('meta-title').value || p.meta.title || '';
    p.meta.status = $('meta-status').value || p.meta.status || 'Draft';
    p.meta.date = $('meta-date').value || p.meta.date || '';
    p.meta.analyst = $('meta-analyst').value || p.meta.analyst || '';

    p.meta.context.journey = $('context-journey').value || '';
    p.meta.context.incident_type = $('context-incident-type').value || '';
    p.meta.context.audience = $('context-audience').value || '';
    p.meta.context.analysis_framework.key = $('context-framework').value || '';
    p.meta.context.analysis_framework.version = $('context-framework-version').value || '';

    return p;
  }

  async function loadLatestVersionPayload() {
    const versions = await api(`/api/artefacts/${state.artefactId}/versions`, { method: 'GET' });
    if (!Array.isArray(versions) || versions.length === 0) {
      throw new Error('No versions found for this report.');
    }
    state.latestVersionNo = versions[0].version_no;
    const v = await api(`/api/artefacts/${state.artefactId}/versions/${state.latestVersionNo}`, { method: 'GET' });
    state.payload = v.payload;
  }

  async function onSave() {
    const payload = buildPayloadFromForm();
    const notes = $('version-notes') ? $('version-notes').value : '';
    const result = await api(`/api/artefacts/${state.artefactId}/versions`, {
      method: 'POST',
      body: JSON.stringify({
        payload: payload,
        version_notes: notes,
        created_by: payload.meta && payload.meta.analyst ? payload.meta.analyst : 'system'
      })
    });

    // Show confirmation
    const conf = $('save-confirmation');
    if (conf) conf.classList.remove('hidden');
    const savedNo = $('saved-version-no');
    if (savedNo) savedNo.textContent = result.version_no || '';
    const link = $('saved-version-link');
    if (link) {
      link.href = `/artefacts/${state.artefactId}/v/${result.version_no || state.latestVersionNo}`;
    }

    // Refresh latest payload
    state.latestVersionNo = result.version_no || state.latestVersionNo;
    state.payload = payload;
    if ($('version-notes')) $('version-notes').value = '';
  }

  function onPreview() {
    const vno = state.latestVersionNo || 1;
    window.open(`/artefacts/${state.artefactId}/v/${vno}`, '_blank', 'noopener');
  }

  async function init() {
    const idEl = $('artefact-id');
    if (!idEl) return;
    state.artefactId = idEl.value;

    try {
      await loadContextOptions();
      await loadLatestVersionPayload();
      applyPayloadToForm(state.payload);
    } catch (e) {
      console.error(e);
      alert(`Editor failed to load: ${e.message}`);
    }

    const btnSave = $('btn-save-version');
    if (btnSave) btnSave.addEventListener('click', () => onSave().catch(e => alert(e.message)));

    const btnPrev = $('btn-preview');
    if (btnPrev) btnPrev.addEventListener('click', onPreview);

    // Default date if empty
    if ($('meta-date') && !$('meta-date').value) {
      const d = new Date();
      $('meta-date').value = d.toISOString().slice(0,10);
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
