// Standalone batch-record helpers. The host owns event delegation, saving and undo.
const own = (value, key) => Object.prototype.hasOwnProperty.call(value ?? {}, key);
const record = value => value != null && typeof value === 'object' && !Array.isArray(value);
const list = value => Array.isArray(value) ? value : [];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const clone = value => Array.isArray(value) ? value.map(clone) : record(value) ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)])) : value;
// An explicit canonical null/empty value overrides an imported alias, including after reload.
const firstOwn = (row, keys) => row?.[keys.find(key => own(row, key))];
const text = value => String(value ?? '').trim();
let sequence = 0;
const uid = () => globalThis.crypto?.randomUUID?.() ?? `batch-record-${Date.now().toString(36)}-${++sequence}-${Math.random().toString(36).slice(2)}`;

function rowsFor(actual, key) {
  if (!record(actual)) throw new TypeError('actual must be a record');
  if (actual[key] == null) actual[key] = [];
  if (!Array.isArray(actual[key])) throw new TypeError(`actual.${key} must be an array`);
  return actual[key];
}
function indexOf(index, rows) {
  const n = typeof index === 'string' && /^(0|[1-9]\d*)$/.test(index) ? Number(index) : index;
  return Number.isSafeInteger(n) && n >= 0 && n < rows.length ? n : -1;
}
function sourceText(source) {
  if (!record(source)) return source == null ? '' : typeof source === 'string' ? source : JSON.stringify(source);
  const file = source.file ?? source.filename ?? source.path ?? '';
  const location = [source.sheet, source.cell ?? source.range ?? (source.row != null ? 'row ' + source.row : '')].filter(Boolean).join('!');
  return [file, location].filter(Boolean).join(' / ') || JSON.stringify(source);
}

/**
 * Pure HTML renderer; sourceLabel(source) returns plain text, escaped here with all values.
 * Inputs expose data-actual-parameter="<index>" and data-key="name|value|unit".
 * Delegate add-actual-parameter / delete-actual-parameter (data-id is the array index).
 * Values use text inputs so numeric observations and wording such as "澄清" both survive.
 */
export function renderActualParameters(actual = {}, sourceLabel = sourceText) {
  const rows = list(actual?.parameters);
  const body = rows.map((row, index) => {
    const input = (key, value, label) => `<input type="text"${key === 'unit' ? ' class="unit-input"' : ''} data-actual-parameter="${index}" data-key="${key}" aria-label="参数 ${index + 1} ${label}" value="${esc(value)}">`;
    const source = own(row, 'source') ? sourceLabel(row.source) : row?.sourceDisplay ?? '';
    return `<tr><td>${input('name', firstOwn(row, ['name', 'item', 'parameter']), '名称')}</td><td>${input('value', firstOwn(row, ['value', 'amount']), '值')}</td><td>${input('unit', row?.unit, '单位')}</td><td class="tiny muted">${esc(source)}</td><td><button type="button" class="small ghost" data-action="delete-actual-parameter" data-id="${index}" title="删除此参数" aria-label="删除参数 ${index + 1}">×</button></td></tr>`;
  }).join('');
  return `<div class="actual-parameters"><div class="card-head"><h3>实际参数（${rows.length}）</h3><button type="button" class="small" data-action="add-actual-parameter">＋ 参数</button></div><div class="table-wrap"><table class="edit-table"><thead><tr><th>参数</th><th>实际值</th><th>单位</th><th>来源</th><th></th></tr></thead><tbody>${body || '<tr><td colspan="5" class="empty">暂无实际参数，可添加记录。</td></tr>'}</tbody></table></div></div>`;
}

/**
 * Mutates one canonical field, preserving the exact string/number/null supplied by the host.
 * Deep-copies the complete original row to row.sourceBaseline on the first real change only.
 * Imported aliases and source/raw fields remain intact. Returns whether anything changed;
 * unsupported keys, invalid indices and no-ops return false without creating a baseline.
 */
