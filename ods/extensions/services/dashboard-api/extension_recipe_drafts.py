"""Durable recipe proposals; never scanned as installed extensions or catalog entries."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def save_draft(directory, owner, candidate, validation):
    """Caller owns the directory lock. Consumers must revalidate before installation."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Invalid draft directory')
    encoded = json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if (len(encoded) > 262144 or validation.get('valid') is not True
            or validation.get('recipeDigest') != digest
            or validation.get('validationScope') != 'static-manifest-and-compose'
            or validation.get('existingExtensionIds') != []
            or validation.get('errors') != []
            or any(validation.get(key) is not False for key in
                   ('registered', 'installationStarted', 'runtimeVerified', 'provenanceVerified'))):
        raise ValueError('Draft requires matching static validation')
    owner_id = hashlib.sha256(owner.encode()).hexdigest()
    draft_id = hashlib.sha256((owner_id + ':' + digest).encode()).hexdigest()
    path = directory / (draft_id + '.json')
    document = {'schemaVersion': 1, 'state': 'draft', 'recipeDigest': digest,
                'ownerId': owner_id, 'candidate': candidate}
    if path.is_symlink():
        raise ValueError('Invalid draft file')
    if path.exists():
        if path.stat().st_size > 524288 or json.loads(path.read_text(encoding='utf-8')) != document:
            raise ValueError('Draft content requires inspection')
    else:
        # Bounded per-owner storage. Distinct proposals remain immutable.
        count = 0
        for existing in directory.glob('*.json'):
            if existing.is_symlink() or existing.stat().st_size > 524288:
                raise ValueError('Draft storage requires inspection')
            stored = json.loads(existing.read_text(encoding='utf-8'))
            if not isinstance(stored, dict):
                raise ValueError('Draft storage requires inspection')
            if stored.get('ownerId') == owner_id:
                count += 1
        if count >= 100:
            raise ValueError('Draft limit reached')
        descriptor, temporary = tempfile.mkstemp(prefix='.recipe-', dir=directory)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(document, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'schemaVersion': 1, 'draftId': draft_id, 'recipeDigest': digest, 'state': 'draft',
            'installationStarted': False, 'registered': False, 'requiresRevalidation': True}


def read_draft(directory, owner, draft_id):
    """Recover one owner's exact proposal; never infer it is still installable."""
    directory = Path(directory)
    if not isinstance(draft_id, str) or not re.fullmatch('[a-f0-9]{64}', draft_id) or directory.is_symlink():
        raise ValueError('Invalid draft')
    path = directory / (draft_id + '.json')
    if path.is_symlink() or path.stat().st_size > 524288:
        raise ValueError('Invalid draft')
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict):
        raise ValueError('Invalid draft document')
    owner_id = hashlib.sha256(owner.encode()).hexdigest()
    if document.get('ownerId') != owner_id or document.get('state') != 'draft' or document.get('schemaVersion') != 1:
        raise ValueError('Invalid draft owner or state')
    candidate = document['candidate']
    encoded = json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if document.get('recipeDigest') != digest or hashlib.sha256((owner_id + ':' + digest).encode()).hexdigest() != draft_id:
        raise ValueError('Draft content changed')
    return candidate
