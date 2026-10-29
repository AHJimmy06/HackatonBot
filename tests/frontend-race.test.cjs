const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('async function loadDetail()');
const end = source.indexOf('function linkText', start);
const pending = [];
const rendered = [];
const context = vm.createContext({selected: '4', detailSeq: 0, detail: null,
  api: () => new Promise(resolve => pending.push(resolve)),
  renderThread: () => rendered.push(context.detail),
});
vm.runInContext(source.slice(start, end), context);
(async () => {
  const older = vm.runInContext('loadDetail()', context);
  const newer = vm.runInContext('loadDetail()', context);
  pending[1]({version: 'new'}); await newer;
  pending[0]({version: 'old'}); await older;
  assert.equal(context.detail.version, 'new');
  assert.equal(rendered.length, 1);
  const switching = vm.runInContext('loadDetail()', context);
  context.selected = '5';
  pending[2]({version: 'wrong-chat'}); await switching;
  assert.equal(rendered.length, 1);
  console.log('Frontend async: 2 scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
