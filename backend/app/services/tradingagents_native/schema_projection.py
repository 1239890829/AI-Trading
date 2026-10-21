"""Explicit wire-only strict projection; original models/schema are unchanged.

Codex's documented strict subset requires closed objects and every property in
required. Optional nonnullable values use null as an *omitted key token*. Only
those keys are removed before the original Pydantic/tool validator executes.
No malformed result, string amount, enum or source fact is repaired here.
"""
from copy import deepcopy


class ProjectionError(ValueError):
    pass


def nullable(schema, root=None, seen=frozenset()):
    root = schema if root is None else root
    if '$ref' in schema:
        reference = schema['$ref']
        if not isinstance(reference, str) or not reference.startswith('#/') or reference in seen:
            raise ProjectionError('Unreviewed external or recursive nullable reference')
        target = root
        for part in reference[2:].split('/'):
            target = target[part.replace('~1', '/').replace('~0', '~')]
        return nullable(target, root, seen | {reference})
    kind = schema.get('type')
    return kind == 'null' or isinstance(kind, list) and 'null' in kind or any(nullable(item, root, seen) for item in schema.get('anyOf', []))


def strict_wire_schema(original):
    notes = []
    def visit(value, path):
        if isinstance(value, list):
            return [visit(item, path + '/' + str(index)) for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return deepcopy(value)
        if any(key in value for key in ('$id', '$anchor', '$dynamicRef', 'allOf', 'not', 'if', 'then', 'else', 'patternProperties')):
            raise ProjectionError('Unreviewed schema feature; do not silently drop a constraint')
        result = {}
        for key, item in value.items():
            if key == 'default':
                notes.append({'path': path, 'operation': 'default_retained_in_original_not_wire'})
            elif key == 'oneOf':
                names = [branch.get('properties', {}).get('name', {}).get('const') for branch in item]
                if not names or not all(isinstance(name, str) for name in names) or len(set(names)) != len(names):
                    raise ProjectionError('oneOf can become anyOf only for disjoint original tool-name constants')
                result['anyOf'] = visit(item, path + '/anyOf')
                notes.append({'path': path, 'operation': 'disjoint_tool_oneOf_to_anyOf'})
            elif key == 'const':
                result['enum'] = [deepcopy(item)]
            elif key == '$ref':
                if not isinstance(item, str) or not (item == '#' or item.startswith('#/')):
                    raise ProjectionError('External reference prohibited')
                result[key] = item.replace('/oneOf/', '/anyOf/')
            else:
                result[key] = visit(item, path + '/' + key)
        if result.get('type') == 'object' or 'properties' in result:
            properties = result.setdefault('properties', {})
            if result.get('additionalProperties') not in (None, False):
                raise ProjectionError('Open/dictionary schemas require separate design, not silent narrowing')
            original_required = value.get('required', [])
            for name, field in list(properties.items()):
                if name not in original_required and not nullable(value['properties'][name], original):
                    properties[name] = {'anyOf': [field, {'type': 'null'}]}
                    notes.append({'path': path + '/properties/' + name,
                                  'operation': 'wire_null_means_omit_original_optional_key'})
            result['required'] = list(properties)
            result['additionalProperties'] = False
        return result
    schema = visit(original, '#')
    if schema.get('type') != 'object':
        raise ProjectionError('Codex final schema requires root object')
    return schema, notes


def restore_original_omissions(value, original):
    omitted = []
    def visit(item, schema, path):
        if '$ref' in schema:
            target = original
            for name in schema['$ref'][2:].split('/'):
                target = target[name]
            return visit(item, target, path)
        if 'oneOf' in schema:
            branches = [branch for branch in schema['oneOf'] if isinstance(item, dict)
                        and branch.get('properties', {}).get('name', {}).get('const') == item.get('name')]
            if len(branches) != 1:
                raise ProjectionError('Unmatched original offered tool name')
            return visit(item, branches[0], path)
        if 'anyOf' in schema:
            if item is None:
                return None
            branches = [branch for branch in schema['anyOf'] if branch.get('type') != 'null']
            if len(branches) == 1:
                return visit(item, branches[0], path)
        if isinstance(item, dict):
            properties = schema.get('properties', {})
            if set(item) - set(properties):
                raise ProjectionError('Unknown output field, tool_result or injected argument')
            result = {}
            for name, child in item.items():
                field = properties[name]
                if child is None and name not in schema.get('required', []) and not nullable(field, original):
                    omitted.append(path + '/' + name)
                else:
                    result[name] = visit(child, field, path + '/' + name)
            return result
        if isinstance(item, list):
            return [visit(child, schema.get('items', {}), path + '/' + str(index)) for index, child in enumerate(item)]
        return deepcopy(item)
    return visit(value, original, ''), omitted
