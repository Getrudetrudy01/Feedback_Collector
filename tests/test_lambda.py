import pytest
from conftest import ADMIN_TOKEN, ORIGIN


def test_preflight(call):
    status, _, headers = call('OPTIONS')
    assert status == 200
    assert headers['Access-Control-Allow-Origin'] == ORIGIN


def test_submit_stores_cleaned_feedback(call, lf, valid_feedback):
    status, data, _ = call('POST', {**valid_feedback, 'name': '  Ann  ', 'rating': '4'})
    assert status == 200
    item = lf.table.get_item(Key={'feedbackId': data['feedbackId']})['Item']
    assert item['name'] == 'Ann'
    assert item['rating'] == 4
    assert item['category'] == 'general'
    assert item['status'] == 'new'
    assert item['timestamp'].endswith('+00:00')


def test_submit_with_category(call, lf, valid_feedback):
    _, data, _ = call('POST', {**valid_feedback, 'category': 'bug'})
    item = lf.table.get_item(Key={'feedbackId': data['feedbackId']})['Item']
    assert item['category'] == 'bug'


def test_submit_ignores_client_timestamp(call, lf, valid_feedback):
    _, data, _ = call('POST', {**valid_feedback, 'timestamp': '1999-01-01T00:00:00'})
    item = lf.table.get_item(Key={'feedbackId': data['feedbackId']})['Item']
    assert not item['timestamp'].startswith('1999')


@pytest.mark.parametrize('change', [
    {'rating': 0}, {'rating': 6}, {'rating': 4.5}, {'rating': True}, {'rating': 'four'}, {'rating': None},
    {'category': 'spam'}, {'email': 'not-an-email'}, {'name': '   '}, {'name': 'x' * 101},
    {'message': 'x' * 2001}, {'message': 7},
])
def test_submit_rejects_invalid_fields(call, valid_feedback, change):
    status, data, _ = call('POST', {**valid_feedback, **change})
    assert status == 400
    assert data['message']


@pytest.mark.parametrize('body', ['not json', '[1, 2]', '"text"', ''])
def test_submit_rejects_bad_body(call, body):
    status, _, _ = call('POST', body)
    assert status == 400


def test_list_requires_token(call):
    assert call('GET')[0] == 401
    assert call('GET', headers={'authorization': 'Bearer wrong'})[0] == 401
    assert call('GET', headers={'authorization': ADMIN_TOKEN})[0] == 401


def test_list_fails_closed_without_configured_token(call, lf):
    lf.ADMIN_TOKEN = ''
    assert call('GET', headers={'authorization': 'Bearer '})[0] == 401


def test_list_returns_feedback(call, valid_feedback):
    call('POST', valid_feedback)
    status, data, _ = call('GET', admin=True)
    assert status == 200
    assert data['nextToken'] is None
    [feedback] = data['feedbacks']
    assert feedback['name'] == 'Ann'
    assert feedback['rating'] == 4


def test_list_pages_with_next_token(call, valid_feedback):
    for name in ['A', 'B', 'C']:
        call('POST', {**valid_feedback, 'name': name})
    names, token, pages = [], None, 0
    while True:
        query = {'limit': '2', **({'nextToken': token} if token else {})}
        status, data, _ = call('GET', admin=True, query=query)
        assert status == 200
        assert len(data['feedbacks']) <= 2
        names += [f['name'] for f in data['feedbacks']]
        token, pages = data['nextToken'], pages + 1
        if not token:
            break
    assert sorted(names) == ['A', 'B', 'C']
    assert pages >= 2


@pytest.mark.parametrize('query', [
    {'limit': '0'}, {'limit': '501'}, {'limit': 'ten'},
    {'nextToken': 'not-base64!'}, {'nextToken': 'eyJ4IjogMX0='},  # second is {"x": 1}
])
def test_list_rejects_bad_query(call, query):
    assert call('GET', admin=True, query=query)[0] == 400


def test_update_status(call, valid_feedback):
    _, created, _ = call('POST', valid_feedback)
    status, data, _ = call('PATCH', {'status': 'resolved'}, admin=True, path_id=created['feedbackId'])
    assert status == 200
    assert data['feedback']['status'] == 'resolved'
    assert data['feedback']['updatedAt']
    _, listed, _ = call('GET', admin=True)
    assert listed['feedbacks'][0]['status'] == 'resolved'


def test_update_requires_token(call, valid_feedback):
    _, created, _ = call('POST', valid_feedback)
    assert call('PATCH', {'status': 'resolved'}, path_id=created['feedbackId'])[0] == 401


def test_update_unknown_id_is_404_and_creates_nothing(call):
    assert call('PATCH', {'status': 'resolved'}, admin=True, path_id='missing')[0] == 404
    assert call('GET', admin=True)[1]['feedbacks'] == []


@pytest.mark.parametrize('body', [{'status': 'deleted'}, {}, 'not json'])
def test_update_rejects_bad_status(call, valid_feedback, body):
    _, created, _ = call('POST', valid_feedback)
    assert call('PATCH', body, admin=True, path_id=created['feedbackId'])[0] == 400


def test_honeypot_discards_spam(call, valid_feedback):
    status, data, _ = call('POST', {**valid_feedback, 'website': 'http://spam.example'})
    assert status == 200
    assert data['feedbackId']
    assert call('GET', admin=True)[1]['feedbacks'] == []


def test_notifies_on_low_rating_only(call, lf, valid_feedback, monkeypatch):
    published = []
    monkeypatch.setattr(lf.sns, 'publish', lambda **kw: published.append(kw))
    call('POST', {**valid_feedback, 'rating': 5})
    call('POST', {**valid_feedback, 'rating': 2, 'message': 'Broken checkout'})
    [alert] = published
    assert alert['Subject'].startswith('New feedback (2/5)')
    assert 'Broken checkout' in alert['Message']


def test_notification_failure_does_not_fail_submit(call, lf, valid_feedback):
    lf.NOTIFY_TOPIC_ARN = 'arn:aws:sns:us-east-1:123456789012:does-not-exist'
    status, _, _ = call('POST', {**valid_feedback, 'rating': 1})
    assert status == 200
    assert len(call('GET', admin=True)[1]['feedbacks']) == 1


def test_cors_rejects_unknown_origin(call):
    _, _, headers = call('OPTIONS', headers={'origin': 'https://evil.example'})
    assert 'Access-Control-Allow-Origin' not in headers


def test_unknown_route(call):
    assert call('DELETE', admin=True)[0] == 405
    assert call('POST', {}, path_id='abc')[0] == 405
    assert call('PATCH', {'status': 'new'}, admin=True)[0] == 405


def test_internal_errors_are_not_leaked(call, lf, valid_feedback, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError('secret internal detail')
    monkeypatch.setattr(lf.table, 'put_item', boom)
    status, data, _ = call('POST', valid_feedback)
    assert status == 500
    assert 'secret' not in data['message']
