
/* Minimal New Report Wizard for FM MVP
   - Picks report type
   - Creates artefact via /api/artefacts
   - Redirects to /artefacts/<id>/edit
*/
(function () {
  'use strict';
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const $ = (id) => document.getElementById(id);

  const state = { type: null };

  async function api(url, bodyObj) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bodyObj),
      credentials: 'same-origin'
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = j.error || msg; } catch(e) {}
      throw new Error(msg);
    }
    return res.json();
  }

  function showStep(n) {
    ['step1','step2','step3'].forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.style.display = (id === `step${n}`) ? 'block' : 'none';
    });
  }

  function initTypeCards() {
    $$('.type-card').forEach((card) => {
      card.addEventListener('click', () => {
        $$('.type-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        state.type = card.getAttribute('data-type');
      });
    });
  }

  async function onCreate() {
    const title = $('title') ? $('title').value.trim() : '';
    if (!state.type) throw new Error('Select a report type.');
    if (!title) throw new Error('Enter a title.');

    const created = await api('/api/artefacts', {
      artefact_type: state.type,
      title: title,
      status: 'Draft',
      modules_enabled: state.modules || [],
      created_by: ($('analyst') && $('analyst').value.trim()) ? $('analyst').value.trim() : 'system'
    });

    // API returns artefact_id
    const id = created.artefact_id || created.id;
    if (!id) throw new Error('Create succeeded but no artefact id returned.');

    window.location.href = `/artefacts/${id}/edit`;
  }

  let _currentStep = 1;

  function goStep(n) {
    showStep(n);
    _currentStep = n;
    const back   = $('btn-back');
    const next   = $('btn-next');
    const create = $('btn-create');
    if (back)   back.style.display   = n === 1 ? 'none' : 'inline-block';
    if (next)   next.style.display   = n === 3 ? 'none' : 'inline-block';
    if (create) create.style.display = n === 3 ? 'inline-block' : 'none';
  }

  function initNavButtons() {
    const next = $('btn-next');
    const back = $('btn-back');
    const create = $('btn-create');

    if (next) next.addEventListener('click', () => {
      if (_currentStep === 1) {
        if (!state.type) { alert('Select a report type.'); return; }
        // Populate selected modules summary
        goStep(2);
      } else if (_currentStep === 2) {
        const title = $('title') ? $('title').value.trim() : '';
        const modules = Array.from(document.querySelectorAll('.module-card input[type=checkbox]:checked'))
                             .map(cb => cb.value);
        state.modules = modules;
        // Update summary
        const typeLabels = {engagement:'Inspection Report',incident:'Incident Report',investigation:'Investigation',assessment:'Assessment'};
        if ($('summary-type'))    $('summary-type').textContent    = typeLabels[state.type] || state.type;
        if ($('summary-modules')) $('summary-modules').textContent = modules.length ? modules.join(', ') : 'None';
        if ($('summary-title'))   $('summary-title').textContent   = title || '(untitled)';
        goStep(3);
      }
    });

    if (back) back.addEventListener('click', () => {
      goStep(_currentStep === 3 ? 2 : 1);
    });

    if (create) create.addEventListener('click', () => onCreate().catch(e => alert(e.message)));
  }

  function init() {
    initTypeCards();
    initNavButtons();
    // Basic step visibility: show step1, keep others hidden
    showStep(1);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
