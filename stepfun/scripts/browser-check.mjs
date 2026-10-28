/**
 * Headless browser driver: opens the app through CDP, imports the sample corpus,
 * runs a query and the ×10 benchmark, then reports every timing it can read.
 *
 *   node _tmp/cdp/driver.mjs --url http://127.0.0.1:8787/ --port 9333 --out results/browser-wasm.json
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const args = {};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, "")] = process.argv[i + 1];
const CDP = args.port ?? "9333";
const URL_ = args.url ?? "http://127.0.0.1:8787/";
const OUT = args.out ?? "results/browser.json";
const QUERY = args.query ?? "刚导入的文档搜不到是什么原因";

const log = (...a) => console.log("[driver]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function newTab() {
  const res = await fetch(`http://127.0.0.1:${CDP}/json/new?${encodeURIComponent(URL_)}`, { method: "PUT" });
  if (!res.ok) throw new Error(`cannot open tab: ${res.status}`);
  return res.json();
}

function connect(wsUrl) {
  return new Promise((resolvePromise, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let seq = 0;
    ws.onopen = () => resolvePromise({ send, close: () => ws.close() });
    ws.onerror = (e) => reject(new Error(`ws error: ${e.message ?? e}`));
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        pending.get(msg.id)(msg);
        pending.delete(msg.id);
      }
    };
    function send(method, params = {}) {
      const id = ++seq;
      return new Promise((res) => {
        pending.set(id, res);
        ws.send(JSON.stringify({ id, method, params }));
      });
    }
  });
}

async function main() {
  const tab = await newTab();
  log("tab:", tab.id, tab.url);
  const cdp = await connect(tab.webSocketDebuggerUrl);
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");

  const evaluate = async (expression) => {
    const res = await cdp.send("Runtime.evaluate", {
      expression: `(async () => { ${expression} })()`,
      awaitPromise: true,
      returnByValue: true,
      timeout: 240000,
    });
    if (res.result?.exceptionDetails) {
      throw new Error(res.result.exceptionDetails.exception?.description ?? "eval failed");
    }
    return res.result?.result?.value;
  };

  // Wait for the app to boot (backend badge appears).
  const badge = await evaluate(`
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      const b = [...document.querySelectorAll('.badge')].map(x => x.textContent.trim());
      const has = b.find(t => /WebGPU|WASM/.test(t));
      if (has && !/加载中/.test(has)) return { badge: has, all: b };
      await new Promise(r => setTimeout(r, 400));
    }
    return { badge: null, all: [...document.querySelectorAll('.badge')].map(x => x.textContent.trim()), body: document.body.innerText.slice(0, 400) };
  `);
  log("badge:", JSON.stringify(badge));

  const health = await evaluate(`return await (await fetch('/api/health')).json();`);
  log("health:", JSON.stringify(health.db));

  // Import the sample corpus.
  const clickLoad = await evaluate(`
    const btn = [...document.querySelectorAll('button')].find(b => b.textContent.includes('载入示例语料'));
    if (!btn) return 'no-button';
    btn.click();
    return 'clicked';
  `);
  log("load sample:", clickLoad);

  const ingest = await evaluate(`
    const deadline = Date.now() + 300000;
    let last = '';
    while (Date.now() < deadline) {
      const rows = [...document.querySelectorAll('.timings tr')].map(tr => tr.innerText.replace(/\\s+/g,' ').trim());
      const counts = [...document.querySelectorAll('.collection-list .count')].map(x => x.textContent.trim());
      const hint = document.querySelector('.import .hint')?.textContent?.trim() ?? '';
      last = 'counts=' + counts.join(',') + ' hint=' + hint;
      if (rows.some(r => r.includes('文档向量化')) && rows.some(r => r.includes('写入'))) {
        return { rows, counts, hint };
      }
      await new Promise(r => setTimeout(r, 500));
    }
    return { rows: [], counts: [], hint: 'TIMEOUT ' + last };
  `);
  log("ingest:", JSON.stringify(ingest).slice(0, 600));

  // Run a query.
  const queryResult = await evaluate(`
    const input = document.querySelector('.search-row input');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, ${JSON.stringify(QUERY)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 100));
    const btn = [...document.querySelectorAll('.search-row button')].find(b => b.textContent.trim() === '检索');
    btn.click();
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const items = [...document.querySelectorAll('.results li')];
      if (items.length && !document.querySelector('.bench')) return {
        meta: document.querySelector('.result-meta')?.innerText,
        top3: items.slice(0,3).map(li => li.querySelector('.title')?.textContent + ' :: ' + (li.querySelector('.score')?.textContent ?? '')),
        count: items.length,
      };
      await new Promise(r => setTimeout(r, 400));
    }
    return { error: 'timeout', body: document.body.innerText.slice(0, 600) };
  `);
  log("query:", JSON.stringify(queryResult).slice(0, 700));

  // ×10 benchmark.
  const bench = await evaluate(`
    const btn = [...document.querySelectorAll('.search-row button')].find(b => b.textContent.includes('基准'));
    if (!btn) return { error: 'no bench button' };
    btn.click();
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      const el = document.querySelector('.bench');
      if (el) return { text: el.innerText.replace(/\\s+/g,' ').trim() };
      await new Promise(r => setTimeout(r, 500));
    }
    return { error: 'timeout' };
  `);
  log("bench:", JSON.stringify(bench));

  const timings = await evaluate(`
    return [...document.querySelectorAll('.timings tr')].map(tr => {
      const td = tr.querySelectorAll('td');
      return { label: td[0]?.innerText.split('\\n')[0] ?? '', detail: td[0]?.querySelector('.detail')?.innerText ?? '', value: td[1]?.innerText ?? '' };
    });
  `);

  const ua = await evaluate(`return navigator.userAgent;`);
  const gpu = await evaluate(`
    if (!navigator.gpu) return 'no navigator.gpu';
    try { const a = await navigator.gpu.requestAdapter(); return a ? JSON.stringify(a.info ?? {}) : 'no adapter'; }
    catch (e) { return 'adapter error: ' + e.message; }
  `);

  // Second load: reload and read how long the model takes when cached.
  await cdp.send("Page.reload", { ignoreCache: false });
  const secondLoad = await evaluate(`
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      const rows = [...document.querySelectorAll('.timings tr')].map(tr => tr.innerText.replace(/\\s+/g,' ').trim());
      const load = rows.find(r => r.includes('模型加载'));
      const badge = [...document.querySelectorAll('.badge')].map(x => x.textContent.trim()).find(t => /WebGPU|WASM/.test(t));
      if (load && badge && !/加载中/.test(badge)) return { load, badge };
      await new Promise(r => setTimeout(r, 400));
    }
    return { load: null };
  `);
  log("second load:", JSON.stringify(secondLoad));

  const report = { when: new Date().toISOString(), url: URL_, ua, gpu, badge, health: health.db, ingest, query: queryResult, bench, secondLoad, timings };
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(report, null, 2), "utf8");
  log("wrote", OUT);

  cdp.close();
  await fetch(`http://127.0.0.1:${CDP}/json/close/${tab.id}`).catch(() => undefined);
  process.exit(0);
}

main().catch((err) => {
  console.error("[driver] FATAL", err?.stack ?? err);
  process.exit(1);
});
