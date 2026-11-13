# Procumaterial landing motion upgrade

Implemented in the local repository. Production deployment was not performed.
Existing content, sections, URLs, metadata, structured data, bilingual copy and demonstration data are preserved.

## Files changed

| Files | Change |
| --- | --- |
| `features/marketing/home-motion.css` | Immediate heading/CTA visibility; restrained hero movement, gradient accent, desktop perspective, supplier stagger, workflow connectors, column hover and reduced-motion overrides. |
| `features/marketing/home-motion.ts` | 16px one-time reveals; one delegated pointer listener and one pending animation frame; bounded 3-degree tilt (1 degree for comparison); cleanup for pointer exit, blur, device preference and unmount. |
| `features/marketing/previews.tsx` | Motion's `useAnimate` from `motion/react-mini` for workflow-tab transitions, cancellation on tab changes and reduced motion. No extra animation at initial hydration. |
| `features/marketing/previews-static.tsx` (new), `features/marketing/home.tsx` | Extracted the RFQ form, supplier dashboard and comparison into Server Components, preserving their markup. |
| `features/marketing/chrome.tsx` | Separately loaded nonessential enhancements; header initialization scheduled in an animation frame. Existing native modal focus management, keyboard behavior and CTA session routing remain. |
| `app/layout.tsx`, `components/route-view.tsx`, `features/marketing/industry-catalog.tsx`, `features/suppliers/profile.tsx` | Moved three unrelated global stylesheet imports to their consumers so they do not block the landing page. |
| `package.json`, `package-lock.json` | Motion 14.13.0; development-only ESLint, Next ESLint configuration and Lighthouse; repeatable marketing lint and landing test commands. |
| `eslint.config.mjs` (new) | Next core web vitals rules; generated/build output ignored. |
| `tests/landing-motion-browser.mjs` | Added actual pointer/reset/touch checks, hard reload, client navigation/back navigation and finite-animation settling. |
| `tests/landing-lighthouse.mjs` (new) | Serial, local-only baseline/final Lighthouse comparisons for mobile and desktop. |

No new client animation wrapper components were needed. The existing `WorkflowPreview` and `MarketingEnhancements` were extended; `mountHomeMotion` remains the shared enhancement controller.

## Performance and responsiveness

- Static marketing content remains server-rendered and visible without JavaScript or IntersectionObserver. Primary heading and CTA entrance never starts at opacity zero.
- Only the required Motion mini React API is imported. Static previews have no client component boundary. No 3D engine, image assets, animation dependency beyond Motion, API polling or fullscreen loader was added.
- Existing local font loading and caching policies are retained. Previews use HTML/SVG, so there are no new bitmap images to optimize with `next/image`.
- Perspective is limited to fine mouse pointers at widths of at least 1024px. Small screens do not run the initial float/gradient animation. All nonessential animation is disabled for reduced motion.
- CSS uses transform/opacity for decorative animation. Pointer listeners are delegated, passive and frame-limited; `will-change` is removed when the active tilt ends.
- Public promotion reads, authentication behavior and dynamic-content caching semantics are unchanged. The browser checks recorded zero landing-page API requests.

## Validation

- Optimized production build: passed (`NEXT_BUILD_DIR=.test-runtime/landing-optimized`).
- Standalone TypeScript check: passed.
- `npm run lint:marketing`: passed with three existing warnings: CTA hard navigation, industry memo dependencies and live-pricing effect dependencies. No lint errors. This is scoped marketing lint, not a claim that unrelated code passes full-repository ESLint.
- Landing browser suite: passed at 320, 375, 430, 768, 1024, 1440 and 1920px in English and Arabic.
- At reduced motion, all 14 full-page screenshots are pixel-identical to the fresh baseline; geometry, copy, links and sampled visual styles are unchanged.
- Menus, focus containment, Escape, workflow tabs, keyboard FAQ operation, locale switching, scroll overflow, pointer reset, mobile tilt gating, preference changes, hard refresh and back navigation passed. No running animations remain after the finite entrance/reveal effects settle.
- JavaScript-disabled and missing-IntersectionObserver checks passed. No browser console/hydration errors were recorded.
- Existing industry viewport suite passed at seven widths in English and Arabic after relocating its stylesheet.
- Browser-observed maximum CLS across motion cases: 0.015. The longest recorded synthetic keyboard event was 496ms; this does **not** establish field INP under 200ms.

## Lighthouse

| Metric | Mobile before | Mobile after | Desktop before | Desktop after |
| --- | ---: | ---: | ---: | ---: |
| Performance | 76 | 87 | 99 | 100 |
| LCP | 3.79s | 1.27s | 0.84s | 0.35s |
| CLS | 0 | 0 | 0.000005 | 0.001008 |
| Total blocking time | 505ms | 509ms | 47ms | 32ms |
| Total transferred bytes | 337,281 | 334,650 | 347,702 | 345,074 |

The final mobile sample meets the LCP and CLS targets. Mobile TBT remains elevated and is essentially unchanged; TBT is not INP. The synthetic keyboard result above means the interaction target is not established. The CSS loading change reduced total transfer even with the new Motion dependency. These are individual samples from the final serial run on 2026-11-13, with the preceding build and browser checks finished.

Raw local evidence is in `.test-runtime/landing-motion/`: `lighthouse-*-mobile.json`, `lighthouse-*-desktop.json`, `lighthouse-summary.json`, `after-results.json`, `pixel-results.json`, and before/after screenshots. These generated artifacts are gitignored.

The comparison uses local production servers, Lighthouse 13.6.0 and headless Chrome 157 on Windows. Mobile uses Lighthouse's default simulated network and 4x CPU slowdown; desktop uses its desktop preset. Measurements are lab samples, not live-site or real-device results. Earlier exploratory runs varied and are retained in the local artifacts; they are not release guarantees.

## Remaining verification

Field INP and deployed-site Core Web Vitals need production monitoring and representative devices. The under-200ms interaction target is not verified; mobile TBT remains about 509ms, and the synthetic keyboard sequence included a 496ms event. Broader application CSS/locale bundles are still shared by the root layout; changing their architecture would need additional route coverage. Supplier-profile and authenticated-workspace integration suites were not run; their CSS import ownership was checked by the production build. Public backend promotion responses were not benchmarked against the live backend.
