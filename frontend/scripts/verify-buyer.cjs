// Local verification with recorded child exits; never contacts a hosted database or delivery provider.
const {spawnSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const cwd=path.resolve(__dirname,'..');
const evidence=path.resolve(cwd,'../docs');
const env={...process.env,NEXT_TELEMETRY_DISABLED:'1',NEXT_BUILD_DIR:'.next-buyer-current',
 NEXT_PUBLIC_API_URL:'http://127.0.0.1:18081/api/v1',SMOKE_BACKEND_PORT:'18081',SMOKE_FRONTEND_PORT:'13081',AUTH_DELIVERY_AUTO_DISPATCH:'false'};
const stages=[['typecheck',['node_modules/typescript/bin/tsc','--noEmit','--incremental','false']],
 ['unit',['--test',...fs.readdirSync(path.join(cwd,'tests')).filter(file=>file.endsWith('.test.cjs')).map(file=>'tests/'+file)]],
 ['build',['node_modules/next/dist/bin/next','build','--webpack']],
 ['browser',['tests/http-smoke.mjs','--buyer-browser']]];
const results=process.argv.includes('--browser-only')?JSON.parse(fs.readFileSync(path.join(evidence,'buyer-verified-exits.json'),'utf8')):{};
for(const [name,args] of stages){
 if(process.argv.includes('--browser-only')&&name!=='browser')continue;
 const fd=fs.openSync(path.join(evidence,'buyer-verified-'+name+'.log'),'w');
 const started=Date.now();let result;
 try{result=spawnSync(process.execPath,args,{cwd,env,stdio:['ignore',fd,fd],windowsHide:true,timeout:900000});}
 finally{fs.closeSync(fd);}
 results[name]={exit_code:result.status,signal:result.signal,error:result.error?.code||null,duration_ms:Date.now()-started};
 fs.writeFileSync(path.join(evidence,'buyer-verified-exits.json'),JSON.stringify(results,null,2));
 console.log(name+': '+JSON.stringify(results[name]));
 if(result.status!==0)process.exit(result.status||1);
}
