import pytest
from conftest import OWNER_TOKEN

FEEDBACK = '/v1/projects/{projectId}/feedback'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'


def only_item(call, project_id):
    [item] = call(f'GET {FEEDBACK}', token=OWNER_TOKEN, path={'projectId': project_id})[1]['feedbacks']
    return item


def test_context_from_headers_and_widget(make_project, submit, call):
    project, _ = make_project()
    status, _ = submit(project['projectId'], {
        'category': 'bug',
        'context': {
            'screen': '1920x1080', 'viewport': '1280x720', 'timezone': 'Africa/Accra',
            'errors': [
                {'message': 'TypeError: x is undefined', 'source': 'https://customer.example/app.js', 'line': 42},
                {'message': 'y' * 900},
            ],
        },
    }, headers={'user-agent': UA, 'accept-language': 'en-GB,en;q=0.9'})
    assert status == 201
    context = only_item(call, project['projectId'])['context']
    assert context['userAgent'] == UA
    assert context['language'] == 'en-GB'
    assert context['screen'] == '1920x1080'
    assert context['viewport'] == '1280x720'
    assert context['timezone'] == 'Africa/Accra'
    assert context['errors'][0] == {
        'message': 'TypeError: x is undefined', 'source': 'https://customer.example/app.js', 'line': 42}
    assert len(context['errors'][1]['message']) == 500  # long errors are trimmed, not rejected


def test_no_context_is_fine(make_project, submit, call):
    project, _ = make_project()
    submit(project['projectId'])
    assert 'context' not in only_item(call, project['projectId'])


@pytest.mark.parametrize('context', [
    'Chrome', {'screen': '1920 by 1080'}, {'viewport': 1280}, {'timezone': '<script>'},
    {'errors': 'oops'}, {'errors': [{'message': 'e'}] * 11}, {'errors': [{'source': 'no message'}]},
])
def test_invalid_context_rejected(make_project, submit, context):
    project, _ = make_project()
    assert submit(project['projectId'], {'context': context})[0] == 400


def test_odd_error_fields_dropped(make_project, submit, call):
    project, _ = make_project()
    submit(project['projectId'], {'context': {'errors': [{'message': 'm', 'source': 5, 'line': -1, 'extra': 'x'}]}})
    assert only_item(call, project['projectId'])['context']['errors'] == [{'message': 'm'}]
