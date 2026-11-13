# Floating public navbar

Implemented locally; not deployed. The original wordmark, all seven navigation destinations, login/signup links, CTA session routing, locale switching and page metadata/content are preserved. Buyer, supplier and admin dashboard navigation is unchanged.

## Design and behavior

- Centered glass pill, up to 1280px wide, with a 16px desktop top gutter and 12px mobile gutter. Translucent white, a thin blue-gray border, a soft shadow, and static 16px backdrop blur with 130% saturation.
- The bar visually compacts and becomes more opaque after scrolling 16px. A fixed bar plus a reserved 104px/84px shell keeps document geometry stable and avoids the site's legacy mobile overflow rules breaking sticky positioning.
- Original logo, centered desktop links, language/login controls and rounded blue CTA. Active links have a pale blue pill and small blue marker. Hover transitions use CSS.
- Below 1280px, the native modal opens as a centered rounded glass panel below the bar. At phone sizes, language and authentication actions live inside the panel. Short screens scroll inside the panel.
- English/Arabic layouts mirror through logical properties and explicit menu direction. Switching language preserves an open menu and its placement.
- Motion's existing `motion/react-mini` API handles short menu entrance/exit transitions. The initial appearance moves only the small text/control layers, keeping the blur static. Reduced motion removes decorative transitions and cancels an in-flight menu animation.
- Native modal semantics plus endpoint Tab/Shift+Tab wrapping, focus restoration, Escape, outside-click closing in both axes, 44px minimum targets and visible focus indicators. Accessible language names include the visible language label.
- IntersectionObserver handles the scroll threshold; a passive scroll listener is installed only when observers are unavailable. No new animation dependency, polling, continuously animated blur/shadow or dashboard styling was added.

## Files

| File | Purpose |
| --- | --- |
| `features/marketing/chrome.tsx` | Floating-header shell, scroll observer, menu motion, focus/closing behavior and accessible language names. Authentication/API code is unchanged. |
| `features/marketing/navigation.css` | Public-header-only glass, pill, active/hover, responsive and reduced-motion styles. |
| `tests/navbar-browser.mjs` | Local production browser checks and screenshots. |
| `tests/landing-lighthouse.mjs` | Optional output-directory and category settings, preserving its existing defaults. |
| `package.json` | Adds `npm run test:navbar`. No dependency changes. |
| `docs/navbar-report.md` | Implementation and validation record. |

## Verification

- Production build passed with `.test-runtime/navbar-release` as the isolated build directory.
- Standalone TypeScript passed. Marketing ESLint passed with the same three existing warnings about CTA hard navigation, industry memo dependencies and live-pricing effect dependencies.
- Browser checks passed in English and Arabic at 320, 375, 768, 1024, 1279, 1280, 1440 and 1920px, plus a 320x568 short phone viewport.
- Verified no logo/link/action overlap, no horizontal overflow, unchanged destinations and login/signup hrefs, scroll compaction, stable main-content position, active section indicators and 44px menu controls.
- Verified keyboard cycling in both directions, Escape/focus return, outside click above the menu, menu-link closing, language switching while open, desktop-resize cleanup, public-route reuse and client navigation between About, Home and Contact.
- Reduced-motion, missing-IntersectionObserver and JavaScript-disabled initial navigation checks passed.
- All 16 main viewport/language cases recorded CLS 0 in the browser suite. There were no console/hydration errors or initial account API requests.
- Desktop English and mobile Arabic screenshots were visually reviewed. Screenshots and raw browser evidence are in `.test-runtime/navbar/` and are gitignored.

## Lighthouse

Final controlled measurements are recorded after the comparison completes. The comparison runs the pre-change production artifact and the final production build serially using Lighthouse 13.6.0, Chrome 157, the default simulated mobile settings and the desktop preset. Scores are whole-page local lab results, not field Core Web Vitals or a navbar-only score. Raw reports are in `.test-runtime/navbar/benchmarks/`.

## Reproduce locally

Run a production server, then set `NAVBAR_TEST_ORIGIN` to its loopback origin and run `npm run test:navbar`. For Lighthouse, set `LANDING_BEFORE_ORIGIN`, `LANDING_AFTER_ORIGIN`, `LANDING_LIGHTHOUSE_DIRECTORY` and optionally `LANDING_LIGHTHOUSE_CATEGORIES=performance,accessibility`, then run `node tests/landing-lighthouse.mjs` from `frontend`.
