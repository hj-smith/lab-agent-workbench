"""Effective-component doses grouped by active material for platform callers.

Every mass is the selected active fraction (solid or volatile) of a source, never
the solid plus volatile sum. Rows keep the stored design values; premixes are
split into their active materials with the registry definitions, or with the
row's frozen composition when the historical premix is no longer defined.
"""
from .formula_core import (CalculationError, FRACTION_TOL, normalize_dose_basis,
                       resolve_source)
from .dose_report import DOSE_UNITS, actual_summary, dose_value_for_mass

BASES = ('resin_solid_pct', 'total_solid_pct', 'total_pct', 'total_ppm', 'fixed_g', 'mmol_per_g_resin')
DENOMINATORS = {'resin_solid_pct': 'resin_solid_mass_g', 'total_solid_pct': 'solid_mass_g',
                'total_pct': 'total_mass_g', 'total_ppm': 'total_mass_g',
                'mmol_per_g_resin': 'resin_solid_mass_g'}
DENOMINATOR_LABELS = {'resin_solid_mass_g': '树脂固体质量', 'solid_mass_g': '固体质量',
                      'total_mass_g': '总质量'}
TOTAL_KEYS = ('total_mass_g', 'solid_mass_g', 'resin_solid_mass_g')


def _quantity(value, unit, basis, status, reason=None):
    item = {'value': value, 'unit': unit, 'basis': basis, 'status': status}
    if reason:
        item['reason'] = reason
    return item


def _basis_values(active, totals, molar_mass, status='planned'):
    """All six dose bases for one active mass; unusable ones stay null with a reason."""
    values = {}
    for basis in BASES:
        key = DENOMINATORS.get(basis)
        if active is None:
            reason = '有效份质量无法确定'
        elif key and not (totals.get(key) or 0) > 0:
            reason = DENOMINATOR_LABELS[key] + '为 0 或缺失，无法换算为' + basis
        elif basis == 'mmol_per_g_resin' and not molar_mass:
            reason = '缺少有效成分摩尔质量，无法按 mmol/g 树脂换算'
        else:
            reason = None
        value = None if reason else dose_value_for_mass(active, basis, totals, molar_mass)
        values[basis] = _quantity(value, DOSE_UNITS[basis], basis,
                                  'not_computable' if reason else status, reason)
    return values


def _pending_values(status, reason):
    return {basis: _quantity(None, DOSE_UNITS[basis], basis, status, reason) for basis in BASES}


def _entries(registry, key):
    value = registry.get(key, {}) or {}
    return value if isinstance(value, dict) else {item['id']: item for item in value}


def _leaves(source_type, source_id, registry, weight=1.0, stack=()):
    """Active materials of a source with their fraction of the source mass."""
    identity = (source_type, source_id)
    if identity in stack:
        raise CalculationError('PREMIX_CYCLE', f'预混液 {source_id}存在循环引用')
    item = _entries(registry, 'materials' if source_type == 'material' else 'premixes').get(source_id)
    if not isinstance(item, dict):
        raise CalculationError('UNKNOWN_SOURCE',
                               f"找不到{'原料' if source_type == 'material' else '预混液'}编号：{source_id}")
    composition = resolve_source(source_type, source_id, registry)
    if source_type == 'material':
        return [{'material_id': source_id, 'name': composition['name'],
                 'solid': composition['solid_fraction'], 'volatile': composition['volatile_fraction'],
                 'fraction': weight, 'molar_mass': item.get('molar_mass_g_mol')}]
    leaves = []
    for component in item.get('components') or []:
        leaves.extend(_leaves(component.get('source_type', 'material'), component['source_id'], registry,
                              weight * float(component['fraction']), (*stack, identity)))
    return leaves


