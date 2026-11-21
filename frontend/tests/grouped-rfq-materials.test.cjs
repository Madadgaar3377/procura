const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function loadModule() {
  const filePath = path.join(__dirname, '..', 'features', 'rfqs', 'grouped-rfq-materials.tsx');
  const source = fs.readFileSync(filePath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText;

  const mod = { exports: {} };
  const fakeRequire = (specifier) => {
    if (specifier === 'react' || specifier === 'react/jsx-runtime') return {};
    if (specifier.endsWith('.css')) return {};
    if (specifier === '@/lib/api/client') return { apiUrl: (p) => p };
    if (specifier === '@/lib/locale') return { useLocale: () => ({ t: (s) => s, arabic: false }) };
    if (specifier === '@/components/ui/button') return { Button: () => null };
    throw new Error(`Unexpected require: ${specifier}`);
  };
  vm.runInNewContext(compiled, { exports: mod.exports, module: mod, require: fakeRequire });
  return mod.exports;
}

const categories = [
  { id: 'cat-mech', parent_id: null, name_en: 'Mechanical', name_ar: 'ميكانيكا' },
  { id: 'cat-plumb', parent_id: null, name_en: 'Plumbing & Water', name_ar: 'السباكة والمياه' },
  { id: 'sub-pumps', parent_id: 'cat-mech', name_en: 'Pumps', name_ar: 'مضخات' },
  { id: 'sub-valves', parent_id: 'cat-mech', name_en: 'Valves', name_ar: 'صمامات' },
  { id: 'sub-filters', parent_id: 'cat-plumb', name_en: 'Water Filters', name_ar: 'فلاتر المياه' },
];

test('groupRfqItems groups multiple materials under the same subcategory and preserves original IDs', () => {
  const { groupRfqItems } = loadModule();
  const items = [
    { id: 'item-1', main_category_id: 'cat-mech', subcategory_id: 'sub-pumps', description: 'Booster Pump 5kW', unit: 'NO.', quantity: '2' },
    { id: 'item-2', main_category_id: 'cat-plumb', subcategory_id: 'sub-filters', description: 'Sand Media Water Filter', unit: 'NO.', quantity: '3' },
    { id: 'item-3', main_category_id: 'cat-mech', subcategory_id: 'sub-pumps', description: 'Submersible Sump Pump 3kW', unit: 'NO.', quantity: '4' },
    { id: 'item-4', main_category_id: 'cat-mech', subcategory_id: 'sub-valves', description: 'Gate Valve DN80', unit: 'NO.', quantity: '6' },
  ];

  const grouped = groupRfqItems(items, categories, false);
  assert.equal(grouped.length, 2);
  assert.equal(grouped[0].main_category_name, 'Mechanical');
  assert.equal(grouped[0].totalItems, 3);
  assert.equal(grouped[0].subcategories.length, 2);
  assert.equal(grouped[0].subcategories[0].subcategory_name, 'Pumps');
  assert.equal(
    JSON.stringify(grouped[0].subcategories[0].items.map((x) => ({ id: x.item.id, idx: x.originalIndex }))),
    JSON.stringify([{ id: 'item-1', idx: 0 }, { id: 'item-3', idx: 2 }]),
  );
  assert.equal(grouped[0].subcategories[1].subcategory_name, 'Valves');
  assert.equal(grouped[1].main_category_name, 'Plumbing & Water');
  assert.equal(grouped[1].subcategories[0].subcategory_name, 'Water Filters');
  assert.equal(grouped[1].subcategories[0].items[0].item.id, 'item-2');
});

test('groupRfqItems renders Arabic category names and handles unclassified custom items', () => {
  const { groupRfqItems } = loadModule();
  const items = [
    { id: 'item-a', main_category_id: 'cat-mech', subcategory_id: 'sub-pumps', description: 'مضخة مياه', unit: 'NO.', quantity: '1' },
    { id: 'item-b', main_category_id: null, subcategory_id: null, description: 'Custom item', unit: 'NO.', quantity: '2' },
  ];

  const groupedAr = groupRfqItems(items, categories, true);
  assert.equal(groupedAr.length, 2);
  assert.equal(groupedAr[0].main_category_name, 'ميكانيكا');
  assert.equal(groupedAr[0].subcategories[0].subcategory_name, 'مضخات');
  assert.equal(groupedAr[1].main_category_name, 'غير مصنف');
  assert.equal(groupedAr[1].subcategories[0].subcategory_name, 'عام');
});
