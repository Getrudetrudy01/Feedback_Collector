import importlib
import json

import boto3
import pytest
from moto import mock_aws

ADMIN_TOKEN = 'test-admin-token-123'
ORIGIN = 'https://feedback.example'


@pytest.fixture
def lf(monkeypatch):
    """The Lambda module, loaded against a fresh mocked DynamoDB table."""
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
    monkeypatch.setenv('TABLE_NAME', 'TestFeedback')
    monkeypatch.setenv('ADMIN_TOKEN', ADMIN_TOKEN)
    monkeypatch.setenv('ALLOWED_ORIGINS', ORIGIN)

    with mock_aws():
        boto3.client('dynamodb').create_table(
            TableName='TestFeedback',
            BillingMode='PAY_PER_REQUEST',
            KeySchema=[{'AttributeName': 'feedbackId', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'feedbackId', 'AttributeType': 'S'}],
        )
        topic_arn = boto3.client('sns').create_topic(Name='feedback')['TopicArn']
        monkeypatch.setenv('NOTIFY_TOPIC_ARN', topic_arn)
        monkeypatch.setenv('NOTIFY_MAX_RATING', '2')
        import lambda_function
        # Reload so module-level config picks up the env vars above
        yield importlib.reload(lambda_function)


@pytest.fixture
def call(lf):
    """Invoke the handler with an HTTP API (v2) style event."""
    def _call(method, body=None, headers=None, admin=False, path_id=None, query=None):
        event = {
            'requestContext': {'http': {'method': method}},
            'headers': {'origin': ORIGIN, **(headers or {})},
        }
        if path_id is not None:
            event['pathParameters'] = {'id': path_id}
        if query is not None:
            event['queryStringParameters'] = query
        if admin:
            event['headers']['authorization'] = f'Bearer {ADMIN_TOKEN}'
        if body is not None:
            event['body'] = body if isinstance(body, str) else json.dumps(body)
        result = lf.lambda_handler(event, None)
        data = json.loads(result['body']) if result['body'] else None
        return result['statusCode'], data, result['headers']
    return _call


@pytest.fixture
def valid_feedback():
    return {'name': 'Ann', 'email': 'ann@example.com', 'message': 'Great app', 'rating': 4}
