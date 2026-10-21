import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

# Configuration (set as Lambda environment variables)
TABLE_NAME = os.environ.get('TABLE_NAME', 'FeedBackTable')
# Secret token the admin dashboard must send to read feedback
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')
# Comma-separated list of origins allowed to call the API, or "*" for any
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '*').split(',') if o.strip()]

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

# Validation limits
MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
MAX_MESSAGE_LENGTH = 2000
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class ValidationError(Exception):
    pass


def get_header(event, name):
    # Header names are lowercase in HTTP API events but not in REST API events
    for key, value in (event.get('headers') or {}).items():
        if key.lower() == name:
            return value
    return None


def cors_headers(event):
    headers = {
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'
    }
    origin = get_header(event, 'origin')
    if '*' in ALLOWED_ORIGINS:
        headers['Access-Control-Allow-Origin'] = '*'
    elif origin in ALLOWED_ORIGINS:
        headers['Access-Control-Allow-Origin'] = origin
        headers['Vary'] = 'Origin'
    return headers


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


def get_method(event):
    # HTTP API (payload v2) or REST API (payload v1)
    return (event.get('requestContext', {}).get('http', {}).get('method')
            or event.get('httpMethod', ''))


def is_admin(event):
    if not ADMIN_TOKEN:
        # Fail closed: never expose feedback when no token is configured
        return False
    auth = get_header(event, 'authorization') or ''
    token = auth[len('Bearer '):] if auth.startswith('Bearer ') else ''
    return hmac.compare_digest(token.encode(), ADMIN_TOKEN.encode())


def require_text(body, field, max_length):
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f'{field} is required')
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError(f'{field} must be at most {max_length} characters')
    return value


def validate_feedback(body):
    if not isinstance(body, dict):
        raise ValidationError('Request body must be a JSON object')

    name = require_text(body, 'name', MAX_NAME_LENGTH)
    email = require_text(body, 'email', MAX_EMAIL_LENGTH)
    if not EMAIL_PATTERN.match(email):
        raise ValidationError('email is not a valid email address')
    message = require_text(body, 'message', MAX_MESSAGE_LENGTH)

    # Accept 4 or "4", but not 4.5, True, or "four"
    rating = body.get('rating')
    if isinstance(rating, str) and rating.strip().isdigit():
        rating = int(rating)
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValidationError('rating must be a whole number from 1 to 5')

    return {'name': name, 'email': email, 'message': message, 'rating': rating}


def create_feedback(event):
    try:
        body = json.loads(event.get('body') or '')
    except json.JSONDecodeError:
        return response(400, {'message': 'Request body must be valid JSON'})

    try:
        feedback = validate_feedback(body)
    except ValidationError as e:
        return response(400, {'message': str(e)})

    item = {
        'feedbackId': str(uuid.uuid4()),
        **feedback,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    # Save to DynamoDB
    table.put_item(Item=item)

    return response(200, {
        'message': 'Feedback submitted successfully',
        'feedbackId': item['feedbackId']
    })


def list_feedback(event):
    if not is_admin(event):
        return response(401, {'message': 'Unauthorized'})

    # Scan returns at most 1 MB per call, so follow the pagination key
    items = []
    scan_kwargs = {}
    while True:
        result = table.scan(**scan_kwargs)
        items.extend(result.get('Items', []))
        if 'LastEvaluatedKey' not in result:
            break
        scan_kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']

    # Newest first
    items.sort(key=lambda item: item.get('timestamp', ''), reverse=True)

    return response(200, {'feedbacks': items})


def route(event):
    method = get_method(event)

    # Handle preflight request
    if method == 'OPTIONS':
        return {'statusCode': 200, 'body': ''}

    try:
        if method == 'POST':
            return create_feedback(event)
        if method == 'GET':
            return list_feedback(event)
        return response(405, {'message': f'Method {method} not allowed'})
    except Exception as e:
        # Log the details, but don't expose internals to the client
        print(f'Error: {e!r}')
        return response(500, {'message': 'Internal server error'})


def lambda_handler(event, context):
    result = route(event)
    result['headers'] = cors_headers(event)
    return result
