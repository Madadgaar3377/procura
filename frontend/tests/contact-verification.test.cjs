const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');

test('contact verification requires phone first, then configured email',()=>{
 const exports={};const stored=new Map();
 const source=ts.transpileModule(fs.readFileSync(require.resolve('../features/auth/verification.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
 vm.runInNewContext(source,{exports,sessionStorage:{setItem:(key,value)=>stored.set(key,value)}});
 const user={phone:'+966511111119',email:'pending@example.test',phone_verified:false,email_verified:false};
 assert.equal(exports.verificationPath(user),'/verify-mobile');
 assert.equal(stored.get('procura.pending-verification-phone'),user.phone);
 assert.equal(exports.verificationPath({...user,phone_verified:true}),'/verify-email');
 assert.equal(exports.verificationPath({...user,phone_verified:true,email_verified:true}),null);
 assert.equal(exports.verificationPath({...user,phone_verified:true,email:null}),null);
});

test('verification routing still works when browser storage is unavailable',()=>{
 const exports={};
 const source=ts.transpileModule(fs.readFileSync(require.resolve('../features/auth/verification.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
 vm.runInNewContext(source,{exports,sessionStorage:{setItem:()=>{throw new Error('Storage disabled');}}});
 assert.equal(exports.verificationPath({phone:'+966511111119',email:'pending@example.test',phone_verified:false,email_verified:false}),'/verify-mobile');
});
