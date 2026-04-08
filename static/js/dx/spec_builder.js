/**
 * Devex AI Studios — Spec Scope Builder
 * static/js/dx/spec_builder.js
 *
 * Handles: section navigation, answer autosave, AI analysis,
 * section/question CRUD, progress tracking, status updates.
 */

/* global SPEC_ID, SPEC_SECTIONS, COMPLETION, TOTAL_Q, ANSWERED_Q */

let currentSectionId = null;
let autosaveTimers = {};
let totalQ = TOTAL_Q;
let answeredQ = ANSWERED_Q;

// ── Init ─────────────────────────────────────────────────────────────────

function initSpecBuilder() {
  if (SPEC_SECTIONS.length > 0) {
    currentSectionId = SPEC_SECTIONS[0].id;
  }
  bindAutosave();
  updateProgress(totalQ, answeredQ);
}

// ── Section Navigation ──────────────────────────────────────────────────

function gotoSection(sectionId) {
  // Hide all sections — support both old and new class names
  document.querySelectorAll('.spec-section, .rm-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.spec-nav-item, .rr-item').forEach(el => el.classList.remove('active'));

  // Show target
  const sec = document.getElementById('sec-' + sectionId);
  const nav = document.getElementById('nav-' + sectionId);
  if (sec) sec.classList.add('active');
  if (nav) nav.classList.add('active');
  currentSectionId = sectionId;

  // Scroll main to top
  const main = document.getElementById('specMain');
  if (main) main.scrollTop = 0;

  // Hide add-section form
  if (typeof hideAddSectionForm === 'function') hideAddSectionForm();
}

// ── Answer Autosave ─────────────────────────────────────────────────────

function bindAutosave() {
  // Text + textarea inputs
  document.querySelectorAll('.q-input, .q-textarea, .q-date').forEach(el => {
    const qid = el.getAttribute('data-qid');
    if (!qid) return;
    el.addEventListener('input', () => debounceAutosave(qid, el.value, []));
  });
}

function debounceAutosave(qid, text, jsonVal) {
  if (autosaveTimers[qid]) clearTimeout(autosaveTimers[qid]);
  autosaveTimers[qid] = setTimeout(() => saveAnswer(qid, text, jsonVal), 800);
}

function saveAnswer(qid, text, jsonVal) {
  const body = { question_id: parseInt(qid), answer_text: text || '', answer_json: jsonVal || [] };

  fetch(`/studio/specs/${SPEC_ID}/autosave`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        // Flash save indicator
        const ind = document.getElementById('save-ind-' + qid);
        if (ind) {
          ind.classList.add('show');
          setTimeout(() => ind.classList.remove('show'), 2000);
        }
        // Update question status
        const hasAnswer = (text && text.trim()) || (jsonVal && jsonVal.length > 0);
        updateQuestionStatus(qid, hasAnswer);
        // Update section dot
        if (d.section_status) {
          const dot = document.getElementById('dot-' + getSectionForQuestion(qid));
          if (dot) {
            dot.className = 'rr-dot ' + d.section_status;
          }
        }
        // Update progress
        totalQ = d.total_q;
        answeredQ = d.answered_q;
        updateProgress(d.total_q, d.answered_q);
      }
    })
    .catch(e => console.error('Autosave error:', e));
}

function getSectionForQuestion(qid) {
  const card = document.querySelector(`[data-question-id="${qid}"]`);
  return card ? card.getAttribute('data-section-id') : null;
}

function updateQuestionStatus(qid, answered) {
  const qs = document.getElementById('qs-' + qid);
  const label = document.getElementById('qs-label-' + qid);
  const card = document.getElementById('qcard-' + qid);
  if (qs) {
    qs.className = 'q-status' + (answered ? ' answered' : '');
  }
  if (label) {
    label.textContent = answered ? 'Answered' : 'Not answered';
  }
  if (card) {
    if (answered) card.classList.add('answered-card');
    else card.classList.remove('answered-card');
  }
}

