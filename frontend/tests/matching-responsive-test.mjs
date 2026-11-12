import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import assert from 'node:assert/strict';

const artifacts = path.resolve('.test-runtime/matching');
await fs.mkdir(artifacts, { recursive: true });

// Read CSS files to serve in the test harness
const workspaceCss = await fs.readFile(path.resolve('frontend/app/workspace.css'), 'utf8');
const matchingCss = await fs.readFile(path.resolve('frontend/app/matching.css'), 'utf8');

// Sample test HTML containing the exact Matched Suppliers component markup
function generateHtml(rtl = false) {
  return `<!DOCTYPE html>
<html lang="${rtl ? 'ar' : 'en'}" dir="${rtl ? 'rtl' : 'ltr'}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Supplier Matching Test</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #1e293b; padding: 24px 16px; }
    ${workspaceCss}
    ${matchingCss}
  </style>
</head>
<body>
  <div class="pm-matching-container">
    <!-- Breadcrumbs -->
    <nav class="pm-matching-crumbs">
      <a href="#">Buyer Workspace</a>
      <span class="pm-matching-crumbs-sep">›</span>
      <span class="pm-matching-crumbs-current">Matched suppliers</span>
    </nav>

    <!-- Header -->
    <header class="pm-matching-header">
      <div class="pm-matching-title-area">
        <span class="pm-matching-eyebrow">WORKSPACE</span>
        <h1 class="pm-matching-title">Supplier Matching</h1>
        <p class="pm-matching-subtitle">Review matched suppliers evaluated against category capabilities, service coverage, and qualifications.</p>
      </div>
      <div class="pm-matching-header-actions">
        <button class="pm-filter-tab" style="background:#fff;border:1px solid #e2e8f0;">Refresh</button>
      </div>
    </header>

    <!-- RFQ Selector -->
    <div class="pm-matching-selector-bar">
      <div class="pm-matching-selector-top">
        <label class="pm-matching-selector-label">Select RFQ</label>
        <div class="pm-matching-selector-dropdown-wrap">
          <select class="pm-matching-select">
            <option>RFQ-2026-001 — Industrial valves for municipal water</option>
          </select>
        </div>
      </div>
    </div>

    <!-- RFQ Summary Card -->
    <section class="pm-rfq-summary-card">
      <div class="pm-rfq-summary-top">
        <div class="pm-rfq-summary-ref-lockup">
          <span class="pm-rfq-ref-badge">RFQ-2026-001</span>
          <h2 class="pm-rfq-title-text">Industrial valves for municipal water treatment facility</h2>
          <span class="pm-badge pm-badge-verified">Published</span>
        </div>
        <a href="#" class="pm-materials-toggle">View RFQ details →</a>
      </div>
      <div class="pm-rfq-summary-grid">
        <div class="pm-rfq-summary-item">
          <span class="pm-rfq-summary-label">Project name</span>
          <span class="pm-rfq-summary-value">Riyadh Municipal Plant Expansion</span>
        </div>
        <div class="pm-rfq-summary-item">
          <span class="pm-rfq-summary-label">Main category</span>
          <span class="pm-rfq-summary-value">Mechanical & HVAC</span>
        </div>
        <div class="pm-rfq-summary-item">
          <span class="pm-rfq-summary-label">Delivery city</span>
          <span class="pm-rfq-summary-value">Riyadh</span>
        </div>
        <div class="pm-rfq-summary-item">
          <span class="pm-rfq-summary-label">Response deadline</span>
          <span class="pm-rfq-summary-value">24 Nov 2026, 17:00</span>
        </div>
      </div>
    </section>

    <!-- Overview Stats -->
    <div class="pm-overview-grid">
      <div class="pm-stat-card">
        <span class="pm-stat-label">Matched suppliers</span>
        <div class="pm-stat-value">6</div>
        <span class="pm-stat-note">Pass category & location rules</span>
      </div>
      <div class="pm-stat-card">
        <span class="pm-stat-label">Suppliers invited</span>
        <div class="pm-stat-value">3</div>
        <span class="pm-stat-note">Dispatched for quotation</span>
      </div>
      <div class="pm-stat-card">
        <span class="pm-stat-label">Pending responses</span>
        <div class="pm-stat-value">2</div>
        <span class="pm-stat-note">Awaiting supplier offer</span>
      </div>
      <div class="pm-stat-card">
        <span class="pm-stat-label">Quotations received</span>
        <div class="pm-stat-value">1</div>
        <span class="pm-stat-note">Ready for comparison</span>
      </div>
    </div>

    <!-- State Banner -->
    <div class="pm-state-banner">
      <div class="pm-state-banner-left">
        <span class="pm-state-indicator-dot completed"></span>
        <div>
          <strong>Matching state: Completed</strong>
          <span style="font-size:13px;color:#64748b;">6 suppliers qualify based on approved category capability and configured eligibility.</span>
        </div>
      </div>
      <span style="font-size:13px;color:#64748b;">14 suppliers evaluated</span>
    </div>

    <!-- Favorites -->
    <section class="pm-favorites-section">
      <div class="pm-favorites-header">
        <div class="pm-favorites-title">
          <span>★ Favorite suppliers</span>
          <span class="pm-filter-count">2</span>
        </div>
      </div>
      <div class="pm-favorites-list">
        <div class="pm-favorite-chip">
          <a href="#">Riyadh Industrial Supply Co.</a>
          <button class="pm-favorite-remove">×</button>
        </div>
        <div class="pm-favorite-chip">
          <a href="#">FlowTek Middle East Trading</a>
          <button class="pm-favorite-remove">×</button>
        </div>
      </div>
    </section>

    <!-- Filter Bar -->
    <div class="pm-filter-bar">
      <div class="pm-filter-row">
        <div class="pm-filter-tabs">
          <button class="pm-filter-tab active">All suppliers <span class="pm-filter-count">6</span></button>
          <button class="pm-filter-tab">Eligible <span class="pm-filter-count">4</span></button>
          <button class="pm-filter-tab">Invited <span class="pm-filter-count">3</span></button>
          <button class="pm-filter-tab">Location mismatch <span class="pm-filter-count">2</span></button>
        </div>
      </div>
      <div class="pm-search-input-wrap">
        <input class="pm-search-input" placeholder="Search suppliers by name, category, or city..." />
      </div>
    </div>

    <!-- Results Grid -->
    <div class="pm-results-grid">
      <!-- Card 1 -->
      <article class="pm-supplier-card">
        <header class="pm-card-header">
          <div class="pm-card-company-lockup">
            <div class="pm-company-avatar">RI</div>
            <div class="pm-card-company-info">
              <a href="#" class="pm-company-name-link">Riyadh Industrial Supply Co.</a>
              <div class="pm-card-badges-row">
                <span class="pm-badge pm-badge-verified">Verified</span>
                <span class="pm-badge pm-badge-eligible">Eligible</span>
                <span class="pm-badge pm-badge-invited">Invited</span>
              </div>
            </div>
          </div>
          <button class="pm-card-fav-btn is-fav">★</button>
        </header>

        <div class="pm-card-body">
          <div class="pm-card-matrix">
            <div class="pm-matrix-item">
              <span class="pm-matrix-label">Item coverage</span>
              <span class="pm-matrix-value">All 3 requested items</span>
            </div>
            <div class="pm-matrix-item">
              <span class="pm-matrix-label">Delivery coverage</span>
              <span class="pm-matrix-value" style="color:#15803d;">Covered (Riyadh)</span>
            </div>
            <div class="pm-matrix-item">
              <span class="pm-matrix-label">Match level</span>
              <div class="pm-chip-group">
                <span class="pm-chip pm-chip-level">Exact material</span>
              </div>
            </div>
            <div class="pm-matrix-item">
              <span class="pm-matrix-label">Quotation status</span>
              <span class="pm-matrix-value">Submitted</span>
            </div>
          </div>

          <div class="pm-materials-box">
            <div class="pm-materials-header">
              <span>Requested materials matched (3)</span>
            </div>
            <ul class="pm-materials-list">
              <li>Butterfly valve, chilled water DN150</li>
              <li>Cast iron gate valve DN200 flanged</li>
            </ul>
          </div>

          <div class="pm-explanation-box eligible">
            <div class="pm-explanation-text">
              <strong>Eligibility: </strong>Approved Industrial valves capability. Delivery coverage matches Riyadh.
            </div>
          </div>

          <div class="pm-contact-bar">
            <a href="https://riyadhsupply.example.com" class="pm-contact-link">riyadhsupply.example.com ↗</a>
            <a href="mailto:procurement@riyadhsupply.example.com" class="pm-contact-link">procurement@riyadhsupply.example.com</a>
            <a href="tel:+966114567890" class="pm-contact-link">+966 11 456 7890</a>
          </div>
        </div>

        <footer class="pm-card-actions">
          <div style="display:flex;gap:8px;">
            <button class="pm-filter-tab" style="background:#fff;border:1px solid #e2e8f0;">View profile</button>
            <a href="#" class="pm-materials-toggle" style="align-self:center;">Compare quotations →</a>
          </div>
          <span style="font-size:13px;color:#f59e0b;">★ Favorited</span>
        </footer>
      </article>

      <!-- Card 2: Location Mismatch -->
      <article class="pm-supplier-card">
        <header class="pm-card-header">
          <div class="pm-card-company-lockup">
            <div class="pm-company-avatar" style="background:#fff7ed;color:#c2410c;border-color:#fed7aa;">JW</div>
            <div class="pm-card-company-info">
              <a href="#" class="pm-company-name-link">Jeddah Water Systems Trading</a>
              <div class="pm-card-badges-row">
                <span class="pm-badge pm-badge-verified">Verified</span>
                <span class="pm-badge pm-badge-mismatch">Location mismatch</span>
              </div>
            </div>
          </div>
          <button class="pm-card-fav-btn">☆</button>
        </header>

        <div class="pm-card-body">
          <div class="pm-card-matrix">
            <div class="pm-matrix-item">
              <span class="pm-matrix-label">Item coverage</span>
              <span class="pm-matrix-value">2 of 3 items</span>
            </div>
            <div class="pm-matrix-item">
              <span class="pm-matrix-label">Delivery coverage</span>
              <span class="pm-matrix-value" style="color:#c2410c;">Not covered</span>
            </div>
          </div>

          <div class="pm-explanation-box location_mismatch">
            <div class="pm-explanation-text">
              <strong>Eligibility: </strong>Supplier serves Jeddah, Makkah. RFQ requires delivery in Riyadh.
            </div>
          </div>

          <div class="pm-contact-bar">
            <a href="https://jeddahwater.example.com" class="pm-contact-link">jeddahwater.example.com ↗</a>
            <a href="tel:+966126789012" class="pm-contact-link">+966 12 678 9012</a>
          </div>
        </div>

        <footer class="pm-card-actions">
          <button class="pm-filter-tab" style="background:#fff;border:1px solid #e2e8f0;">View profile</button>
        </footer>
      </article>
    </div>

    <!-- Invitations Section -->
    <section class="pm-invitations-panel">
      <div class="pm-invitations-header">
        <div class="pm-invitations-title-group">
          <h2 class="pm-invitations-title">Invitation responses</h2>
          <span class="pm-filter-count">1</span>
        </div>
        <span class="pm-invitations-cooldown-badge">⏱ Next reminder available: 12 Nov, 14:00</span>
      </div>

      <div class="pm-invitations-table-wrap">
        <table class="pm-invitations-table">
          <thead>
            <tr>
              <th style="width:44px;text-align:center;"></th>
              <th>Supplier</th>
              <th>Invitation status</th>
              <th>Quotation status</th>
              <th>Invited</th>
              <th style="text-align:end;">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style="text-align:center;"><input type="checkbox" disabled /></td>
              <td><strong>Riyadh Industrial Supply Co.</strong></td>
              <td><span class="pm-badge pm-badge-invited">Invited</span></td>
              <td><span class="pm-badge pm-badge-verified">Submitted</span></td>
              <td>11 Nov 2026</td>
              <td style="text-align:end;"><a href="#" class="pm-materials-toggle">Compare</a></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</body>
</html>`;
}

