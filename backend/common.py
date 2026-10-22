"""Shared helpers: errors, responses, request parsing, and validation."""
import base64
import binascii
import json
from datetime import datetime, timezone
from decimal import Decimal


class ApiError(Exception):
    """An error that is safe to show to the caller."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def response(status_code, body):
    return {
        'statusCode': status_code,
        'body': json.dumps(body, default=json_default)
    }


def json_default(value):
    # DynamoDB returns numbers as Decimal
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def get_header(event, name):
    # Header names are lowercase in HTTP API events, but don't rely on it
    for key, value in (event.get('headers') or {}).items():
        if key.lower() == name:
            return value
    return None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_body(event):
    try:
        body = json.loads(event.get('body') or '')
    except json.JSONDecodeError:
        raise ApiError(400, 'Request body must be valid JSON') from None
    if not isinstance(body, dict):
        raise ApiError(400, 'Request body must be a JSON object')
    return body


def require_text(body, field, max_length):
    value = optional_text(body, field, max_length)
    if value is None:
        raise ApiError(400, f'{field} is required')
    return value


def optional_text(body, field, max_length):
    """Return the trimmed string, or None when missing or blank."""
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, f'{field} must be text')
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise ApiError(400, f'{field} must be at most {max_length} characters')
    return value


def optional_bool(body, field):
    value = body.get(field)
    if value is not None and not isinstance(value, bool):
        raise ApiError(400, f'{field} must be true or false')
    return value


def encode_page_token(key):
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


def decode_page_token(token):
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (binascii.Error, ValueError):
        raise ApiError(400, 'nextToken is invalid') from None
    if not isinstance(key, dict):
        raise ApiError(400, 'nextToken is invalid')
    return key


def page_limit(params, default, maximum):
    limit = params.get('limit', str(default))
    if not limit.isdigit() or not 1 <= int(limit) <= maximum:
        raise ApiError(400, f'limit must be a whole number from 1 to {maximum}')
    return int(limit)
