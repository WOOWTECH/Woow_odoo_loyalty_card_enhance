/**
 * Balance Display Feature — Playwright E2E Tests
 * Tests: stability, completeness, edge cases
 */

const TEST_RESULTS = [];

function assert(name, condition, detail) {
  TEST_RESULTS.push({ test: name, pass: !!condition, detail: detail || '' });
}

// ---- Helper: login ----
async function login(page) {
  await page.goto('http://localhost:9100/web/login');
  await page.waitForLoadState('networkidle');
  // Odoo 18 hides form with d-none
  await page.evaluate(() => {
    const form = document.querySelector('.oe_login_form');
    if (form) form.classList.remove('d-none');
  });
  await page.fill('input[name="login"]', 'portal');
  await page.fill('input[name="password"]', 'portal');
  await page.click('button[type="submit"]');
  await page.waitForLoadState('networkidle');
}

// ---- Helper: extract table data ----
function extractTableData(doc) {
  const headers = [...doc.querySelectorAll('thead th')].map(th => th.textContent.trim());
  const rows = [...doc.querySelectorAll('tbody tr')].map(tr => {
    const cells = [...tr.querySelectorAll('td')];
    return {
      date: cells[0]?.textContent?.trim(),
      desc: cells[1]?.textContent?.trim(),
      order: cells[2]?.textContent?.trim(),
      before: cells[3]?.textContent?.trim(),
      issued: cells[4]?.textContent?.trim(),
      used: cells[5]?.textContent?.trim(),
      after: cells[6]?.textContent?.trim(),
    };
  });
  return { headers, rows };
}

function parseNum(text) {
  if (!text || text.trim() === '' || text.trim() === '—') return 0;
  const cleaned = text.replace(/[^0-9.\-]/g, '');
  return parseFloat(cleaned) || 0;
}

