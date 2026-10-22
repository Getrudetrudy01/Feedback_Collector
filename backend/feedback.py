"""Feedback submitted through the widget or hosted form, stored per project."""
import re
import secrets
import time

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

import auth
import screenshots
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
from projects import check_origin, load_project

MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
MAX_MESSAGE_LENGTH = 2000
MAX_URL_LENGTH = 2000
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CATEGORIES = ('general', 'bug', 'feature', 'praise', 'other')
STATUSES = ('new', 'resolved')

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
# Upper bound on items read by one filtered list request, to keep it fast
MAX_EXAMINED = 2000
MAX_SEARCH_LENGTH = 200
SEARCH_FIELDS = ('name', 'email', 'message', 'pageUrl')
FEEDBACK_ID_PATTERN = re.compile(r'^\d{13}-[0-9a-f]{1,32}$')

# Technical context sent by the widget to help reproduce problems
SIZE_PATTERN = re.compile(r'^\d{1,5}x\d{1,5}$')
TIMEZONE_PATTERN = re.compile(r'^[A-Za-z0-9_+\-/]{1,64}$')
MAX_ERRORS = 10
MAX_ERROR_TEXT = 500
MAX_USER_AGENT = 500

# Hidden form field that people can't see but spam bots fill in
HONEYPOT_FIELD = 'website'


def new_feedback_id():
    # Starts with the time in milliseconds, so IDs sort newest-last
    return f'{int(time.time() * 1000):013d}-{secrets.token_hex(4)}'


def validate_context(event, body):
    """Browser details: browser and language from request headers, the rest from the widget."""
    context = {}

    user_agent = get_header(event, 'user-agent')
    if user_agent:
        context['userAgent'] = user_agent[:MAX_USER_AGENT]
    language = (get_header(event, 'accept-language') or '').split(',')[0].split(';')[0].strip()
    if language:
        context['language'] = language[:35]

    sent = body.get('context')
    if sent is None:
        return context
    if not isinstance(sent, dict):
        raise ApiError(400, 'context must be an object')

    for field in ('screen', 'viewport'):
        value = sent.get(field)
        if value is not None:
            if not isinstance(value, str) or not SIZE_PATTERN.match(value):
                raise ApiError(400, f'context.{field} must look like 1920x1080')
            context[field] = value

    timezone = sent.get('timezone')
    if timezone is not None:
        if not isinstance(timezone, str) or not TIMEZONE_PATTERN.match(timezone):
            raise ApiError(400, 'context.timezone is invalid')
        context['timezone'] = timezone

    errors = sent.get('errors')
    if errors is not None:
        if not isinstance(errors, list) or len(errors) > MAX_ERRORS:
            raise ApiError(400, f'context.errors must be a list of at most {MAX_ERRORS} errors')
        cleaned = []
        for error in errors:
            if not isinstance(error, dict) or not isinstance(error.get('message'), str):
                raise ApiError(400, 'each context error needs a message')
            # Error text comes from the page and can be long; keep it, but bounded
            entry = {'message': error['message'][:MAX_ERROR_TEXT]}
            if isinstance(error.get('source'), str) and error['source']:
                entry['source'] = error['source'][:MAX_ERROR_TEXT]
            if isinstance(error.get('line'), int) and not isinstance(error['line'], bool) and error['line'] >= 0:
                entry['line'] = error['line']
            cleaned.append(entry)
        if cleaned:
            context['errors'] = cleaned

    return context


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
                f'Screenshot: {"yes, see the dashboard" if item.get("screenshotKey") else "no"}\n'
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
    context = validate_context(event, body)
    if context:
        item['context'] = context
    # Last, after all validation, so a rejected submission doesn't consume the upload
    if body.get('screenshotId') is not None:
        item['screenshotKey'] = screenshots.attach(project_id, body['screenshotId'])

    settings.feedback_table.put_item(Item=item)
    notify(project, item)

    return response(201, {'message': 'Feedback submitted successfully', 'feedbackId': item['feedbackId']})


