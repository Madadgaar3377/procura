import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import sharp from 'sharp';
const directory=path.resolve('.test-runtime/landing-motion');
const results=[];
for(const width of [320,375,430,768,1024,1440,1920])for(const locale of ['en','ar']){
 const before=await sharp(path.join(directory,`before-${width}-${locale}.png`)).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 const after=await sharp(path.join(directory,`after-${width}-${locale}.png`)).ensureAlpha().raw().toBuffer({resolveWithObject:true});
 assert.deepEqual(after.info,before.info,`${width}/${locale}: dimensions unchanged`);
 let pixels=0;
 for(let i=0;i<before.data.length;i+=4)if(before.data[i]!==after.data[i]||before.data[i+1]!==after.data[i+1]||before.data[i+2]!==after.data[i+2]||before.data[i+3]!==after.data[i+3])pixels++;
 results.push({width,locale,height:before.info.height,differentPixels:pixels,totalPixels:before.info.width*before.info.height});
 assert.equal(pixels,0,`${width}/${locale}: screenshots must be pixel-identical at rest`);
 console.log(`${width}/${locale}: pixel-identical`);
}
await fs.writeFile(path.join(directory,'pixel-results.json'),JSON.stringify(results,null,2));
