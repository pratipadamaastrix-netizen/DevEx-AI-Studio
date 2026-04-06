#!/usr/bin/env python3
"""
Devex AI Studios — Meeting Rooms
Project/app_rooms.py

Routes: /studio/rooms, /studio/rooms/<id>
Tables: meeting_rooms, meeting_room_sections, meeting_room_questions, meeting_room_answers
"""

import json
import os
import sqlite3
from datetime import datetime

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session
)

ENGINE_DB_PATH = 'engine.db'


def get_room_db():
    conn = sqlite3.connect(ENGINE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_room_migrations(conn):
    """Idempotent. Call from init_db() in app.py."""
    print("  Running Meeting Rooms migrations...")
    needs = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meeting_rooms'"
    ).fetchone() is None

    if needs:
        schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema_rooms.sql')
        try:
            with open(schema_path, 'r') as f:
                conn.executescript(f.read())
            conn.commit()
            print("  + Meeting Rooms tables created")
        except FileNotFoundError:
            print(f"  ! schema_rooms.sql not found at {schema_path}")
    else:
        print("  + Meeting Rooms tables already exist")


# =========================================================================
# HELPERS
# =========================================================================

def _load_room(conn, room_id):
    row = conn.execute("SELECT * FROM meeting_rooms WHERE id=?", (room_id,)).fetchone()
    return dict(row) if row else None