// ---- Test Suite ----
async function runTests(page) {

  // ========================================
  // TEST 1: Loyalty Card (集點卡) - card 17
  // ========================================
  await page.goto('http://localhost:9100/my/member-center/loyalty/17');
  await page.waitForLoadState('networkidle');

  const t1 = await page.evaluate(() => {
    const headers = [...document.querySelectorAll('thead th')].map(th => th.textContent.trim());
    const rows = [...document.querySelectorAll('tbody tr')];
    const rowData = rows.map(tr => {
      const c = [...tr.querySelectorAll('td')];
      return {
        before: (c[3]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        issued: (c[4]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        used:   (c[5]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        after:  (c[6]?.textContent || '').replace(/[^0-9.\-]/g, ''),
      };
    });
    return { headers, rowData, rowCount: rows.length };
  });

  assert('T1.1 Loyalty: 7 column headers',
    t1.headers.length === 7 &&
    t1.headers[3] === '前餘額' && t1.headers[6] === '後餘額',
    t1.headers.join(' | '));

  assert('T1.2 Loyalty: has 10 rows (snippet limit)',
    t1.rowCount === 10, `${t1.rowCount} rows`);

  // Math check
  let t1MathOk = true;
  for (const r of t1.rowData) {
    const b = parseFloat(r.before) || 0;
    const i = parseFloat(r.issued) || 0;
    const u = parseFloat(r.used) || 0;
    const a = parseFloat(r.after) || 0;
    if (Math.abs(a - (b + i - u)) > 0.01) t1MathOk = false;
  }
  assert('T1.3 Loyalty: balance_after = before + issued - used', t1MathOk);

  // Continuity (DESC order)
  let t1ContOk = true;
  for (let i = 0; i < t1.rowData.length - 1; i++) {
    if (t1.rowData[i].before !== t1.rowData[i + 1].after) t1ContOk = false;
  }
  assert('T1.4 Loyalty: balance continuity (DESC)', t1ContOk);

  // ========================================
  // TEST 2: eWallet (電子錢包) - card 16
  // ========================================
  await page.goto('http://localhost:9100/my/member-center/ewallet/16');
  await page.waitForLoadState('networkidle');

  const t2 = await page.evaluate(() => {
    const headers = [...document.querySelectorAll('thead th')].map(th => th.textContent.trim());
    const rows = [...document.querySelectorAll('tbody tr')];
    const rowData = rows.map(tr => {
      const c = [...tr.querySelectorAll('td')];
      return {
        before: (c[3]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        issued: (c[4]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        used:   (c[5]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        after:  (c[6]?.textContent || '').replace(/[^0-9.\-]/g, ''),
      };
    });
    return { headers, rowData, rowCount: rows.length, title: document.title };
  });

  assert('T2.1 eWallet: 7 column headers',
    t2.headers.length === 7 &&
    t2.headers[3] === '前餘額' && t2.headers[6] === '後餘額',
    t2.headers.join(' | '));

  assert('T2.2 eWallet: page loaded correctly',
    t2.title.includes('電子錢包'), t2.title);

  assert('T2.3 eWallet: has transaction rows',
    t2.rowCount > 0, `${t2.rowCount} rows`);

  // ========================================
  // TEST 3: Gift Card (禮品卡) - card 18
  // ========================================
  await page.goto('http://localhost:9100/my/member-center/gift-card/18');
  await page.waitForLoadState('networkidle');

  const t3 = await page.evaluate(() => {
    const headers = [...document.querySelectorAll('thead th')].map(th => th.textContent.trim());
    const rows = [...document.querySelectorAll('tbody tr')];
    const rowData = rows.map(tr => {
      const c = [...tr.querySelectorAll('td')];
      return {
        before: (c[3]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        issued: (c[4]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        used:   (c[5]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        after:  (c[6]?.textContent || '').replace(/[^0-9.\-]/g, ''),
      };
    });
    return { headers, rowData, rowCount: rows.length, title: document.title };
  });

  assert('T3.1 Gift Card: 7 column headers',
    t3.headers.length === 7 &&
    t3.headers[3] === '前餘額' && t3.headers[6] === '後餘額',
    t3.headers.join(' | '));

  assert('T3.2 Gift Card: page loaded correctly',
    t3.title.includes('禮品卡'), t3.title);

  assert('T3.3 Gift Card: has transaction rows',
    t3.rowCount > 0, `${t3.rowCount} rows`);

  // Math check
  let t3MathOk = true;
  for (const r of t3.rowData) {
    const b = parseFloat(r.before) || 0;
    const i = parseFloat(r.issued) || 0;
    const u = parseFloat(r.used) || 0;
    const a = parseFloat(r.after) || 0;
    if (Math.abs(a - (b + i - u)) > 0.01) t3MathOk = false;
  }
  assert('T3.4 Gift Card: balance math correct', t3MathOk);

  // ========================================
  // TEST 4: Full History Page (集點卡)
  // ========================================
  await page.goto('http://localhost:9100/my/member-center/loyalty/17/history');
  await page.waitForLoadState('networkidle');

  const t4 = await page.evaluate(() => {
    const headers = [...document.querySelectorAll('thead th')].map(th => th.textContent.trim());
    const rows = [...document.querySelectorAll('tbody tr')];
    const rowData = rows.map(tr => {
      const c = [...tr.querySelectorAll('td')];
      return {
        before: (c[3]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        issued: (c[4]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        used:   (c[5]?.textContent || '').replace(/[^0-9.\-]/g, ''),
        after:  (c[6]?.textContent || '').replace(/[^0-9.\-]/g, ''),
      };
    });
    return { headers, rowData, rowCount: rows.length, title: document.title };
  });

  assert('T4.1 Full History: 7 column headers',
    t4.headers.length === 7 &&
    t4.headers[3] === '前餘額' && t4.headers[6] === '後餘額',
    t4.headers.join(' | '));

  assert('T4.2 Full History: shows more rows than snippet',
    t4.rowCount > 10, `${t4.rowCount} rows (should be > 10)`);

  // Math check on full history
  let t4MathOk = true;
  for (const r of t4.rowData) {
    const b = parseFloat(r.before) || 0;
    const i = parseFloat(r.issued) || 0;
    const u = parseFloat(r.used) || 0;
    const a = parseFloat(r.after) || 0;
    if (Math.abs(a - (b + i - u)) > 0.01) t4MathOk = false;
  }
  assert('T4.3 Full History: balance math correct for all rows', t4MathOk);

  // Continuity on full history
  let t4ContOk = true;
  for (let i = 0; i < t4.rowData.length - 1; i++) {
    if (t4.rowData[i].before !== t4.rowData[i + 1].after) t4ContOk = false;
  }
  assert('T4.4 Full History: balance continuity', t4ContOk);

  // ========================================
  // TEST 5: Edge Cases
  // ========================================
  // 5a: Large numbers (loyalty card 17 has values like 494500)
  const hasLargeNum = t1.rowData.some(r => parseFloat(r.after) > 1000000);
  assert('T5.1 Edge: large numbers display correctly', hasLargeNum,
    `Max after: ${Math.max(...t1.rowData.map(r => parseFloat(r.after) || 0))}`);

  // 5b: First transaction's balance_before should be 0
  // Get the earliest (last row in DESC order on full history)
  const lastRow = t4.rowData[t4.rowData.length - 1];
  assert('T5.2 Edge: first transaction starts from 0',
    parseFloat(lastRow?.before) === 0,
    `First tx before: ${lastRow?.before}`);

  // 5c: Check for negative balances in ewallet
  const hasNegative = t2.rowData.some(r => parseFloat(r.after) < 0);
  assert('T5.3 Edge: negative balances render correctly', hasNegative || t2.rowCount === 0,
    'Negative balance display check');

  // 5d: Zero values - cells with no issued/used should be empty
  const emptyUsedCells = t1.rowData.filter(r => r.used === '');
  assert('T5.4 Edge: empty used cells when value is 0',
    emptyUsedCells.length > 0, `${emptyUsedCells.length} empty used cells`);

  // ========================================
  // TEST 6: CSS Styling
  // ========================================
  await page.goto('http://localhost:9100/my/member-center/loyalty/17');
  await page.waitForLoadState('networkidle');

  const t6 = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('tbody tr')];
    if (rows.length === 0) return { beforeMuted: false, afterBold: false, issuedGreen: false, usedRed: false };
    const firstRow = rows[0];
    const beforeCell = firstRow.cells[3];
    const afterCell = firstRow.cells[6];
    const issuedSpan = firstRow.cells[4]?.querySelector('span');
    const usedSpan = firstRow.cells[5]?.querySelector('span');
    return {
      beforeMuted: beforeCell?.classList?.contains('text-muted'),
      afterBold: afterCell?.classList?.contains('fw-bold'),
      issuedGreen: issuedSpan?.classList?.contains('text-success') || false,
      usedRed: usedSpan?.classList?.contains('text-danger') || !usedSpan, // no span if empty
    };
  });

  assert('T6.1 Style: 前餘額 has text-muted class', t6.beforeMuted);
  assert('T6.2 Style: 後餘額 has fw-bold class', t6.afterBold);
  assert('T6.3 Style: 增加 has text-success class', t6.issuedGreen);
  assert('T6.4 Style: 使用 has text-danger class (or empty)', t6.usedRed);

  return TEST_RESULTS;
}

// Export for use with playwright-cli run-code
module.exports = { runTests, TEST_RESULTS };