def _active_leaves(source_type, source_id, registry):
    """Split a source into active materials; solvent-only components are dropped."""
    merged = {}
    try:
        leaves = _leaves(source_type, source_id, registry)
    except CalculationError:
        if source_type != 'premix':
            raise
        leaves = []
    for leaf in leaves:
        if not leaf['solid'] > 0 and not leaf['volatile'] > 0:
            continue
        known = merged.get(leaf['material_id'])
        if known is None:
            merged[leaf['material_id']] = dict(leaf)
        else:
            known['fraction'] += leaf['fraction']
    return list(merged.values())


def _matches_frozen(composition, leaves):
    """A historical row may only be split when its frozen pools equal the live recipe."""
    pools = {'solid': 0.0, 'volatile': 0.0}
    for leaf in leaves:
        pools['solid'] += leaf['fraction'] * leaf['solid']
        pools['volatile'] += leaf['fraction'] * leaf['volatile']
    return all(abs(pools[key] - (composition.get(key + '_fraction') or 0)) <= FRACTION_TOL
               for key in pools)


def _row_molar_mass(row, formula, registry):
    from .formula_core import molar_mass_for_source
    try:
        return molar_mass_for_source(row, formula, registry)
    except CalculationError:
        return None


def _row_parts(row, line, formula, registry):
    """Effective parts of one additive row and each part's share of its active pool."""
    sid, stype = row.get('source_id'), row.get('source_type', 'material')
    row_basis = 'volatile' if row.get('basis') == 'volatile' else 'solid'
    frozen = row.get('frozen_source')
    composition = (line or {}).get('source_composition') or {}
    note = None
    if line is not None:
        try:
            leaves = _active_leaves(stype, sid, registry)
        except CalculationError:
            leaves = []
        if leaves:
            if frozen:
                if _matches_frozen(composition, leaves):
                    note = '历史冻结行未记录成分比例，按当前预混定义拆分；冻结的固体与易挥发份和当前定义一致'
                else:
                    note = '历史冻结组成与当前预混定义不一致，未拆分成分'
                    leaves = []
            split = 'live-registry' if frozen else 'definition'
        else:
            split = 'unresolved'
        if leaves:
            # Every part takes the row's own basis: a solid row lists each
            # component's solid share, a volatile row its volatile share. Parts
            # with no share in that basis are not listed, so the shares always
            # add up to the design-page row mass.
            pools = {'solid': 0.0, 'volatile': 0.0}
            for leaf in leaves:
                for basis in pools:
                    pools[basis] += leaf['fraction'] * leaf[basis]
            parts = []
            for leaf in leaves:
                active = leaf['fraction'] * leaf[row_basis]
                if not active > 0:
                    continue
                parts.append({'material_id': leaf['material_id'], 'name': leaf['name'],
                              'basis': row_basis, 'share': active / pools[row_basis],
                              'molar_mass': leaf.get('molar_mass'), 'split': split, 'note': note})
            if parts:
                if len(parts) == 1:
                    parts[0]['share'] = 1.0
                return parts
        if not frozen:
            return [{'material_id': sid, 'name': composition.get('name') or sid, 'basis': row_basis,
                     'share': 1.0, 'molar_mass': _row_molar_mass(row, formula, registry),
                     'split': 'unresolved', 'note': '来源定义无法解析为有效成分'}]
    return [{'material_id': sid, 'name': (frozen or composition or {}).get('name') or sid,
             'basis': row_basis, 'share': 1.0,
             'molar_mass': _row_molar_mass(row, formula, registry),
             'split': 'frozen' if frozen else 'unresolved', 'note': note}]


def _row_dose(row, line, dose_basis, totals, molar_mass, status):
    """The row's own dose in its stored basis, as the design or work-order page shows it."""
    basis = 'volatile' if row.get('basis') == 'volatile' else 'solid'
    active = None if line is None else line.get(basis + '_mass_g')
    reason = None
    if active is None:
        reason = '缺少该行的核算质量'
    elif dose_basis == 'mmol_per_g_resin' and not molar_mass:
        reason = '缺少有效成分摩尔质量，无法按 mmol/g 树脂换算'
    value = None if reason else dose_value_for_mass(active, dose_basis, totals, molar_mass)
    return _quantity(value, DOSE_UNITS[dose_basis], dose_basis,
                     'not_computable' if reason else status, reason)


