const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const exportsUnderTest={};
const source=ts.transpileModule(fs.readFileSync(require.resolve('../lib/validation.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
vm.runInNewContext(source,{exports:exportsUnderTest,require});
const {normalizeVat,vatError,vatMessage}=exportsUnderTest;

test('VAT stays optional and accepts formatted English/Arabic digits',()=>{
 for(const value of ['','  ','300000000000003','300 000 000 000 003','٣٠٠٠٠٠٠٠٠٠٠٠٠٠٣','۳۰۰۰۰۰۰۰۰۰۰۰۰۰۳'])assert.equal(vatError(value),'');
 assert.equal(normalizeVat('٣٠٠ ٠٠٠ ٠٠٠ ٠٠٠ ٠٠٣'),'300000000000003');
 assert.equal(normalizeVat('۳۰۰ ۰۰۰ ۰۰۰ ۰۰۰ ۰۰۳'),'300000000000003');
});

test('13-digit VAT is rejected with actionable guidance without padding or changing its digits',()=>{
 assert.equal(vatError('1500006541651'),vatMessage);
 assert.equal(normalizeVat('1500006541651'),'1500006541651');
 for(const value of ['3000000000000003','30000000000000X','letters'])assert.equal(vatError(value),vatMessage);
});
