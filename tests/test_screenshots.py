import base64
import json

import boto3
import pytest
from conftest import OWNER_TOKEN

UPLOAD = 'POST /v1/projects/{projectId}/screenshots'
FEEDBACK = '/v1/projects/{projectId}/feedback'
BUCKET = 'test-screenshots'


def request_upload(call, project_id, content_type='image/jpeg', **kwargs):
    return call(UPLOAD, {'contentType': content_type}, path={'projectId': project_id}, **kwargs)


def upload(call, project_id, data=b'\xff\xd8fake-jpeg'):
    """Get an upload slot, then do what the browser's form POST would: store the object."""
    status, slot = request_upload(call, project_id)
    assert status == 201, slot
    key = slot['upload']['fields']['key']
    boto3.client('s3').put_object(Bucket=BUCKET, Key=key, Body=data, ContentType='image/jpeg')
    return slot['screenshotId'], key


def list_feedback(call, project_id):
    return call(f'GET {FEEDBACK}', token=OWNER_TOKEN, path={'projectId': project_id})[1]['feedbacks']


def test_upload_slot_is_limited_to_one_small_image(make_project, call):
    project, _ = make_project()
    status, slot = request_upload(call, project['projectId'], 'image/png')
    assert status == 201
    fields = slot['upload']['fields']
    assert fields['key'] == f'pending/{project["projectId"]}/{slot["screenshotId"]}'
    assert fields['Content-Type'] == 'image/png'
    policy = json.loads(base64.b64decode(fields['policy']))
    assert ['content-length-range', 1, 5 * 1024 * 1024] in policy['conditions']
    assert {'Content-Type': 'image/png'} in policy['conditions']


@pytest.mark.parametrize('content_type', ['image/svg+xml', 'text/html', None, 'application/pdf'])
def test_upload_rejects_other_types(make_project, call, content_type):
    project, _ = make_project()
    assert request_upload(call, project['projectId'], content_type)[0] == 400


def test_upload_follows_project_origin_rules(make_project, call):
    project, _ = make_project()
    assert request_upload(call, project['projectId'], origin='https://evil.example')[0] == 403
    assert request_upload(call, 'proj_doesnotexist')[0] == 404


def test_screenshot_attached_and_viewable(make_project, submit, call):
    project, _ = make_project()
    screenshot_id, pending = upload(call, project['projectId'])
    status, _ = submit(project['projectId'], {'category': 'bug', 'screenshotId': screenshot_id})
    assert status == 201

    s3 = boto3.client('s3')
    final_key = f'screenshots/{project["projectId"]}/{screenshot_id}'
    assert s3.get_object(Bucket=BUCKET, Key=final_key)['Body'].read() == b'\xff\xd8fake-jpeg'
    with pytest.raises(s3.exceptions.NoSuchKey):
        s3.get_object(Bucket=BUCKET, Key=pending)  # moved out of pending/

    [item] = list_feedback(call, project['projectId'])
    assert item['screenshotKey'] == final_key
    url = item['screenshotUrl']
    assert BUCKET in url and final_key in url
    assert 'X-Amz-Signature=' in url and 'X-Amz-Expires=3600' in url  # signed, expires in an hour


def test_missing_or_reused_screenshot_rejected(make_project, submit, call):
    project, _ = make_project()
    assert submit(project['projectId'], {'screenshotId': 'a' * 32})[0] == 400
    screenshot_id, _ = upload(call, project['projectId'])
    assert submit(project['projectId'], {'screenshotId': screenshot_id})[0] == 201
    # The pending upload was consumed; it can't be attached twice
    assert submit(project['projectId'], {'screenshotId': screenshot_id})[0] == 400


@pytest.mark.parametrize('screenshot_id', ['../../secret', 'A' * 32, 123, '', 'abc'])
def test_screenshot_id_format_checked(make_project, submit, screenshot_id):
    project, _ = make_project()
    assert submit(project['projectId'], {'screenshotId': screenshot_id})[0] == 400


def test_cannot_attach_another_projects_upload(make_project, submit, call):
    a, _ = make_project('A')
    b, _ = make_project('B')
    screenshot_id, _ = upload(call, a['projectId'])
    assert submit(b['projectId'], {'screenshotId': screenshot_id})[0] == 400


def test_invalid_feedback_keeps_the_upload(make_project, submit, call):
    project, _ = make_project()
    screenshot_id, _ = upload(call, project['projectId'])
    assert submit(project['projectId'], {'message': '', 'screenshotId': screenshot_id})[0] == 400
    # Fixing the form and resubmitting still works
    assert submit(project['projectId'], {'screenshotId': screenshot_id})[0] == 201


def test_honeypot_ignores_screenshot(make_project, submit, call):
    project, _ = make_project()
    screenshot_id, pending = upload(call, project['projectId'])
    assert submit(project['projectId'], {'website': 'spam', 'screenshotId': screenshot_id})[0] == 201
    assert list_feedback(call, project['projectId']) == []