def _load_room_sections(conn, room_id):
    rows = conn.execute(
        "SELECT * FROM meeting_room_sections WHERE room_id=? ORDER BY order_idx, id",
        (room_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _load_room_questions(conn, room_id):
    rows = conn.execute(
        "SELECT * FROM meeting_room_questions WHERE room_id=? ORDER BY section_id, order_idx, id",
        (room_id,)
    ).fetchall()
    qs = []
    for r in rows:
        q = dict(r)
        try:
            q['options'] = json.loads(q.get('options_json') or '[]')
        except Exception:
            q['options'] = []
        qs.append(q)
    return qs


def _load_room_answers(conn, room_id):
    rows = conn.execute(
        "SELECT * FROM meeting_room_answers WHERE room_id=?", (room_id,)
    ).fetchall()
    out = {}
    for r in rows:
        a = dict(r)
        try:
            a['checked'] = json.loads(a.get('checked_json') or '[]')
        except Exception:
            a['checked'] = []
        try:
            a['notes'] = json.loads(a.get('notes_json') or '{}')
        except Exception:
            a['notes'] = {}
        out[a['question_id']] = a
    return out


def _room_progress(sections, questions, answers):
    """Return {section_id: {total, answered, status}}, total, answered."""
    q_by_sec = {}
    for q in questions:
        q_by_sec.setdefault(q['section_id'], []).append(q)

    completion = {}
    total_all = answered_all = 0
    for sec in sections:
        qs = q_by_sec.get(sec['id'], [])
        total = len(qs)
        answered = sum(1 for q in qs if answers.get(q['id'], {}).get('status') == 'answered')
        partial = sum(1 for q in qs if answers.get(q['id'], {}).get('status') == 'partial')
        if total and answered == total:
            status = 'complete'
        elif answered > 0 or partial > 0:
            status = 'partial'
        else:
            status = 'empty'
        completion[sec['id']] = {'total': total, 'answered': answered, 'status': status}
        total_all += total
        answered_all += answered
    return completion, total_all, answered_all


# =========================================================================
# SEED: POP Telecom x Broadband Buddy room (example)
# =========================================================================

POP_ROOM_DATA = {
    "title": "POP Telecom x Broadband Buddy",
    "subtitle": "Integration Clarification Form",
    "client_name": "POP Telecom",
    "project_type": "Integration",
    "groups": [
        {
            "group": "Architecture",
            "sections": [
                {
                    "title": "Architecture Decisions",
                    "description": "Structural decisions that must be locked before build starts. They affect the estimate, the build order, and what gets built.",
                    "questions": [
                        {"code": "A1", "label": "Does a separate adapter service sit between POP Telecom and Broadband Buddy, or does POP call BB directly?",
                         "context": "The scope spec leaves this open. It affects the estimate and what gets built.",
                         "field_type": "options", "tag_text": "Arch meeting", "tag_class": "tag-arch",
                         "callout_text": "This is a blocker for the integration estimate. The two options have different build costs.", "callout_type": "amber",
                         "options_json": json.dumps([
                             {"label": "Option A \u2014 POP calls Broadband Buddy API directly. No separate service.", "sub": "Lower build cost. Simpler architecture."},
                             {"label": "Option B \u2014 A thin adapter service sits between POP and BB.", "sub": "More control. Useful if POP and BB have different auth models."}
                         ])},
                        {"code": "A2", "label": "Who performs the postcode / district code lookup \u2014 POP Telecom or Broadband Buddy?",
                         "context": "The flow diagram shows POP performing an ALK/CSS district code lookup. The client Q&A suggests passing postcode to BB. These are different flows.",
                         "field_type": "options", "tag_text": "Arch meeting", "tag_class": "tag-arch",
                         "callout_text": "Exact field payload must also be agreed and documented in the API contract.", "callout_type": "blue",
                         "options_json": json.dumps([
                             {"label": "Option A \u2014 POP performs ALK/CSS lookup first, sends resolved code to BB.", "sub": "POP needs access to lookup service."},
                             {"label": "Option B \u2014 POP sends raw postcode + address to BB. BB handles resolution.", "sub": "Simpler POP side."}
                         ])},
                        {"code": "A3", "label": "Is the POP Telecom Laravel backend being extended or rebuilt?",
                         "context": "Spec confirms: extend, not rebuild. Recorded here for sign-off.",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-required",
                         "callout_text": "Spec direction: extend existing Laravel backend. Full rebuild ~30 weeks vs integration ~10 weeks.", "callout_type": "green",
                         "options_json": json.dumps([
                             {"label": "Confirmed \u2014 extend existing Laravel backend only. No rebuild."},
                             {"label": "Change of direction \u2014 partial or full rebuild is now being considered.", "sub": "If selected, describe scope in notes."}
                         ])},
                    ]
                },
                {
                    "title": "Stage 1 \u2014 Availability",
                    "description": "Stage 1 covers availability checking only. No ordering, no payment, no AKJ.",
                    "questions": [
                        {"code": "B1", "label": "Which suppliers are in scope for Stage 1 availability checks?",
                         "context": "PXC is already working in BB. Freedom Fibre and Giacom need to be added.",
                         "field_type": "checklist", "tag_text": "Confirm", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "PXC \u2014 already integrated in BB", "sub": "Includes CityFibre (CFH) results"},
                             {"label": "Giacom \u2014 to be added to BB in Stage 1"},
                             {"label": "Freedom Fibre \u2014 to be added to BB in Stage 1"}
                         ])},
                        {"code": "B2", "label": "What does POP display when Broadband Buddy is unavailable?",
                         "context": "BB unavailability handling must be explicitly built and tested on the POP side.",
                         "field_type": "options", "tag_text": "Define", "tag_class": "tag-required",
                         "callout_text": "This must be defined before frontend build starts.", "callout_type": "red",
                         "options_json": json.dumps([
                             {"label": "Show error message and prevent product display entirely."},
                             {"label": "Show error but allow fallback to existing direct API flow.", "sub": "More complex. Old flow stays active."},
                             {"label": "Other \u2014 define in notes."}
                         ])},
                        {"code": "B3", "label": "Has Stage 1 sign-off criteria been agreed?",
                         "context": "Stage 2 cannot go live until Stage 1 is complete and stable.",
                         "field_type": "checklist", "tag_text": "Define", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "BB availability endpoint live and returning results"},
                             {"label": "POP catalogue mapping working"},
                             {"label": "Product display signed off by JB on staging"},
                             {"label": "BB unavailability fallback tested and confirmed"},
                             {"label": "All three suppliers returning results in testing"},
                             {"label": "Priority and deduplication logic confirmed"}
                         ])},
                    ]
                },
                {
                    "title": "Stage 2 \u2014 Ordering",
                    "description": "Stage 2 adds order placement, Order ID handling, and AKJ integration.",
                    "questions": [
                        {"code": "C1", "label": "What is the exact order placement sequence?",
                         "context": "Payment must happen before order sent to BB. Confirm the sequence.",
                         "field_type": "checklist", "tag_text": "Confirm", "tag_class": "tag-required",
                         "callout_text": "Working assumption: customer selects product > POP applies pricing > customer pays > POP sends order to BB > supplier returns Order ID > POP stores and writes to AKJ.", "callout_type": "green",
                         "options_json": json.dumps([
                             {"label": "1. Customer selects product on POP Telecom"},
                             {"label": "2. POP applies pricing from internal catalogue"},
                             {"label": "3. Customer pays via existing POP payment gateway"},
                             {"label": "4. POP sends order to BB only after payment confirmed"},
                             {"label": "5. BB determines supplier and places order"},
                             {"label": "6. Supplier returns Order ID to BB"},
                             {"label": "7. BB stores Order ID and returns to POP"},
                             {"label": "8. POP stores Order ID against customer record"},
                             {"label": "9. POP writes Order ID to AKJ task notes"}
                         ])},
                        {"code": "C2", "label": "What happens if the order fails after payment has been taken?",
                         "context": "Payment is taken before order is sent. If BB rejects, there is a customer with a charge and no order.",
                         "field_type": "options", "tag_text": "Blocker", "tag_class": "tag-blocker",
                         "callout_text": "Not defined in the current spec. Risk of taking payment without fulfilling the order.", "callout_type": "red",
                         "options_json": json.dumps([
                             {"label": "POP automatically refunds customer and creates a support task."},
                             {"label": "POP flags order as failed, holds payment, notifies team manually."},
                             {"label": "Other \u2014 define in notes."}
                         ])},
                    ]
                },
            ]
        },
        {
            "group": "Products",
            "sections": [
                {
                    "title": "Product Catalogue",
                    "description": "POP owns the internal product catalogue. BB returns a catalogueName and POP maps it to an internal record.",
                    "questions": [
                        {"code": "D1", "label": "Has JB produced the product equivalence mapping sheet?",
                         "context": "This sheet maps equivalent products across Freedom Fibre, Giacom, and PXC. Hard blocker for BB matching logic.",
                         "field_type": "options", "tag_text": "Blocker", "tag_class": "tag-blocker",
                         "callout_text": "On the critical path. BB build cannot start without it.", "callout_type": "red",
                         "options_json": json.dumps([
                             {"label": "Completed \u2014 mapping sheet is ready."},
                             {"label": "In progress \u2014 expected completion date in notes."},
                             {"label": "Not started \u2014 needs to be prioritised."}
                         ])},
                        {"code": "D2", "label": "Who maintains the catalogue mapping when suppliers add new products?",
                         "context": "Mapping must stay aligned. Unmapped products will not display.",
                         "field_type": "options", "tag_text": "Action: JB", "tag_class": "tag-jb",
                         "options_json": json.dumps([
                             {"label": "JB manages mapping manually when notified."},
                             {"label": "BB notifies POP via structured process."},
                             {"label": "Admin UI surfaces unmapped products for JB."}
                         ])},
                    ]
                },
                {
                    "title": "Suppliers & Priority",
                    "description": "Priority rules and supplier configuration live in Broadband Buddy.",
                    "questions": [
                        {"code": "E1", "label": "Is the supplier priority order confirmed as Freedom Fibre (1), Giacom (2), PXC (3)?",
                         "context": "This is the order BB uses to decide which supplier to show when products overlap.",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "Confirmed \u2014 FF first, Giacom second, PXC third."},
                             {"label": "Priority order needs to change \u2014 define in notes."}
                         ])},
                    ]
                },
            ]
        },
        {
            "group": "Commercial",
            "sections": [
                {
                    "title": "Payment & Pricing",
                    "description": "Payment processing remains on POP. Pricing is POP-controlled, not BB-controlled.",
                    "questions": [
                        {"code": "F1", "label": "Is POP's existing payment gateway being used unchanged?",
                         "context": "Confirm payment is POP-side, not routed through BB.",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "Confirmed \u2014 existing POP payment gateway unchanged."},
                             {"label": "Change required \u2014 describe in notes."}
                         ])},
                        {"code": "F2", "label": "Pricing always from POP catalogue, not BB?",
                         "context": "BB returns availability. POP maps to internal catalogue and applies its own pricing.",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "Confirmed \u2014 POP pricing only. BB prices not displayed."},
                             {"label": "Exception \u2014 some BB pricing used. Define in notes."}
                         ])},
                    ]
                },
                {
                    "title": "Routers & Add-ons",
                    "description": "Router and add-on selection in the checkout flow.",
                    "questions": [
                        {"code": "G1", "label": "Are routers/add-ons excluded from Stage 1+2?",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "Confirmed \u2014 routers and add-ons deferred to later phase."},
                             {"label": "In scope \u2014 router selection must be included. Define in notes."}
                         ])},
                    ]
                },
            ]
        },
        {
            "group": "Integration",
            "sections": [
                {
                    "title": "AKJ & Order Sync",
                    "description": "AKJ integration for writing Order IDs to task notes.",
                    "questions": [
                        {"code": "H1", "label": "Is the AKJ Task ID already stored in the POP backend?",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-required",
                         "options_json": json.dumps([
                             {"label": "Confirmed \u2014 AKJ Task ID is stored and accessible."},
                             {"label": "Not stored \u2014 needs to be added. Define in notes."}
                         ])},
                        {"code": "H2", "label": "What is the format of the AKJ task note text?",
                         "context": "Define the exact text written to AKJ when an order is placed.",
                         "field_type": "textarea", "tag_text": "Define", "tag_class": "tag-required"},
                    ]
                },
                {
                    "title": "CRM & Data",
                    "description": "CRM and data centralisation requirements.",
                    "questions": [
                        {"code": "I1", "label": "Is CRM integration deferred \u2014 not in Stage 1 or 2?",
                         "field_type": "options", "tag_text": "Confirm", "tag_class": "tag-tbc",
                         "options_json": json.dumps([
                             {"label": "Confirmed deferred \u2014 CRM out of scope for Stage 1 and 2."},
                             {"label": "Partially in scope \u2014 define in notes."}
                         ])},
                        {"code": "I2", "label": "Is there a data centralisation requirement in scope?",
                         "context": "Whether order/customer data needs to be unified across POP, BB, and AKJ.",
                         "field_type": "options", "tag_text": "TBC", "tag_class": "tag-tbc",
                         "options_json": json.dumps([
                             {"label": "No \u2014 each system manages its own data. No sync required."},
                             {"label": "Yes \u2014 define centralisation scope in notes."}
                         ])},
                    ]
                },
            ]
        },
    ]
}


