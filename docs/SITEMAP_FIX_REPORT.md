# Procumaterial sitemap correction — 2026-11-12

## Root cause and production observations

The production sitemap is already well-formed XML, not an HTML fallback or a
text file mislabeled as XML. The final response is `200 application/xml` and
contains 20 URLs with the correct sitemap namespace. Its `xhtml:link` language
alternates trigger Chromium's XHTML handling and suppress the native XML tree.
An actual Chrome comparison reproduced this with the original production XML:
`nativeTree=false` before and `nativeTree=true` after removing XHTML elements.
No application CSS, JavaScript, XSL, or XML stylesheet was added.

The original route also relied on a cached fetch without explicit runtime route
handling; production was serving a prerendered/static cached 20-URL result. That
can retain a public-pages-only fallback after supplier discovery fails at build
time. The revised route discovers suppliers at runtime and caches successful
public-directory reads. Upstream failures return the ten public pages in both
languages with a short cache instead of silently caching missing suppliers for
an hour. Last-known successful data can be served by Next's data cache while it
revalidates.

Production host configuration is a separate, unresolved issue:

```text
https://procumaterial.com/sitemap.xml
  308 -> https://www.procumaterial.com/sitemap.xml
  200 Content-Type: application/xml
Server: Vercel
X-Vercel-Cache: HIT
```

The existing production metadata, sitemap URLs, robots Host, and sitemap
declaration all use the requested apex hostname. The live Vercel domain redirect
must be aligned with that choice. No `vercel.json` or frontend Nginx configuration
is present in this checkout; the Nginx reference is an API-only upload proxy.
Adding a reverse redirect in application code while the platform still redirects
apex to www would create a loop. No such redirect was added.

## Files changed for this task

- `frontend/features/marketing/sitemap.ts`: plain protocol XML, escaping and
  deduplication, paginated existing public directory, successful-read caching,
  short-cache failure response, large-directory sitemap index and shards.
- `frontend/app/sitemap.xml/route.ts`: explicit runtime generation and XML headers.
- `frontend/app/sitemaps/pages.xml/route.ts`: public-page sitemap for index mode.
- `frontend/app/sitemaps/suppliers/[page]/route.ts`: bounded supplier shards,
  invalid/missing page 404 and temporary upstream failure 503.
- `frontend/app/app/layout.tsx`: explicit private workspace `noindex, nofollow`
  including Googlebot, inherited by admin, buyer, and supplier dashboards.
- `frontend/tests/sitemap.test.cjs`: XML/header/HEAD, robots, localized metadata,
  private noindex, and invalid shard route checks.
- `frontend/tests/sitemap-generator.test.cjs`: escaping, deduplication, pagination,
  empty/error/malformed responses, and large-directory splitting.
- `frontend/tests/sitemap-browser.mjs`: actual native XML viewer regression check.
- This report.

The prior dashboard changes already present in the workspace were retained.
No landing-page design, business logic, database records, supplier matching, or
backend endpoint was changed for this task. There are no conflicting static
`public/sitemap.xml` files or `app/sitemap.ts` routes. The existing custom XML
route was retained because it needs explicit response headers, graceful upstream
failure handling, and conditional sitemap-index output.

## HTTP output and URL count

Validated against the local production build with an actual PostgreSQL-backed
public directory, using read-only transactions:

```text
HTTP/1.1 200 OK
Content-Type: application/xml; charset=utf-8
Cache-Control: public, max-age=0, s-maxage=3600, must-revalidate
X-Content-Type-Options: nosniff
```

HEAD returns the same status and content type with no response body. Public
supplier discovery failure uses `s-maxage=60`. Successful public directory pages
are cached for 3600 seconds; the response CDN cache is also 3600 seconds, so
visibility can lag a change across both cache layers. No cookies or private
credentials are used for discovery.

