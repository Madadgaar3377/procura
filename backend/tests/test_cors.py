from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.cors import with_cors


ALLOWED_ORIGINS = [
    'https://www.procumaterial.com',
    'https://procumaterial.com',
    'http://localhost:3000',
]


def _client(origins: list[str]) -> TestClient:
    app = FastAPI()

    @app.get('/api/v1/auth/me')
    def auth_me():
        raise HTTPException(status_code=401, detail='Not authenticated')

    return TestClient(with_cors(app, origins))


def test_cors_origins_accept_comma_separated_and_json_array():
    comma_separated = Settings(_env_file=None, cors_origins=','.join(ALLOWED_ORIGINS))
    json_array = Settings(_env_file=None, cors_origins='["' + '","'.join(ALLOWED_ORIGINS) + '"]')

    assert comma_separated.cors_origin_list == ALLOWED_ORIGINS
    assert json_array.cors_origin_list == ALLOWED_ORIGINS


def test_allowed_frontend_origins_receive_matching_cors_headers_on_auth_error():
    client = _client(ALLOWED_ORIGINS)

    for origin in ALLOWED_ORIGINS[:2]:
        response = client.get('/api/v1/auth/me', headers={'Origin': origin})
        assert response.status_code == 401
        assert response.headers['access-control-allow-origin'] == origin
        assert response.headers['access-control-allow-credentials'] == 'true'


def test_unlisted_origin_is_rejected_by_cors():
    client = _client(ALLOWED_ORIGINS)
    for origin in ('https://attacker.example', 'http://127.0.0.1:3000'):
        response = client.get('/api/v1/auth/me', headers={'Origin': origin})
        assert response.status_code == 401
        assert 'access-control-allow-origin' not in response.headers


def test_auth_preflight_returns_requested_method_and_headers():
    response = _client(ALLOWED_ORIGINS).options(
        '/api/v1/auth/me',
        headers={
            'Origin': 'https://www.procumaterial.com',
            'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'authorization,content-type,x-custom-header',
        },
    )

    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'https://www.procumaterial.com'
    assert response.headers['access-control-allow-credentials'] == 'true'
    assert 'GET' in response.headers['access-control-allow-methods']
    assert 'authorization' in response.headers['access-control-allow-headers'].lower()
    assert 'x-custom-header' in response.headers['access-control-allow-headers'].lower()


def test_wildcard_origin_is_not_allowed_with_credentials():
    try:
        Settings(_env_file=None, cors_origins='*')
    except ValueError:
        return
    raise AssertionError('Wildcard CORS origins must be rejected')
