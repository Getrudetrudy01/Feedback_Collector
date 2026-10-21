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
        import lambda_function
        # Reload so module-level config picks up the env vars above
        yield importlib.reload(lambda_function)


@pytest.fixture
def call(lf):
    """Invoke the handler with an HTTP API (v2) style event."""
    def _call(method, body=None, headers=None, admin=False):
        event = {
            'requestContext': {'http': {'method': method}},
            'headers': {'origin': ORIGIN, **(headers or {})},
        }
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