def _actual_state(summary):
    """Measured actuals only; incomplete weighings stay to_confirm, never planned."""
    if not summary:
        return 'to_confirm', '没有工单实称记录'
    if not summary['complete']:
        return 'to_confirm', summary.get('message')
    if not summary['composition_complete'] or summary.get('actual_formula') is None:
        return 'to_confirm', summary.get('message')
    return 'measured', None


def formula_components(item, store=None):
    """Group one formula or work order by active material with design and actual doses."""
    if not isinstance(item, dict):
        raise ValueError('每组输入应为一个对象。')
    if item.get('revision') is not None and item.get('formula_id'):
        if store is None:
            raise ValueError('按配方编号和修订号调用时，请指定项目库。')
        saved = store.get_formula(item['formula_id'])
        if int(saved['revision']) != int(item['revision']):
            raise CalculationError('RECORD_NOT_FOUND',
                                   f"配方 {item['formula_id']} 没有修订 {item['revision']}。")
    from .capabilities import _formula_input
    formula, registry, batch = _formula_input(item, store)
    from .formula_core import calculate_formula
    calculation = (batch['result'] if batch and formula == batch.get('formula_data')
                   else calculate_formula(formula, registry))
    totals = calculation.get('totals') or {}
    lines = {line['id']: line for line in calculation.get('lines') or []}
    row_errors = {}
    for error in calculation.get('errors') or []:
        row_errors.setdefault(error.get('row_id'), error)

    summary = actual_summary(batch) if batch else None
    actual_status, actual_reason = _actual_state(summary)
    actual_formula_result = (summary or {}).get('actual_formula')
    actual_totals = ({'total_mass_g': summary['total_mass_g'], 'solid_mass_g': summary['solid_mass_g'],
                      'resin_solid_mass_g': actual_formula_result['resin_solid_mass_g']}
                     if actual_status == 'measured' else {})
    measured = {line['line_id']: line for line in (summary or {}).get('lines') or []}

    components = {}
    for index, row in enumerate(formula.get('additives') or []):
        rid = row.get('id') or f'additive-{index+1}'
        line = lines.get(rid)
        dose_basis = normalize_dose_basis(row.get('dose_basis'))
        molar_mass = _row_molar_mass(row, formula, registry)
        error = row_errors.get(rid)
        if error is not None and line is None:
            parts = [{'material_id': row.get('source_id'), 'name': row.get('source_id'),
                      'basis': 'volatile' if row.get('basis') == 'volatile' else 'solid', 'share': 1.0,
                      'molar_mass': molar_mass, 'split': 'unresolved', 'note': error.get('message')}]
        else:
            parts = _row_parts(row, line, formula, registry)
        plan_dose = _row_dose(row, line, dose_basis, totals, molar_mass, 'planned')
        row_actual_dose = _row_dose(row, measured.get(rid), dose_basis, actual_totals,
                                    molar_mass, actual_status)
        if actual_status != 'measured' and not (actual_reason or row_actual_dose.get('reason')):
            row_actual_dose['reason'] = actual_reason
        for part in parts:
            pool = None if line is None else line.get(part['basis'] + '_mass_g')
            active = None if pool is None else pool * part['share']
            reason = None if active is not None else (
                (error or {}).get('message') or '该行没有可用的核算质量，不能换算剂量')
            entry = {'row_id': rid, 'source_id': row.get('source_id'),
                     'source_type': row.get('source_type', 'material'),
                     'percent': row.get('percent'), 'dose_basis': dose_basis,
                     'basis': row.get('basis') or 'solid', 'active_basis': part['basis'],
                     'share': part['share'], 'split': part['split'],
                     'molar_mass_g_mol': part['molar_mass'],
                     'active_mass_g': _quantity(active, 'g', part['basis'],
                                                'not_computable' if reason else 'planned', reason),
                     'dose': dict(plan_dose),
                     'values': (_pending_values('not_computable', reason) if reason
                                else _basis_values(active, totals, part['molar_mass']))}
            if part.get('note'):
                entry['note'] = part['note']
            if batch:
                observed = None
                if actual_status == 'measured':
                    record = measured.get(rid)
                    observed_pool = None if record is None else record.get(part['basis'] + '_mass_g')
                    observed = None if observed_pool is None else observed_pool * part['share']
                entry['actual'] = {
                    'status': actual_status,
                    'active_mass_g': _quantity(observed, 'g', part['basis'], actual_status, actual_reason),
                    'dose': {**row_actual_dose, 'status': 'not_computable' if row_actual_dose['status'] == 'not_computable'
                             else actual_status,
                             **({'reason': actual_reason} if actual_status != 'measured' else {})},
                    'values': (_pending_values(actual_status, actual_reason) if actual_status != 'measured'
                               else _basis_values(observed, actual_totals, part['molar_mass'], 'measured'))}
            component = components.get(part['material_id'])
            if component is None:
                component = components[part['material_id']] = {
                    'material_id': part['material_id'], 'name': part['name'], 'design': [],
                    'source_row_ids': [], 'source_ids': []}
            component['design'].append(entry)
            if rid not in component['source_row_ids']:
                component['source_row_ids'].append(rid)

    for component in components.values():
        entries = component['design']
        component['source_ids'] = list(dict.fromkeys(entry['source_id'] for entry in entries))
        reason = None
        if len({entry['active_basis'] for entry in entries}) > 1:
            reason = '同一成分在不同行分别按固体和易挥发有效份计入，不能合计'
        else:
            reason = next((entry['active_mass_g'].get('reason') for entry in entries
                           if entry['active_mass_g']['value'] is None), None)
        molars = {entry['molar_mass_g_mol'] for entry in entries}
        component_molar = molars.pop() if len(molars) == 1 and None not in molars else None
        component['values'] = (_pending_values('not_computable', reason) if reason
                               else _basis_values(sum(entry['active_mass_g']['value'] for entry in entries),
                                                  totals, component_molar))
        if not batch:
            continue
        if actual_status != 'measured' or reason:
            component['actual'] = {'status': 'not_computable' if reason else actual_status,
                                   'values': _pending_values('not_computable' if reason else actual_status,
                                                             reason or actual_reason)}
        else:
            absent = next((entry['actual']['active_mass_g'].get('reason') for entry in entries
                           if entry['actual']['active_mass_g']['value'] is None), None)
            component['actual'] = {'status': 'measured' if absent is None else 'not_computable',
                                   'values': (_basis_values(
                                       sum(entry['actual']['active_mass_g']['value'] for entry in entries),
                                       actual_totals, component_molar, 'measured') if absent is None
                                       else _pending_values('not_computable', absent))}

    result = {'ok': bool(calculation.get('ok')),
              'formula': {'id': item.get('formula_id'), 'code': formula.get('code'),
                          'revision': _revision(item, batch)},
              'batch': ({'id': batch['id'], 'code': batch.get('batch_code'),
                         'formula_code': batch.get('formula_code'), 'revision': batch.get('revision')}
                        if batch else None),
              'totals': {key: _quantity(totals.get(key), 'g', key, 'planned') for key in TOTAL_KEYS},
              'actual_totals': ({key: _quantity(actual_totals.get(key), 'g', key, 'measured')
                                 for key in TOTAL_KEYS} if actual_status == 'measured' else (
                  {key: _quantity(None, 'g', key, actual_status, actual_reason) for key in TOTAL_KEYS}
                  if batch else None)),
              'components': list(components.values()),
              'errors': calculation.get('errors') or [],
              'warnings': calculation.get('warnings') or []}
    return result


def _revision(item, batch):
    if batch is not None:
        return batch.get('revision')
    return item.get('revision')