export function editActualParameter(actual, index, key, value) {
  const rows = list(actual?.parameters), n = indexOf(index, rows), row = rows[n];
  if (!record(row) || !['name', 'value', 'unit'].includes(key) || !(value === null || ['string', 'number'].includes(typeof value))) return false;
  const current = firstOwn(row, key === 'name' ? ['name', 'item', 'parameter'] : key === 'value' ? ['value', 'amount'] : ['unit']);
  if (Object.is(current, value)) return false;
  if (!own(row, 'sourceBaseline')) row.sourceBaseline = clone(row);
  row[key] = value;
  return true;
}

/** Appends and returns an empty editable parameter with an internally generated ID. */
export function addActualParameter(actual) {
  const row = { id: uid(), name: '', value: null, unit: '' };
  rowsFor(actual, 'parameters').push(row);
  return row;
}

/** Removes by array index; returns the untouched row for host-owned undo, or null if invalid. */
export function deleteActualParameter(actual, index) {
  const rows = list(actual?.parameters), n = indexOf(index, rows);
  return n < 0 ? null : rows.splice(n, 1)[0];
}

const metadataKeys = ['name', 'materialId', 'branchId', 'workupKey', 'planRowId', 'stage', 'unit', 'branchScope'];
export function findRecordedCharge(planned, recorded = [], used = new Set()) {
  const remaining = recorded.map((row,index)=>({row,index})).filter(item=>!used.has(item.index));
  const planId=planned.planRowId ?? planned.id;
  const linked=planId ? remaining.filter(({row})=>row.planRowId===planId) : [];
  if(linked.length===1)return linked[0].index;
  const scope=row=>JSON.stringify(['branchId','workupKey','stage','unit'].map(key=>text(row[key])));
  const candidates=remaining.filter(({row})=>scope(row)===scope(planned) && (row.materialId&&planned.materialId?row.materialId===planned.materialId:text(row.name??row.material??row.item)===text(planned.name)));
  if(candidates.length===1)return candidates[0].index;
  return -1;
}
export function undoPlannedCharges(actual, before, after) {
  const rows = actual.charges ?? [], keys = [...metadataKeys, 'plannedMass'];
  for (const saved of after) {
    const prior = before.find(row => row.id === saved.id), index = rows.findIndex(row => row.id === saved.id);
    if (index < 0) continue;
    const current = rows[index];
    if (!prior) {
      if (JSON.stringify(current) === JSON.stringify(saved)) rows.splice(index,1);
      continue;
    }
    for (const key of keys) if (JSON.stringify(prior[key]) !== JSON.stringify(saved[key]) && JSON.stringify(current[key]) === JSON.stringify(saved[key])) {
      if (own(prior,key)) current[key] = clone(prior[key]); else delete current[key];
    }
  }
}
const identityOf = row => ({
  materialId: text(row.materialId),
  name: text(firstOwn(row, ['name', 'material', 'item'])),
  scope: JSON.stringify(['branchId', 'workupKey', 'stage', 'unit'].map(key => text(row[key]))),
});
function plannedAmount(value) {
  if (!['number', 'string'].includes(typeof value) || !/^[+]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(String(value).trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}
function metadataOf(row) {
  // Name-only legacy plans can enrich a match, but must not erase its known material ID.
  return Object.fromEntries(metadataKeys
    .filter(key => own(row, key) && row[key] !== undefined && (key !== 'materialId' || text(row[key])))
    .map(key => [key, clone(row[key])]));
}

/**
 * syncPlannedCharges(actual, plannedRows, { mode: 'missing' | 'refresh' } = {})
 * Mutates actual.charges, using plannedWorkupCharges rows (amount is the PLANNED amount).
 * Only positive finite amounts with a material ID or name are actionable; others are skipped.
 * Matching requires the same branchId, workupKey, stage and unit (trimmed, case-sensitive;
 * absent/null/empty scope fields are equivalent, never wildcards and never unit conversions).
 * Stable material IDs match first; names are a fallback only when at least one ID is missing.
 * Different nonempty material IDs never match just because their names are equal.
 *
 * 'missing' appends definitely missing rows only. 'refresh' also updates plannedMass and
 * declared metadataKeys on unique matches. Neither mode deletes/merges existing rows or
 * changes their actual amounts, lots, notes, IDs, timestamps, raw fields or provenance.
 * New rows receive mass:null, a fresh ID and plan notes, never an actual amount from the plan.
 * Ambiguous many-to-one/one-to-many matches are skipped in BOTH modes rather than guessed.
 * Repeated rows with no existing candidates are all appended, preserving plan multiplicity.
 *
 * Returns { added, updated, matched, skipped, ambiguous }, with counts except ambiguous:
 * [{ plannedIndex, chargeIndexes, by: 'materialId' | 'name' }]. Indices refer to input arrays.
 * 'matched' counts unique matches; 'updated' counts matched rows with real changes.
 * The host can use these results for notifications/review; plannedRows is never mutated.
 */
export function syncPlannedCharges(actual, plannedRows, { mode = 'missing' } = {}) {
  if (!['missing', 'refresh'].includes(mode)) throw new RangeError('mode must be missing or refresh');
  const charges = rowsFor(actual, 'charges');
  const result = { added: 0, updated: 0, matched: 0, skipped: 0, ambiguous: [] };
  const pending = new Map();
  list(plannedRows).forEach((row, index) => {
    const amount = record(row) ? plannedAmount(row.amount) : null;
    const identity = record(row) ? identityOf(row) : null;
    if (amount == null || (!identity.materialId && !identity.name)) { result.skipped++; return; }
    pending.set(index, { row, amount, ...identity });
  });
  // Capture identities before any refresh; newly appended rows must not swallow repeated plans.
  const available = new Map();
  charges.forEach((row, index) => { if (record(row)) available.set(index, identityOf(row)); });
  function matchPass(by) {
    const candidates = new Map(), uses = new Map();
    for (const [plannedIndex, plan] of pending) {
      const indexes = [];
      for (const [chargeIndex, charge] of available) {
        const materialMatches = by === 'materialId'
          ? plan.materialId && plan.materialId === charge.materialId
          : (!plan.materialId || !charge.materialId) && plan.name && plan.name === charge.name;
        if (plan.scope !== charge.scope || !materialMatches) continue;
        indexes.push(chargeIndex);
        uses.set(chargeIndex, (uses.get(chargeIndex) ?? 0) + 1);
      }
      if (indexes.length) candidates.set(plannedIndex, indexes);
    }
    for (const [plannedIndex, indexes] of candidates) {
      const plan = pending.get(plannedIndex);
      if (indexes.length === 1 && uses.get(indexes[0]) === 1) {
        result.matched++;
        if (mode === 'refresh') {
          const charge = charges[indexes[0]], updates = { ...metadataOf(plan.row), plannedMass: plan.amount };
          let changed = false;
          if (!own(charge, 'sourceBaseline') && Object.entries(updates).some(([key,value]) => !Object.is(charge[key],value))) charge.sourceBaseline = clone(charge);
          for (const [key, value] of Object.entries(updates)) if (!Object.is(charge[key], value)) { charge[key] = value; changed = true; }
          if (changed) result.updated++;
        }
      } else result.ambiguous.push({ plannedIndex, chargeIndexes: indexes, by });
      // Reserve ambiguous candidates too: lower-confidence fallback must not repurpose them.
      pending.delete(plannedIndex);
      for (const index of indexes) available.delete(index);
    }
  }
  matchPass('materialId');
  matchPass('name');
  for (const { row, amount } of pending.values()) {
    charges.push({ id: uid(), name: '', unit: '', ...metadataOf(row), plannedMass: amount, mass: null, lot: '', notes: clone(row.notes ?? ''), recordedAt: new Date().toISOString() });
    result.added++;
  }
  return result;
}
