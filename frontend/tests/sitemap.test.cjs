const test = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');

const origin = process.env.SITEMAP_TEST_ORIGIN || 'http://127.0.0.1:3000';

async function get(path) {
  return fetch(new URL(path, origin), {redirect: 'manual', signal: AbortSignal.timeout(10000)});
}

test('sitemap is valid canonical XML and robots advertises it', async () => {
  const response = await get('/sitemap.xml');
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type') || '', /^application\/xml\s*;\s*charset=utf-8$/i);
  assert.match(response.headers.get('cache-control') || '', /s-maxage=(3600|60)(?:,|$)/);
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  const xml = await response.text();
  assert.ok(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
  assert.ok(!xml.includes('www.w3.org/1999/xhtml'),'Hreflang belongs in HTML metadata; preserve native XML tree rendering');
  assert.ok(!xml.includes('<?xml-stylesheet')&&!xml.includes('<script'),'No sitemap styling or JavaScript');

  const python = String.raw`
import sys, xml.etree.ElementTree as ET
root = ET.fromstring(sys.stdin.buffer.read())
assert root.tag == '{http://www.sitemaps.org/schemas/sitemap/0.9}urlset'
urls = root.findall('{http://www.sitemaps.org/schemas/sitemap/0.9}url')
assert urls
locs = []
allowed = {'/', '/how-it-works', '/suppliers', '/industries', '/pricing', '/about', '/contact', '/faq', '/privacy', '/terms'}
for entry in urls:
    loc = entry.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
    assert loc
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(loc)
    assert parsed.scheme == 'https' and parsed.netloc == 'procumaterial.com'
    assert parsed.path in allowed or (parsed.path.startswith('/suppliers/') and parsed.path.count('/') == 2)
    query = parse_qs(parsed.query)
    assert not query or query == {'lang': ['ar']}
    assert not parsed.fragment
    assert entry.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod') is None
    freq = entry.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}changefreq')
    assert freq is None or freq in {'always','hourly','daily','weekly','monthly','yearly','never'}
    priority = entry.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}priority')
    assert priority is None or 0.0 <= float(priority) <= 1.0
    locs.append(loc)
assert len(locs) == len(set(locs)), 'duplicate sitemap loc values'
assert len(locs) >= 20 and len(locs) % 2 == 0
for path in allowed:
    english = 'https://procumaterial.com' + (path if path != '/' else '/')
    arabic = english + ('?lang=ar')
    assert english in locs, 'missing English URL: ' + english
    assert arabic in locs, 'missing Arabic URL: ' + arabic
assert all('/app/' not in loc and '/api/' not in loc and '/login' not in loc and '/register' not in loc for loc in locs)
assert len(locs) <= 50000
`;
  const parsed = spawnSync(process.env.PYTHON || 'python', ['-c', python], {input: xml, encoding: 'utf8'});
  assert.equal(parsed.status, 0, parsed.stderr || parsed.stdout);

  const robots = await get('/robots.txt');
  assert.equal(robots.status, 200);
  assert.match(await robots.text(), /Sitemap:\s*https:\/\/procumaterial\.com\/sitemap\.xml/i);

  for (const route of ['/how-it-works','/how-it-works?lang=ar','/suppliers','/suppliers?lang=ar']) {
    const localized = await get(route);
    assert.equal(localized.status, 200, `${route} should resolve without redirects`);
    assert.match(localized.headers.get('content-type') || '', /text\/html/i);
    const html=await localized.text();
    const arabic=route.includes('lang=ar');
    assert.match(html,new RegExp(`<html[^>]*lang="${arabic?'ar':'en'}"`,'i'));
    const canonical='https://procumaterial.com'+route;
    const english='https://procumaterial.com'+route.split('?')[0];
    assert.ok(html.includes(`rel="canonical" href="${canonical}"`),route+' self canonical');
    for(const [language,url] of [['en',english],['ar',english+'?lang=ar'],['x-default',english]]){
      assert.ok(html.includes(`hrefLang="${language}" href="${url}"`)||html.includes(`hreflang="${language}" href="${url}"`),route+' reciprocal '+language);
    }
  }
  for(const route of ['/login','/app/admin/dashboard','/app/buyer/dashboard','/app/supplier/dashboard']){
    const response=await get(route);assert.equal(response.status,200);
    assert.match(await response.text(),/name="robots" content="noindex, nofollow"/i,route+' must be noindex');
  }
  const head=await fetch(new URL('/sitemap.xml',origin),{method:'HEAD'});
  assert.equal(head.status,200);assert.match(head.headers.get('content-type'),/^application\/xml/i);
  assert.equal(await head.text(),'');
  assert.equal((await get('/sitemaps/suppliers/0.xml')).status,404);
});