function updateProgress(total, answered) {
  const pct = total > 0 ? Math.round((answered / total) * 100) : 0;
  const fill = document.getElementById('mainProgFill');
  const label = document.getElementById('mainProgLabel');
  const asideTotal = document.getElementById('asideTotalQ');
  const asideAns = document.getElementById('asideAnsweredQ');
  if (fill) fill.style.width = pct + '%';
  if (label) label.textContent = answered + '/' + total + ' answered';
  if (asideTotal) asideTotal.textContent = total;
  if (asideAns) asideAns.textContent = answered;
}

// ── Yes/No Selection ────────────────────────────────────────────────────

function selectYesNo(btn) {
  const qid = btn.getAttribute('data-qid');
  const value = btn.getAttribute('data-value');
  // Clear siblings
  btn.parentNode.querySelectorAll('.q-yn-btn').forEach(b => {
    b.className = 'q-yn-btn';
  });
  // Apply selection class
  if (value === 'Yes') btn.classList.add('selected-yes');
  else if (value === 'No') btn.classList.add('selected-no');
  else btn.style.borderColor = '#94a3b8';
  saveAnswer(qid, value, []);
}

// ── Radio Selection ─────────────────────────────────────────────────────

function selectRadio(row, qid) {
  row.closest('.q-options').querySelectorAll('.q-opt-row').forEach(r => r.classList.remove('selected'));
  row.classList.add('selected');
  const value = row.querySelector('.q-opt-label').textContent.trim();
  saveAnswer(qid, value, []);
}

// ── Checkbox Toggle ─────────────────────────────────────────────────────

function toggleCheckbox(row, qid, value) {
  row.classList.toggle('selected');
  // Collect all selected
  const selected = [];
  row.closest('.q-options').querySelectorAll('.q-opt-row.selected').forEach(r => {
    selected.push(r.querySelector('.q-opt-label').textContent.trim());
  });
  saveAnswer(qid, selected.join(', '), selected);
}

// ── Chip Toggle ─────────────────────────────────────────────────────────

function toggleChip(chip, qid) {
  chip.classList.toggle('selected');
  const container = document.getElementById('chips-' + qid);
  const selected = [];
  container.querySelectorAll('.q-chip.selected').forEach(c => {
    selected.push(c.getAttribute('data-value'));
  });
  saveAnswer(qid, selected.join(', '), selected);
}

// ── Risk Level ──────────────────────────────────────────────────────────

function selectRisk(btn) {
  const qid = btn.getAttribute('data-qid');
  const level = btn.getAttribute('data-level');
  btn.closest('.q-risk-level').querySelectorAll('.q-risk-btn').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
  saveAnswer(qid, level, []);
}

// ── Priority ────────────────────────────────────────────────────────────

function selectPriority(btn) {
  const qid = btn.getAttribute('data-qid');
  const priority = btn.getAttribute('data-priority');
  btn.closest('.q-priority').querySelectorAll('.q-pri-btn').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
  saveAnswer(qid, priority, []);
}

// ── Status ──────────────────────────────────────────────────────────────

