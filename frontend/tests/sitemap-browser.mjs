import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
const origin=process.env.SITEMAP_TEST_ORIGIN||'http://127.0.0.1:13112';
const artifacts=path.resolve('.test-runtime/sitemap');await fs.mkdir(artifacts,{recursive:true});
let baseline;
try{baseline=await fs.readFile(path.join(artifacts,'production-before.xml'),'utf8');}catch{}
const server=createServer((_req,res)=>{res.writeHead(200,{'Content-Type':'application/xml; charset=utf-8'});res.end(baseline);});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-first-run','--no-default-browser-check','--remote-debugging-port=24918',`--user-data-dir=${path.join(artifacts,'chrome-'+process.pid)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
let ws;
try{
 let target;
 for(let i=0;i<80;i++){try{target=(await(await fetch('http://127.0.0.1:24918/json/list')).json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(200);}
 assert.ok(target,'Chrome debugging interface');ws=new WebSocket(target.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let sequence=0;const pending=new Map();
 ws.addEventListener('message',e=>{const data=JSON.parse(e.data);if(data.id){const call=pending.get(data.id);pending.delete(data.id);data.error?call?.reject(Error(data.error.message)):call?.resolve(data.result);}});
 function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>reject(Error(method+' timed out')),15000);pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true});return r.result.value;}
 await command('Page.enable');await command('Runtime.enable');
 const results=[];
 for(const [name,url] of [...(baseline?[['before',`http://127.0.0.1:${server.address().port}/before.xml`]]:[]),['after',origin+'/sitemap.xml']]){
  await command('Page.navigate',{url});
  for(let i=0;i<100;i++){if(await evaluate('document.readyState==="complete"'))break;await delay(100);}
  await delay(500);
  const result=await evaluate(`({type:document.contentType,nativeTree:!!document.querySelector('#webkit-xml-viewer-source-xml'),text:document.documentElement.textContent.slice(0,280)})`);
  results.push({name,...result});
  if(name==='before')assert.equal(result.nativeTree,false,'Reproduce plain text with original XHTML sitemap');
  if(name==='after'){assert.equal(result.type,'application/xml');assert.equal(result.nativeTree,true,'Native expandable XML tree is present');}
  const shot=await command('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(artifacts,name+'.png'),Buffer.from(shot.data,'base64'));
 }
 await fs.writeFile(path.join(artifacts,'browser-results.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
}finally{ws?.close();chrome.kill();server.close();}