The verified current public directory returned **535 eligible supplier profiles**.
The complete generated sitemap contains **1,090 unique URLs**:
20 public-page language variants plus 1,070 supplier-profile language variants.
These are observed counts, not code constants. Supplier membership is determined
by the existing public directory: supplier=true, active=true, verified status.
The detail route applies the same gate. The model has no separate page-publication
flag; unpublished contact/profile fields are governed separately and are neither
requested nor emitted by the sitemap. Unverified/inactive/deleted records absent
from the public directory cannot enter sitemap discovery.

The query uses the existing paginated directory projection (ID, name, city and
verified flag); only IDs are serialized. Six pages cover the observed dataset,
with at most four concurrent requests and no per-supplier detail calls. The
database performs filtered, ordered, bounded pagination; no full table of ORM
entities is loaded into Python. No new database indexes or migrations were added.

Above 24,990 supplier profiles, `/sitemap.xml` becomes a standard sitemap index
referencing `/sitemaps/pages.xml` and `/sitemaps/suppliers/1.xml`, etc. Each supplier
shard has at most 200 language URLs. This avoids the previous silent truncation
at 50,000 URLs. The existing directory API supports up to 10,000 pages; beyond
that API capacity, discovery fails safely and logs a warning rather than emitting
an invalid oversized file. With the existing bounded supplier IDs, the URL limit
is reached well before the 50 MB uncompressed size limit.

## XML, localization, metadata and robots validation

- Python ElementTree parsed the document as
  `{http://www.sitemaps.org/schemas/sitemap/0.9}urlset`.
- Correct UTF-8 XML declaration; 1,090 unique absolute HTTPS URLs; no private,
  login, registration, API, search, or upload URLs.
- All ten requested public routes exist, including `/pricing` and `/faq`.
- Ampersands and all XML special characters are escaped; no HTML, XHTML
  rendering elements, custom script or stylesheet is emitted.
- A real supplier shard parsed as a 200-URL sitemap.
- No guessed `lastmod` values. The existing public directory does not supply a
  trustworthy page-update timestamp. Optional changefreq/priority were omitted.
- All **22 localized page checks** passed: ten public pages and one actual
  supplier profile, each in English and Arabic. Self-canonicals and reciprocal
  en/ar/x-default HTML alternates match the sitemap (normalizing equivalent
  empty-root versus slash-root URL serialization).
- Production `/how-it-works` and `?lang=ar` were separately fetched: both returned
  localized HTML and correct self-canonicals/reciprocal language alternates.
  Existing language switching uses `?lang=ar`; there is no crawler redirect to English.
- Hreflang remains in HTML metadata. Google documents HTML, HTTP headers and XML
  sitemap annotations as equivalent methods; duplicating them is unnecessary.
- Existing `robots.txt` was already correct and was preserved. It advertises
  `Sitemap: https://procumaterial.com/sitemap.xml`, blocks private/authentication
  routes, and leaves public pages/assets crawlable. Private pages are also noindex.

## Tests and deployment status

- `npm.cmd run typecheck`: passed.
- Production build with isolated `NEXT_BUILD_DIR=.next-sitemap-fix`: passed.
- Generator and HTTP sitemap tests: **5 passed**.
- Full frontend API/component suite: **65 passed**.
- Chrome before/after comparison: passed; native expandable XML tree confirmed
  and screenshot inspected.
- XML parser, uniqueness, headers, HEAD, real supplier shard, private noindex,
  empty/error upstream behavior, split-index behavior and 22 localized metadata
  checks: passed.
- `git diff --check`: passed.

No production deployment or Vercel domain-setting change was performed: this
session has no connected Vercel deployment workflow. The deployed sitemap still
needs the source fix published. Existing unrelated dashboard work must not be
inadvertently published as part of a sitemap-only release.

Google Search Console validation remains pending authenticated property access.
After the focused deployment and domain correction, inspect the live sitemap
headers/body, submit `/sitemap.xml` in the verified property, and confirm processing
status. Local XML validation does not establish Search Console acceptance or indexing.

References:

- https://www.sitemaps.org/protocol.html
- https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap
- https://developers.google.com/search/docs/specialty/international/localized-versions
