const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const phone=require('libphonenumber-js/max');
const examples=require('libphonenumber-js/mobile/examples');
const exportsUnderTest={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(require.resolve('../lib/validation.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports:exportsUnderTest,require});

test('Saudi formats normalize to one mobile identity',()=>{
 for(const raw of ['0501234567','501234567','+966501234567','00966501234567']){
  assert.equal(exportsUnderTest.saudiPhone(raw),'+966501234567');
  assert.equal(exportsUnderTest.identifierError(raw),'');
 }
});
test('foreign, invalid and landline identities rejected; email retained',()=>{
 for(const raw of ['+923241851476','+966112345678','123','+999123456789'])assert.notEqual(exportsUnderTest.identifierError(raw),'');
 assert.equal(exportsUnderTest.identifierError('person@example.test'),'');
});
