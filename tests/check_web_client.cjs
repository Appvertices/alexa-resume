// Browser-client logic against the real loopback server using a small DOM stub.
// This does NOT exercise layout, keyboard behavior, accessibility or a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const url = new URL(process.argv[2]);
assert.equal(url.hostname, '127.0.0.1');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
class Element {
  constructor(tag, id) { Object.assign(this, {tag, id, textContent: '', value: '', dataset: {}, children: [], disabled: false, checked: false}); }
  append(el) { this.children.push(el); }
  replaceChildren() { this.children = []; }
}
function client(storage = new Map(), fetcher = fetch) {
  const elements = new Map();
  for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) elements.set(match[2], new Element(match[1], match[2]));
  const commands = [...html.matchAll(/<button\b[^>]*data-command="([^"]+)"[^>]*>/g)].map(match => { const e = new Element('button');e.dataset.command = match[1];return e; });
  const controls = [...elements.values()].filter(e => ['button','input','select'].includes(e.tag)).concat(commands);
  const context = vm.createContext({document: {
    getElementById: id => elements.get(id), createElement: tag => new Element(tag),
    querySelectorAll: selector => selector === '[data-command]' ? commands : controls
  }, sessionStorage: {getItem: key => storage.get(key), setItem: (key,value) => storage.set(key,value), removeItem: key => storage.delete(key)},
  fetch: (route, options) => {assert.equal(route, '/mcp'); return fetcher(new URL(route,url), options);},
  setTimeout, Blob, URL});
  vm.runInContext(source, context);
  return {context, elements, storage, read: expression => vm.runInContext(expression, context)};
}
async function loaded(c) {
  const deadline = Date.now() + 3000;
  while (!c.read('ready')) {
    if(c.elements.get('error').textContent) throw Error(c.elements.get('error').textContent);
    assert.ok(Date.now() < deadline, 'Client initialization timed out');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
(async () => {
  let release;
  const gate = new Promise(resolve => release = resolve);
  const pending = client(new Map(), async (...args) => {await gate;return fetch(...args);});
  assert.equal(pending.elements.get('start').disabled, true);
  await pending.read('turn("done")');
  release(); await loaded(pending);
  assert.equal(pending.read('state.completed'), 0);
  console.log('PASS: controls and mutations wait for initialization');

  const a = client(); await loaded(a); await a.read('turn("done")');
  assert.equal(a.read('state.completed'), 1);
  const reload = client(a.storage); await loaded(reload);
  assert.equal(reload.read('state.completed'), 1);
  assert.equal(reload.read('mcpSession'), a.read('mcpSession'));
  console.log('PASS: same-tab reload preserves session and progress');

  const b = client(); await loaded(b);
  assert.equal(b.read('state.completed'), 0);
  assert.notEqual(b.read('mcpSession'), a.read('mcpSession'));
  console.log('PASS: independent client has independent progress');

  const deleted = await fetch(new URL('/mcp', url), {method:'DELETE',headers:{'MCP-Session-Id':reload.read('mcpSession'),'MCP-Protocol-Version':'2025-11-25'}});
  assert.equal(deleted.status, 200);
  await reload.read('turn("done")');
  assert.equal(reload.read('ready'), false);
  assert.equal(reload.read('state.completed'), 1);
  assert.equal(reload.storage.has('resume-mcp-session'), false);
  assert.equal(reload.elements.get('start').disabled, true);
  assert.equal(reload.elements.get('save-checkpoint').disabled, false);
  assert.match(reload.elements.get('error').textContent, /failed action was not retried/);
  console.log('PASS: expired session clears token, retains checkpoint, blocks writes without replay');

  let offline = false;
  const c = client(new Map(), (...args) => { if(offline) return Promise.reject(Error('Simulated local server offline.')); return fetch(...args); });
  await loaded(c); offline = true;
  await c.read('turn("done")');
  assert.equal(c.read('state.completed'), 0);
  assert.equal(c.read('busy'), false);
  assert.match(c.elements.get('error').textContent, /offline/);
  console.log('PASS: failed action and refresh resolve without an unhandled rejection');
  console.log('5 client-logic checks passed; rendered browser QA NOT RUN.');
})().catch(error => {console.error(error);process.exitCode=1;});
