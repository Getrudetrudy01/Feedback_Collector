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
    assert item['timestamp'].endswith('+00:00')


def test_submit_ignores_client_timestamp(call, lf, valid_feedback):
    _, data, _ = call('POST', {**valid_feedback, 'timestamp': '1999-01-01T00:00:00'})
    item = lf.table.get_item(Key={'feedbackId': data['feedbackId']})['Item']
    assert not item['timestamp'].startswith('1999')


@pytest.mark.parametrize('change', [
    {'rating': 0}, {'rating': 6}, {'rating': 4.5}, {'rating': True}, {'rating': 'four'}, {'rating': None},
    {'email': 'not-an-email'}, {'name': '   '}, {'name': 'x' * 101}, {'message': 'x' * 2001}, {'message': 7},
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


def test_list_returns_feedback_newest_first(call, valid_feedback):
    for name in ['First', 'Second', 'Third']:
        call('POST', {**valid_feedback, 'name': name})
    status, data, _ = call('GET', admin=True)
    assert status == 200
    feedbacks = data['feedbacks']
    assert [f['name'] for f in feedbacks] == ['Third', 'Second', 'First']
    assert feedbacks[0]['rating'] == 4


def test_list_follows_scan_pagination(call, lf, valid_feedback, monkeypatch):
    for _ in range(3):
        call('POST', valid_feedback)
    real_scan = lf.table.scan
    monkeypatch.setattr(lf.table, 'scan', lambda **kw: real_scan(Limit=1, **kw))
    _, data, _ = call('GET', admin=True)
    assert len(data['feedbacks']) == 3


def test_cors_rejects_unknown_origin(call):
    _, _, headers = call('OPTIONS', headers={'origin': 'https://evil.example'})
    assert 'Access-Control-Allow-Origin' not in headers


def test_unknown_method(call):
    assert call('DELETE')[0] == 405


def test_internal_errors_are_not_leaked(call, lf, valid_feedback, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError('secret internal detail')
    monkeypatch.setattr(lf.table, 'put_item', boom)
    status, data, _ = call('POST', valid_feedback)
    assert status == 500
    assert 'secret' not in data['message']
