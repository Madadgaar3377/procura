"""Read-only public proxy/CORS probe. Never uploads data or sends credentials.

python scripts/probe_supplier_import.py --base https://procumaterial.com
Use the actual deployed backend origin for --base if it differs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import httpx


def probe(base, origin):
    try:
        response = httpx.options(f'{base.rstrip("/")}/api/v1/imports/bulk/suppliers',
            headers={'Origin': origin, 'Access-Control-Request-Method': 'POST',
                     'Access-Control-Request-Headers': 'cache-control'}, timeout=8, follow_redirects=False)
        return {'origin': origin, 'status': response.status_code,
                'allow_origin': response.headers.get('access-control-allow-origin'),
                'allow_credentials': response.headers.get('access-control-allow-credentials'),
                'location': response.headers.get('location'),
                'server': response.headers.get('server'),
                'allow': response.headers.get('allow'),
                'request_id': response.headers.get('x-request-id')}
    except httpx.HTTPError as exc:
        return {'origin': origin, 'error_type': type(exc).__name__}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    args = parser.parse_args()
    origins = ['https://procumaterial.com', 'https://www.procumaterial.com']
    with ThreadPoolExecutor(max_workers=2) as executor:
        for result in executor.map(lambda origin: probe(args.base, origin), origins):
            print(result)
