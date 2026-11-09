import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';

const origin=process.env.CONTACT_TEST_ORIGIN||'http://127.0.0.1:13091';
const port=24100+Math.floor(Math.random()*1000);
const browser=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
  '--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-sandbox','--disable-dev-shm-usage',
  '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${process.env.TEMP||'.'}\\contact-responsive-${process.pid}`,'about:blank'
],{windowsHide:true,stdio:'ignore'});
let socket;
try{
  let target;
  for(let i=0;i<100;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item=>item.type==='page');if(target)break;}catch{}await delay(100);}
  assert.ok(target,'Chrome starts');socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let seq=0;const pending=new Map();socket.addEventListener('message',event=>{const msg=JSON.parse(event.data);if(msg.id){const task=pending.get(msg.id);pending.delete(msg.id);if(msg.error)task.reject(new Error(msg.error.message));else task.resolve(msg.result);}});
  const command=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(result.exceptionDetails.text);return result.result.value;};
  await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
  const results=[];
  for(const width of [320,375,430,768,1024,1280,1440])for(const locale of ['en','ar']){
    await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
    await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
    await command('Page.navigate',{url:`${origin}/contact?lang=${locale}`});
    for(let i=0;i<100;i++){if(await evaluate('document.readyState==="complete"&&!!document.querySelector(".live-contact form")'))break;await delay(100);}
    const result=await evaluate(`({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,dir:document.documentElement.dir,fields:[...document.querySelectorAll('.live-contact form input:not([name=website]),.live-contact form select,.live-contact form textarea')].length,mail:!!document.querySelector('a[href="mailto:support@procumaterial.com"]'),phone:!!document.querySelector('a[href="tel:0501496516"]')})`);
    assert.ok(result.scroll<=result.width+1,`${locale} ${width}px overflow: ${JSON.stringify(result)}`);
    assert.equal(result.fields,9,`${locale} ${width}px has all contact inputs`);
    assert.ok(result.mail&&result.phone,'Official support contact links are present');
    results.push({width,locale,dir:result.dir,overflow:result.scroll-result.width});
  }
  console.log(`Contact responsive checks passed (${results.length} viewport/locale combinations): ${results.map(x=>`${x.width}/${x.locale}`).join(', ')}`);
}finally{socket?.close();browser.kill();}
