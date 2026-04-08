/**
 * FM Operations Dashboard — Main JavaScript
 * static/js/fm/dashboard.js
 * Batch FM3
 *
 * All state in-memory; persisted via /fm/api/* REST endpoints.
 * Requires: import.js (loaded first via template)
 */

const FM = (() => {
    'use strict';

    let _activeRef   = null;
    let _importParsed = null;
    let _tabFilter   = '';

    // =========================================================================
    // TICKET SELECTION
    // =========================================================================

    function selectTicket(ref) {
        _activeRef = ref;

        // Highlight active row
        document.querySelectorAll('.fm-ticket').forEach(el => {
            el.classList.toggle('active', el.dataset.ref === ref);
        });

        _loadThread(ref);
        _loadDetail(ref);
    }

    async function _loadThread(ref) {
        const log = document.getElementById('fm-chatlog');
        log.innerHTML = '<div style="padding:24px;text-align:center;color:var(--muted2)">Loading…</div>';

        try {
            const r   = await fetch(`/fm/api/tickets/${ref}/messages`);
            const msgs = await r.json();
            _renderThread(msgs);
        } catch(e) {
            log.innerHTML = '<div style="padding:24px;color:var(--bad)">Failed to load thread</div>';
        }
    }

    function _renderThread(msgs) {
        const log = document.getElementById('fm-chatlog');
        if (!msgs.length) {
            log.innerHTML = '<div style="padding:24px;text-align:center;color:var(--muted2);font-size:13px">No messages yet</div>';
            return;
        }
        log.innerHTML = msgs.map(m => {
            const cls = m.sender === 'customer' ? 'customer' : m.sender === 'ai' ? 'ai' : '';
            const internal = m.is_internal
                ? '<span class="fm-badge" style="font-size:10px;padding:2px 6px;margin-left:6px">internal</span>'
                : '';
            return `<div class="fm-msg ${cls}">
                <div class="fm-mmeta">${_esc(m.created_at.slice(0,16))} UTC · ${_esc(m.sender)} · ${_esc(m.source||'')}${internal}</div>
                <div class="fm-mbody">${_esc(m.body)}</div>
            </div>`;
        }).join('');
        log.scrollTop = log.scrollHeight;
    }

    async function _loadDetail(ref) {
        try {
            const r = await fetch(`/fm/api/tickets/${ref}`);
            const t = await r.json();

            document.getElementById('fm-detail-ref').textContent = ref;
            _setVal('d-estate',    t.estate);
            _setVal('d-unit',      t.unit);
            _setVal('d-customer',  t.customer);
            _setVal('d-source',    t.source);
            _setVal('d-status',    t.status);
            _setVal('d-priority',  t.priority);
            _setVal('d-category',  t.category);
            _setVal('d-assignee',  t.assignee);
            _setVal('d-summary',   t.summary);
            _setVal('d-materials', t.materials);

            // Thread status badges
            const bp = document.getElementById('fm-thread-badges');
            if (bp) {
                bp.innerHTML = [
                    `<span class="fm-badge warn">${_esc(t.status)}</span>`,
                    `<span class="fm-badge info">${_esc(t.category)}</span>`,
                    `<span class="fm-badge ${t.priority==='urgent'?'bad':t.priority==='low'?'good':'warn'}">${_esc(t.priority)}</span>`
                ].join('');
            }

            // Deep link to ticket detail page
            const link = document.getElementById('fm-detail-link');
            if (link) link.href = `/fm/ticket/${ref}`;

        } catch(e) {
            console.error('Detail load failed', e);
        }
    }

    function _setVal(id, val) {
        const el = document.getElementById(id);
        if (!el) return;
        el.value = val || '';
    }

    // =========================================================================
    // QUEUE FILTERS
    // =========================================================================

    function filterTab(el, mode) {
        document.querySelectorAll('.fm-tab').forEach(t => t.classList.remove('active'));
        el.classList.add('active');
        _tabFilter = mode;
        _applyQueueFilter();
    }

    function applyFilters() { _applyQueueFilter(); }

    function _applyQueueFilter() {
        const q      = (document.getElementById('fm-search')?.value || '').toLowerCase();
        const status = (document.getElementById('fm-filter-status')?.value || '').toLowerCase();
        const estate = (document.getElementById('fm-filter-estate')?.value || '').toLowerCase();

        document.querySelectorAll('.fm-ticket').forEach(row => {
            const rowStatus   = (row.dataset.status   || '').toLowerCase();
            const rowPriority = (row.dataset.priority || '').toLowerCase();
            const rowEstate   = (row.dataset.estate   || '').toLowerCase();
            const rowText     = row.textContent.toLowerCase();

            let show = true;
            if (q      && !rowText.includes(q))           show = false;
            if (status && rowStatus !== status)            show = false;
            if (estate && rowEstate !== estate)            show = false;

            // Tab filters
            if (_tabFilter === 'urgent'   && rowPriority !== 'urgent')         show = false;
            if (_tabFilter === 'assigned' && rowStatus   !== 'assigned')        show = false;
            if (_tabFilter === 'waiting'  && rowStatus   !== 'waiting_customer') show = false;
            if (_tabFilter === 'blocked'  && rowStatus   !== 'blocked')          show = false;

            row.style.display = show ? '' : 'none';
        });
    }

    // =========================================================================
    // SAVE TICKET
    // =========================================================================

    async function saveTicket() {
        if (!_activeRef) return toast('Select a ticket first', 'error');

        const payload = {
            estate:    document.getElementById('d-estate')?.value,
            unit:      document.getElementById('d-unit')?.value,
            customer:  document.getElementById('d-customer')?.value,
            status:    document.getElementById('d-status')?.value,
            priority:  document.getElementById('d-priority')?.value,
            category:  document.getElementById('d-category')?.value,
            assignee:  document.getElementById('d-assignee')?.value,
            summary:   document.getElementById('d-summary')?.value,
            materials: document.getElementById('d-materials')?.value,
        };

        try {
            const r = await fetch(`/fm/api/tickets/${_activeRef}`, {
                method: 'PATCH',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(payload)
            });
            if (r.ok) {
                toast('Ticket saved', 'success');
                _updateQueueRow(_activeRef, payload);
            } else {
                const e = await r.json();
                toast('Save failed: ' + (e.error || ''), 'error');
            }
        } catch(e) {
            toast('Network error', 'error');
        }
    }

    function _updateQueueRow(ref, data) {
        const row = document.querySelector(`[data-ref="${ref}"]`);
        if (!row) return;
        if (data.status)   row.dataset.status   = data.status;
        if (data.priority) row.dataset.priority = data.priority;
        if (data.estate)   row.dataset.estate   = data.estate;
    }

    // =========================================================================
    // SEND REPLY
    // =========================================================================

    async function sendReply() {
        if (!_activeRef) return toast('Select a ticket first', 'error');
        const body = document.getElementById('fm-reply-input')?.value.trim();
        if (!body) return toast('Enter a message first', 'error');

        try {
            const r = await fetch(`/fm/api/tickets/${_activeRef}/messages`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({body, sender:'staff', source:'staff'})
            });
            if (r.ok) {
                document.getElementById('fm-reply-input').value = '';
                await _loadThread(_activeRef);
            } else {
                toast('Failed to send', 'error');
            }
        } catch(e) {
            toast('Network error', 'error');
        }
    }

    async function sendAIDraft() {
        if (!_activeRef) return;
        const summary = document.getElementById('d-summary')?.value || '';
        try {
            const r = await fetch('/fm/api/classify', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({text: summary})
            });
            const d = await r.json();
            const input = document.getElementById('fm-reply-input');
            if (input) input.value = d.draft_reply || '';
            toast('AI draft loaded', 'success');
        } catch(e) {
            toast('Classify failed', 'error');
        }
    }

    // =========================================================================
    // QUICK ACTIONS
    // =========================================================================

    function requestAccessWindow() {
        if (!_activeRef) return;
        const el = document.getElementById('d-status');
        if (el) el.value = 'WAITING_CUSTOMER';
        saveTicket();
    }

    function markInProgress() {
        if (!_activeRef) return;
        const el = document.getElementById('d-status');
        if (el) el.value = 'IN_PROGRESS';
        saveTicket();
    }

    // =========================================================================
    // NEW TICKET MODAL
    // =========================================================================

    function openNewTicketModal() {
        document.getElementById('fm-new-ticket-modal')?.classList.add('show');
    }

    function closeNewTicketModal() {
        document.getElementById('fm-new-ticket-modal')?.classList.remove('show');
    }

    async function createTicket() {
        const summary = document.getElementById('nt-summary')?.value.trim();
        if (!summary) return toast('Summary is required', 'error');

        const payload = {
            estate:        document.getElementById('nt-estate')?.value.trim(),
            unit:          document.getElementById('nt-unit')?.value.trim(),
            customer:      document.getElementById('nt-customer')?.value.trim(),
            source:        document.getElementById('nt-source')?.value,
            summary,
            first_message: document.getElementById('nt-message')?.value.trim()
        };

        try {
            const r = await fetch('/fm/api/tickets', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(payload)
            });
            if (r.ok) {
                const d = await r.json();
                toast(`Created ${d.ref}`, 'success');
                closeNewTicketModal();
                setTimeout(() => location.reload(), 900);
            } else {
                const e = await r.json();
                toast('Error: ' + (e.error || ''), 'error');
            }
        } catch(e) {
            toast('Network error', 'error');
        }
    }

    // =========================================================================
    // WHATSAPP JSON IMPORT (delegates to import.js for parsing)
    // =========================================================================

    function importPreview() {
        const raw = document.getElementById('fm-import-json')?.value.trim();
        if (!raw) return toast('Paste JSON first', 'error');

        try {
            _importParsed = FMImport.parse(raw);
        } catch(e) {
            return toast('Invalid JSON: ' + e.message, 'error');
        }

        const preview = document.getElementById('fm-import-preview');
        if (preview) {
            preview.innerHTML = FMImport.renderPreview(_importParsed);
            preview.style.display = 'block';
        }
        const confirmBtn = document.getElementById('fm-import-confirm-btn');
        const clearBtn   = document.getElementById('fm-import-clear-btn');
        if (confirmBtn) confirmBtn.style.display = '';
        if (clearBtn)   clearBtn.style.display   = '';
    }

    async function importConfirm() {
        if (!_importParsed) return;

        try {
            const r = await fetch('/fm/api/import/whatsapp', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(_importParsed)
            });
            const d = await r.json();
            if (r.ok || r.status === 201) {
                toast(`Imported → ${d.ref}`, 'success');
                importClear();
                setTimeout(() => location.reload(), 1000);
            } else {
                toast('Import failed: ' + (d.error || ''), 'error');
            }
        } catch(e) {
            toast('Network error', 'error');
        }
    }

    function importClear() {
        const ta = document.getElementById('fm-import-json');
        const preview = document.getElementById('fm-import-preview');
        const confirmBtn = document.getElementById('fm-import-confirm-btn');
        const clearBtn   = document.getElementById('fm-import-clear-btn');
        if (ta)         ta.value             = '';
        if (preview)  { preview.style.display = 'none'; preview.innerHTML = ''; }
        if (confirmBtn) confirmBtn.style.display = 'none';
        if (clearBtn)   clearBtn.style.display   = 'none';
        _importParsed = null;
    }

    // =========================================================================
    // TOAST
    // =========================================================================

    function toast(msg, type) {
        const el = document.getElementById('fm-toast');
        if (!el) return;
        el.textContent = msg;
        el.className = `fm-toast show ${type || ''}`;
        clearTimeout(el._t);
        el._t = setTimeout(() => el.classList.remove('show'), 3200);
    }

    // =========================================================================
    // HELPERS
    // =========================================================================

    function _esc(s) {
        return String(s)
            .replace(/&/g,'&amp;').replace(/</g,'&lt;')
            .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // =========================================================================
    // INIT
    // =========================================================================

    function _init() {
        // Auto-select first visible ticket
        const first = document.querySelector('.fm-ticket');
        if (first) selectTicket(first.dataset.ref);

        // Live search
        const search = document.getElementById('fm-search');
        if (search) search.addEventListener('input', _applyQueueFilter);

        // Drop zone for WhatsApp import
        if (typeof FMImport !== 'undefined') {
            FMImport.initDropZone(
                document.getElementById('fm-drop-zone'),
                document.getElementById('fm-import-json')
            );
        }

        // Close new-ticket modal on overlay click
        const modal = document.getElementById('fm-new-ticket-modal');
        if (modal) {
            modal.addEventListener('click', function(e) {
                if (e.target === this) closeNewTicketModal();
            });
        }

        // Hash-based scroll to import panel
        if (window.location.hash === '#import') {
            const panel = document.getElementById('import');
            if (panel) setTimeout(() => panel.scrollIntoView({behavior:'smooth'}), 300);
        }
    }

    document.addEventListener('DOMContentLoaded', _init);

    return {
        selectTicket,
        filterTab, applyFilters,
        saveTicket, sendReply, sendAIDraft,
        requestAccessWindow, markInProgress,
        openNewTicketModal, closeNewTicketModal, createTicket,
        importPreview, importConfirm, importClear,
        toast
    };
})();
