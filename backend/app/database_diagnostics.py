"""Credential-safe database failure classification; no connections or logging."""
import re


def classify(error):
    detail = str(getattr(error, 'orig', error)).lower()
    rules = [
        (('does not match host name', 'hostname mismatch', 'host name mismatch'),
         'certificate_hostname_mismatch', 'Use the service hostname covered by the Aiven certificate, not its IP address or an alias; retain verify-full.'),
        (('certificate verify failed', 'certificate verification failed'),
         'certificate_verification_failed', 'Configure DATABASE_SSLROOTCERT with the Aiven service CA certificate; retain verify-full.'),
        (('root certificate', 'sslrootcert'),
         'root_certificate_configuration', 'Check the configured CA file exists and is readable by the API and worker.'),
        (('unsupported startup parameter', 'not allowed in options', 'unrecognized configuration parameter'),
         'unsupported_startup_parameter', 'Check whether DATABASE_URL uses a pooling endpoint that rejects PostgreSQL startup options; use the direct service endpoint.'),
        (('password authentication failed',),
         'authentication_failed', 'Check the application database role credentials privately.'),
        (('no pg_hba.conf entry',),
         'server_access_rejected', 'Check the service IP allowlist and whether the configured role can access the configured database.'),
        (('does not exist',),
         'database_or_role_missing', 'Check the database and role names in DATABASE_URL against the service configuration.'),
        (('could not read certificate', 'could not load certificate', 'pem lib', 'no certificate or crl found'),
         'invalid_certificate_file', 'Download the service CA again and check the configured file contains a PEM certificate.'),
        (('server does not support ssl', 'ssl is not enabled'),
         'server_ssl_unavailable', 'Check DATABASE_URL points to the TLS-enabled PostgreSQL service port.'),
        (('ssl syscall', 'ssl error', 'tls', 'connection reset', 'server closed the connection', 'eof detected'),
         'connection_interrupted', 'Check the service endpoint, port, service status and any VPN or firewall interrupting TLS traffic.'),
        (('invalid connection option', 'invalid sslmode', 'invalid integer value'),
         'invalid_connection_configuration', 'Check DATABASE_URL query options and the installed PostgreSQL driver configuration.'),
        (('forbidden by its access permissions', 'permission denied', 'winerror 10013'),
         'network_permission_denied', 'Check local firewall, endpoint security and execution-environment network permissions.'),
        (('could not translate host', 'name or service not known', 'getaddrinfo failed'),
         'dns_resolution_failed', 'Check service hostname and DNS resolution.'),
        (('timeout', 'timed out'),
         'connection_timeout', 'Check Aiven service status, port, IP allowlist and network connectivity.'),
        (('connection refused',),
         'connection_refused', 'Check service status, host and port.'),
    ]
    for phrases, category, action in rules:
        if any(phrase in detail for phrase in phrases):
            return {'category': category, 'action': action}
    return {'category': 'unclassified_database_error', 'action': 'Provide this safe report for further diagnosis.'}


def error_metadata(error):
    """Expose only allowlisted signals, never arbitrary driver messages."""
    original = getattr(error, 'orig', error)
    detail = str(original).lower()
    markers = ('ssl', 'certificate', 'authentication', 'password', 'hostname',
               'timeout', 'refused', 'permission', '10013', '10060', '10061',
               'resolve', 'encoding', 'connection', 'pool', 'endpoint', 'tenant',
               'startup', 'options', 'fatal', 'unexpected', 'closed', 'eof')
    sqlstate = getattr(original, 'sqlstate', None)
    return {
        'driver_error_type': type(original).__name__,
        'sqlstate': sqlstate if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate) else None,
        'error_signals': [marker for marker in markers if marker in detail],
    }


