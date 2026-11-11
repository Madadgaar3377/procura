# Approved landing page — motion-only implementation

## Scope and files

The existing landing page remains server rendered, with its original sections,
copy, translations, links, inline previews, typography, colors, backgrounds,
borders, dimensions, spacing, cards and responsive grids. No dependencies were
installed. No backend, API, data-fetching, caching, image or font code was changed.
There are no new API requests, loading states, skeletons, counters or status changes.

Application files:

- `frontend/features/marketing/home.tsx`: imports the scoped motion stylesheet and
  supplies the existing locale to the enhancement effect. Section markup is unchanged.
- `frontend/features/marketing/chrome.tsx`: reuses the existing enhancement entry
  point, adds an animated native-dialog close/Escape path, and handles reduced
  motion. Existing CTA/session behavior, navigation routes and footer markup remain.
- `frontend/features/marketing/home-motion.ts`: a single one-shot reveal observer,
  native FAQ enhancements, reduced-motion listeners and cleanup. There is no CSS
  visibility gate: content remains readable when JS or observation is unavailable.
- `frontend/features/marketing/home-motion.css`: motion-only styles scoped to
  `.pm-home`, with no layout, typography, spacing or background changes.

Validation files:

- `frontend/tests/landing-motion-browser.mjs`: local Chrome DevTools capture,
  structural/style comparison, responsive and accessibility interaction checks,
  and optional throttled performance observations.
- `frontend/tests/landing-motion-pixels.mjs`: compares screenshot pixels using the
  existing Sharp installation supplied by Next.js.

## Motion

- Hero: 550 ms fade-up entrances; subtitle/actions/preview stagger, with a separate
  small entrance for the existing secondary CTA. Original positions and dimensions stay fixed.
- Workflow and benefits: 460 ms one-shot scroll entrances, 80 ms sibling staggering,
  and subtle existing-icon hover movement.
- RFQ preview: enters with the existing section; its existing decorative cursor
  can pulse twice on a fine-pointer hover. No inputs or validation are touched.
- Supplier dashboard: stat cards/invitations reveal independently without changing
  numbers or statuses. The dark matching network retains its original background
  and cards, with restrained reveals and 2 px hover lifts.
- Comparison: existing preview enters; existing columns fade independently, with
  no table geometry or data changes.
- Trust, directory, buyer/supplier access and final CTA: short entrances and
  lightweight card, icon, arrow and button transitions.
- Header: existing sticky behavior and native modal focus trap; 200 ms drawer
  opening, 180 ms closing, RTL-aware movement, Escape/backdrop/close support.
- FAQ: native details/summary semantics remain; answers fade/translate on opening,
  then fade out before native closing. Keyboard and rapid reversal remain supported.
- Footer: link transitions only; the footer itself is never observed or hidden.

Reveals use opacity and independent translate, preserving existing transforms and
RTL icon directions. There are no infinite animations, layout-property animations,
new decorative graphics or permanent `will-change` hints. Already-visible content
is not hidden after hydration. The observer disconnects from each revealed target.
Animations and listeners are canceled/removed on cleanup and reduced-motion changes.

## Approved visual baseline

Before editing application files, full-page screenshots were captured at **320,
375, 430, 768, 1024, 1440 and 1920 px**, each in English and Arabic, at 1× scale and
a 1000 px viewport height. The original local Next.js page was the baseline; the
after capture uses the optimized production build. Both use the same local API
fixture returning empty promotional lists, preserving the existing conditional
promotions behavior without involving production.

Static screenshots use reduced motion to compare reproducible settled states.
Normal-motion behavior is tested separately. All **14 full-page screenshot pairs
are pixel-identical**, with **zero differing pixels**. DOM comparisons also check
section order, text, link destinations, element rectangles and computed typography,
colors, backgrounds, borders, padding, margins, gaps and grid columns.

Ignored local artifacts are in `frontend/.test-runtime/landing-motion/`:

- `before-{width}-{en|ar}.png` and `after-{width}-{en|ar}.png`
- Matching JSON geometry/style snapshots
- `pixel-results.json`, `after-results.json`, `performance-results.json`

No baseline images or production data are committed.

## Functional and accessibility validation

- TypeScript checks passed.
- Optimized production build passed, including its TypeScript check.
- Existing frontend API/UI suite: **58 passed**, with sitemap checks pointed at
  the local production server.
- At all seven widths, both languages retain no horizontal page overflow during
  the full scroll/reveal sequence. Mobile drawers fit the viewport, receive focus,
  close with Escape and restore their expanded state.
- Existing four workflow tabs still change their selected panel.
- FAQ summaries open and close with native Enter input. Language-switch handling
  reinstalls motion listeners on translated content without changing translation code.
- No continuous animations remain running after reveals settle.
- Reduced motion cancels running enhancements; missing IntersectionObserver and
  disabled-JavaScript scenarios leave server-rendered content visible.
- Console/hydration error collection is empty. Link destinations are unchanged
  from baseline, and there are no protected-account API requests on page load.

Production external CTA destinations and authenticated account flows were not
submitted during this motion-only validation. Their existing handlers and hrefs
are unchanged; browser tests exercise the interactions changed by this patch.

## Performance measurements and limits

Local optimized-build samples used Chrome, disabled cache, **150 ms latency,
1.6 Mbps download, 0.75 Mbps upload and 4× CPU throttling**. The existing API reads
use a loopback fixture, so these are lab results, not production network/backend
latency measurements or real-user 75th percentiles.

| Viewport / locale | LCP | CLS | Longest synthetic keyboard event |
| --- | ---: | ---: | ---: |
| 375 / English | 1.808 s | 0 | 488 ms |
| 375 / Arabic | 1.744 s | 0 | 208 ms |
| 1440 / English | 2.108 s | 0.00886 | 216 ms |
| 1440 / Arabic | 0.948 s | 0.00884 | 192 ms |

LCP and CLS are within the requested thresholds in these samples. **INP ≤200 ms
is not established**: synthetic event timings include samples above that target.
The 488 ms sample was keyup; measured JavaScript handling was approximately 0.1 ms,
with most duration after handling until presentation. These event samples are not
a field INP measurement. Real-user p75 LCP/CLS/INP requires deployed telemetry.
Lighthouse was not installed in this environment, so no Lighthouse score is claimed.

The new helper is 4.7 KB of TypeScript source (approximately 1.75 KB gzipped source)
and scoped CSS is 2.8 KB (approximately 0.84 KB gzipped source). Those are source
measurements, not a before/after production bundle-size measurement.

## Reproduction

Use a loopback Next.js server and the same stable public API fixture. On Windows:

```powershell
$env:LANDING_TEST_ORIGIN='http://127.0.0.1:13096'
node tests/landing-motion-browser.mjs --baseline # Run against approved code first
node tests/landing-motion-browser.mjs           # Run against updated production build
node tests/landing-motion-pixels.mjs
node tests/landing-motion-browser.mjs --perf
npm.cmd run typecheck
npm.cmd run build
```

Implementation and verification are local. No push or deployment was performed.
