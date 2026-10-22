"""Feedback submitted through the widget or hosted form, stored per project."""
import re
import secrets
import time

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

import auth
import settings
from common import (
    ApiError,
    decode_page_token,
    encode_page_token,
    get_header,
    now_iso,
    optional_text,
    page_limit,
    parse_body,
    require_text,
    response,
)
from projects import load_project

MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
MAX_MESSAGE_LENGTH = 2000
MAX_URL_LENGTH = 2000
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CATEGORIES = ('general', 'bug', 'feature', 'praise', 'other')
STATUSES = ('new', 'resolved')

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

# Hidden form field that people can't see but spam bots fill in
HONEYPOT_FIELD = 'website'


def new_feedback_id():
    # Starts with the time in milliseconds, so IDs sort newest-last
    return f'{int(time.time() * 1000):013d}-{secrets.token_hex(4)}'


def validate_feedback(body):
    feedback = {'message': require_text(body, 'message', MAX_MESSAGE_LENGTH)}

    name = optional_text(body, 'name', MAX_NAME_LENGTH)
    if name:
        feedback['name'] = name

    email = optional_text(body, 'email', MAX_EMAIL_LENGTH)
    if email:
        if not EMAIL_PATTERN.match(email):
            raise ApiError(400, 'email is not a valid email address')
        feedback['email'] = email

    # Optional. Accept 4 or "4", but not 4.5, True, or "four"
    rating = body.get('rating')
    if rating not in (None, ''):
        if isinstance(rating, str) and rating.strip().isdigit():
            rating = int(rating)
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ApiError(400, 'rating must be a whole number from 1 to 5')
        feedback['rating'] = rating

    category = body.get('category') or 'general'
    if category not in CATEGORIES:
        raise ApiError(400, f'category must be one of: {", ".join(CATEGORIES)}')
    feedback['category'] = category

    page_url = optional_text(body, 'pageUrl', MAX_URL_LENGTH)
    if page_url:
        if not page_url.startswith(('http://', 'https://')):
            raise ApiError(400, 'pageUrl must be an http(s) URL')
        feedback['pageUrl'] = page_url

    return feedback


def check_origin(event, project):
    """Only accept submissions from the project's websites, when it lists any.

    Browsers always send Origin on these cross-site requests and pages can't fake it,
    so this stops other sites from using the widget key. (Scripts outside a browser
    can still set any Origin; rate limits cover that.)
    """
    allowed = project.get('allowedOrigins') or []
    if not allowed:
        return
    origin = (get_header(event, 'origin') or '').rstrip('/').lower()
    if origin not in allowed and not (settings.SITE_ORIGIN and origin == settings.SITE_ORIGIN):
        raise ApiError(403, 'This website is not allowed to send feedback to this project')


def notify(project, item):
    if not settings.sns or not project.get('notifyOwner'):
        return
    rating = item.get('rating')
    if rating is not None and rating > settings.NOTIFY_MAX_RATING:
        return
    rating_text = f'{rating}/5' if rating is not None else 'no rating'
    sender = item.get('name') or item.get('email') or 'anonymous'
    try:
        settings.sns.publish(
            TopicArn=settings.NOTIFY_TOPIC_ARN,
            Subject=f'[{project["name"]}] New feedback ({rating_text}) from {sender}'[:100],
            Message=(
                f'Project: {project["name"]} ({project["projectId"]})\n'
                f'Rating: {rating_text}\n'
                f'Category: {item["category"]}\n'
                f'From: {item.get("name", "-")} <{item.get("email", "-")}>\n'
                f'Page: {item.get("pageUrl", "-")}\n'
                f'Time: {item["timestamp"]}\n\n'
                f'{item["message"]}'
            )
        )
    except ClientError as e:
        # The feedback is already saved; a failed alert shouldn't fail the request
        print(f'Notification failed: {e!r}')


def submit_feedback(event, project_id):
    project = load_project(project_id)
    check_origin(event, project)
    body = parse_body(event)

    # Bots fill in every field; pretend it worked so they don't adapt
    if body.get(HONEYPOT_FIELD):
        print(f'Honeypot field filled for {project_id}, discarding submission')
        return response(201, {'message': 'Feedback submitted successfully', 'feedbackId': new_feedback_id()})

    item = {
        'projectId': project_id,
        'feedbackId': new_feedback_id(),
        **validate_feedback(body),
        'status': 'new',
        'timestamp': now_iso()
    }
    settings.feedback_table.put_item(Item=item)
    notify(project, item)

    return response(201, {'message': 'Feedback submitted successfully', 'feedbackId': item['feedbackId']})


def list_feedback(event, principal, project_id):
    """Return one page of a project's feedback, newest first."""
    auth.require_access(principal, project_id)
    params = event.get('queryStringParameters') or {}

    query_kwargs = {
        'KeyConditionExpression': Key('projectId').eq(project_id),
        'ScanIndexForward': False,
        'Limit': page_limit(params, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
    }
    if params.get('nextToken'):
        start_key = decode_page_token(params['nextToken'])
        # A token from another project must not be usable here
        if set(start_key) != {'projectId', 'feedbackId'} or start_key['projectId'] != project_id \
                or not isinstance(start_key['feedbackId'], str):
            raise ApiError(400, 'nextToken is invalid')
        query_kwargs['ExclusiveStartKey'] = start_key

    result = settings.feedback_table.query(**query_kwargs)
    last_key = result.get('LastEvaluatedKey')
    return response(200, {
        'feedbacks': result.get('Items', []),
        'nextToken': encode_page_token(last_key) if last_key else None
    })


def update_feedback(event, principal, project_id, feedback_id):
    auth.require_access(principal, project_id)
    body = parse_body(event)
    status = body.get('status')
    if status not in STATUSES:
        raise ApiError(400, f'status must be one of: {", ".join(STATUSES)}')

    try:
        result = settings.feedback_table.update_item(
            Key={'projectId': project_id, 'feedbackId': feedback_id},
            UpdateExpression='SET #status = :status, updatedAt = :now',
            # Don't create a new item when the ID doesn't exist
            ConditionExpression=Attr('feedbackId').exists(),
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': status, ':now': now_iso()},
            ReturnValues='ALL_NEW'
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ApiError(404, 'Feedback not found') from None
        raise
    return response(200, {'feedback': result['Attributes']})
