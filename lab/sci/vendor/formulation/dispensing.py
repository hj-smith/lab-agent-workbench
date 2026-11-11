"""Product-scoped sample dispensing instructions, independent of weighing plans."""
from copy import deepcopy
import json
import math
import uuid

from .export_format import format_number

PLAN_FIELDS={'density_g_ml','rows','purpose','pore_size','container','note','id','bottles','amount','unit','filtered'}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS batch_dispensing(batch_id TEXT PRIMARY KEY, data TEXT NOT NULL, FOREIGN KEY(batch_id) REFERENCES batches(id))')
    db.execute('CREATE TABLE IF NOT EXISTS dispensing_templates(id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL)')


def normalize(plan):
    plan = plan or {}
    density = float(plan.get('density_g_ml', 1))
    if not math.isfinite(density) or density <= 0:
        raise ValueError('分装密度须大于 0。')
    rows, seen = [], set()
    for source in plan.get('rows', []):
        count, amount = float(source.get('bottles', 1)), float(source.get('amount', 0))
        if not math.isfinite(count) or count < 1 or not count.is_integer():
            raise ValueError('瓶数须为正整数。')
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('每瓶量须大于 0。')
        unit = source.get('unit', 'g')
        if unit not in ('g', 'mL'):
            raise ValueError('分装单位请选择 g 或 mL。')
        row = {**deepcopy(source), **{key: str(source.get(key) or '').strip() for key in ('purpose', 'pore_size', 'container', 'note')}}
        if not row['purpose']:
            raise ValueError('请填写分装用途。')
        rid = str(source.get('id') or uuid.uuid4().hex)
        if rid in seen:
            raise ValueError('分装行标识不能重复。')
        seen.add(rid)
        row.update(id=rid, bottles=int(count), amount=amount, unit=unit, filtered=bool(source.get('filtered', False)))
        rows.append(row)
    return {**deepcopy(plan), 'density_g_ml': density, 'rows': rows}


def summary(plan, planned_mass_g):
    plan = plan or {}
    total = sum(row['bottles'] * row['amount'] * (plan.get('density_g_ml', 1) if row['unit'] == 'mL' else 1)
                for row in plan.get('rows', []))
    return {'total_g': total, 'planned_mass_g': planned_mass_g, 'exceeds': total > planned_mass_g}


def numbered_rows(plan):
    start = 1
    for row in (plan or {}).get('rows', []):
        end = start + row['bottles'] - 1
        yield {**row, 'numbers': str(start) if start == end else f'{start}–{end}'}
        start = end + 1


TABLE_HEADER = ('瓶号', '用途', '每瓶量', '过滤', '容器', '说明')


def note_chunks(text, size=40, lines=6):
    """Split a long note into bounded pieces so no single table row outgrows a page."""
    pieces = [part[i:i + size] for part in str(text).splitlines() for i in range(0, max(len(part), 1), size)]
    return ['\n'.join(pieces[i:i + lines]) for i in range(0, len(pieces), lines)] or ['']


def instruction_table(batch):
    """Return (summary line, rows of six strings) for printed work orders, or None."""
    plan = batch.get('dispensing_plan') or {}
    if not plan.get('rows'):
        return None
    totals = summary(plan, batch['formula_data']['total_mass_g'])
    num = lambda value: format_number(value).rstrip('0').rstrip('.')
    line = f"计划分装 {num(totals['total_g'])} g / 配样总量 {num(totals['planned_mass_g'])} g"
    if any(row['unit'] == 'mL' for row in plan['rows']):
        line += f"；密度 {num(plan.get('density_g_ml', 1))} g/mL"
    if totals['exceeds']:
        line += '；超出配样总量，请核对'
    rows = [[row['numbers'], row['purpose'], f"{num(row['amount'])} {row['unit']}",
             (row['pore_size'] or '孔径未填写') if row['filtered'] else '—', row['container'] or '—', row['note']]
            for row in numbered_rows(plan)]
    return line, rows


def get_plan(db, bid):
    row = db.execute('SELECT data FROM batch_dispensing WHERE batch_id=?', (bid,)).fetchone()
    return json.loads(row['data']) if row else {'density_g_ml': 1, 'rows': []}


def resolve(db, plan=None, template_id=None):
    if template_id:
        row = db.execute('SELECT data FROM dispensing_templates WHERE id=?', (template_id,)).fetchone()
        if row is None:
            raise ValueError('分装方案已删除，请重新选择。')
        plan = json.loads(row['data'])
    return normalize(plan)


def attach(db, bid, plan):
    # Each batch owns a copy; later template edits never alter issued orders.
    plan = deepcopy(plan)
    for row in plan['rows']:
        row['id'] = uuid.uuid4().hex
    db.execute('INSERT INTO batch_dispensing(batch_id,data) VALUES(?,?)', (bid, json.dumps(plan, ensure_ascii=False)))


def save_batch(store, bid, payload):
    from .batch_management import ensure_active, _expected, record_change
    plan = normalize(payload.get('plan'))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        before = store._get_batch(db, bid)
        ensure_active(before, '修改分装指示')
        _expected(before, payload)
        from .json_compat import preserve_unknown
        plan=preserve_unknown(before['dispensing_plan'],plan,PLAN_FIELDS)
        db.execute('INSERT INTO batch_dispensing(batch_id,data) VALUES(?,?) ON CONFLICT(batch_id) DO UPDATE SET data=excluded.data',
                   (bid, json.dumps(plan, ensure_ascii=False)))
        after = store._get_batch(db, bid)
        record_change(db, before, after, 'dispensing_edit', '修改分装指示')
        return store._get_batch(db, bid)


def templates(store):
    with store.connect() as db:
        return [dict(id=row['id'], name=row['name'], plan=json.loads(row['data']))
                for row in db.execute('SELECT * FROM dispensing_templates ORDER BY name,id')]


def save_template(store, payload):
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('请填写分装方案名称。')
    plan = normalize(payload.get('plan'))
    tid = payload.get('id') or uuid.uuid4().hex
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT data FROM dispensing_templates WHERE id=?',(tid,)).fetchone()
        if previous:
            from .json_compat import preserve_unknown
            plan=preserve_unknown(json.loads(previous[0]),plan,PLAN_FIELDS)
        db.execute('INSERT INTO dispensing_templates(id,name,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,data=excluded.data',
                   (tid, name, json.dumps(plan, ensure_ascii=False)))
    return {'ok': True, 'id': tid}


def delete_template(store, tid):
    with store.connect() as db:
        db.execute('DELETE FROM dispensing_templates WHERE id=?', (tid,))
    return {'ok': True}
