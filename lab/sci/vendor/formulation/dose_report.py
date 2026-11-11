"""Public dose and frozen-weighing reports shared by the UI and module API.

Dose means the selected solid or volatile mass, never their combined mass.
"""
from .formula_core import (calculate_formula, resolve_source, CalculationError, FRACTION_TOL,
                       normalize_dose_basis, molar_mass_for_source)


def line_composition(batch,line):
    """Use only a batch's frozen data; never access the live registry."""
    if line['id'] in batch.get('actual_source_compositions',{}):
        return batch['actual_source_compositions'][line['id']]
    comp=line.get('source_composition')
    if comp:return comp
    rows=[r for section in ('resins','additives','solvents')
          for r in batch.get('formula_data',{}).get(section,[])
          if r.get('source_id')==line.get('source_id')]
    exact=next((r for r in rows if r.get('id')==line.get('id')),None)
    candidate=exact or (rows[0] if len(rows)==1 else None)
    if candidate and candidate.get('frozen_source'):return candidate['frozen_source']
    if not line.get('frozen'):
        try:
            return resolve_source(line.get('source_type'),line.get('source_id'),batch.get('registry_snapshot') or {})
        except CalculationError:
            return None
    return None

def actual_formula(batch,summary):
    if not summary['complete'] or not summary['composition_complete']:return None
    design=batch.get('formula_data') or {}
    records=summary['lines'];by_id={r['line_id']:r for r in records}
    matched=[];used=set()
    for group in ('resins','additives','solvents'):
        for row in design.get(group,[]):
            line=by_id.get(row.get('id'))
            if line is None:
                candidates=[r for r in records if r['source_id']==row.get('source_id') and r['line_id'] not in used]
                if len(candidates)!=1:return None
                line=candidates[0]
            if line['source_id']!=row.get('source_id') or line['line_id'] in used:return None
            used.add(line['line_id']);matched.append((group,row,line))
    if len(used)!=len(records):return None
    resin_total=sum(line['solid_mass_g'] for group,_,line in matched if group=='resins')
    if resin_total<=0:return None
    resin_parts_total=sum(line['actual_mass_g'] if row.get('parts_basis','solid')=='solution' else line['solid_mass_g']
                          for group,row,line in matched if group=='resins')
    result={'resin_solid_mass_g':resin_total,'resins':[],'additives':[],
            'solvents':[],'solvent_species_complete':summary['solvent_species_complete']}
    for group,row,line in matched:
        identity={'line_id':line['line_id'],'source_id':line['source_id']}
        if group=='resins':
            parts_basis=row.get('parts_basis','solid')
            actual_parts=(line['actual_mass_g'] if parts_basis=='solution' else line['solid_mass_g'])/resin_parts_total*100
            result['resins'].append({**identity,'parts_basis':parts_basis,
                                     'planned_parts':row.get('parts',0),'actual_parts':actual_parts})
        elif group=='additives':
            basis=row.get('basis','solid')
            if basis not in ('solid','volatile'):return None
            dose_basis=normalize_dose_basis(row.get('dose_basis'))
            active=line[basis+'_mass_g']
            denominator={'resin_solid_pct':resin_total,'total_solid_pct':summary['solid_mass_g'],
                         'total_pct':summary['total_mass_g'],'total_ppm':summary['total_mass_g']}.get(dose_basis)
            actual_value=(active if dose_basis=='fixed_g' else
                          active/denominator*(1e6 if dose_basis=='total_ppm' else 100) if denominator else None)
            if dose_basis=='mmol_per_g_resin':
                try:
                    mw=molar_mass_for_source(row,design,batch.get('registry_snapshot') or {})
                    actual_value=active/resin_total*1000/mw
                    identity['molar_mass_g_mol']=mw
                except CalculationError as exc:
                    actual_value=None
                    identity['dose_error']=str(exc)
            result['additives'].append({**identity,'basis':basis,'dose_basis':dose_basis,
                'planned_percent':row.get('percent',0),'actual_percent':actual_value,
                'planned_dose_value':row.get('percent',0),'actual_dose_value':actual_value})
    solvent_total=summary['solvent_mass_g']
    result['solvents']=[{**item,'solvent_pool_fraction':item['mass_g']/solvent_total if solvent_total>0 else None}
                        for item in summary['solvents']]
    return result

