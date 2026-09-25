// Trích NHÃN nhìn thấy được của một trang (tiêu đề, nút, liên kết, ô nhập) để gợi ý flow UI. Chạy bằng: node dom_labels.mjs <url> <thư mục node_modules>
// Chỉ ĐỌC: mở trang, chờ mạng yên, lấy văn bản hiển thị. Không bấm, không gõ, không đọc GIÁ TRỊ của ô nhập (chỉ nhãn/placeholder), không lấy mã nguồn/HTML.
// stdout = một JSON; exit 0 = xong, 2 = tham số sai, 77 = không mở được trình duyệt, 1 = lỗi khác.
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const [url, modules] = process.argv.slice(2);
if (!url || !modules) { console.error('dùng: node dom_labels.mjs <url> <node_modules>'); process.exit(2); }
if (!/^https?:\/\//.test(url)) { console.error('chỉ nhận URL http(s)'); process.exit(2); }

let chromium;
try { ({ chromium } = await import(pathToFileURL(join(modules, 'playwright', 'index.mjs')).href)); }
catch (error) { console.error('không nạp được playwright từ ' + modules + ': ' + String(error).split('\n')[0]); process.exit(77); }

let browser;
try { browser = await chromium.launch({ args: ['--no-sandbox', '--disable-gpu'] }); }
catch (error) { console.error('không mở được Chromium: ' + String(error).split('\n')[0]); process.exit(77); }

const LIMIT = 30;      // số mục tối đa mỗi loại
const TEXT_MAX = 80;   // độ dài tối đa mỗi nhãn

try {
  const page = await (await browser.newContext()).newPage();
  page.on('dialog', (dialog) => dialog.dismiss());
  await page.goto(url, { waitUntil: 'networkidle', timeout: 25000 });
  const data = await page.evaluate(({ LIMIT, TEXT_MAX }) => {
    const clean = (text) => (text || '').replace(/\s+/g, ' ').trim().slice(0, TEXT_MAX);
    const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
    const unique = (items) => [...new Set(items.filter(Boolean))].slice(0, LIMIT);
    const texts = (selector) => unique([...document.querySelectorAll(selector)].filter(visible).map((el) => clean(el.innerText || el.getAttribute('aria-label'))));
    const labelOf = (el) => {
      const byFor = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
      return clean(el.getAttribute('aria-label') || (byFor && byFor.innerText) || (el.closest('label') && el.closest('label').innerText) || '');
    };
    const inputs = [...document.querySelectorAll('input, textarea, select')]
      .filter((el) => visible(el) && !['hidden', 'password'].includes(el.type))
      .map((el) => ({ kind: el.tagName.toLowerCase() + (el.type && el.tagName === 'INPUT' ? ':' + el.type : ''), label: labelOf(el), placeholder: clean(el.getAttribute('placeholder')) }))
      .filter((item) => item.label || item.placeholder).slice(0, LIMIT);
    return {
      title: clean(document.title),
      headings: texts('h1, h2, h3'),
      buttons: texts('button, [role=button], input[type=submit], input[type=button]'),
      links: texts('a[href]'),
      tabs: texts('[role=tab]'),
      inputs,
    };
  }, { LIMIT, TEXT_MAX });
  const parsed = new URL(url);
  console.log(JSON.stringify({ page: parsed.origin + parsed.pathname + parsed.hash, ...data }));
} catch (error) {
  console.error('lỗi khi đọc trang: ' + String(error).split('\n')[0]);
  process.exitCode = 1;
} finally {
  await browser.close();
}
