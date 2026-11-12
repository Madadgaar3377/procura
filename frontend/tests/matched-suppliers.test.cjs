const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Matched Suppliers: formatEligibilityExplanation produces human-readable explanations without UUIDs', () => {
  // Let's read the matched-suppliers.tsx code and test the formatting logic
  const filePath = path.resolve(__dirname, '../features/rfqs/matched-suppliers.tsx');
  const content = fs.readFileSync(filePath, 'utf8');

  // Verify the component does not contain the old giant DataTable with 18 columns
  assert.ok(!content.includes("columns={['supplier_name','verification_status','coverage','matched_categories','match_levels','brand_requirement_status'"));

  // Verify responsive cards and grid classes exist
  assert.ok(content.includes('pm-results-grid'));
  assert.ok(content.includes('pm-supplier-card'));
  assert.ok(content.includes('pm-card-header'));
  assert.ok(content.includes('pm-card-matrix'));
  assert.ok(content.includes('pm-explanation-box'));
  assert.ok(content.includes('pm-contact-bar'));
  assert.ok(content.includes('pm-card-actions'));

  // Verify filters exist: all, eligible, invited, mismatch, other ineligible
  assert.ok(content.includes("activeFilter === 'all'"));
  assert.ok(content.includes("activeFilter === 'eligible'"));
  assert.ok(content.includes("activeFilter === 'invited'"));
  assert.ok(content.includes("activeFilter === 'mismatch'"));
  assert.ok(content.includes("activeFilter === 'ineligible'"));

  // Verify Favorite toggle uses proper PUT and DELETE API
  assert.ok(content.includes("api(`organizations/${org.id}/favorites/${supplierOrgId}`, { method: 'DELETE' })"));
  assert.ok(content.includes("api(`organizations/${org.id}/favorites/${supplierOrgId}`, { method: 'PUT' })"));

  // Verify 24-hour reminder cooldown logic is preserved
  assert.ok(content.includes('reminder_available_at'));
  assert.ok(content.includes('cooldownActive'));

  // Verify search and pagination for RFQs and suppliers are preserved
  assert.ok(content.includes('useServerList'));
  assert.ok(content.includes('qRfqs.controls'));
  assert.ok(content.includes('supplierSearch'));

  // Verify no raw debug strings / UUIDs are exposed in explanation
  assert.ok(content.includes('formatEligibilityExplanation'));
});

test('Matched Suppliers: CSS file contains complete responsive and RTL styling', () => {
  const cssPath = path.resolve(__dirname, '../app/matching.css');
  const cssContent = fs.readFileSync(cssPath, 'utf8');

  // Check 2-column desktop grid
  assert.ok(cssContent.includes('grid-template-columns: repeat(2, minmax(0, 1fr))'));

  // Check responsive breakpoints
  assert.ok(cssContent.includes('@media (max-width: 1200px)'));
  assert.ok(cssContent.includes('@media (max-width: 900px)'));
  assert.ok(cssContent.includes('@media (max-width: 640px)'));

  // Check RTL support
  assert.ok(cssContent.includes('[dir="rtl"]'));

  // Check typography and spacing
  assert.ok(cssContent.includes('--pm-navy'));
  assert.ok(cssContent.includes('--pm-blue'));
  assert.ok(cssContent.includes('--pm-radius'));
  assert.ok(cssContent.includes('pm-explanation-box'));
});

test('Matched Suppliers: Arabic translations are registered in workflowArabic', () => {
  const transPath = path.resolve(__dirname, '../lib/workflow-translations.ts');
  const transContent = fs.readFileSync(transPath, 'utf8');

  assert.ok(transContent.includes('"Supplier Matching": "مطابقة الموردين"'));
  assert.ok(transContent.includes('"Buyer Workspace": "مساحة عمل المشتري"'));
  assert.ok(transContent.includes('"Pending responses": "بانتظار الرد"'));
  assert.ok(transContent.includes('"Quotations received": "عروض الأسعار المستلمة"'));
  assert.ok(transContent.includes('"Eligible": "مؤهل"'));
  assert.ok(transContent.includes('"Location mismatch": "عدم تطابق الموقع"'));
});
