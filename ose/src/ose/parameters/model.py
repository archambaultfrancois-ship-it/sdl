"""Validation of parameter declarations before code generation or execution."""
import keyword
import re
from ose.modeling.model import fields


def validate_parameters(model, deployment):
    blocks = model.get('shared_parameters', {})
    if not isinstance(blocks, dict) or len(blocks) > 4096:
        raise ValueError('shared_parameters must contain at most 4096 blocks')
    for path, desc in blocks.items():
        if (not isinstance(path, str) or not 0 < len(path.encode()) <= 200 or
                any(not re.fullmatch(r'[A-Za-z0-9_-]+', part) for part in path.split('/'))):
            raise ValueError('invalid shared parameter path')
        fields(desc, ('root', 'schema', 'initial'), ('root', 'initial'))
        if not isinstance(desc['root'], str) or not desc['root']:
            raise ValueError('parameter root must be a nonempty string')
        if 'schema' in desc and (not isinstance(desc['schema'], str) or not desc['schema']):
            raise ValueError('parameter schema must be a nonempty path')
    for node in model['components'].values():
        if not isinstance(node, dict):
            raise ValueError('components must be objects')
        bindings = node.get('parameter_bindings', {})
        if not isinstance(bindings, dict) or len(bindings) > 256:
            raise ValueError('parameter_bindings must contain at most 256 bindings')
        folded = set()
        for alias, binding in bindings.items():
            if (not isinstance(alias, str) or len(alias) > 200 or
                    not re.fullmatch(r'[A-Za-z](?:[A-Za-z0-9]|_[A-Za-z0-9])*', alias) or
                    keyword.iskeyword(alias) or alias.lower() in folded):
                raise ValueError('parameter aliases must be distinct portable identifiers')
            folded.add(alias.lower())
            fields(binding, ('path', 'access'), ('path', 'access'))
            if not isinstance(binding['path'], str) or binding['path'] not in blocks:
                raise ValueError('unknown shared parameter binding')
            if binding['access'] not in ('read', 'write', 'read_write'):
                raise ValueError('parameter access must be read, write or read_write')
    clients = deployment.get('parameter_clients', {})
    if not isinstance(clients, dict) or len(clients) > 256:
        raise ValueError('parameter_clients must contain at most 256 identities')
    for identity, permissions in clients.items():
        if not isinstance(identity, str) or not identity or identity in model['components']:
            raise ValueError('invalid external parameter client identity')
        fields(permissions, ('read', 'write', 'token'), ())
        for access in ('read', 'write'):
            paths = permissions.get(access, [])
            if not isinstance(paths, list) or not all(isinstance(p, str) and p in blocks for p in paths):
                raise ValueError('invalid external parameter client paths')
        if 'token' in permissions and (not isinstance(permissions['token'], str) or len(permissions['token']) < 16):
            raise ValueError('local parameter client tokens require at least 16 characters')
        if deployment.get('execution', 'local') == 'local' and 'token' not in permissions:
            raise ValueError('local parameter clients require an explicit token')
