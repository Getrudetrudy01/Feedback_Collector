import pytest
from conftest import OWNER_TOKEN, SITE

PROJECT = '/v1/projects/{projectId}'


def test_create_project_returns_key_once(call, make_project, settings):
    project, key = make_project('Acme', origins=['https://Acme.example/', 'http://localhost:8000'])
    assert project['projectId'].startswith('proj_')
    assert project['allowedOrigins'] == ['https://acme.example', 'http://localhost:8000']
    assert key.startswith(f'sk_{project["projectId"]}_')
    assert 'adminKeyHash' not in project

    stored = settings.projects_table.get_item(Key={'projectId': project['projectId']})['Item']
    assert key.split('_')[-1] not in str(stored)  # only the hash is stored


def test_only_owner_creates_and_lists_projects(call, make_project):
    _, key = make_project()
    assert call('POST /v1/projects', {'name': 'X'})[0] == 401
    assert call('POST /v1/projects', {'name': 'X'}, token=key)[0] == 403
    assert call('GET /v1/projects', token=key)[0] == 403
    status, data = call('GET /v1/projects', token=OWNER_TOKEN)
    assert status == 200 and len(data['projects']) == 1


@pytest.mark.parametrize('body', [
    {}, {'name': '  '}, {'name': 'x' * 101},
    {'name': 'A', 'allowedOrigins': 'https://a.example'},
    {'name': 'A', 'allowedOrigins': ['https://a.example/path']},
    {'name': 'A', 'allowedOrigins': ['javascript:alert(1)']},
    {'name': 'A', 'allowedOrigins': ['https://a.example'] * 21},
    {'name': 'A', 'notifyOwner': 'yes'},
])
def test_create_project_validation(call, body):
    assert call('POST /v1/projects', body, token=OWNER_TOKEN)[0] == 400


def test_project_key_sees_only_its_project(call, make_project):
    a, key_a = make_project('A')
    b, _ = make_project('B')
    assert call(f'GET {PROJECT}', token=key_a, path={'projectId': a['projectId']})[1]['project']['name'] == 'A'
    assert call(f'GET {PROJECT}', token=key_a, path={'projectId': b['projectId']})[0] == 403
    assert call(f'GET {PROJECT}', token=OWNER_TOKEN, path={'projectId': b['projectId']})[0] == 200


@pytest.mark.parametrize('token', [
    'wrong', 'sk_', 'sk_proj_000000000000_abc', 'sk__abc', OWNER_TOKEN + 'x',
])
def test_bad_tokens_rejected(call, make_project, token):
    project, _ = make_project()
    assert call(f'GET {PROJECT}', token=token, path={'projectId': project['projectId']})[0] == 401


def test_key_for_other_project_with_right_secret_rejected(call, make_project):
    a, key_a = make_project('A')
    b, _ = make_project('B')
    forged = key_a.replace(a['projectId'], b['projectId'])
    assert call(f'GET {PROJECT}', token=forged, path={'projectId': b['projectId']})[0] == 401


def test_customer_updates_name_and_origins(call, make_project):
    project, key = make_project()
    path = {'projectId': project['projectId']}
    status, data = call(f'PATCH {PROJECT}', {'name': 'Acme Inc', 'allowedOrigins': []}, token=key, path=path)
    assert status == 200
    assert data['project']['name'] == 'Acme Inc'
    assert data['project']['allowedOrigins'] == []
    assert 'adminKeyHash' not in data['project']


def test_only_owner_toggles_alerts(call, make_project):
    project, key = make_project()
    path = {'projectId': project['projectId']}
    assert call(f'PATCH {PROJECT}', {'notifyOwner': True}, token=key, path=path)[0] == 403
    status, data = call(f'PATCH {PROJECT}', {'notifyOwner': True}, token=OWNER_TOKEN, path=path)
    assert status == 200 and data['project']['notifyOwner'] is True


def test_update_needs_a_field_and_an_existing_project(call):
    assert call(f'PATCH {PROJECT}', {}, token=OWNER_TOKEN, path={'projectId': 'proj_x'})[0] == 400
    assert call(f'PATCH {PROJECT}', {'name': 'N'}, token=OWNER_TOKEN, path={'projectId': 'proj_x'})[0] == 404


def test_rotate_key(call, make_project):
    project, old_key = make_project()
    path = {'projectId': project['projectId']}
    status, data = call(f'POST {PROJECT}/rotate-key', token=old_key, path=path)
    assert status == 200
    new_key = data['adminKey']
    assert new_key != old_key
    assert call(f'GET {PROJECT}', token=old_key, path=path)[0] == 401
    assert call(f'GET {PROJECT}', token=new_key, path=path)[0] == 200


def test_rotate_key_unknown_project(call):
    assert call(f'POST {PROJECT}/rotate-key', token=OWNER_TOKEN, path={'projectId': 'proj_x'})[0] == 404


def test_owner_token_unset_fails_closed(settings):
    # An unset owner token must never match an empty bearer token
    import lambda_function
    settings.OWNER_TOKEN = ''
    result = lambda_function.lambda_handler({
        'routeKey': 'GET /v1/projects',
        'requestContext': {'http': {'method': 'GET'}},
        'headers': {'authorization': 'Bearer '},
    }, None)
    assert result['statusCode'] == 401


def test_unknown_route_and_preflight(call):
    assert call('DELETE /v1/projects', token=OWNER_TOKEN)[0] == 404
    status, _ = call('OPTIONS /v1/projects', origin=SITE)
    assert status == 204