def seed_pop_room(conn):
    """Create the POP Telecom x BB room with all sections and questions."""
    d = POP_ROOM_DATA
    cursor = conn.execute(
        "INSERT INTO meeting_rooms (title, subtitle, client_name, project_type, status) VALUES (?,?,?,?,?)",
        (d['title'], d['subtitle'], d['client_name'], d['project_type'], 'in_clarification')
    )
    room_id = cursor.lastrowid
    order_idx = 0

    for group in d['groups']:
        for sec_def in group['sections']:
            cur_s = conn.execute(
                "INSERT INTO meeting_room_sections (room_id, group_name, title, description, order_idx) VALUES (?,?,?,?,?)",
                (room_id, group['group'], sec_def['title'], sec_def.get('description', ''), order_idx)
            )
            section_id = cur_s.lastrowid
            order_idx += 1

            for q_idx, q in enumerate(sec_def.get('questions', [])):
                conn.execute(
                    """INSERT INTO meeting_room_questions
                       (room_id, section_id, code, label, context, field_type,
                        tag_text, tag_class, callout_text, callout_type,
                        options_json, required, order_idx)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (room_id, section_id, q.get('code', ''), q['label'],
                     q.get('context', ''), q.get('field_type', 'options'),
                     q.get('tag_text', ''), q.get('tag_class', ''),
                     q.get('callout_text', ''), q.get('callout_type', ''),
                     q.get('options_json', '[]'), 1 if q.get('required') else 0, q_idx)
                )

    conn.commit()
    return room_id


# =========================================================================
# ROUTE REGISTRATION
# =========================================================================

def register_room_routes(app: Flask):

    # ── Room list (dashboard) ──────────────────────────────────────────

    @app.route('/studio/rooms')
    def rooms_list():
        conn = get_room_db()
        rooms = [dict(r) for r in conn.execute(
            "SELECT * FROM meeting_rooms ORDER BY updated_at DESC"
        ).fetchall()]

        for room in rooms:
            q_count = conn.execute(
                "SELECT COUNT(*) FROM meeting_room_questions WHERE room_id=?", (room['id'],)
            ).fetchone()[0]
            a_count = conn.execute(
                "SELECT COUNT(*) FROM meeting_room_answers WHERE room_id=? AND status='answered'",
                (room['id'],)
            ).fetchone()[0]
            s_count = conn.execute(
                "SELECT COUNT(*) FROM meeting_room_sections WHERE room_id=?", (room['id'],)
            ).fetchone()[0]
            room['total_questions'] = q_count
            room['answered_questions'] = a_count
            room['section_count'] = s_count

        conn.close()
        return render_template('studio/rooms.html', rooms=rooms, active_nav='meeting_rooms')

    # ── Room detail ────────────────────────────────────────────────────

    @app.route('/studio/rooms/<int:room_id>')
    def room_detail(room_id):
        conn = get_room_db()
        room = _load_room(conn, room_id)
        if not room:
            conn.close()
            return 'Room not found', 404

        sections = _load_room_sections(conn, room_id)
        questions = _load_room_questions(conn, room_id)
        answers = _load_room_answers(conn, room_id)

        q_by_section = {}
        for q in questions:
            q_by_section.setdefault(q['section_id'], []).append(q)

        # Group sections by group_name for navigation
        groups = []
        seen_groups = {}
        for sec in sections:
            gname = sec.get('group_name') or 'General'
            if gname not in seen_groups:
                seen_groups[gname] = []
                groups.append({'name': gname, 'sections': seen_groups[gname]})
            seen_groups[gname].append(sec)

        completion, total_q, answered_q = _room_progress(sections, questions, answers)

        is_admin = session.get('dx_role') in ('admin', None)

        conn.close()
        return render_template(
            'studio/rooms/detail.html',
            room=room,
            sections=sections,
            questions=questions,
            q_by_section=q_by_section,
            groups=groups,
            answers=answers,
            completion=completion,
            total_q=total_q,
            answered_q=answered_q,
            is_admin=is_admin,
            active_nav='meeting_rooms',
        )

    # ── Save answer (autosave) ─────────────────────────────────────────

    @app.route('/studio/rooms/<int:room_id>/save', methods=['POST'])
    def room_save_answer(room_id):
        data = request.get_json(silent=True) or {}
        qid = data.get('question_id')
        if not qid:
            return jsonify({'ok': False}), 400

        selected = data.get('selected_option', '')
        checked = data.get('checked', [])
        notes = data.get('notes', {})

        # Determine status
        has_selected = bool(selected)
        has_checked = bool(checked)
        has_notes = any(v.strip() for v in notes.values()) if isinstance(notes, dict) else False
        if has_selected or has_checked:
            status = 'answered'
        elif has_notes:
            status = 'partial'
        else:
            status = 'empty'

        conn = get_room_db()
        conn.execute(
            """INSERT INTO meeting_room_answers (room_id, question_id, selected_option, checked_json, notes_json, status, answered_at)
               VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(room_id, question_id) DO UPDATE SET
                 selected_option=excluded.selected_option,
                 checked_json=excluded.checked_json,
                 notes_json=excluded.notes_json,
                 status=excluded.status,
                 answered_at=CURRENT_TIMESTAMP""",
            (room_id, qid, selected, json.dumps(checked), json.dumps(notes), status)
        )
        conn.execute("UPDATE meeting_rooms SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (room_id,))
        conn.commit()

        # Return updated progress
        sections = _load_room_sections(conn, room_id)
        questions = _load_room_questions(conn, room_id)
        answers = _load_room_answers(conn, room_id)
        completion, total_q, answered_q = _room_progress(sections, questions, answers)

        q_row = conn.execute("SELECT section_id FROM meeting_room_questions WHERE id=?", (qid,)).fetchone()
        sec_id = q_row['section_id'] if q_row else None
        sec_comp = completion.get(sec_id, {})

        conn.close()
        return jsonify({
            'ok': True, 'total_q': total_q, 'answered_q': answered_q,
            'section_status': sec_comp.get('status', 'empty'),
        })

    # ── Create room ────────────────────────────────────────────────────

    @app.route('/studio/rooms/new', methods=['GET', 'POST'])
    def room_new():
        if request.method == 'GET':
            return render_template('studio/rooms/new.html', active_nav='meeting_rooms')

        # POST: create empty room or seed POP room
        conn = get_room_db()
        seed = request.form.get('seed_pop')
        if seed:
            room_id = seed_pop_room(conn)
        else:
            title = request.form.get('title', '').strip() or 'Untitled Room'
            client = request.form.get('client_name', '').strip()
            cursor = conn.execute(
                "INSERT INTO meeting_rooms (title, client_name) VALUES (?,?)",
                (title, client)
            )
            room_id = cursor.lastrowid
            conn.commit()
        conn.close()
        return redirect(f'/studio/rooms/{room_id}')

    # ── Update room status ─────────────────────────────────────────────

    @app.route('/studio/rooms/<int:room_id>/status', methods=['POST'])
    def room_update_status(room_id):
        data = request.get_json(silent=True) or {}
        status = data.get('status', '')
        valid = ('draft', 'in_clarification', 'scoped', 'in_build', 'delivered')
        if status not in valid:
            return jsonify({'ok': False}), 400
        conn = get_room_db()
        conn.execute("UPDATE meeting_rooms SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, room_id))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})

    print("  + Meeting Rooms routes registered")