def actual_summary(batch,actual=None):
    from .app_service import number
    actual=batch.get('actual',{}) if actual is None else actual
    records=actual.get('lines',{})
    result={'complete':False,'composition_complete':True,'solvent_species_complete':True,
            'status':'incomplete','message':'实际称量尚未全部录入，仅显示已录入小计。',
            'line_count':len(batch['result']['lines']),'recorded_line_count':0,
            'recorded_mass_g':0.0,'recorded_solid_mass_g':0.0,'recorded_solvent_mass_g':0.0,
            'recorded_volatile_mass_g':0.0,'total_mass_g':None,'solid_mass_g':None,
            'solvent_mass_g':None,'volatile_mass_g':None,'solid_fraction':None,
            'mass_delta_g':None,'mass_delta_fraction':None,'solvents':[],'lines':[],
            'composition_missing_line_ids':[],'unattributed_solvent_mass_g':0.0,'actual_formula':None}
    species={}
    for line in batch['result']['lines']:
        lid=line['id'];record=records.get(lid,{})
        value=record.get('mass_g')
        amount=None if value in (None,'') else number(value,'实际称量')
        planned=float(line.get('planned_mass_g') or 0)
        delta=None if amount is None else amount-planned
        ratio=None if amount is None or planned==0 else delta/planned
        output={'line_id':lid,'source_id':line['source_id'],'planned_mass_g':planned,
                'actual_mass_g':amount,'delta_mass_g':delta,'delta_fraction':ratio,
                'delta_percent':None if ratio is None else ratio*100,'review':record.get('review',''),
                'solid_mass_g':None,'solvent_mass_g':None,'volatile_mass_g':None}
        comp=line_composition(batch,line)
        valid=False;shares={}
        if comp:
            try:
                s=number(comp.get('solid_fraction'),'冻结固体比例')
                v=number(comp.get('solvent_fraction'),'冻结溶剂比例')
                e=number(comp.get('volatile_fraction'),'冻结挥发比例')
                valid=abs(s+v+e-1)<=FRACTION_TOL and max(s,v,e)<=1
                shares={str(k):number(val,'冻结溶剂组成') for k,val in (comp.get('solvents') or {}).items()}
                if not shares and comp.get('solvent_id') and v>0:shares={str(comp['solvent_id']):v}
            except ValueError:
                valid=False
        if not valid:
            result['composition_complete']=False
            result['solvent_species_complete']=False
            result['composition_missing_line_ids'].append(lid)
        elif abs(sum(shares.values())-v)>FRACTION_TOL:
            result['solvent_species_complete']=False
            shares={}
        if amount is not None:
            result['recorded_line_count']+=1
            result['recorded_mass_g']+=amount
            if valid:
                output.update(solid_mass_g=amount*s,solvent_mass_g=amount*v,volatile_mass_g=amount*e)
                result['recorded_solid_mass_g']+=amount*s
                result['recorded_solvent_mass_g']+=amount*v
                result['recorded_volatile_mass_g']+=amount*e
                for sid,fraction in shares.items():species[sid]=species.get(sid,0.0)+amount*fraction
                result['unattributed_solvent_mass_g']+=amount*(v-sum(shares.values()))
        result['lines'].append(output)
    result['complete']=result['line_count']>0 and result['recorded_line_count']==result['line_count']
    result['solvents']=[{'source_id':sid,'mass_g':mass} for sid,mass in sorted(species.items())]
    result['solvent_species_status']='complete' if result['complete'] and result['solvent_species_complete'] else 'partial'
    if result['complete']:
        total=result['recorded_mass_g'];result['total_mass_g']=total
        totals=batch.get('result',{}).get('totals',{})
        target=totals.get('derived_total_mass_g',totals.get('target_total_mass_g',batch.get('formula_data',{}).get('total_mass_g')))
        if target is not None:
            result['mass_delta_g']=total-float(target)
            result['mass_delta_fraction']=result['mass_delta_g']/float(target) if float(target)>0 else None
        if result['composition_complete']:
            result.update(solid_mass_g=result['recorded_solid_mass_g'],solvent_mass_g=result['recorded_solvent_mass_g'],
                          volatile_mass_g=result['recorded_volatile_mass_g'])
            result['solid_fraction']=result['solid_mass_g']/total if total>0 else None
            result['status']='complete' if result['solvent_species_complete'] else 'partial'
            result['message']=(('实际称量完整，已按关联预混实称组成及工单冻结原料组成回算。' if batch.get('actual_source_compositions') else '实际称量完整，已按批次冻结组成回算。') if result['solvent_species_complete'] else
                               '实际称量完整，可按历史有效份回算总固含；历史各溶剂组成不完整，仅显示已知溶剂小计。')
            if total==0:result['message']='实际称量均为0，总质量为0，不能计算固含。'
        else:
            result['status']='composition_unknown'
            result['message']='实际称量完整，但关联预混实称未填全或缺少冻结组成；可显示实际总量，暂不能计算整批固含或完整溶剂组成。'
    result['actual_formula']=actual_formula(batch,result)
    return result


