"""Install the existing import runner as one systemd service on the API host.

Run with --activate to restart the API with embedded polling disabled and enable
the dedicated runner. Never changes database, R2, or authentication settings.
"""
import argparse
import os
from pathlib import Path
import subprocess


SERVICE = 'procura-import-worker.service'
MARKER = '# Managed Procumaterial import worker'


def systemctl(*args):
    return subprocess.check_output(['systemctl', *args], text=True).strip()


def install(backend: Path, python: Path, api_service: str, activate: bool):
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Run this installer with sudo on the Linux API host.')
    backend = backend.resolve(strict=True)
    python = python.absolute()
    if not python.is_file() or not (backend / '.env').is_file():
        raise SystemExit('The API Python executable and backend .env must exist.')
    if not (backend / 'app' / 'material_import_worker.py').is_file():
        raise SystemExit('Backend directory does not contain the existing import worker.')
    # Values go into systemd directives, not shell commands. Reject characters
    # requiring quoting/expansion rather than producing an ambiguous unit.
    for value in (str(backend), str(python), api_service):
        if any(c.isspace() or c in '%"\n\r' for c in value):
            raise SystemExit('Use paths and a service name without whitespace or systemd expansion characters.')
    if not api_service.endswith('.service') or '/' in api_service:
        raise SystemExit('API service must be a systemd .service unit name.')
    if systemctl('show', api_service, '-p', 'LoadState', '--value') != 'loaded':
        raise SystemExit('The API service must already exist; it will not be recreated.')
    # Older/manual runners do not share the new lock. Do not launch beside one,
    # stop it, or guess which custom unit is safe to replace.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            if b'app.material_import_worker' not in args:
                continue
            if (proc / 'cwd').resolve() != backend:
                continue
            if SERVICE not in (proc / 'cgroup').read_text():
                raise SystemExit(f'Existing custom/manual import worker PID {proc.name}; reuse its service or stop it explicitly before installation.')
        except (FileNotFoundError, PermissionError):
            continue
    existing = systemctl('show', SERVICE, '-p', 'FragmentPath', '--value')
    unit = Path('/etc/systemd/system') / SERVICE
    if existing and (Path(existing) != unit or MARKER not in unit.read_text()):
        raise SystemExit(f'An existing worker unit is configured at {existing}; reuse it rather than installing a duplicate.')
    api_user = systemctl('show', api_service, '-p', 'User', '--value') or 'root'
    api_group = systemctl('show', api_service, '-p', 'Group', '--value')
    dropin = Path('/etc/systemd/system') / f'{api_service}.d'
    override = dropin / 'procura-import-worker.conf'
    if override.exists() and MARKER not in override.read_text():
        raise SystemExit(f'Unmanaged API override exists at {override}; review it before installation.')
    unit.write_text(f'''{MARKER}
[Unit]
Description=Procumaterial durable import queue worker
After=network-online.target {api_service}
Wants=network-online.target

[Service]
Type=simple
User={api_user}
{f'Group={api_group}' if api_group else ''}
WorkingDirectory={backend}
EnvironmentFile={backend}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart={python} -m app.material_import_worker --interval 1
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=75
TimeoutStopSec=120
KillMode=control-group
UMask=0077

[Install]
WantedBy=multi-user.target
''')
    dropin.mkdir(parents=True, exist_ok=True)
    override.write_text(f'{MARKER}\n[Service]\nEnvironment=EMBEDDED_IMPORT_WORKER_ENABLED=false\n')
    subprocess.run(['systemd-analyze', 'verify', str(unit)], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if activate:
        subprocess.run(['systemctl', 'restart', api_service], check=True)
        subprocess.run(['systemctl', 'enable', SERVICE], check=True)
        # An existing managed worker must load the updated code as well.
        subprocess.run(['systemctl', 'restart', SERVICE], check=True)
        subprocess.run(['systemctl', 'is-active', api_service, SERVICE], check=True)
    print(f'Configured {SERVICE}; embedded API polling disabled on next API restart.')
    if not activate:
        print('Not activated. Rerun with --activate to restart the API and start the worker.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-dir', type=Path, default=Path('/var/www/procura/backend'))
    parser.add_argument('--python', type=Path, default=Path('/var/www/procura/venv/bin/python3'))
    parser.add_argument('--api-service', default='procura-backend.service')
    parser.add_argument('--activate', action='store_true')
    options = parser.parse_args()
    install(options.backend_dir, options.python, options.api_service, options.activate)
