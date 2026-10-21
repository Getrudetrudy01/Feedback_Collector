import pytest
from conftest import HOSTED_SITE, OWNER_TOKEN, SITE

FEEDBACK = '/v1/projects/{projectId}/feedback'
ITEM = '/v1/projects/{projectId}/feedback/{feedbackId}'


def list_all(call, project_id, token=OWNER_TOKEN, **query):
    status, data = call(f'GET {FEEDBACK}', token=token, path={'projectId': project_id}, query=query or None)
    assert status == 200, data
    return data


def test_submit_minimal_widget_feedback(make_project, submit, call, settings):
    project, key = make_project()
    status, data = submit(project['projectId'], {'message': '  Love it  '})
    assert status == 201
    [item] = list_all(call, project['projectId'], token=key)['feedbacks']
    assert item['feedbackId'] == data['feedbackId']
    assert item['message'] == 'Love it'
    assert item['category'] == 'general'
    assert item['status'] == 'new'
    assert 'rating' not in item and 'email' not in item and 'name' not in item


def test_submit_full_feedback(make_project, submit, call):
    project, _ = make_project()
    submit(project['projectId'], {
        'name': 'Ann', 'email': 'ann@example.com', 'rating': '4', 'category': 'bug',
        'pageUrl': 'https://customer.example/pricing', 'timestamp': '1999-01-01',
    })
    [item] = list_all(call, project['projectId'])['feedbacks']
    assert item['rating'] == 4
    assert item['category'] == 'bug'
    assert item['pageUrl'] == 'https://customer.example/pricing'
    assert not item['timestamp'].startswith('1999')


@pytest.mark.parametrize('change', [
    {'message': ''}, {'message': 'x' * 2001}, {'message': 7},
    {'rating': 0}, {'rating': 6}, {'rating': 4.5}, {'rating': True}, {'rating': 'four'},
    {'email': 'not-an-email'}, {'name': 'x' * 101}, {'category': 'spam'},
    {'pageUrl': 'javascript:alert(1)'}, {'pageUrl': 'https://x.example/' + 'a' * 2000},
])
def test_submit_rejects_invalid_fields(make_project, submit, change):
    project, _ = make_project()
    status, data = submit(project['projectId'], change)
    assert status == 400, change
    assert data['message']


@pytest.mark.parametrize('body', ['not json', '[1]', ''])
def test_submit_rejects_bad_body(make_project, call, body):
    project, _ = make_project()
    assert call(f'POST {FEEDBACK}', body, path={'projectId': project['projectId']})[0] == 400


def test_submit_to_unknown_project(submit):
    assert submit('proj_doesnotexist')[0] == 404


def test_origin_allowlist(make_project, submit):
    project, _ = make_project(origins=[SITE])
    assert submit(project['projectId'], origin=SITE)[0] == 201
    assert submit(project['projectId'], origin=SITE + '/')[0] == 201
    assert submit(project['projectId'], origin='https://CUSTOMER.example')[0] == 201
    assert submit(project['projectId'], origin='https://evil.example')[0] == 403
    assert submit(project['projectId'], origin=None)[0] == 403
    # The service's own hosted form and preview page always work
    assert submit(project['projectId'], origin=HOSTED_SITE)[0] == 201


def test_empty_allowlist_accepts_any_origin(make_project, submit):
    project, _ = make_project(origins=[])
    assert submit(project['projectId'], origin='https://anywhere.example')[0] == 201
    assert submit(project['projectId'], origin=None)[0] == 201


def test_honeypot_discards_spam(make_project, submit, call):
    project, _ = make_project()
    status, data = submit(project['projectId'], {'website': 'http://spam.example'})
    assert status == 201 and data['feedbackId']
    assert list_all(call, project['projectId'])['feedbacks'] == []


def test_projects_are_isolated(make_project, submit, call):
    a, key_a = make_project('A')
    b, key_b = make_project('B')
    submit(a['projectId'], {'message': 'for A'})
    submit(b['projectId'], {'message': 'for B'})
    assert [f['message'] for f in list_all(call, a['projectId'], token=key_a)['feedbacks']] == ['for A']
    assert call(f'GET {FEEDBACK}', token=key_a, path={'projectId': b['projectId']})[0] == 403
    assert call(f'GET {FEEDBACK}', path={'projectId': a['projectId']})[0] == 401


