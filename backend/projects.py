"""Projects: one per customer site, each with its own admin key and allowed origins."""
import re

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

import auth
import settings
from common import ApiError, get_header, now_iso, optional_bool, optional_text, parse_body, require_text, response

MAX_NAME_LENGTH = 100
MAX_ORIGINS = 20
# scheme://host[:port], with no path, query, or trailing slash (what browsers send as Origin)
ORIGIN_PATTERN = re.compile(r'^https?://[a-z0-9.-]+(:\d{1,5})?$')


def public_view(project):
    return {key: value for key, value in project.items() if key != 'adminKeyHash'}


def load_project(project_id):
    project = settings.projects_table.get_item(Key={'projectId': project_id}).get('Item')
    if not project:
        raise ApiError(404, 'Project not found')
    return project


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


def validate_origins(value):
    if not isinstance(value, list) or len(value) > MAX_ORIGINS:
        raise ApiError(400, f'allowedOrigins must be a list of at most {MAX_ORIGINS} origins')
    origins = []
    for origin in value:
        if not isinstance(origin, str):
            raise ApiError(400, 'allowedOrigins must contain text')
        origin = origin.strip().rstrip('/').lower()
        if not ORIGIN_PATTERN.match(origin):
            raise ApiError(400, f'"{origin}" is not a valid origin, e.g. https://www.example.com')
        if origin not in origins:
            origins.append(origin)
    return origins


def create_project(event, principal):
    auth.require_owner(principal)
    body = parse_body(event)

    name = require_text(body, 'name', MAX_NAME_LENGTH)
    origins = validate_origins(body.get('allowedOrigins', []))
    notify_owner = optional_bool(body, 'notifyOwner') or False

    # Retry in the very unlikely case of an ID collision
    for _ in range(3):
        project_id = auth.new_project_id()
        admin_key, key_hash = auth.new_admin_key(project_id)
        project = {
            'projectId': project_id,
            'name': name,
            'allowedOrigins': origins,
            'notifyOwner': notify_owner,
            'adminKeyHash': key_hash,
            'createdAt': now_iso()
        }
        try:
            settings.projects_table.put_item(Item=project, ConditionExpression=Attr('projectId').not_exists())
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                continue
            raise
        # The admin key is only ever shown here and on rotation
        return response(201, {'project': public_view(project), 'adminKey': admin_key})

    raise RuntimeError('Could not generate a unique project ID')


def list_projects(principal):
    auth.require_owner(principal)
    projects = []
    scan_kwargs = {}
    while True:
        result = settings.projects_table.scan(**scan_kwargs)
        projects.extend(public_view(p) for p in result.get('Items', []))
        if 'LastEvaluatedKey' not in result:
            break
        scan_kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']
    projects.sort(key=lambda p: p.get('createdAt', ''))
    return response(200, {'projects': projects})


def get_project(principal, project_id):
    auth.require_access(principal, project_id)
    return response(200, {'project': public_view(load_project(project_id))})


def update_project(event, principal, project_id):
    auth.require_access(principal, project_id)
    body = parse_body(event)

    updates = {}
    name = optional_text(body, 'name', MAX_NAME_LENGTH)
    if name is not None:
        updates['name'] = name
    if 'allowedOrigins' in body:
        updates['allowedOrigins'] = validate_origins(body['allowedOrigins'])
    notify_owner = optional_bool(body, 'notifyOwner')
    if notify_owner is not None:
        # Alerts go to the owner's inbox, so only the owner may turn them on
        auth.require_owner(principal)
        updates['notifyOwner'] = notify_owner
    if not updates:
        raise ApiError(400, 'Nothing to update: send name, allowedOrigins, or notifyOwner')

    names = {f'#f{i}': field for i, field in enumerate(updates)}
    values = {f':v{i}': value for i, value in enumerate(updates.values())}
    try:
        result = settings.projects_table.update_item(
            Key={'projectId': project_id},
            UpdateExpression='SET ' + ', '.join(f'#f{i} = :v{i}' for i in range(len(updates))),
            ConditionExpression=Attr('projectId').exists(),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues='ALL_NEW'
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ApiError(404, 'Project not found') from None
        raise
    return response(200, {'project': public_view(result['Attributes'])})


def rotate_key(principal, project_id):
    """Replace the admin key. The old key stops working immediately."""
    auth.require_access(principal, project_id)
    admin_key, key_hash = auth.new_admin_key(project_id)
    try:
        settings.projects_table.update_item(
            Key={'projectId': project_id},
            UpdateExpression='SET adminKeyHash = :hash',
            ConditionExpression=Attr('projectId').exists(),
            ExpressionAttributeValues={':hash': key_hash}
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ApiError(404, 'Project not found') from None
        raise
    return response(200, {'adminKey': admin_key})