// Start lightweight HTTP server to serve the test page
const server = http.createServer((req, res) => {
  const isRtl = req.url?.includes('rtl=true');
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(generateHtml(isRtl));
});

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const { port: serverPort } = server.address();
const testUrl = `http://127.0.0.1:${serverPort}`;

// Start Chrome
const chromePort = 25000 + Math.floor(Math.random() * 4000);
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--disable-extensions', '--no-sandbox',
  '--no-first-run', `--remote-debugging-port=${chromePort}`,
  `--user-data-dir=${path.join(artifacts, 'chrome-' + process.pid)}`, 'about:blank'
], { windowsHide: true, stdio: 'ignore' });

let ws;
try {
  let target;
  for (let i = 0; i < 80; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${chromePort}/json/list`)).json();
      target = list.find(item => item.type === 'page');
      if (target) break;
    } catch {}
    await delay(150);
  }
  assert.ok(target, 'Headless Chrome started');

  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });

  let seq = 0;
  const pending = new Map();
  ws.addEventListener('message', event => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) {
      const p = pending.get(data.id);
      pending.delete(data.id);
      if (data.error) p.reject(new Error(data.error.message));
      else p.resolve(data.result);
    }
  });

  function cdp(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async function evaluate(expression) {
    const res = await cdp('Runtime.evaluate', { expression, returnByValue: true });
    return res.result.value;
  }

  await cdp('Page.enable');
  await cdp('Runtime.enable');

  const testWidths = [320, 375, 430, 768, 1024, 1440, 1920];

  console.log('Testing responsive layouts across all 7 required viewports in LTR and RTL:');

  for (const width of testWidths) {
    // 1. Test LTR
    await cdp('Emulation.setDeviceMetricsOverride', {
      width, height: 900, deviceScaleFactor: 1, mobile: width < 768
    });
    await cdp('Page.navigate', { url: `${testUrl}/?rtl=false` });
    await delay(250);

    const ltrMetrics = await evaluate(`({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      cardsCount: document.querySelectorAll('.pm-supplier-card').length,
      hasOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
    })`);

    assert.equal(ltrMetrics.hasOverflow, false, `Width ${width}px (LTR) should not have horizontal overflow (scrollWidth: ${ltrMetrics.scrollWidth}, clientWidth: ${ltrMetrics.clientWidth})`);
    assert.equal(ltrMetrics.cardsCount, 2, `Width ${width}px (LTR) should render 2 cards`);
    console.log(`  ✓ ${width}px LTR layout passed (scrollWidth: ${ltrMetrics.scrollWidth} <= ${ltrMetrics.clientWidth})`);

    // 2. Test RTL
    await cdp('Page.navigate', { url: `${testUrl}/?rtl=true` });
    await delay(250);

    const rtlMetrics = await evaluate(`({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      dir: document.documentElement.dir,
      hasOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
    })`);

    assert.equal(rtlMetrics.dir, 'rtl', `Width ${width}px (RTL) should have dir="rtl"`);
    assert.equal(rtlMetrics.hasOverflow, false, `Width ${width}px (RTL) should not have horizontal overflow`);
    console.log(`  ✓ ${width}px RTL layout passed (dir: ${rtlMetrics.dir}, scrollWidth: ${rtlMetrics.scrollWidth} <= ${rtlMetrics.clientWidth})`);
  }

  console.log('\nAll 7 viewports passed both LTR and RTL tests with zero horizontal overflow!');
} finally {
  if (ws) ws.close();
  chrome.kill();
  server.close();
}