def test_list_is_newest_first_and_paginates(make_project, submit, call, monkeypatch):
    project, _ = make_project()
    ids = iter([f'{n:013d}-0000' for n in (1, 2, 3)])
    monkeypatch.setattr('feedback.new_feedback_id', lambda: next(ids))
    for message in ['first', 'second', 'third']:
        submit(project['projectId'], {'message': message})

    messages, token, pages = [], None, 0
    while True:
        query = {'limit': '2', **({'nextToken': token} if token else {})}
        data = list_all(call, project['projectId'], **query)
        messages += [f['message'] for f in data['feedbacks']]
        token, pages = data['nextToken'], pages + 1
        if not token:
            break
    assert messages == ['third', 'second', 'first']
    assert pages == 2


def test_page_token_cannot_cross_projects(make_project, submit, call):
    a, _ = make_project('A')
    b, _ = make_project('B')
    for _ in range(2):
        submit(a['projectId'])
    token = list_all(call, a['projectId'], limit='1')['nextToken']
    status, _ = call(f'GET {FEEDBACK}', token=OWNER_TOKEN, path={'projectId': b['projectId']},
                     query={'nextToken': token})
    assert status == 400


@pytest.mark.parametrize('query', [
    {'limit': '0'}, {'limit': '501'}, {'limit': 'ten'},
    {'nextToken': 'not-base64!'}, {'nextToken': 'eyJ4IjogMX0='},  # second is {"x": 1}
])
def test_list_rejects_bad_query(make_project, call, query):
    project, _ = make_project()
    assert call(f'GET {FEEDBACK}', token=OWNER_TOKEN, path={'projectId': project['projectId']},
                query=query)[0] == 400


def test_update_status(make_project, submit, call):
    project, key = make_project()
    _, created = submit(project['projectId'])
    path = {'projectId': project['projectId'], 'feedbackId': created['feedbackId']}
    status, data = call(f'PATCH {ITEM}', {'status': 'resolved'}, token=key, path=path)
    assert status == 200
    assert data['feedback']['status'] == 'resolved'
    assert data['feedback']['updatedAt']


def test_update_checks_access_and_existence(make_project, submit, call):
    a, key_a = make_project('A')
    b, _ = make_project('B')
    _, created = submit(b['projectId'])
    path_b = {'projectId': b['projectId'], 'feedbackId': created['feedbackId']}
    assert call(f'PATCH {ITEM}', {'status': 'resolved'}, token=key_a, path=path_b)[0] == 403
    # Right project, but the ID belongs to another project: must not create an item
    path_a = {'projectId': a['projectId'], 'feedbackId': created['feedbackId']}
    assert call(f'PATCH {ITEM}', {'status': 'resolved'}, token=key_a, path=path_a)[0] == 404
    assert list_all(call, a['projectId'])['feedbacks'] == []


@pytest.mark.parametrize('body', [{'status': 'deleted'}, {}, 'not json'])
def test_update_rejects_bad_status(make_project, submit, call, body):
    project, _ = make_project()
    _, created = submit(project['projectId'])
    path = {'projectId': project['projectId'], 'feedbackId': created['feedbackId']}
    assert call(f'PATCH {ITEM}', body, token=OWNER_TOKEN, path=path)[0] == 400


def test_alerts_only_for_opted_in_projects_and_low_ratings(make_project, submit, settings, monkeypatch):
    published = []
    monkeypatch.setattr(settings.sns, 'publish', lambda **kw: published.append(kw))
    quiet, _ = make_project('Quiet', notify=False)
    loud, _ = make_project('Loud', notify=True)

    submit(quiet['projectId'], {'rating': 1})
    submit(loud['projectId'], {'rating': 5})
    submit(loud['projectId'], {'rating': 2, 'message': 'Checkout broken'})
    submit(loud['projectId'], {'message': 'No rating given'})

    assert [p['Subject'] for p in published] == [
        '[Loud] New feedback (2/5) from anonymous',
        '[Loud] New feedback (no rating) from anonymous',
    ]
    assert 'Checkout broken' in published[0]['Message']


def test_alert_failure_does_not_fail_submit(make_project, submit, call, settings):
    project, _ = make_project(notify=True)
    settings.NOTIFY_TOPIC_ARN = 'arn:aws:sns:us-east-1:123456789012:does-not-exist'
    assert submit(project['projectId'], {'rating': 1})[0] == 201
    assert len(list_all(call, project['projectId'])['feedbacks']) == 1


def test_internal_errors_are_not_leaked(make_project, submit, settings, monkeypatch):
    project, _ = make_project()

    def boom(**kwargs):
        raise RuntimeError('secret internal detail')
    monkeypatch.setattr(settings.feedback_table, 'put_item', boom)
    status, data = submit(project['projectId'])
    assert status == 500
    assert 'secret' not in data['message']
