const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const ts=require('typescript');
function load(fetch){
 const exports={};const file=path.resolve(__dirname,'../features/marketing/sitemap.ts');
 const source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(source,{exports,URL,Response,AbortSignal,process:{env:{}},console:{warn(){}},fetch,require:name=>name==='./seo'?{CANONICAL_SITE_URL:'https://procumaterial.com'}:{unstable_cache:fn=>fn}});
 return exports;
}
const page=(items,total=items.length,n=1)=>Response.json({items,total,page:n,limit:100});
const supplier=id=>({id,verified:true});
test('sitemap serializer escapes XML, deduplicates URLs, and omits unsupported timestamps',()=>{
 const api=load();const xml=api.serializeUrlset(['/about','/about','/suppliers/a&b']);
 assert.equal((xml.match(/<loc>/g)||[]).length,4);assert.match(xml,/a&amp;b/);
 assert.ok(!xml.includes('xhtml')&&!xml.includes('lastmod')&&!xml.includes('priority'));
 assert.equal(api.xmlEscape(`<x a="'">&`),'&lt;x a=&quot;&apos;&quot;&gt;&amp;');
});
test('real directory response contract is paginated and public-only, without detail calls',async()=>{
 const calls=[];const api=load(async url=>{calls.push(String(url));const n=Number(url.searchParams.get('page'));return n===1?page(Array.from({length:100},(_,i)=>supplier('s'+i)),101):page([supplier('last')],101,2);});
 const result=await api.buildSitemapXml();assert.equal(result.complete,true);
 assert.equal((result.xml.match(/<loc>/g)||[]).length,222);assert.equal(calls.length,2);
 assert.ok(calls.every(url=>url.includes('limit=100')&&url.includes('paginated=true')));
 assert.match(result.xml,/suppliers\/last\?lang=ar/);
});
test('empty, failed and malformed upstream datasets never fabricate supplier URLs',async()=>{
 for(const fetch of [async()=>page([]),async()=>new Response(null,{status:503}),async()=>Response.json({items:[{id:'private',verified:false}],total:1,page:1,limit:100})]){
  const result=await load(fetch).buildSitemapXml();assert.equal((result.xml.match(/<loc>/g)||[]).length,20);assert.ok(!result.xml.includes('suppliers/private'));
 }
 const api=load(async()=>{throw Error('offline');});const result=await api.buildSitemapXml();assert.equal(result.complete,false);
 assert.match(api.sitemapResponse(result.xml,false).headers.get('cache-control'),/s-maxage=60,/);
});
test('large directory switches to a sitemap index rather than silently dropping profiles',async()=>{
 let calls=0;const api=load(async()=>{calls++;return page([supplier('first')],25000);});
 const result=await api.buildSitemapXml();assert.equal(calls,1);assert.equal(result.complete,true);
 assert.match(result.xml,/<sitemapindex /);assert.equal((result.xml.match(/<sitemap>/g)||[]).length,251);
 assert.match(result.xml,/\/sitemaps\/suppliers\/250.xml/);
});
