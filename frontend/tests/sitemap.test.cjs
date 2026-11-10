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
  assert.match(response.headers.get('cache-control') || '', /s-maxage=3600/);
  const xml = await response.text();

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
    assert freq in {'always','hourly','daily','weekly','monthly','yearly','never'}
    priority = float(entry.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}priority'))
    assert 0.0 <= priority <= 1.0
    alternatives = {node.attrib['hreflang']: node.attrib['href'] for node in entry.findall('{http://www.w3.org/1999/xhtml}link')}
    assert alternatives.keys() == {'en', 'ar', 'x-default'}
    assert alternatives['x-default'] == alternatives['en']
    assert alternatives['en'] in [item.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}loc') for item in urls]
    assert alternatives['ar'] in [item.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}loc') for item in urls]
    locs.append(loc)
assert len(locs) == len(set(locs)), 'duplicate sitemap loc values'
assert len(locs) >= 20 and len(locs) % 2 == 0
for path in allowed:
    english = 'https://procumaterial.com' + (path if path != '/' else '/')
    arabic = english + ('?lang=ar')
    assert english in locs, 'missing English URL: ' + english
    assert arabic in locs, 'missing Arabic URL: ' + arabic
for entry in urls:
    loc = entry.findtext('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
    alternatives = {node.attrib['hreflang']: node.attrib['href'] for node in entry.findall('{http://www.w3.org/1999/xhtml}link')}
    assert alternatives['en'] == 'https://procumaterial.com' + __import__('urllib.parse', fromlist=['urlparse']).urlparse(loc).path
    assert alternatives['ar'] == alternatives['en'] + '?lang=ar'
assert all('/app/' not in loc and '/api/' not in loc and '/login' not in loc and '/register' not in loc for loc in locs)
assert len(locs) <= 50000
`;
  const parsed = spawnSync(process.env.PYTHON || 'python', ['-c', python], {input: xml, encoding: 'utf8'});
  assert.equal(parsed.status, 0, parsed.stderr || parsed.stdout);

  const robots = await get('/robots.txt');
  assert.equal(robots.status, 200);
  assert.match(await robots.text(), /Sitemap:\s*https:\/\/procumaterial\.com\/sitemap\.xml/i);

  for (const route of ['/how-it-works?lang=ar', '/suppliers?lang=ar']) {
    const localized = await get(route);
    assert.equal(localized.status, 200, `${route} should resolve without redirects`);
    assert.match(localized.headers.get('content-type') || '', /text\/html/i);
    assert.match(await localized.text(), /<html[^>]*lang="ar"/i);
  }
});
