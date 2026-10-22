"""Screenshots attached to feedback, stored in a private S3 bucket.

Flow: the widget asks for an upload slot, uploads the image straight to S3 under
pending/, then submits feedback with the screenshot ID. Submitting moves the image to
screenshots/. Anything left in pending/ is deleted by a bucket lifecycle rule.
"""
import re
import uuid

from botocore.exceptions import ClientError

import settings
from common import ApiError, parse_body, response
from projects import check_origin, load_project

MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
CONTENT_TYPES = ('image/jpeg', 'image/png', 'image/webp')
UPLOAD_URL_SECONDS = 10 * 60
VIEW_URL_SECONDS = 60 * 60
SCREENSHOT_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')


def pending_key(project_id, screenshot_id):
    return f'pending/{project_id}/{screenshot_id}'


def create_upload(event, project_id):
    """Give the widget a short-lived, size-limited permission to upload one image."""
    project = load_project(project_id)
    check_origin(event, project)
    body = parse_body(event)

    content_type = body.get('contentType')
    if content_type not in CONTENT_TYPES:
        raise ApiError(400, f'contentType must be one of: {", ".join(CONTENT_TYPES)}')

    screenshot_id = uuid.uuid4().hex
    upload = settings.s3.generate_presigned_post(
        Bucket=settings.SCREENSHOTS_BUCKET,
        Key=pending_key(project_id, screenshot_id),
        Fields={'Content-Type': content_type},
        Conditions=[
            {'Content-Type': content_type},
            ['content-length-range', 1, MAX_SCREENSHOT_BYTES],
        ],
        ExpiresIn=UPLOAD_URL_SECONDS
    )
    return response(201, {'screenshotId': screenshot_id, 'upload': upload, 'maxBytes': MAX_SCREENSHOT_BYTES})


def attach(project_id, screenshot_id):
    """Move an uploaded screenshot out of pending/ and return its permanent key."""
    if not isinstance(screenshot_id, str) or not SCREENSHOT_ID_PATTERN.match(screenshot_id):
        raise ApiError(400, 'screenshotId is invalid')

    source = pending_key(project_id, screenshot_id)
    key = f'screenshots/{project_id}/{screenshot_id}'
    try:
        settings.s3.copy_object(
            Bucket=settings.SCREENSHOTS_BUCKET,
            Key=key,
            CopySource={'Bucket': settings.SCREENSHOTS_BUCKET, 'Key': source}
        )
    except ClientError as e:
        if e.response['Error']['Code'] in ('NoSuchKey', '404'):
            raise ApiError(400, 'The screenshot upload was not found. Please attach it again.') from None
        raise
    settings.s3.delete_object(Bucket=settings.SCREENSHOTS_BUCKET, Key=source)
    return key


def view_url(key):
    """A link to view a screenshot that expires, so leaked links stop working."""
    return settings.s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.SCREENSHOTS_BUCKET, 'Key': key},
        ExpiresIn=VIEW_URL_SECONDS
    )
