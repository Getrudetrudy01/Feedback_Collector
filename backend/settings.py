"""Configuration and AWS clients, read once per Lambda container.

Other modules access these as settings.X at call time (not `from settings import X`),
so tests can reload this module to apply new environment variables.
"""
import os

import boto3
from botocore.config import Config

# Secret token for the service owner: can create projects and access every project
OWNER_TOKEN = os.environ.get('OWNER_TOKEN', '')

PROJECTS_TABLE = os.environ.get('PROJECTS_TABLE', 'Projects')
FEEDBACK_TABLE = os.environ.get('FEEDBACK_TABLE', 'ProjectFeedback')

# The service's own website addresses (hosted form and widget preview), always allowed
# to submit. Comma-separated: the HTTPS (CloudFront) address and the older S3 one.
SITE_ORIGINS = [o.strip().rstrip('/').lower() for o in os.environ.get('SITE_ORIGINS', '').split(',') if o.strip()]

# Private bucket for screenshots attached to feedback
SCREENSHOTS_BUCKET = os.environ.get('SCREENSHOTS_BUCKET', '')

# Optional SNS topic for owner alerts, and the highest rating that triggers one
NOTIFY_TOPIC_ARN = os.environ.get('NOTIFY_TOPIC_ARN', '')
NOTIFY_MAX_RATING = int(os.environ.get('NOTIFY_MAX_RATING', '5'))

dynamodb = boto3.resource('dynamodb')
projects_table = dynamodb.Table(PROJECTS_TABLE)
feedback_table = dynamodb.Table(FEEDBACK_TABLE)
sns = boto3.client('sns') if NOTIFY_TOPIC_ARN else None
# Signature v4 with virtual-hosted URLs, so presigned upload/view URLs work in every region
s3 = boto3.client('s3', config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'}))
