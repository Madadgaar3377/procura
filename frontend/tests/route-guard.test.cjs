const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function loadRouteGuard() {
  const verificationSrc = fs.readFileSync(path.join(__dirname, '../features/auth/verification.ts'), 'utf8');
  const guardSrc = fs.readFileSync(path.join(__dirname, '../lib/auth/route-guard.ts'), 'utf8');

  const transpile = (src) => ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;

  const verMod = { exports: {} };
  new Function('exports', 'module', 'require', transpile(verificationSrc))(verMod.exports, verMod, require);

  const guardMod = { exports: {} };
  const customRequire = (id) => {
    if (id === '@/features/auth/verification') return verMod.exports;
    return require(id);
  };
  new Function('exports', 'module', 'require', transpile(guardSrc))(guardMod.exports, guardMod, customRequire);
  return guardMod.exports;
}

const guard = loadRouteGuard();

const buyerUser = { is_admin: false, is_active: true, email_verified: true, phone_verified: true, email: 'b@x.sa', phone: '+966500000001' };
const supplierUser = { is_admin: false, is_active: true, email_verified: true, phone_verified: true, email: 's@x.sa', phone: '+966500000002' };
const adminUser = { is_admin: true, is_active: true, email_verified: true, phone_verified: true, email: 'a@x.sa', phone: '+966500000003' };
const suspendedUser = { is_admin: false, is_active: false, email_verified: true, phone_verified: true, email: 'x@x.sa', phone: '+966500000004' };

const buyerWs = { role: 'buyer', org_id: 'org-b', destination: '/app/buyer/dashboard', workspaces: [{ role: 'buyer', org_id: 'org-b', org_name: 'Buyer Co' }], needs_onboarding: false };
const supplierWs = { role: 'supplier', org_id: 'org-s', destination: '/app/supplier/dashboard', workspaces: [{ role: 'supplier', org_id: 'org-s', org_name: 'Supplier Co' }], needs_onboarding: false };
const multiWs = { role: 'supplier', org_id: 'org-s', destination: '/app/supplier/dashboard', workspaces: [{ role: 'buyer', org_id: 'org-b', org_name: 'Buyer Co' }, { role: 'supplier', org_id: 'org-s', org_name: 'Supplier Co' }], needs_onboarding: false };

test('open redirect protection rejects external, protocol-relative, and loop URLs', () => {
  assert.equal(guard.isSafeInternalRedirect('https://evil.com/phish'), null);
  assert.equal(guard.isSafeInternalRedirect('//evil.com/phish'), null);
  assert.equal(guard.isSafeInternalRedirect('/\\evil.com'), null);
  assert.equal(guard.isSafeInternalRedirect('/login?next=/app/buyer/dashboard'), null);
  assert.equal(guard.isSafeInternalRedirect('/app/buyer/rfqs/new'), '/app/buyer/rfqs/new');
});

test('buyer and supplier accessing admin routes are automatically redirected to their own dashboards', () => {
  assert.equal(guard.resolveAuthorizedDestination('admin', buyerUser, buyerWs, 'dashboard', '/app/admin/dashboard'), '/app/buyer/dashboard');
  assert.equal(guard.resolveAuthorizedDestination('admin', buyerUser, buyerWs, 'suppliers', '/app/admin/suppliers'), '/app/buyer/dashboard');
  assert.equal(guard.resolveAuthorizedDestination('admin', supplierUser, supplierWs, 'dashboard', '/app/admin/dashboard'), '/app/supplier/dashboard');
  assert.equal(guard.resolveAuthorizedDestination('admin', supplierUser, supplierWs, 'users', '/app/admin/users'), '/app/supplier/dashboard');
});

test('cross-role buyer/supplier access redirects cleanly without loops', () => {
  assert.equal(guard.resolveAuthorizedDestination('supplier', buyerUser, buyerWs, 'inbox', '/app/supplier/inbox'), '/app/buyer/dashboard');
  assert.equal(guard.resolveAuthorizedDestination('buyer', supplierUser, supplierWs, 'rfqs', '/app/buyer/rfqs'), '/app/supplier/dashboard');
  assert.equal(guard.resolveAuthorizedDestination('buyer', buyerUser, buyerWs, 'dashboard', '/app/buyer/dashboard'), null);
  assert.equal(guard.resolveAuthorizedDestination('supplier', supplierUser, supplierWs, 'dashboard', '/app/supplier/dashboard'), null);
});

test('admin allowed on admin routes and redirected from buyer/supplier routes', () => {
  assert.equal(guard.resolveAuthorizedDestination('admin', adminUser, undefined, 'dashboard', '/app/admin/dashboard'), null);
  assert.equal(guard.resolveAuthorizedDestination('buyer', adminUser, undefined, 'dashboard', '/app/buyer/dashboard'), '/app/admin/dashboard');
  assert.equal(guard.resolveAuthorizedDestination('supplier', adminUser, undefined, 'inbox', '/app/supplier/inbox'), '/app/admin/dashboard');
});

test('multi-workspace user can access both authorized roles but not admin', () => {
  assert.equal(guard.resolveAuthorizedDestination('buyer', buyerUser, multiWs, 'rfqs', '/app/buyer/rfqs'), null);
  assert.equal(guard.resolveAuthorizedDestination('supplier', buyerUser, multiWs, 'inbox', '/app/supplier/inbox'), null);
  assert.equal(guard.resolveAuthorizedDestination('admin', buyerUser, multiWs, 'dashboard', '/app/admin/dashboard'), '/app/supplier/dashboard');
});

test('suspended account redirects to session-expired', () => {
  assert.equal(guard.resolveAuthorizedDestination('buyer', suspendedUser, buyerWs, 'dashboard', '/app/buyer/dashboard'), '/session-expired');
});

test('post-login redirect validates ?next= against user role and rejects unauthorized targets', () => {
  assert.equal(guard.resolvePostLoginDestination('/app/admin/dashboard', buyerUser, buyerWs), '/app/buyer/dashboard');
  assert.equal(guard.resolvePostLoginDestination('/app/buyer/rfqs/new', buyerUser, buyerWs), '/app/buyer/rfqs/new');
  assert.equal(guard.resolvePostLoginDestination('https://evil.com', buyerUser, buyerWs), '/app/buyer/dashboard');
  assert.equal(guard.resolvePostLoginDestination('/app/admin/suppliers', adminUser, { role: 'admin', org_id: null, destination: '/app/admin/dashboard', workspaces: [], needs_onboarding: false }), '/app/admin/suppliers');
});

test('unknown workspace segments are detected for 404 handling', () => {
  assert.equal(guard.isValidWorkspaceSegment('buyer', 'rfqs'), true);
  assert.equal(guard.isValidWorkspaceSegment('buyer', 'users'), false);
  assert.equal(guard.isValidWorkspaceSegment('admin', 'nonexistent-xyz'), false);
});
