// 直接以 Node 抓取 mh.wdf.ink（Anubis 反爬）词条并转 Markdown。
// 流程：GET 页面 → 若 418 则解析挑战 → 自行爆破 PoW → 带 nonce 请求 pass-challenge 拿 cookie → 重新 GET。
// cookie 缓存在 .tools/cookies.json，后续词条免解 PoW。
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const cheerio = require('cheerio');
const TurndownService = require('turndown');
const { gfm } = require('turndown-plugin-gfm');

const ORIGIN = 'https://mh.wdf.ink';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36';
const JAR_FILE = '.tools/cookies.json';
const log = (...a) => console.error('[wiki]', ...a);

const target = process.argv[2] || `${ORIGIN}/wiki/%E6%97%A5%E6%9C%AC%E9%BA%BB%E5%B0%86`;
const outMd = process.argv[3] || 'docs/日本麻将.md';
const outHtml = process.argv[4] || '.tools/page.html';

// ---- cookie jar ----
let jar = {};
try { jar = JSON.parse(await fs.readFile(JAR_FILE, 'utf8')); } catch { /* 首次运行 */ }
const cookieHeader = () => Object.entries(jar).map(([k, v]) => `${k}=${v}`).join('; ');

async function req(url, opts = {}) {
  const headers = { 'user-agent': UA, accept: 'text/html,application/xhtml+xml', 'accept-language': 'zh-CN,zh;q=0.9', ...opts.headers };
  if (Object.keys(jar).length) headers.cookie = cookieHeader();
  const res = await fetch(url, { ...opts, headers, redirect: 'manual' });
  for (const raw of res.headers.getSetCookie?.() ?? []) {
    const [pair] = raw.split(';');
    const i = pair.indexOf('=');
    if (i > 0) jar[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
  }
  return res;
}

// ---- Anubis PoW：hex(sha256(randomData + nonce)) 需有 difficulty 个前导 0 ----
function solvePow(randomData, difficulty) {
  const prefix = '0'.repeat(difficulty);
  const t0 = Date.now();
  for (let n = 0; n < 5e8; n++) {
    if (createHash('sha256').update(randomData + n).digest('hex').startsWith(prefix)) {
      return { nonce: n, elapsed: Date.now() - t0 };
    }
  }
  throw new Error('PoW 未在 5e8 次内解出');
}

async function fetchPage(url) {
  let res = await req(url);
  if (res.status !== 418) return res;

  const html = await res.text();
  const m = html.match(/<script id="anubis_challenge" type="application\/json">([\s\S]*?)<\/script>/);
  if (!m) throw new Error('418 但未找到 anubis_challenge 脚本');
  const { challenge, rules } = JSON.parse(m[1]);
  log(`Anubis 挑战 difficulty=${rules.difficulty} id=${challenge.id}`);

  const { nonce, elapsed } = solvePow(challenge.randomData, rules.difficulty);
  const hash = createHash('sha256').update(challenge.randomData + nonce).digest('hex');
  log(`PoW 解出 nonce=${nonce} hash=${hash} (${elapsed}ms)`);

  const pass = new URL('/.within.website/x/cmd/anubis/api/pass-challenge', ORIGIN);
  pass.searchParams.set('id', challenge.id);
  pass.searchParams.set('response', hash);
  pass.searchParams.set('nonce', String(nonce));
  pass.searchParams.set('redir', url);
  pass.searchParams.set('elapsedTime', String(Math.max(elapsed, 1000) + 1200));

  res = await req(pass.toString());
  log(`pass-challenge → ${res.status} ${res.headers.get('location') ?? ''}`);
  if (res.status !== 302 && res.status !== 303) throw new Error('pass-challenge 未通过: ' + res.status);

  await fs.mkdir(path.dirname(JAR_FILE), { recursive: true });
  await fs.writeFile(JAR_FILE, JSON.stringify(jar, null, 2));

  res = await req(new URL(res.headers.get('location') || url, ORIGIN).toString());
  return res;
}

// ---- 抓取 ----
const res = await fetchPage(target);
log(`${target} → ${res.status} ${res.headers.get('content-type') ?? ''}`);
if (!res.ok) throw new Error('抓取失败，状态 ' + res.status);
const html = await res.text();
log('HTML', html.length, '字符');

const $ = cheerio.load(html);
const title = $('h1.firstHeading').text().trim() || $('title').text().replace(/\s*-\s*奇葩栖息地\s*$/, '').trim();
const contentHtml = ($('.mw-parser-output').first().length ? $('.mw-parser-output').first() : $('#mw-content-text').first()).html() || $('body').html();
log('标题：', title, '| 正文 HTML', contentHtml.length, '字符');

await fs.mkdir(path.dirname(outHtml), { recursive: true });
await fs.writeFile(outHtml, contentHtml, 'utf8');

// ---- HTML → Markdown ----
const $c = cheerio.load(contentHtml, null, false);
$c('.mw-editsection, style, script, sup.reference, .reference, .toc, #toc, .navbox, .metadata, .mw-empty-elt, .printfooter, .catlinks, link, meta').remove();
$c('a[href]').each((_, el) => {
  const href = $c(el).attr('href');
  if (href && !/^(https?:|mailto:|#)/.test(href)) $c(el).attr('href', new URL(href, ORIGIN).toString());
});
$c('img[src]').each((_, el) => {
  const src = $c(el).attr('src');
  if (src && !/^https?:/.test(src)) $c(el).attr('src', new URL(src, ORIGIN).toString());
});

// 表格策略：GFM 表达不了合并单元格/参差行/caption，硬转会错行。
// 判定：含 caption、rowspan/colspan、各行格数不一致、单元格含块级元素 → 整块保留 HTML；否则转 GFM。
$c('table').each((_, el) => {
  const $t = $c(el);
  const counts = $t.find('tr').toArray().map((tr) => $c(tr).children('th,td').length);
  const ragged = new Set(counts).size > 1;
  const spanned = $t.find('[rowspan],[colspan]').length > 0;
  const blocky = $t.find('p,ul,ol,dl,div,table').length > 0;
  const hasCaption = $t.children('caption').length > 0;
  if (ragged || spanned || blocky || hasCaption) $t.attr('data-md-keep', '1');
});

// 公式：该 wiki 把 LaTeX 当纯文本输出，两种写法都要救回来，否则会被 turndown 转义成 \\\[...\\\]。
// 包成 <span class="md-math"> 交给自定义规则原样输出 $...$（规则返回值不参与转义）。
const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const MATH_RE = /\[math\]\\displaystyle\{([\s\S]*?)\}\[\/math\]|\\\[([\s\S]*?)\\\]/g;
const seenTextNodes = $c('*').contents().toArray().filter((n) => n.type === 'text');
for (const node of seenTextNodes) {
  const text = node.data;
  if (!text || !(text.includes('[math]') || text.includes('\\['))) continue;
  MATH_RE.lastIndex = 0;
  let out = '';
  let last = 0;
  for (let m; (m = MATH_RE.exec(text)); ) {
    const tex = (m[1] ?? m[2]).trim();
    out += escapeHtml(text.slice(last, m.index)) + `<span class="md-math">${escapeHtml(tex)}</span>`;
    last = m.index + m[0].length;
  }
  out += escapeHtml(text.slice(last));
  $c(node).replaceWith(out);
}

const td = new TurndownService({ headingStyle: 'atx', hr: '---', bulletListMarker: '-', codeBlockStyle: 'fenced', emDelimiter: '*', linkStyle: 'inlined' });
td.use(gfm);
td.addRule('md-math', {
  filter: (node) => node.nodeName === 'SPAN' && node.getAttribute('class') === 'md-math',
  replacement: (_content, node) => ` $${node.textContent.trim()}$ `,
});
// 保留 HTML 的复杂表（自定义规则优先级高于 gfm 插件）
td.addRule('keep-html-table', {
  filter: (node) => node.nodeName === 'TABLE' && node.getAttribute('data-md-keep') === '1',
  replacement: (_content, node) => {
    const clone = node.cloneNode(true);
    clone.removeAttribute('data-md-keep');
    clone.removeAttribute('class');
    for (const el of clone.querySelectorAll('[data-md-keep]')) el.removeAttribute('data-md-keep');
    // 保留 HTML 的表里没有 GFM 规则接手，把公式标记还原成可读的 LaTeX 文本
    return '\n\n' + clone.outerHTML.replace(/<span class="md-math">([\s\S]*?)<\/span>/g, '\\($1\\)') + '\n\n';
  },
});
// 单元格内的换行必须是内联 <br>，否则换行会把 GFM 表格行截断
td.addRule('br-in-table', {
  filter: (node) => {
    if (node.nodeName !== 'BR') return false;
    for (let p = node.parentNode; p; p = p.parentNode) if (p.nodeName === 'TABLE') return true;
    return false;
  },
  replacement: () => '<br>',
});

const md = td.turndown($c.html()).replace(/\n{3,}/g, '\n\n').trim();
const header = `<!-- 来源: ${target} （奇葩栖息地 wiki） | 抓取: ${new Date().toISOString()} -->\n\n# ${title}\n\n`;
await fs.mkdir(path.dirname(outMd), { recursive: true });
await fs.writeFile(outMd, header + md + '\n', 'utf8');

log(`写出 ${outMd}：${md.length} 字符 | ${(md.match(/^#{1,6} /gm) || []).length} 标题 | ${(md.match(/^\|/gm) || []).length} 表格行`);
