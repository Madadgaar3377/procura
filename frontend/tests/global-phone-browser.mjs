import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
async function browserCheck({origin,frontend}){
 const port=20000+Math.floor(Math.random()*10000);
 const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-sandbox','--disable-dev-shm-usage','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(frontend,'.test-runtime','chrome-profile-'+process.pid)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
 let ws;
 try{
  let target;
  for(let i=0;i<80;i++){try{const r=await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(2000)});target=(await r.json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(250);}
  if(!target)throw new Error('Headless Chrome debugging interface unavailable');
  ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Chrome WebSocket connection timed out')),5000);ws.addEventListener('open',()=>{clearTimeout(timeout);resolve();},{once:true});ws.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();let intercept;const runtimeErrors=[];
  ws.addEventListener('message',e=>{const data=JSON.parse(e.data);if(data.method==='Fetch.requestPaused')intercept?.(data.params);if(data.method==='Runtime.exceptionThrown')runtimeErrors.push(data.params.exceptionDetails.text);if(data.id){const call=pending.get(data.id);pending.delete(data.id);if(data.error)call?.reject(new Error(data.error.message));else call?.resolve(data.result);}});
  function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Chrome command timed out: '+method));},20000);pending.set(id,{resolve:value=>{clearTimeout(timeout);resolve(value);},reject:error=>{clearTimeout(timeout);reject(error);}});ws.send(JSON.stringify({id,method,params}));});}
  async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
  async function until(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await delay(100);}await screenshot('browser-failure');throw new Error('Browser condition timed out: '+expression+' | '+await evaluate('document.body.innerText.slice(-2500)'));}
  async function navigate(route){await command('Page.navigate',{url:origin+route});await until('document.readyState === "complete"');await delay(350);}
  const artifacts=path.join(frontend,'.test-runtime','screenshots');await fs.mkdir(artifacts,{recursive:true});
  async function screenshot(name){const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(artifacts,name+'.png'),Buffer.from(result.data,'base64'));}
  await command('Page.enable');await command('Runtime.enable');await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});


  const calls=[];
  intercept=async({requestId,request})=>{
    const route=new URL(request.url).pathname;calls.push(route);
    await command('Fetch.fulfillRequest',{requestId,responseCode:401,responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'}],body:Buffer.from(JSON.stringify({detail:'Anonymous test session'})).toString('base64')});
  };
  await command('Fetch.enable',{patterns:[{urlPattern:'*api/v1/*',requestStage:'Request'},{urlPattern:'*api/backend/*',requestStage:'Request'}]});
  async function fill(selector,text){await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus();document.querySelector(${JSON.stringify(selector)}).select()`);await command('Input.insertText',{text});}
  async function selectCountry(query,code){
    await evaluate(`document.querySelector('.global-phone-country').click()`);await until(`!!document.querySelector('.phone-country-dialog')`);
    await fill('.phone-country-dialog input',query);
    await until(`!!Array.from(document.querySelectorAll('[role=option]')).find(el=>el.textContent.includes(${JSON.stringify(code)}))`);
    await evaluate(`Array.from(document.querySelectorAll('[role=option]')).find(el=>el.textContent.includes(${JSON.stringify(code)})).click()`);
    await until(`!document.querySelector('.phone-country-dialog')`);
  }
  await navigate('/register');await until(`!!document.querySelector('.global-phone-country')`);
  assert.ok(await evaluate(`document.querySelector('.global-phone-country').textContent.includes('SA')`));
  await evaluate(`document.querySelector('.global-phone-country').click()`);await until(`document.querySelectorAll('[role=option]').length>200`);
  await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await until(`!document.querySelector('.phone-country-dialog')`);
  await selectCountry('Pakistan','PK');await fill('.global-phone input[type=tel]','03241851476');
  await until(`document.querySelector('input[name=phone]').value==='+923241851476'`);
  assert.equal(await evaluate(`document.querySelector('.global-phone-prefix').textContent`),'+92');
  await screenshot('global-phone-pakistan-desktop');
  await fill('.global-phone input[type=tel]','+447400123456');await until(`document.querySelector('.global-phone-country').textContent.includes('GB')`);
  assert.equal(await evaluate(`document.querySelector('input[name=phone]').value`),'+447400123456');
  await fill('.global-phone input[type=tel]','+1');
  assert.equal(await evaluate(`document.querySelector('input[name=phone_country]').value`),'');
  await selectCountry('+971','AE');
  await evaluate(`document.querySelector('.global-phone-country').click()`);await until(`!!document.querySelector('.phone-country-dialog')`);
  await fill('.phone-country-dialog input','Germany');
  await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});await until(`!document.querySelector('.phone-country-dialog')`);
  assert.ok(await evaluate(`document.querySelector('.global-phone-country').textContent.includes('DE')`));
  for(const arabic of [false,true]){
    await navigate('/register?lang='+(arabic?'ar':'en'));await until(`!!document.querySelector('.global-phone-country')`);
    for(const width of [360,390,768,1440]){
      await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<600});
      await evaluate(`document.querySelector('.global-phone-country').click()`);await until(`!!document.querySelector('.phone-country-dialog')`);
      assert.equal(await evaluate(`document.documentElement.dir`),arabic?'rtl':'ltr');
      assert.ok(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`));
      assert.ok(await evaluate(`document.querySelector('.phone-country-dialog').getBoundingClientRect().width<=innerWidth`));
      if(width===390)await screenshot('global-phone-mobile-'+(arabic?'ar':'en'));
      await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await until(`!document.querySelector('.phone-country-dialog')`);
    }
  }
  assert.deepEqual(runtimeErrors,[]);
  console.log('PASS: all metadata countries selectable; name/prefix search; national PK canonical identity; explicit international GB detection; ambiguous +1 has no false country context; keyboard Enter/Escape; English/Arabic at four viewport widths. No real APIs or OTP sends.');
 }finally{ws?.close();chrome.kill();}
}
const frontend=path.resolve('.');const origin='http://127.0.0.1:14089';
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','14089'],{cwd:frontend,env:{...process.env,NEXT_BUILD_DIR:'.next-global-phone-test',NEXT_TELEMETRY_DISABLED:'1'},windowsHide:true,stdio:['ignore','pipe','pipe']});
let serverOutput='';server.stdout.on('data',d=>serverOutput+=d);server.stderr.on('data',d=>serverOutput+=d);
try{let ready=false;for(let i=0;i<150;i++){try{if((await fetch(origin+'/register',{signal:AbortSignal.timeout(2000)})).ok){ready=true;break;}}catch{}await delay(300);}assert.ok(ready,'Local Next server ready');await browserCheck({origin,frontend});}catch(e){console.error(e.message);console.error(serverOutput.slice(-1500));process.exitCode=1;}finally{if(process.platform==='win32')spawn('taskkill',['/pid',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});else server.kill();}
