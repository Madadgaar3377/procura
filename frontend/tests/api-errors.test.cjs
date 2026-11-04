const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const exportsForTest={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(require.resolve('../lib/api/errors.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:exportsForTest});
const {ApiError}=exportsForTest;
test('missing profile migration is actionable without exposing arbitrary server errors',()=>{
 const known='Saving extended company details requires the pending supplier profile database migration. No changes were saved.';
 assert.equal(new ApiError(503,known).message,'Company profile updates require the pending database migration. No changes were saved.');
 assert.equal(new ApiError(503,'SQL connection failed: private database host').message,'The service is temporarily busy. Please try again.');
 assert.equal(new ApiError(500,known).message,'The server could not complete this request.');
});
