# Login and workspace loading fix

## Causes

- `/login` previously resolved through `app/[...segments]/page.tsx`; there was no dedicated login route. Its loading boundary used `route-loading` classes, while global/workspace CSS was imported inside the suspended page. Commit `bcfa5c9` removed those CSS imports from the root layout for landing performance. The fallback could therefore render before its stylesheet existed.
- The authentication component replaced the entire login form while awaiting `auth/me`, cookie refresh, and organization discovery. Slow authentication therefore hid the public form.
- After submission, `redirecting` was set before organization discovery. Request failure did not reliably recover from the separate `checkingSession` state.
- Workspace guards and organization selection requested organization data concurrently with authentication, including anonymous visits to protected routes.
- An aborted login probe could finish a shared refresh and emit a stale session-expired event after a newer login succeeded.
- Pending company claims had no claimant-facing read endpoint. A saved signup preference could also override existing memberships when resolving a workspace.

## Changes

- Added a dedicated `/login` route and layout. It imports only auth styles, localization and toast providers, avoiding the catch-all route's dashboard/chart imports. Disabled automatic link prefetch in auth screens to avoid downloading those routes while entering credentials.
- Login renders the form on the server. Session discovery runs in the background, and its status occupies reserved space. Submitting cancels the old probe. Authentication errors release pending state and provide retry without technical details.
- Shared loading and error screens use their own CSS module, independent of page/workspace CSS. They include a centered branded card, light blue background, accessible status, RTL copy, and reduced-motion support. Error boundaries use this installed Next.js version's `retry` prop.
- User verification precedes organization requests. Administrators go directly to admin; single-role memberships go to the matching dashboard; multiple roles go to workspace selection. Signup preferences apply only when there are no eligible existing memberships.
- Added `GET /api/v1/organizations/claims/mine`, filtered by authenticated claimant ID. Pending claims lead to the supplier company verification screen, which suppresses company creation until the claim is resolved. This lookup performs no writes. Existing organization IDs and memberships remain intact.
- Workspace and organization failures provide retry. Existing bounded API transport deadlines remain; no UI timer was added to mask unresolved work. Canceled probes cannot emit stale expiration events.
- Root marketing CSS isolation remains in place; login/workspace CSS was not restored to the root layout. The localization proxy now includes `/login` for correct initial query-string locale.

## Validation

- TypeScript typecheck passed.
- Next.js production build passed using `NEXT_BUILD_DIR=.next-login-final`. The default `.next` output was locked, so verification used a separate build directory.
- API client/error tests: **9 passed**, including canceled refresh versus a newer login.
- Full backend suite against disposable test databases: **189 passed, 12 skipped**. The new claims test covers anonymous denial, claimant privacy, and unchanged company/membership counts.
- Local Chrome browser suite **passed against the final production build**, using intercepted API responses. It covers all six widths (320, 375, 768, 1024, 1440, 1920) in English and Arabic, a pending session with the form visible, failure/retry, role redirects, onboarding, pending claims, refresh recovery, logout, loading geometry, reduced motion, protected routes and server-rendered login with JavaScript disabled. **Zero console or hydration errors** were recorded. Screenshots and observations are saved under `frontend/.test-runtime/login/`. Run with `LOGIN_TEST_ORIGIN=http://127.0.0.1:13111 node tests/login-browser.mjs` (set the environment variable using the shell's syntax).
- Local mobile Lighthouse: final build **57 performance / 100 accessibility**, versus **53 / 100** on the earlier build from this task. Both recorded CLS **0.0125**. This is an earlier/final local comparison, not a clean pre-change or production benchmark. Landing client manifests contain no login stylesheet or loading component references.
- The existing broad navbar suite **did not pass on either build**. The final build failed a dropdown-bound assertion, then a repeated diagnostic run failed its compact-transform assertion; the earlier build failed a section-link hash assertion after passing several viewport cases. These failures remain unresolved. Separate marketing/footer edits appeared concurrently in the workspace and were left untouched; this login change does not claim to fix or verify that separate work.
- These browser scenarios validate UI behavior against controlled responses, not production account access or live provider availability. The production URL could not be opened by the web inspection tool. No production account, organization, deployment, database or messaging provider was changed.
- Further supplier-claim work appeared after the full-suite/build validation, extending the claims response and adding model/migration changes. The focused claimant privacy/no-write test was rerun against those changes and **passed**. The full-suite and production-build results describe the earlier tested snapshot, not all subsequent concurrent edits.

## Rollout

Deploy the backend endpoint and regenerated API contract before or with the frontend. This login fix alone adds no database columns; separate concurrent supplier-claim work adds its own migration and requires independent rollout review. Verify a controlled buyer, supplier, administrator and pending claimant on the deployed environment. Production Lighthouse scores require a deployed measurement; local checks cannot establish unchanged production scores.
