"""API entry point: routes HTTP API requests to the projects and feedback modules."""
import auth
import feedback
import projects
from common import ApiError, response

# The only route that needs no key: the widget and hosted form post here
SUBMIT_ROUTE = 'POST /v1/projects/{projectId}/feedback'

# Keyed by the HTTP API routeKey; each handler gets (event, principal, path parameters)
ROUTES = {
    'POST /v1/projects': lambda e, p, path: projects.create_project(e, p),
    'GET /v1/projects': lambda e, p, path: projects.list_projects(p),
    'GET /v1/projects/{projectId}': lambda e, p, path: projects.get_project(p, path['projectId']),
    'PATCH /v1/projects/{projectId}': lambda e, p, path: projects.update_project(e, p, path['projectId']),
    'POST /v1/projects/{projectId}/rotate-key': lambda e, p, path: projects.rotate_key(p, path['projectId']),
    'GET /v1/projects/{projectId}/feedback': lambda e, p, path: feedback.list_feedback(e, p, path['projectId']),
    'PATCH /v1/projects/{projectId}/feedback/{feedbackId}':
        lambda e, p, path: feedback.update_feedback(e, p, path['projectId'], path['feedbackId']),
}

# API Gateway answers CORS itself; these cover direct invocations and local testing.
# Any origin is fine: the public route checks each project's allowed origins, and
# every other route needs a secret key sent in a header (never a cookie).
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'GET, POST, PATCH, OPTIONS'
}


def route(event):
    route_key = event.get('routeKey', '')
    path = event.get('pathParameters') or {}

    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {'statusCode': 204, 'body': ''}

    try:
        if route_key == SUBMIT_ROUTE:
            return feedback.submit_feedback(event, path['projectId'])

        handler = ROUTES.get(route_key)
        if handler is None:
            return response(404, {'message': 'Not found'})

        principal = auth.authenticate(event)
        if principal is None:
            return response(401, {'message': 'Unauthorized'})
        return handler(event, principal, path)
    except ApiError as e:
        return response(e.status, {'message': e.message})
    except Exception as e:
        # Log the details, but don't expose internals to the client
        print(f'Error: {e!r}')
        return response(500, {'message': 'Internal server error'})


def lambda_handler(event, context):
    result = route(event)
    result['headers'] = CORS_HEADERS
    return result
