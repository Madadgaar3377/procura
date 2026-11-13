// Compare two local production servers with identical, serial Lighthouse runs.
import {spawnSync} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const directory=path.resolve('.test-runtime/landing-motion');
await fs.mkdir(directory,{recursive:true});
const summary=[];
for(const device of ['mobile','desktop'])for(const stage of ['before','after']){
  const origin=process.env[`LANDING_${stage.toUpperCase()}_ORIGIN`]||`http://127.0.0.1:${stage==='before'?13093:13095}`;
  assert.ok(['localhost','127.0.0.1'].includes(new URL(origin).hostname),'Only measure local previews');
  const output=path.join(directory,`lighthouse-${stage}-${device}.json`);
  const args=['node_modules/lighthouse/cli/index.js',`${origin}/?lang=en`,
    '--chrome-flags=--headless=new --no-sandbox','--only-categories=performance',
    '--output=json',`--output-path=${output}`,'--quiet'];
  if(device==='desktop')args.push('--preset=desktop');
  const result=spawnSync(process.execPath,args,{stdio:'inherit',windowsHide:true});
  assert.equal(result.status,0,`${stage}/${device}: Lighthouse completes`);
  const report=JSON.parse(await fs.readFile(output,'utf8'));
  assert.ok(!report.runtimeError,JSON.stringify(report.runtimeError));
  const metric=id=>report.audits[id].numericValue;
  const row={stage,device,fetchTime:report.fetchTime,performance:report.categories.performance.score*100,
    lcpMs:metric('largest-contentful-paint'),cls:metric('cumulative-layout-shift'),
    tbtMs:metric('total-blocking-time'),transferBytes:metric('total-byte-weight'),warnings:report.runWarnings};
  summary.push(row);console.log(JSON.stringify(row));
}
await fs.writeFile(path.join(directory,'lighthouse-summary.json'),JSON.stringify(summary,null,2));
