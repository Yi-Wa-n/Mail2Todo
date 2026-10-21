const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app/web/app.js'), 'utf8');
let resolve, reject, calls = 0;
let pending = new Promise((yes, no) => { resolve = yes; reject = no; });
function element(tag, text) {
  return {tag, text, dataset: {}, children: [], append(...items) {this.children.push(...items);}, replaceChildren(...items) {this.children = items;}, setAttribute() {}};
}
const context = vm.createContext({
  api: () => {calls++; return pending;}, el: element, button: () => element('button'),
  tell: () => {}, fmt: () => '', renderContent: () => {},
});
vm.runInContext(`let contentRevision=1, settingsRequest=null, settingsCache=null, page='settings';
${source.slice(source.indexOf('function loadSettings('), source.indexOf('\nlet data ='))}
${source.slice(source.indexOf('async function renderSettings('), source.indexOf('\n$("#refresh").onclick'))}`, context);
(async () => {
  const main = element('main'); context.main = main;
  const first = vm.runInContext('renderSettings(main, 1)', context);
  assert.equal(main.children[0].text, '加载设置…');
  vm.runInContext("page='emails'; contentRevision=2; main.replaceChildren(); page='settings'; contentRevision=3", context);
  const second = vm.runInContext('renderSettings(main, 3)', context);
  assert.equal(calls, 1, 'rapid return shares in-flight request');
  resolve({model:'test', fallback:'auto', effort:'low', schedule:{installed:false}});
  await Promise.all([first, second]);
  assert.equal(main.children.filter(e=>e.tag==='form').length, 1, 'stale result cannot duplicate form');
  vm.runInContext('contentRevision=4; main.replaceChildren()', context);
  await vm.runInContext('renderSettings(main, 4)', context);
  assert.equal(calls, 1, 'revisit uses loaded settings');
  vm.runInContext('settingsCache=null; contentRevision=5; main.replaceChildren()', context);
  pending = new Promise((yes,no) => {resolve=yes;reject=no;});
  const staleFailure = vm.runInContext('renderSettings(main, 5)', context);
  vm.runInContext("page='emails'; contentRevision=6; main.replaceChildren(el('div','emails'))", context);
  reject(Error('failed')); await staleFailure;
  assert.equal(main.children[0].text, 'emails', 'stale failure cannot alter next page');
  console.log('设置检查通过：请求合并、过期响应隔离、缓存及切页失败隔离');
})().catch(error => {console.error(error);process.exitCode=1;});
