from fastapi.middleware.cors import CORSMiddleware


def with_cors(app, origins: list[str]):
    """Wrap an ASGI application so CORS headers cover FastAPI error responses too."""
    return CORSMiddleware(
        app,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
        expose_headers=['Retry-After', 'X-Request-ID', 'Server-Timing'],
    )