DOSE_UNITS = {'resin_solid_pct': '%', 'total_solid_pct': '%', 'total_pct': '%',
              'total_ppm': 'ppm', 'fixed_g': 'g', 'mmol_per_g_resin': 'mmol/g'}


def dose_value_for_mass(mass, dose_basis, totals, molar_mass=None):
    """Same unrounded conversion as display_text.js:doseValueForMass."""
    basis = normalize_dose_basis(dose_basis)
    total = totals.get('total_mass_g', totals.get('actual_mass_g', 0)) or 0
    solid = totals.get('solid_mass_g') or 0
    resin = totals.get('resin_solid_mass_g') or 0
    denominator = {'resin_solid_pct': resin / 100, 'total_solid_pct': solid / 100,
                   'total_pct': total / 100, 'total_ppm': total / 1e6, 'fixed_g': 1,
                   'mmol_per_g_resin': resin * (molar_mass or 0) / 1000}[basis]
    return mass / denominator if mass is not None and denominator > 0 else None


def dose_report(formula, registry, batch=None):
    """Report stored inputs, selected active doses and frozen actual weighings.

    Each quantity carries its unit and basis. Incomplete actual weighings remain
    explicitly unavailable. No database or live-registry lookup is performed.
    """
    calculation = (batch['result'] if batch and formula == batch.get('formula_data')
                   else calculate_formula(formula, registry))
    totals = calculation.get('totals') or {}
    lines = {line['id']: line for line in calculation.get('lines', [])}
    summary = actual_summary(batch) if batch else None
    actual = summary.get('actual_formula') if summary else None
    actual_lines = {line['line_id']: line for line in summary['lines']} if summary else {}
    rows = []

    def quantity(value, unit, basis, dose_basis):
        return {'value': value, 'unit': unit, 'basis': basis, 'dose_basis': dose_basis}

    for group in ('resins', 'additives', 'solvents'):
        for index, row in enumerate(formula.get(group, []), 1):
            rid = row.get('id') or {'resins': 'resin', 'additives': 'additive', 'solvents': 'solvent'}[group] + '-' + str(index)
            line = lines.get(rid)
            basis = row.get('basis', 'solid') if group == 'additives' else row.get('parts_basis', 'solid') if group == 'resins' else 'solvent'
            dose_basis = normalize_dose_basis(row.get('dose_basis')) if group == 'additives' else 'resin_parts' if group == 'resins' else 'fixed_g' if row.get('solvent_mode') == 'fixed_g' else 'solvent_parts'
            field = 'percent' if group == 'additives' else 'fixed_g' if group == 'solvents' and row.get('solvent_mode') == 'fixed_g' else 'parts'
            unit = DOSE_UNITS[dose_basis] if group == 'additives' else 'g' if field == 'fixed_g' else '份'
            design = quantity(row.get(field, 0), unit, basis, dose_basis)
            mass_key = 'volatile_mass_g' if basis == 'volatile' else 'solid_mass_g'
            active = (line.get(mass_key) or 0) if line else None
            mw = None
            if group == 'additives' and dose_basis == 'mmol_per_g_resin':
                try:
                    mw = molar_mass_for_source(row, formula, registry)
                except CalculationError:
                    pass
            calculated = quantity(None, unit, basis, dose_basis)
            if group == 'additives':
                calculated['value'] = dose_value_for_mass(active, dose_basis, totals, mw)
                calculated['active_mass'] = quantity(active, 'g', basis, 'mass')
                calculated['doses'] = {b: quantity(dose_value_for_mass(active, b, totals), DOSE_UNITS[b], basis, b)
                                       for b in ('resin_solid_pct', 'total_solid_pct', 'total_pct', 'total_ppm')}
                if line and line.get(mass_key) is None:
                    calculated['note'] = '旧数据缺少质量，按 0 计'
            elif group == 'resins' and line:
                parts_mass = (line.get('planned_mass_g') if basis == 'solution' else line.get('solid_mass_g')) or 0
                denominator = sum((lines.get(r.get('id') or f'resin-{i}', {}).get('planned_mass_g') if r.get('parts_basis', 'solid') == 'solution'
                                   else lines.get(r.get('id') or f'resin-{i}', {}).get('solid_mass_g')) or 0 for i, r in enumerate(formula.get('resins', []), 1))
                calculated['value'] = parts_mass / denominator * 100 if denominator else None
                calculated['unit'] = '%'
                calculated['dose_basis'] = 'resin_parts_pct'
                if line.get('planned_mass_g' if basis == 'solution' else 'solid_mass_g') is None:
                    calculated['note'] = '旧数据缺少质量，按 0 计'
            elif group == 'solvents':
                solvent = next((s for s in calculation.get('solvent_balance', []) if s['source_id'] == row.get('source_id')), None)
                calculated = quantity(solvent.get('final_mass_g') if solvent else None, 'g', 'solvent', 'final_mass')
            if line:
                calculated['weigh_mass'] = quantity(line.get('planned_mass_g'), 'g', 'solution', 'weigh_mass')
            observed = quantity(None, calculated['unit'], basis, calculated['dose_basis'])
            observed['status'] = 'not_provided' if not batch else 'unavailable'
            if actual:
                if group in ('resins', 'additives'):
                    matches = [r for r in actual[group] if r['line_id'] == rid and r['source_id'] == row.get('source_id')]
                    if matches:
                        observed['value'] = matches[0]['actual_dose_value' if group == 'additives' else 'actual_parts']
                        observed['basis'] = matches[0].get('basis', matches[0].get('parts_basis', basis))
                        if group == 'additives':
                            observed['dose_basis'] = matches[0]['dose_basis']
                            observed['unit'] = DOSE_UNITS[matches[0]['dose_basis']]
                else:
                    species = next((s for s in summary['solvents'] if s['source_id'] == row.get('source_id')), None)
                    observed['value'] = species['mass_g'] if species else None
                observed['status'] = 'available' if observed['value'] is not None else 'unavailable'
                measured = actual_lines.get(rid)
                if measured and measured['source_id'] == row.get('source_id'):
                    observed['weigh_mass'] = quantity(measured['actual_mass_g'], 'g', 'solution', 'weigh_mass')
                    if group == 'additives':
                        actual_basis = observed['basis']
                        observed['active_mass'] = quantity(measured[actual_basis + '_mass_g'], 'g', actual_basis, 'mass')
            rows.append({'group': group, 'row_id': rid, 'source_id': row.get('source_id'),
                         'source_type': row.get('source_type', 'material'),
                         'design': design, 'calculated': calculated, 'actual': observed})
    return {'ok': calculation.get('ok', False), 'rows': rows, 'totals': totals,
            'errors': calculation.get('errors', []), 'warnings': calculation.get('warnings', []),
            'actual_summary': summary}