function updateStatus(newStatus) {
  fetch(`/studio/specs/${SPEC_ID}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: newStatus }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        toast('Status updated to ' + newStatus.replace('_', ' '));
        const pill = document.getElementById('statusPill');
        if (pill) {
          pill.className = 'spec-status-pill pill-' + newStatus;
          pill.textContent = newStatus.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
        }
      }
    });
}

function cycleStatus() {
  const order = ['draft', 'in_review', 'final', 'archived'];
  const sel = document.getElementById('statusSelect');
  if (!sel) return;
  const idx = order.indexOf(sel.value);
  const next = order[(idx + 1) % order.length];
  sel.value = next;
  updateStatus(next);
}

// ── Title autosave ──────────────────────────────────────────────────────

let titleTimer = null;
document.addEventListener('DOMContentLoaded', function() {
  const titleInput = document.getElementById('specTitleInput');
  if (titleInput) {
    titleInput.addEventListener('input', function() {
      if (titleTimer) clearTimeout(titleTimer);
      titleTimer = setTimeout(() => {
        fetch(`/studio/specs/${SPEC_ID}/update`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: titleInput.value }),
        })
          .then(r => r.json())
          .then(d => { if (d.ok) toast('Title saved'); });
      }, 1000);
    });
  }
});

// ── AI Analysis ─────────────────────────────────────────────────────────

function runAnalysis() {
  const btns = document.querySelectorAll('#analyseBtn, #analyseBtn2');
  btns.forEach(b => { b.disabled = true; b.innerHTML = '<span class="spin"></span> Analysing...'; });

  const failCard = document.getElementById('aiFailCard');
  if (failCard) failCard.style.display = 'none';

  fetch(`/studio/specs/${SPEC_ID}/analyse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
    .then(r => {
      if (!r.ok) throw new Error('Server error ' + r.status);
      return r.json();
    })
    .then(d => {
      btns.forEach(b => { b.disabled = false; b.textContent = '✦ Re-run Analysis'; });
      if (d.ok && d.result) {
        toast('AI analysis complete');
        renderAiResult(d.result);
      } else {
        showAiFail(d.error || 'Unknown error');
      }
    })
    .catch(e => {
      btns.forEach(b => { b.disabled = false; b.textContent = '✦ Analyse'; });
      showAiFail(e.message);
    });
}

function renderAiResult(ai) {
  const panel = document.getElementById('aiResultPanel');
  if (!panel) return;

  let html = '<div class="ai-result-panel">';
  const cx = ai.complexity_score || '';
  if (cx) {
    html += `<span class="ai-cx-badge cx-${cx}">${cx}`;
    if (ai.estimated_weeks) html += ` · ~${ai.estimated_weeks} weeks`;
    html += '</span>';
  }
  if (ai.executive_summary) {
    html += `<div class="ai-summary-text">${escHtml(ai.executive_summary)}</div>`;
  }
  if (ai.gaps_found && ai.gaps_found.length) {
    html += '<div class="aside-card-title" style="margin-bottom:6px">Gaps Found</div>';
    html += '<div style="background:#fffbeb;border-radius:6px;padding:8px;margin-bottom:10px">';
    ai.gaps_found.forEach(g => { html += `<div class="ai-gap-item">▲ ${escHtml(g)}</div>`; });
    html += '</div>';
  }
  if (ai.risks && ai.risks.length) {
    html += '<div class="aside-card-title" style="margin-bottom:6px">Risks</div>';
    ai.risks.forEach(r => {
      const sev = r.severity || 'Medium';
      const text = r.risk || r;
      let sevStyle = 'background:#fef3c7;color:#92400e';
      if (sev === 'Critical') sevStyle = 'background:#fef2f2;color:#b91c1c';
      else if (sev === 'High') sevStyle = 'background:#fff7ed;color:#9a3412';
      html += `<div class="ai-risk-item"><span style="font-size:10px;padding:1px 6px;border-radius:3px;flex-shrink:0;${sevStyle}">${sev}</span><span style="font-size:11px;color:#374151">${escHtml(typeof text === 'string' ? text : JSON.stringify(text))}</span></div>`;
    });
  }
  html += '</div>';
  panel.innerHTML = html;
}

function showAiFail(msg) {
  const failCard = document.getElementById('aiFailCard');
  const failMsg = document.getElementById('aiFailMsg');
  if (failCard) failCard.style.display = 'block';
  if (failMsg) failMsg.textContent = msg;
  toast('AI analysis failed', false);
}

// ── Section CRUD ────────────────────────────────────────────────────────

function showAddSectionForm() {
  const form = document.getElementById('addSecForm');
  if (!form) return; // Not available in client view
  form.style.display = 'block';
  document.querySelectorAll('.spec-section, .rm-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.spec-nav-item, .rr-item').forEach(el => el.classList.remove('active'));
  const titleInput = document.getElementById('newSecTitle');
  if (titleInput) titleInput.focus();
}
function hideAddSectionForm() {
  const form = document.getElementById('addSecForm');
  if (form) form.style.display = 'none';
}

function addSection() {
  const title = document.getElementById('newSecTitle').value.trim();
  const desc = document.getElementById('newSecDesc').value.trim();
  if (!title) return toast('Section title required', false);

  fetch(`/studio/specs/${SPEC_ID}/sections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, description: desc }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        toast('Section added — reloading...');
        setTimeout(() => location.reload(), 600);
      } else toast(d.error || 'Failed', false);
    });
}

function updateSection(secId) {
  const title = document.getElementById('sec-title-' + secId).value.trim();
  const desc = document.getElementById('sec-desc-' + secId).value.trim();
  if (!title) return toast('Title required', false);

  fetch(`/studio/specs/${SPEC_ID}/sections/${secId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, description: desc }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { toast('Section updated'); setTimeout(() => location.reload(), 600); }
      else toast('Failed', false);
    });
}

