"""Owner token and per-project admin keys.

Admin keys look like sk_<projectId>_<secret>. Only a SHA-256 hash of the secret
is stored, so a leaked table doesn't leak working keys.
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass

import settings
from common import ApiError, get_header

ADMIN_KEY_PREFIX = 'sk_'


@dataclass(frozen=True)
class Principal:
    owner: bool
    project_id: str | None = None

    def can_access(self, project_id):
        return self.owner or self.project_id == project_id


def new_project_id():
    return 'proj_' + secrets.token_hex(6)


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def new_admin_key(project_id):
    """Return (key to give the customer, hash to store)."""
    secret = secrets.token_hex(24)
    return f'{ADMIN_KEY_PREFIX}{project_id}_{secret}', hash_secret(secret)


def authenticate(event):
    """Return the Principal for the request's bearer token, or None."""
    auth = get_header(event, 'authorization') or ''
    if not auth.startswith('Bearer '):
        return None
    token = auth[len('Bearer '):].strip()

    if settings.OWNER_TOKEN and hmac.compare_digest(token.encode(), settings.OWNER_TOKEN.encode()):
        return Principal(owner=True)

    if token.startswith(ADMIN_KEY_PREFIX):
        project_id, _, secret = token[len(ADMIN_KEY_PREFIX):].rpartition('_')
        if project_id and secret:
            project = settings.projects_table.get_item(Key={'projectId': project_id}).get('Item')
            if project and hmac.compare_digest(hash_secret(secret), project.get('adminKeyHash', '')):
                return Principal(owner=False, project_id=project_id)

    return None


def require_owner(principal):
    if not principal.owner:
        raise ApiError(403, 'Only the service owner can do this')


def require_access(principal, project_id):
    if not principal.can_access(project_id):
        raise ApiError(403, 'This key does not have access to that project')
