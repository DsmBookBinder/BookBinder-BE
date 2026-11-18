"""Minimal CORS for the Vite dev server / configured FE origins (no wildcard, exact match only)."""
from flask import current_app, make_response, request

ALLOW_HEADERS = 'Content-Type, X-CSRF-Token'
ALLOW_METHODS = 'GET, POST, PATCH, DELETE, OPTIONS'


def allowed_origin():
    origin = request.headers.get('Origin')
    if origin and origin in current_app.config.get('CORS_ORIGINS', ()):
        return origin
    return None


def apply(response, origin):
    response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
    response.vary.add('Origin')
    return response


def preflight():
    """Answer OPTIONS preflights before authentication runs."""
    if request.method != 'OPTIONS' or 'Access-Control-Request-Method' not in request.headers:
        return None
    origin = allowed_origin()
    if origin is None:
        return None
    response = apply(make_response('', 204), origin)
    response.headers['Access-Control-Allow-Headers'] = ALLOW_HEADERS
    response.headers['Access-Control-Allow-Methods'] = ALLOW_METHODS
    return response


def add_headers(response):
    origin = allowed_origin()
    if origin is not None:
        apply(response, origin)
    elif request.headers.get('Origin') and current_app.config.get('CORS_ORIGINS'):
        response.vary.add('Origin')
    return response


def init_cors(app):
    origins = tuple(o.strip() for o in app.config.get('CORS_ORIGINS_RAW', '').split(',') if o.strip() and o.strip() != '*')
    app.config['CORS_ORIGINS'] = origins
    if origins:
        app.before_request(preflight)
        app.after_request(add_headers)
