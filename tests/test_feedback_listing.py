"""Filtering, incremental polling, and stats for the dashboard."""
import pytest
from conftest import OWNER_TOKEN

FEEDBACK = '/v1/projects/{projectId}/feedback'
ITEM = '/v1/projects/{projectId}/feedback/{feedbackId}'
STATS = '/v1/projects/{projectId}/stats'


def list_page(call, project_id, token=OWNER_TOKEN, **query):
    status, data = call(f'GET {FEEDBACK}', token=token, path={'projectId': project_id}, query=query or None)
    assert status == 200, data
    return data


def list_all(call, project_id, **query):
    """Follow nextToken to the end and return every message."""
    messages, token = [], None
    while True:
        data = list_page(call, project_id, **query, **({'nextToken': token} if token else {}))
        messages += [f['message'] for f in data['feedbacks']]
        token = data['nextToken']
        if not token:
            return messages


@pytest.fixture
def seeded(make_project, submit, call, monkeypatch):
    """A project with five items of known IDs, oldest first."""
    project, key = make_project()
    ids = iter([f'{n:013d}-0000' for n in range(1, 6)])
    monkeypatch.setattr('feedback.new_feedback_id', lambda: next(ids))
    for body in [
        {'message': 'Checkout is BROKEN', 'category': 'bug', 'rating': 1},
        {'message': 'Love it', 'category': 'praise', 'rating': 5, 'email': 'pat@example.com'},
        {'message': 'Add dark mode', 'category': 'feature'},
        {'message': 'Broken link on pricing', 'category': 'bug', 'rating': 2},
        {'message': 'Nice', 'rating': 5},
    ]:
        submit(project['projectId'], body)
    path = {'projectId': project['projectId'], 'feedbackId': '0000000000001-0000'}
    assert call(f'PATCH {ITEM}', {'status': 'resolved'}, token=key, path=path)[0] == 200
    return project['projectId']


@pytest.mark.parametrize('query, expected', [
    ({'category': 'bug'}, ['Broken link on pricing', 'Checkout is BROKEN']),
    ({'status': 'resolved'}, ['Checkout is BROKEN']),
    ({'status': 'new', 'category': 'bug'}, ['Broken link on pricing']),
    ({'rating': '5'}, ['Nice', 'Love it']),
    ({'rating': 'none'}, ['Add dark mode']),
    # Case-insensitive, across message, email, name, and page
    ({'q': 'broken'}, ['Broken link on pricing', 'Checkout is BROKEN']),
    ({'q': 'PAT@'}, ['Love it']),
    ({'q': 'nothing matches'}, []),
])
def test_filters(seeded, call, query, expected):
    assert list_all(call, seeded, **query) == expected


def test_filtered_pages_are_full_and_continue(seeded, call):
    first = list_page(call, seeded, category='bug', limit='1')
    assert [f['message'] for f in first['feedbacks']] == ['Broken link on pricing']
    second = list_page(call, seeded, category='bug', limit='1', nextToken=first['nextToken'])
    assert [f['message'] for f in second['feedbacks']] == ['Checkout is BROKEN']


def test_filtered_request_stops_after_examining_enough(seeded, call, monkeypatch):
    monkeypatch.setattr('feedback.MAX_EXAMINED', 2)
    monkeypatch.setattr('feedback.MAX_PAGE_SIZE', 2)
    # Only the oldest item matches, beyond the examine budget: an empty page with a token
    data = list_page(call, seeded, status='resolved', limit='2')
    assert data['feedbacks'] == []
    assert data['nextToken']
    assert list_all(call, seeded, status='resolved', limit='2') == ['Checkout is BROKEN']


def test_since_returns_only_newer_items(seeded, call):
    data = list_page(call, seeded, since='0000000000003-0000')
    assert [f['message'] for f in data['feedbacks']] == ['Nice', 'Broken link on pricing']
    assert list_page(call, seeded, since='0000000000005-0000')['feedbacks'] == []


@pytest.mark.parametrize('query', [
    {'status': 'deleted'}, {'category': 'spam'}, {'rating': '6'}, {'rating': '4.5'},
    {'q': 'x' * 201}, {'since': 'yesterday'}, {'since': '123-abc'},
])
def test_list_rejects_bad_filters(seeded, call, query):
    assert call(f'GET {FEEDBACK}', token=OWNER_TOKEN, path={'projectId': seeded}, query=query)[0] == 400


def test_stats(seeded, call):
    status, data = call(f'GET {STATS}', token=OWNER_TOKEN, path={'projectId': seeded})
    assert status == 200
    assert data == {
        'total': 5,
        'unresolved': 4,
        'ratings': {'1': 1, '2': 1, '3': 0, '4': 0, '5': 2},
        'latestFeedbackId': '0000000000005-0000',
    }


def test_stats_for_empty_project(make_project, call):
    project, key = make_project()
    status, data = call(f'GET {STATS}', token=key, path={'projectId': project['projectId']})
    assert status == 200
    assert data['total'] == 0 and data['latestFeedbackId'] is None


def test_stats_checks_access(make_project, call):
    a, key_a = make_project('A')
    b, _ = make_project('B')
    assert call(f'GET {STATS}', token=key_a, path={'projectId': b['projectId']})[0] == 403
    assert call(f'GET {STATS}', path={'projectId': a['projectId']})[0] == 401