def list_filters(params):
    """Optional filters from the query string; returns a function that tests one item."""
    status = params.get('status')
    if status and status not in STATUSES:
        raise ApiError(400, f'status must be one of: {", ".join(STATUSES)}')
    category = params.get('category')
    if category and category not in CATEGORIES:
        raise ApiError(400, f'category must be one of: {", ".join(CATEGORIES)}')
    rating = params.get('rating')
    if rating and rating not in ('1', '2', '3', '4', '5', 'none'):
        raise ApiError(400, 'rating must be a whole number from 1 to 5, or none')
    search = (params.get('q') or '').strip().lower()
    if len(search) > MAX_SEARCH_LENGTH:
        raise ApiError(400, f'q must be at most {MAX_SEARCH_LENGTH} characters')

    if not (status or category or rating or search):
        return None

    def matches(item):
        # Older items may lack status or category
        if status and item.get('status', 'new') != status:
            return False
        if category and item.get('category', 'general') != category:
            return False
        if rating == 'none' and item.get('rating') is not None:
            return False
        if rating and rating != 'none' and item.get('rating') != int(rating):
            return False
        if search and not any(search in str(item.get(field) or '').lower() for field in SEARCH_FIELDS):
            return False
        return True
    return matches


def list_feedback(event, principal, project_id):
    """Return one page of a project's feedback, newest first.

    Filters are applied here rather than in DynamoDB so search can ignore case. A filtered
    request reads at most MAX_EXAMINED items; when it stops early it returns a nextToken
    even if the page isn't full, and the caller continues from there.
    """
    auth.require_access(principal, project_id)
    params = event.get('queryStringParameters') or {}
    limit = page_limit(params, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
    matches = list_filters(params)

    key_condition = Key('projectId').eq(project_id)
    # Only items newer than one the caller already has, for cheap polling
    since = params.get('since')
    if since:
        if not FEEDBACK_ID_PATTERN.match(since):
            raise ApiError(400, 'since must be a feedback ID')
        key_condition = key_condition & Key('feedbackId').gt(since)

    query_kwargs = {
        'KeyConditionExpression': key_condition,
        'ScanIndexForward': False,
        'Limit': limit if matches is None else MAX_PAGE_SIZE
    }
    if params.get('nextToken'):
        start_key = decode_page_token(params['nextToken'])
        # A token from another project must not be usable here
        if set(start_key) != {'projectId', 'feedbackId'} or start_key['projectId'] != project_id \
                or not isinstance(start_key['feedbackId'], str):
            raise ApiError(400, 'nextToken is invalid')
        query_kwargs['ExclusiveStartKey'] = start_key

    items, last_key, examined = [], None, 0
    while True:
        result = settings.feedback_table.query(**query_kwargs)
        last_key = result.get('LastEvaluatedKey')
        if matches is None:
            items = result.get('Items', [])
            break
        for item in result.get('Items', []):
            examined += 1
            if matches(item):
                items.append(item)
                if len(items) == limit:
                    # Full page: continue after this item next time
                    last_key = {'projectId': project_id, 'feedbackId': item['feedbackId']}
                    break
        if len(items) == limit or not last_key or examined >= MAX_EXAMINED:
            break
        query_kwargs['ExclusiveStartKey'] = last_key

    for item in items:
        if item.get('screenshotKey'):
            item['screenshotUrl'] = screenshots.view_url(item['screenshotKey'])
    return response(200, {
        'feedbacks': items,
        'nextToken': encode_page_token(last_key) if last_key else None
    })


def feedback_stats(principal, project_id):
    """Totals for the dashboard header, so it doesn't need to download every item."""
    auth.require_access(principal, project_id)
    total = unresolved = 0
    ratings = {str(n): 0 for n in range(1, 6)}
    latest_id = None
    query_kwargs = {
        'KeyConditionExpression': Key('projectId').eq(project_id),
        'ScanIndexForward': False,
        'ProjectionExpression': 'feedbackId, rating, #status',
        'ExpressionAttributeNames': {'#status': 'status'}
    }
    while True:
        result = settings.feedback_table.query(**query_kwargs)
        for item in result.get('Items', []):
            latest_id = latest_id or item['feedbackId']
            total += 1
            if item.get('status', 'new') != 'resolved':
                unresolved += 1
            if str(item.get('rating')) in ratings:
                ratings[str(item['rating'])] += 1
        if 'LastEvaluatedKey' not in result:
            break
        query_kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']

    # The dashboard polls with ?since=latestFeedbackId to pick up only new items
    return response(200, {'total': total, 'unresolved': unresolved, 'ratings': ratings,
                          'latestFeedbackId': latest_id})


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
