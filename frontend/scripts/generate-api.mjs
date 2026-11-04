import fs from 'node:fs';
const spec = JSON.parse(fs.readFileSync(new URL('../../docs/openapi.json', import.meta.url), 'utf8'));
function type(s) {
  if (!s || Object.keys(s).length === 0) return 'unknown';
  if (s.$ref) return `Schemas[${JSON.stringify(s.$ref.split('/').at(-1))}]`;
  if (s.const !== undefined) return JSON.stringify(s.const);
  if (s.enum) return s.enum.map(v=>JSON.stringify(v)).join(' | ');
  if (s.anyOf || s.oneOf) return (s.anyOf || s.oneOf).map(type).join(' | ');
  if (s.allOf) return s.allOf.map(type).join(' & ');
  if (s.type === 'array') return `Array<${type(s.items)}>`;
  if (s.type === 'object' || s.properties) {
    if (!s.properties) return `Record<string, ${typeof s.additionalProperties === 'object' ? type(s.additionalProperties) : 'unknown'}>`;
    return '{ '+Object.entries(s.properties).map(([k,v])=>`${JSON.stringify(k)}${s.required?.includes(k)?'':'?'}: ${type(v)}`).join('; ')+' }';
  }
  return ({string:'string',integer:'number',number:'number',boolean:'boolean',null:'null'})[s.type] || 'unknown';
}
const lines=['// Generated from docs/openapi.json. Run npm run api:generate; do not edit.', 'export interface Schemas {'];
for (const [name,s] of Object.entries(spec.components.schemas)) lines.push(`  ${JSON.stringify(name)}: ${type(s)};`);
lines.push('}', 'export interface Operations {');
for (const [path,item] of Object.entries(spec.paths)) for (const [method,op] of Object.entries(item)) {
 if (!['get','post','patch','put','delete'].includes(method)) continue;
 const response=Object.entries(op.responses).find(([code])=>code.startsWith('2'))?.[1];
 const req=op.requestBody?.content?.['application/json']?.schema;
 lines.push(`  ${JSON.stringify(method.toUpperCase()+' '+path)}: { body: ${req?type(req):'never'}; response: ${type(response?.content?.['application/json']?.schema)} };`);
}
lines.push('}');
fs.mkdirSync(new URL('../lib/api/generated/', import.meta.url), {recursive:true});
fs.writeFileSync(new URL('../lib/api/generated/schema.ts', import.meta.url),lines.join('\n')+'\n');
fs.writeFileSync(new URL('../lib/api/generated/routes.json', import.meta.url),JSON.stringify(Object.fromEntries(Object.entries(spec.paths).filter(([p])=>p.startsWith('/api/v1')).map(([p,v])=>[p.replace('/api/v1/',''),Object.keys(v).map(m=>m.toUpperCase())])),null,2));
console.log('Generated API request/response types and server route allowlist.');