function deleteSection(secId, title) {
  if (!confirm(`Delete section "${title}" and all its questions? This cannot be undone.`)) return;

  fetch(`/studio/specs/${SPEC_ID}/sections/${secId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'delete' }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { toast('Section deleted'); setTimeout(() => location.reload(), 600); }
      else toast('Failed', false);
    });
}

// ── Question CRUD ───────────────────────────────────────────────────────

function addQuestion(secId) {
  const label = document.getElementById('new-q-label-' + secId).value.trim();
  const fieldType = document.getElementById('new-q-type-' + secId).value;
  const helpText = document.getElementById('new-q-help-' + secId).value.trim();
  const optionsRaw = document.getElementById('new-q-options-' + secId).value.trim();

  if (!label) return toast('Question label required', false);

  const options = optionsRaw ? optionsRaw.split('\n').map(o => o.trim()).filter(Boolean) : [];

  fetch(`/studio/specs/${SPEC_ID}/questions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section_id: secId, label, field_type: fieldType, help_text: helpText, options }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { toast('Question added'); setTimeout(() => location.reload(), 600); }
      else toast(d.error || 'Failed', false);
    });
}

function showEditQuestion(qid) {
  // For simplicity, edit via prompt dialogs
  const card = document.getElementById('qcard-' + qid);
  if (!card) return;
  const labelEl = card.querySelector('.q-label');
  const currentLabel = labelEl ? labelEl.textContent.trim().replace('Required', '').trim() : '';
  const newLabel = prompt('Edit question label:', currentLabel);
  if (!newLabel || newLabel.trim() === currentLabel) return;

  fetch(`/studio/specs/${SPEC_ID}/questions/${qid}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label: newLabel.trim() }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) { toast('Question updated'); setTimeout(() => location.reload(), 500); }
      else toast(d.error || 'Failed', false);
    });
}

function deleteQuestion(qid, label) {
  if (!confirm(`Delete question "${label}"?`)) return;

  fetch(`/studio/specs/${SPEC_ID}/questions/${qid}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'delete' }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        const card = document.getElementById('qcard-' + qid);
        if (card) card.remove();
        toast('Question deleted');
      } else toast('Failed', false);
    });
}

// ── Delete Spec ─────────────────────────────────────────────────────────

function deleteSpec() {
  if (!confirm('Delete this entire spec document? This cannot be undone.')) return;
  if (!confirm('Are you sure? All sections, questions, and answers will be permanently deleted.')) return;

  fetch(`/studio/specs/${SPEC_ID}/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) window.location.href = '/studio/specs';
    });
}

// ── Toast ───────────────────────────────────────────────────────────────

function toast(msg, ok) {
  if (ok === undefined) ok = true;
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = (ok ? '✓ ' : '✗ ') + msg;
  t.style.background = ok ? '#0d1117' : '#7f1d1d';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

// ── Util ────────────────────────────────────────────────────────────────

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
