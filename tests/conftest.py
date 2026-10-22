import importlib
import json

import boto3
import pytest
from moto import mock_aws

OWNER_TOKEN = 'test-owner-token-123'
SITE = 'https://customer.example'
HOSTED_SITE = 'https://feedback-service.example'
HOSTED_SITE_OLD = 'http://feedback-service-old.example'


@pytest.fixture
def settings(monkeypatch):
    """The settings module, reloaded against fresh mocked tables and topic."""
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
    monkeypatch.setenv('OWNER_TOKEN', OWNER_TOKEN)
    monkeypatch.setenv('PROJECTS_TABLE', 'TestProjects')
    monkeypatch.setenv('FEEDBACK_TABLE', 'TestFeedback')
    monkeypatch.setenv('NOTIFY_MAX_RATING', '2')
    monkeypatch.setenv('SITE_ORIGINS', f'{HOSTED_SITE}, {HOSTED_SITE_OLD}/')
    monkeypatch.setenv('SCREENSHOTS_BUCKET', 'test-screenshots')

    with mock_aws():
        dynamodb = boto3.client('dynamodb')
        dynamodb.create_table(
            TableName='TestProjects',
            BillingMode='PAY_PER_REQUEST',
            KeySchema=[{'AttributeName': 'projectId', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'projectId', 'AttributeType': 'S'}],
        )
        dynamodb.create_table(
            TableName='TestFeedback',
            BillingMode='PAY_PER_REQUEST',
            KeySchema=[
                {'AttributeName': 'projectId', 'KeyType': 'HASH'},
                {'AttributeName': 'feedbackId', 'KeyType': 'RANGE'},
            ],
            AttributeDefinitions=[
                {'AttributeName': 'projectId', 'AttributeType': 'S'},
                {'AttributeName': 'feedbackId', 'AttributeType': 'S'},
            ],
        )
        boto3.client('s3').create_bucket(Bucket='test-screenshots')
        monkeypatch.setenv('NOTIFY_TOPIC_ARN', boto3.client('sns').create_topic(Name='alerts')['TopicArn'])

        import settings
        # Reload so module-level config picks up the env vars above
        yield importlib.reload(settings)


@pytest.fixture
def call(settings):
    """Invoke the handler with an HTTP API (v2) event for the given route."""
    import lambda_function

    def _call(route_key, body=None, token=None, path=None, query=None, origin=SITE, headers=None):
        method = route_key.split(' ')[0]
        event = {
            'routeKey': route_key,
            'requestContext': {'http': {'method': method}},
            'headers': {**({'origin': origin} if origin else {}), **(headers or {})},
            'pathParameters': path,
            'queryStringParameters': query,
        }
        if token:
            event['headers']['authorization'] = f'Bearer {token}'
        if body is not None:
            event['body'] = body if isinstance(body, str) else json.dumps(body)
        result = lambda_function.lambda_handler(event, None)
        data = json.loads(result['body']) if result['body'] else None
        return result['statusCode'], data
    return _call


@pytest.fixture
def make_project(call):
    """Create a project as the owner; returns (project, admin key)."""
    def _make(name='Acme', origins=(SITE,), notify=False):
        status, data = call('POST /v1/projects',
                            {'name': name, 'allowedOrigins': list(origins), 'notifyOwner': notify},
                            token=OWNER_TOKEN)
        assert status == 201, data
        return data['project'], data['adminKey']
    return _make


@pytest.fixture
def submit(call):
    """Submit feedback to a project through the public route."""
    def _submit(project_id, body=None, **kwargs):
        body = {'message': 'Great app', **(body or {})}
        return call('POST /v1/projects/{projectId}/feedback', body, path={'projectId': project_id}, **kwargs)
    return _submit
