"""Code-only exports preserve existing identifiers and omit name descriptions."""
from __future__ import annotations

import re


def product_export_context(product):
    if not product:
        return None
    return {'id': product['id'], 'name': product['name']}


def project_export_context(project):
    if not project:
        return None
    identity = project.get('id') or project.get('series_id') or 'project'
    return {'id': identity, 'name': project.get('code') or '实验项目', 'revision': project.get('revision')}


def collect_name_codes(value):
    """Map saved business descriptions to the identifiers already used on bills."""
    result = {}
    def visit(item, inherited=''):
        if isinstance(item, dict):
            identity = item.get('source_id') or inherited
            business = bool(item.get('source_id') or 'components' in item or 'solid_fraction' in item
                            or 'registry_snapshot' in item or 'relative_directory' in item
                            or 'base_formula' in item or 'series_id' in item)
            if not identity and business:
                identity = item.get('id') or item.get('code') or ''
            if item.get('name') and identity:
                result[str(item['name'])] = str(identity)
            for key, child in item.items():
                if key == 'categories':
                    continue  # Product labels are not source names or record identities.
                if key in ('materials', 'premixes') and isinstance(child, dict):
                    for source_id, entry in child.items():
                        visit(entry, str(source_id))
                else:
                    visit(child, str(identity or ''))
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, inherited)
    visit(value)
    return result


def redact_export_text(value, product=None, project=None, name_codes=None):
    text = str(value)
    replacements = list((name_codes or {}).items())
    if product and product.get('name'):
        replacements.append((str(product['name']), product_export_context(product)['name']))
    if project:
        name = project.get('name') or (project.get('data') or {}).get('name')
        if name:
            replacements.append((str(name), project_export_context(project)['name']))
    # Longest match first when a project name contains the product name.
    # Explicit product/project contexts take priority over incidental name maps.
    replacements = list(dict(replacements).items())
    for original, neutral in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
        if original == neutral:
            continue
        pattern = re.escape(original)
        if neutral.startswith(original) and len(neutral) > len(original):
            pattern += '(?!' + re.escape(neutral[len(original):]) + ')'
        text = re.sub(pattern, lambda match: neutral, text, flags=re.IGNORECASE)
    return text


def anonymize_export_data(value, product=None, project=None):
    """Return a detached printable copy, keeping numeric values unchanged."""
    names = collect_name_codes(value)
    def visit(item):
        if isinstance(item, str):
            return redact_export_text(item, product, project, names)
        if isinstance(item, dict):
            identifiers = {'source_id', 'source_type', 'id', 'line_id', 'batch_id', 'preparation_id',
                           'component_id', 'point_id', 'product_id', 'formula_id', 'series_id', 'group_key', 'key',
                           'formula_code', 'batch_code', 'code', 'batch_codes'}
            return {key: '' if key in ('operator', 'review') else child if key in identifiers else visit(child)
                    for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, tuple):
            return tuple(visit(child) for child in item)
        return item
    return visit(value)
