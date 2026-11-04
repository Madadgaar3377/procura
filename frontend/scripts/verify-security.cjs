// Record actual child exit codes without PowerShell stderr-redirection ambiguity.
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const cwd = path.resolve(__dirname, '..');
const evidence = path.resolve(cwd, '../docs');
const env = { ...process.env, NEXT_TELEMETRY_DISABLED: '1',
  NEXT_BUILD_DIR: process.env.NEXT_BUILD_DIR || '.next-procura-security-20261026' };
const results = {};
for (const [name, args] of [
  ['build', ['node_modules/next/dist/bin/next', 'build', '--webpack']],
  ['http-smoke', ['tests/http-smoke.mjs']],
]) {
  const fd = fs.openSync(path.join(evidence, `procura-${name}-20261026.txt`), 'w');
  let result;
  try {
    result = spawnSync(process.execPath, args, { cwd, env, stdio: ['ignore', fd, fd],
      windowsHide: true, timeout: 600_000 });
  } finally {
    fs.closeSync(fd);
  }
  results[name] = { exit_code: result.status, signal: result.signal,
    error: result.error ? result.error.code : null };
  fs.writeFileSync(path.join(evidence, 'procura-frontend-exits-20261026.json'), JSON.stringify(results, null, 2));
  console.log(`${name}: exit=${result.status}, signal=${result.signal}`);
  if (result.status !== 0) process.exit(result.status || 1);
}
