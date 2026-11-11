import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const origin=process.env.MARKETING_TEST_ORIGIN||'http://127.0.0.1:13092';
const artifacts=path.resolve('.test-runtime/industries');
const widths=[320,375,430,768,1024,1280,1440];
await fs.mkdir(artifacts,{recursive:true});
const port=24000+Math.floor(Math.random()*3000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
  '--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-sandbox',
  '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,
  `--user-data-dir=${path.join(artifacts,'chrome-'+process.pid)}`,'about:blank'
],{windowsHide:true,stdio:'ignore'});
let ws;
try{
  let target;
  for(let i=0;i<80;i++){
    try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item=>item.type==='page');if(target)break;}catch{}
    await delay(200);
  }
  assert.ok(target,'Headless browser starts');
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();const errors=[];
  ws.addEventListener('message',event=>{
    const message=JSON.parse(event.data);
    if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.exception?.description||message.params.exceptionDetails.text);
    if(message.id){const item=pending.get(message.id);pending.delete(message.id);if(message.error)item?.reject(new Error(message.error.message));else item?.resolve(message.result);}
  });
  function command(method,params={}){return new Promise((resolve,reject)=>{
    const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method}`));},25000);
    pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
    ws.send(JSON.stringify({id,method,params}));
  });}
  async function evaluate(expression){const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;}
  async function until(expression){for(let i=0;i<120;i++){if(await evaluate(expression))return;await delay(100);}throw new Error(`Timed out waiting for ${expression}`);}
  await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
  await command('Page.addScriptToEvaluateOnNewDocument',{source:`
    (()=>{
      const materials=['Gates, louvers and access metalwork','Handrails and balustrades','Steel frames and supports','Miscellaneous structural steelwork','Long technical description for specialized fabricated assemblies'];
      const catalog={disciplines:[{id:'Civil',name_en:'Civil',name_ar:'مدني',category_count:8,material_count:42},{id:'Electrical',name_en:'Electrical',name_ar:'كهربائي',category_count:6,material_count:30},{id:'Mechanical',name_en:'Mechanical',name_ar:'ميكانيكي',category_count:5,material_count:24}],categories:[{id:'sub-metalwork',name_en:'Architectural Metalwork',name_ar:'الأعمال المعدنية',main_category_id:'main-specialties',main_category:'Architectural Specialties',main_category_ar:'الأعمال المعمارية',subcategory_id:'sub-metalwork',subcategory:'Architectural Metalwork',subcategory_ar:'الأعمال المعدنية',material_count:17,materials:materials.map((name,index)=>({id:'m'+index,name_en:name,name_ar:'مادة هندسية '+index,description:'Material Master specification text '.repeat(4),description_ar:'وصف فني من دليل المواد '+index,unit:index?'PCS':'M'}))}],total:1,page:1,limit:9};
      const nativeFetch=window.fetch.bind(window);
      window.fetch=(input,init)=>{
        const url=typeof input==='string'?input:input?.url||'';
        if(url.includes('/api/v1/taxonomy/industry-catalog'))return Promise.resolve(new Response(JSON.stringify(catalog),{status:200,headers:{'Content-Type':'application/json'}}));
        if(url.includes('/api/v1/taxonomy/materials'))return Promise.resolve(new Response(JSON.stringify([]),{status:200,headers:{'Content-Type':'application/json'}}));
        return nativeFetch(input,init);
      };
    })();`});
  for(const width of widths){
    await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
    for(const locale of ['en','ar']){
      await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
      await command('Page.navigate',{url:`${origin}/industries?lang=${locale}&discipline=Civil`});
      await until(`document.readyState==='complete'&&!!document.querySelector('.pm-industries-page')&&!!document.querySelector('.pm-industry-card')`);
      await until(`document.querySelector('.pm-header')?.dataset.interactive==='ready'`);
      const geometry=await evaluate(`(()=>{const card=document.querySelector('.pm-industry-card');const title=document.querySelector('.pm-industry-card h3');const button=document.querySelector('.pm-industry-explore');return {viewport:innerWidth,scroll:document.documentElement.scrollWidth,dir:document.querySelector('.pm-industries-page').getAttribute('dir'),cardWidth:card.getBoundingClientRect().width,titleSize:getComputedStyle(title).fontSize,buttonSize:getComputedStyle(button).fontSize,hasMaterials:!!document.querySelector('.pm-industry-materials li'),hasCTA:!!document.querySelector('.pm-industry-cta')}})()`);
      assert.ok(geometry.scroll<=width+1,`No horizontal overflow at ${width}px ${locale}: ${JSON.stringify(geometry)}`);
      assert.equal(geometry.dir,locale==='ar'?'rtl':'ltr');
      assert.ok(geometry.cardWidth>0&&geometry.hasMaterials&&geometry.hasCTA,`Catalog content renders at ${width}px ${locale}`);
      assert.ok(Number.parseFloat(geometry.titleSize)>=20,`Category title remains readable: ${geometry.titleSize}`);
      assert.ok(Number.parseFloat(geometry.buttonSize)>=14,`Supplier action remains readable: ${geometry.buttonSize}`);
      if(width===320||width===1440){
        const shot=await command('Page.captureScreenshot',{format:'png'});
        await fs.writeFile(path.join(artifacts,`${width}-${locale}.png`),Buffer.from(shot.data,'base64'));
      }
      console.log(`${width}px ${locale}: scroll ${geometry.scroll}px, card ${Math.round(geometry.cardWidth)}px`);
    }
  }
  assert.deepEqual(errors,[],'No browser runtime exceptions');
}finally{ws?.close();chrome.kill();}
