const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const load=(path)=>{const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(require.resolve(path),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports});return exports;};
const {keepBuyerDescription}=load('../features/rfqs/buyer-description.ts');
const {ApiError}=load('../lib/api/errors.ts');

test('a fuller buyer requirement is never replaced by the catalogue name',()=>{
 assert.equal(keepBuyerDescription('RGB Flood Light','Flood Light LED Type Day Light'),'RGB Flood Light');
 assert.equal(keepBuyerDescription('  RGB Flood Light ','Flood Light LED Type Day Light'),'RGB Flood Light');
});
test('a search fragment of the material name is completed with the catalogue name',()=>{
 assert.equal(keepBuyerDescription('flood li','Flood Light LED Type Day Light'),'Flood Light LED Type Day Light');
 assert.equal(keepBuyerDescription('','Gate Valve DN50'),'Gate Valve DN50');
});
test('structured API errors surface their own message and code',()=>{
 const error=new ApiError(409,{code:'rfq_closed',rfq_status:'closed',message:'This RFQ is closed. Reminders are no longer available.'});
 assert.equal(error.message,'This RFQ is closed. Reminders are no longer available.');
 assert.equal(error.code,'rfq_closed');
 assert.equal(new ApiError(409,'Invitation already processed').code,null);
});
