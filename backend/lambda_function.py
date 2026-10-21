import base64
import binascii
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

# Configuration (set as Lambda environment variables)
TABLE_NAME = os.environ.get('TABLE_NAME', 'FeedBackTable')
# Secret token the admin dashboard must send to read or update feedback
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')
# Comma-separated list of origins allowed to call the API, or "*" for any
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '*').split(',') if o.strip()]
# Optional SNS topic to notify on new feedback, and the highest rating that triggers it
NOTIFY_TOPIC_ARN = os.environ.get('NOTIFY_TOPIC_ARN', '')
NOTIFY_MAX_RATING = int(os.environ.get('NOTIFY_MAX_RATING', '5'))

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
sns = boto3.client('sns') if NOTIFY_TOPIC_ARN else None

# Validation limits
MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
MAX_MESSAGE_LENGTH = 2000
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CATEGORIES = ('general', 'bug', 'feature', 'praise', 'other')
STATUSES = ('new', 'resolved')

# Page size limits for listing feedback
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

# Hidden form field that people can't see but spam bots fill in
HONEYPOT_FIELD = 'website'


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
        'Access-Control-Allow-Methods': 'GET, POST, PATCH, OPTIONS'
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


def parse_body(event):
    try:
        body = json.loads(event.get('body') or '')
    except json.JSONDecodeError:
        raise ValidationError('Request body must be valid JSON') from None
    if not isinstance(body, dict):
        raise ValidationError('Request body must be a JSON object')
    return body


def require_text(body, field, max_length):
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f'{field} is required')
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError(f'{field} must be at most {max_length} characters')
    return value


def validate_feedback(body):
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

    category = body.get('category') or 'general'
    if category not in CATEGORIES:
        raise ValidationError(f'category must be one of: {", ".join(CATEGORIES)}')

    return {'name': name, 'email': email, 'message': message, 'rating': rating, 'category': category}


def notify(item):
    if not sns or item['rating'] > NOTIFY_MAX_RATING:
        return
    try:
        sns.publish(
            TopicArn=NOTIFY_TOPIC_ARN,
            Subject=f'New feedback ({item["rating"]}/5) from {item["name"]}'[:100],
            Message=(
                f'Rating: {item["rating"]}/5\n'
                f'Category: {item["category"]}\n'
                f'From: {item["name"]} <{item["email"]}>\n'
                f'Time: {item["timestamp"]}\n\n'
                f'{item["message"]}'
            )
        )
    except ClientError as e:
        # The feedback is already saved; a failed alert shouldn't fail the request
        print(f'Notification failed: {e!r}')


def create_feedback(event):
    body = parse_body(event)

    # Bots fill in every field; pretend it worked so they don't adapt
    if body.get(HONEYPOT_FIELD):
        print('Honeypot field filled, discarding submission')
        return response(200, {'message': 'Feedback submitted successfully', 'feedbackId': str(uuid.uuid4())})

    item = {
        'feedbackId': str(uuid.uuid4()),
        **validate_feedback(body),
        'status': 'new',
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    # Save to DynamoDB
    table.put_item(Item=item)
    notify(item)

    return response(200, {
        'message': 'Feedback submitted successfully',
        'feedbackId': item['feedbackId']
    })


def encode_page_token(key):
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


def decode_page_token(token):
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (binascii.Error, ValueError):
        raise ValidationError('nextToken is invalid') from None
    if not isinstance(key, dict) or not isinstance(key.get('feedbackId'), str) or len(key) != 1:
        raise ValidationError('nextToken is invalid')
    return key


def list_feedback(event):
    """Return one page of feedback. Pass the returned nextToken to get the next page.

    Items come back in DynamoDB order; the dashboard loads every page and sorts.
    """
    params = event.get('queryStringParameters') or {}

    limit = params.get('limit', str(DEFAULT_PAGE_SIZE))
    if not limit.isdigit() or not 1 <= int(limit) <= MAX_PAGE_SIZE:
        raise ValidationError(f'limit must be a whole number from 1 to {MAX_PAGE_SIZE}')

    scan_kwargs = {'Limit': int(limit)}
    if params.get('nextToken'):
        scan_kwargs['ExclusiveStartKey'] = decode_page_token(params['nextToken'])

    result = table.scan(**scan_kwargs)
    last_key = result.get('LastEvaluatedKey')

    return response(200, {
        'feedbacks': result.get('Items', []),
        'nextToken': encode_page_token(last_key) if last_key else None
    })


def update_feedback(event, feedback_id):
    body = parse_body(event)
    status = body.get('status')
    if status not in STATUSES:
        raise ValidationError(f'status must be one of: {", ".join(STATUSES)}')

    try:
        result = table.update_item(
            Key={'feedbackId': feedback_id},
            UpdateExpression='SET #status = :status, updatedAt = :now',
            # Don't create a new item when the ID doesn't exist
            ConditionExpression='attribute_exists(feedbackId)',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': status, ':now': datetime.now(timezone.utc).isoformat()},
            ReturnValues='ALL_NEW'
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return response(404, {'message': 'Feedback not found'})
        raise

    return response(200, {'feedback': result['Attributes']})


def route(event):
    method = get_method(event)
    feedback_id = (event.get('pathParameters') or {}).get('id')

    # Handle preflight request
    if method == 'OPTIONS':
        return {'statusCode': 200, 'body': ''}

    try:
        if method == 'POST' and not feedback_id:
            return create_feedback(event)

        if method in ('GET', 'PATCH') and not is_admin(event):
            return response(401, {'message': 'Unauthorized'})
        if method == 'GET' and not feedback_id:
            return list_feedback(event)
        if method == 'PATCH' and feedback_id:
            return update_feedback(event, feedback_id)

        return response(405, {'message': f'Method {method} not allowed here'})
    except ValidationError as e:
        return response(400, {'message': str(e)})
    except Exception as e:
        # Log the details, but don't expose internals to the client
        print(f'Error: {e!r}')
        return response(500, {'message': 'Internal server error'})


def lambda_handler(event, context):
    result = route(event)
    result['headers'] = cors_headers(event)
    return result
