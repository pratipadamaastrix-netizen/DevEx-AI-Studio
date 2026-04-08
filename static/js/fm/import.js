/**
 * FM WhatsApp JSON Import Module
 * Batch FM4 | static/js/fm/import.js
 *
 * Handles:
 *  - Drag-and-drop .json file onto drop zone
 *  - Paste raw JSON into textarea
 *  - Flexible field mapping (handles many LLM output shapes)
 *  - Preview render before confirm
 *  - Exposes FMImport.parse(), FMImport.renderPreview(), FMImport.initDropZone()
 */

const FMImport = (() => {
    'use strict';

    // -------------------------------------------------------------------------
    // Field mapping — tries multiple key names per field.
    // The LLM might output "name", "customer", "from_name" etc.
    // Order = priority: first non-empty value wins.
    // -------------------------------------------------------------------------
    const FIELD_MAP = {
        customer: ['customer', 'name', 'client', 'tenant', 'from_name', 'contact'],
        phone:    ['phone', 'mobile', 'whatsapp', 'number', 'tel'],
        estate:   ['estate', 'property', 'building', 'development', 'complex', 'block'],
        unit:     ['unit', 'flat', 'apartment', 'room', 'suite', 'door'],
        summary:  ['summary', 'issue', 'problem', 'description', 'subject', 'title', 'complaint'],
        priority: ['priority', 'urgency', 'severity'],
        category: ['category', 'type', 'trade', 'department'],
        materials:['materials', 'notes', 'parts', 'equipment', 'tools'],
        assignee: ['assignee', 'assigned_to', 'technician', 'engineer', 'staff'],
    };

    const VALID_PRIORITIES = ['urgent', 'normal', 'low'];
    const VALID_CATEGORIES = [
        'general','electrical','plumbing','hvac',
        'security','carpentry','cleaning','painting','pest_control'
    ];

    /**
     * Resolve a field value from a raw object using the field map.
     * @param {Object} raw
     * @param {string} field
     * @returns {string}
     */
    function _resolve(raw, field) {
        const keys = FIELD_MAP[field] || [field];
        for (const k of keys) {
            const v = raw[k];
            if (v !== undefined && v !== null && String(v).trim() !== '') {
                return String(v).trim();
            }
        }
        return '';
    }

    /**
     * Infer priority from text using same keyword rules as backend.
     * @param {string} text
     * @returns {string}
     */
    function _inferPriority(text) {
        const t = text.toLowerCase();
        const urgent = ['urgent','emergency','flooding','no power','no heat',
                        'fire','gas smell','sparks','no water','burst','broken'];
        return urgent.some(kw => t.includes(kw)) ? 'urgent' : 'normal';
    }

    /**
     * Infer category from text.
     * @param {string} text
     * @returns {string}
     */
    function _inferCategory(text) {
        const t = text.toLowerCase();
        const rules = [
            ['plumbing',    ['leak','flood','water','pipe','tap','drain','boiler','burst']],
            ['electrical',  ['power','socket','switch','circuit','fuse','spark','electric','light']],
            ['security',    ['cctv','camera','lock','alarm','gate','access','key']],
            ['hvac',        ['ac','air con','cooling','ventilation','noise','thermostat','heating']],
            ['carpentry',   ['door','hinge','window','wardrobe','frame','cabinet','shelf']],
            ['cleaning',    ['clean','dirt','stain','rubbish','waste']],
            ['painting',    ['paint','crack','plaster','wall','ceiling']],
            ['pest_control',['pest','rat','mouse','cockroach','insect','bug']],
        ];
        for (const [cat, keywords] of rules) {
            if (keywords.some(kw => t.includes(kw))) return cat;
        }
        return 'general';
    }

    /**
     * Normalise messages array — handles several LLM output shapes.
     * Supported shapes:
     *  - [{from, text, ts}, ...]               standard
     *  - [{role, content, timestamp}, ...]      OpenAI-style
     *  - [{sender, message, time}, ...]         alternate
     *  - [{author, body}, ...]                  WhatsApp export
     */
    function _normaliseMessages(raw) {
        if (!Array.isArray(raw)) return [];
        return raw.map(m => {
            const fromRaw = String(
                m.from || m.role || m.sender || m.author || 'customer'
            ).toLowerCase();

            const sender =
                ['customer','user','client','tenant'].includes(fromRaw) ? 'customer' :
                ['ai','bot','llm','assistant','system'].includes(fromRaw) ? 'ai' :
                'staff';

            const body = String(
                m.text || m.content || m.body || m.message || ''
            ).trim();

            const ts = m.ts || m.timestamp || m.time || m.created_at || '';

            return body ? { from: sender, text: body, ts: String(ts) } : null;
        }).filter(Boolean);
    }

    // -------------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------------

    /**
     * Parse raw JSON string into a normalised import payload.
     * Throws on invalid JSON.
     * @param {string} rawJson
     * @returns {Object} normalised payload ready to POST
     */
    function parse(rawJson) {
        const raw = JSON.parse(rawJson); // throws if invalid

        // Handle array wrapper: [{...}] → pick first element
        const data = Array.isArray(raw) ? raw[0] : raw;

        let summary   = _resolve(data, 'summary');
        const messages = _normaliseMessages(data.messages || data.conversation || data.thread || []);

        // If no summary, pull from first customer message
        if (!summary && messages.length) {
            const firstCustomer = messages.find(m => m.from === 'customer');
            if (firstCustomer) summary = firstCustomer.text.slice(0, 200);
        }

        let priority = _resolve(data, 'priority').toLowerCase();
        if (!VALID_PRIORITIES.includes(priority)) {
            priority = _inferPriority(summary);
        }

        let category = _resolve(data, 'category').toLowerCase();
        if (!VALID_CATEGORIES.includes(category)) {
            category = _inferCategory(summary);
        }

        return {
            source:    'whatsapp_json',
            customer:  _resolve(data, 'customer'),
            phone:     _resolve(data, 'phone'),
            estate:    _resolve(data, 'estate'),
            unit:      _resolve(data, 'unit'),
            summary:   summary,
            priority:  priority,
            category:  category,
            materials: _resolve(data, 'materials'),
            assignee:  _resolve(data, 'assignee'),
            messages:  messages,
            // Preserve any extra fields the LLM included
            _raw_keys: Object.keys(data)
        };
    }

    /**
     * Render an HTML preview string from a parsed payload.
     * @param {Object} parsed
     * @returns {string} HTML
     */
    function renderPreview(parsed) {
        const rows = [
            ['Customer',   parsed.customer || '—'],
            ['Phone',      parsed.phone    || '—'],
            ['Estate',     parsed.estate   || '—'],
            ['Unit',       parsed.unit     || '—'],
            ['Summary',    parsed.summary  || '—'],
            ['Priority',   parsed.priority ],
            ['Category',   parsed.category ],
            ['Materials',  parsed.materials|| '—'],
            ['Assignee',   parsed.assignee || '—'],
            ['Messages',   `${parsed.messages.length} messages will be imported`],
        ];

        const fields = rows.map(([k, v]) =>
            `<div><span class="pk">${_esc(k)}:</span> <span class="pv">${_esc(v)}</span></div>`
        ).join('');

        const msgPreview = parsed.messages.slice(0, 3).map(m =>
            `<div style="margin-top:6px;padding:6px 8px;border-radius:8px;background:${
                m.from === 'customer' ? '#eff6ff' : '#f0fdf4'
            };border:1px solid ${
                m.from === 'customer' ? '#bfdbfe' : '#86efac'
            }">
                <span style="font-family:var(--mono);font-size:10px;color:#64748b">${_esc(m.from)} · ${_esc(m.ts)}</span><br>
                <span style="font-size:12px">${_esc(m.text.slice(0, 120))}${m.text.length > 120 ? '…' : ''}</span>
            </div>`
        ).join('');

        return `
            <div style="margin-bottom:10px">${fields}</div>
            ${parsed.messages.length ? `<div style="margin-top:10px;font-size:12px;color:#64748b;font-weight:700">Message preview (first 3):</div>${msgPreview}` : ''}
            <div style="margin-top:10px;font-size:11px;color:#94a3b8">
                Confirm to create ticket and import ${parsed.messages.length} message(s).
            </div>
        `;
    }

    /**
     * Initialise drag-and-drop on a drop zone element.
     * On drop, reads the file, puts JSON into the textarea.
     * @param {HTMLElement} dropZone
     * @param {HTMLTextAreaElement} textarea
     */
    function initDropZone(dropZone, textarea) {
        if (!dropZone || !textarea) return;

        ['dragenter','dragover'].forEach(evt => {
            dropZone.addEventListener(evt, e => {
                e.preventDefault();
                dropZone.classList.add('dragover');
            });
        });

        ['dragleave','drop'].forEach(evt => {
            dropZone.addEventListener(evt, e => {
                dropZone.classList.remove('dragover');
            });
        });

        dropZone.addEventListener('drop', e => {
            e.preventDefault();
            const file = e.dataTransfer.files[0];
            if (!file) return;
            if (!file.name.endsWith('.json') && file.type !== 'application/json') {
                if (window.FM) FM.toast('Only .json files are supported', 'error');
                return;
            }
            const reader = new FileReader();
            reader.onload = ev => {
                textarea.value = ev.target.result;
                if (window.FM) FM.toast(`Loaded ${file.name}`, 'success');
            };
            reader.readAsText(file);
        });
    }

    // ---- Helper ----
    function _esc(s) {
        return String(s)
            .replace(/&/g,'&amp;').replace(/</g,'&lt;')
            .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    return { parse, renderPreview, initDropZone };
})();
