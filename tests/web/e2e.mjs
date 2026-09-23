// E2E của giao diện web bằng Chromium thật. Được tests/test_web.py chạy; exit 0 = đạt, 77 = không có trình duyệt (skip), khác = lỗi.
// Env: BASE_URL, EMAIL, PASSWORD. Kiểm: luồng chính, XSS (intent của task chứa <img onerror>), vi phạm CSP, huỷ job, đăng xuất.
import { chromium } from 'playwright';

const { BASE_URL, EMAIL, PASSWORD } = process.env;
const fail = (message) => { console.error('E2E FAIL: ' + message); process.exitCode = 1; };
const step = (name) => console.log('· ' + name);

let browser;
try { browser = await chromium.launch(); }
catch (error) { console.error('không mở được Chromium: ' + String(error).split('\n')[0]); process.exit(77); }

const page = await (await browser.newContext()).newPage();
const problems = [];
page.on('console', (msg) => {
  if (/status of (401|403|422)/.test(msg.text())) return; // phản hồi lỗi KỲ VỌNG (đăng nhập sai, sai mật khẩu hiện tại, validate); 404/500 và CSP vẫn tính là lỗi
  if (msg.type() === 'error' || /content security policy/i.test(msg.text())) problems.push('console: ' + msg.text());
});
page.on('pageerror', (error) => problems.push('pageerror: ' + error.message));
page.on('dialog', (dialog) => { problems.push('dialog xuất hiện: ' + dialog.message()); dialog.dismiss(); });

try {
  step('đăng nhập sai bị từ chối');
  await page.goto(BASE_URL);
  await page.fill('#email', EMAIL);
  await page.fill('#password', 'sai-mat-khau-1');
  await page.click('button[type=submit]');
  await page.waitForSelector('.alert:not([hidden])');

  step('đăng nhập đúng, thấy project và người dùng');
  await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForSelector('#project-select');
  if ((await page.textContent('#whoami')) !== EMAIL) throw new Error('whoami sai');
  if (!(await page.textContent('#project-select')).includes('Demo')) throw new Error('không thấy project Demo');

  step('nội dung từ SUT hiển thị như văn bản, không chạy script (XSS)');
  await page.click('#run-form details summary');
  const labelText = await page.textContent('#run-form details');
  if (!labelText.includes('<img src=x onerror="window.__xss=1">')) throw new Error('intent phải hiện nguyên văn: ' + labelText);
  if ((await page.locator('#run-form img').count()) !== 0) throw new Error('có thẻ img được dựng từ dữ liệu SUT');
  await page.waitForTimeout(300);
  if ((await page.evaluate(() => window.__xss)) !== undefined) throw new Error('XSS: onerror đã chạy');

  step('tạo job chỉ với suite core');
  for (const name of ['extra', 'slow']) await page.uncheck(`input[name=suite][value=${name}]`);
  await page.click('#submit-job');
  await page.waitForSelector('#detail');
  await page.waitForSelector('#detail[data-status="succeeded"]', { timeout: 90000 });
  if (!(await page.textContent('#detail')).includes('PASS')) throw new Error('không thấy verdict PASS');
  for (const id of ['t-1', 't-2']) await page.waitForSelector(`#detail .card[data-task-id="${id}"][data-status="pass"]`);
  if ((await page.locator('#detail .card[data-task-id="t-9"]').count()) !== 0) throw new Error('t-9 không thuộc suite đã chọn');

  step('không có chữ null/undefined lọt ra giao diện');
  const stray = await page.evaluate(() => {  // nút văn bản TRẦN là chữ null/undefined/[object Object] (vd. Node.append(null))
    const found = [], walker = document.createTreeWalker(document.getElementById('root'), NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (['null', 'undefined', '[object Object]', 'NaN'].includes(n.nodeValue.trim())) found.push(n.nodeValue.trim());
    return found;
  });
  if (stray.length) throw new Error('giao diện in ra chữ rác: ' + stray.join(', '));

  step('artifact, report và log');
  const href = await page.getAttribute('#artifacts a', 'href');
  if (!href.startsWith('/api/v1/jobs/')) throw new Error('href artifact sai: ' + href);
  if (!(await page.textContent('#artifacts')).includes('report.json')) throw new Error('thiếu report.json');
  if (!(await page.textContent('#log')).includes('QC Gate Report')) throw new Error('log không có report');
  await page.waitForSelector('#jobs-table tr.job .badge[data-status="succeeded"]');

  if (process.env.SHOT_OK) await page.screenshot({ path: process.env.SHOT_OK, fullPage: true }); // ảnh minh hoạ tuỳ chọn

  step('tạo job chạy lâu rồi huỷ');
  await page.uncheck('input[name=suite][value=core]');
  await page.check('input[name=suite][value=slow]');
  await page.click('#submit-job');
  await page.waitForSelector('#detail[data-status="running"]', { timeout: 60000 });
  await page.click('#cancel-job');
  await page.waitForSelector('#detail[data-status="cancelled"]', { timeout: 60000 });

  step('lỗi validate hiện thông điệp của API');
  await page.route('**/api/v1/projects/demo/jobs', (route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: 'suite core task t-2: lane=discovery nhưng nằm trong blocking_suites' }) })
    : route.continue());
  await page.click('#submit-job');
  await page.waitForSelector('#form-error');
  if (!(await page.textContent('#form-error')).includes('blocking_suites')) throw new Error('UI không hiện thông điệp lỗi của API');
  await page.unroute('**/api/v1/projects/demo/jobs');

  step('đổi mật khẩu: sai mật khẩu hiện tại bị từ chối');
  await page.click('text=Đổi mật khẩu');
  await page.fill('#pw-current', 'khong-dung-abc');
  await page.fill('#pw-next', 'mat-khau-moi-12345');
  await page.click('dialog button[type=submit]');
  await page.waitForSelector('dialog .alert');
  await page.click('dialog button[type=button]');

  step('đăng xuất, tải lại vẫn ở màn đăng nhập');
  await page.click('#logout');
  await page.waitForSelector('#email');
  await page.reload();
  await page.waitForSelector('#email');
  if ((await page.locator('#project-select').count()) !== 0) throw new Error('phiên chưa bị thu hồi');

  if (problems.length) throw new Error('CSP/console/dialog:\n' + problems.join('\n'));
  console.log('E2E OK');
} catch (error) {
  fail(String(error && error.stack ? error.stack : error));
  try { await page.screenshot({ path: process.env.SHOT || 'e2e-failure.png', fullPage: true }); } catch (_) { /* bỏ qua */ }
  if (problems.length) console.error('problems:\n' + problems.join('\n'));
} finally {
  await browser.close();
}
