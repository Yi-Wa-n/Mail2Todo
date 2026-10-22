const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app/web/app.js'), 'utf8');
let input, renders = 0, focus = 0, caret;
const timers = new Map(); let timerId = 0;
function flush() { for (const [id, fn] of [...timers]) {timers.delete(id); fn();} }
function el(tag, text, cls) {
  const node = {children: [], append(...items) { this.children.push(...items); }};
  if (cls === 'search') input = node;
  return node;
}
const context = vm.createContext({el,
  setTimeout(fn) {timers.set(++timerId, fn); return timerId;},
  clearTimeout(id) {timers.delete(id);},
  $: () => ({focus() {focus++;}, setSelectionRange(start) {caret = start;}}),
  renderContent() {renders++;},
});
vm.runInContext(`let query='', emailPage=3, page='test';
${source.slice(source.indexOf('function controls('), source.indexOf('function matches('))}
controls('测试');`, context);
input.oncompositionstart();
input.value = 'hui'; input.selectionStart = 3;
input.oninput({isComposing: true});
input.value = '汇'; input.oninput({isComposing: true});
assert.equal(renders, 0, '组词期间不能重建输入框');
input.value = '汇川'; input.selectionStart = 2;
input.oncompositionend();
flush();
assert.equal(renders, 1);
assert.equal(vm.runInContext('query', context), '汇川');
assert.equal(vm.runInContext('emailPage', context), 1);
assert.equal(caret, 2); assert.equal(focus, 1);
input.oninput({isComposing: false});
flush();
assert.equal(renders, 1, '选字后的尾随 input 不应重复刷新');
input.value = '南京银行'; input.selectionStart = 4;
input.oninput({isComposing: false});
flush();
assert.equal(renders, 2, '粘贴中文立即搜索');
input.value = ''; input.selectionStart = 0; input.oninput({});
flush();
assert.equal(vm.runInContext('query', context), '');
input.value = 'AI'; input.selectionStart = 2; input.oninput({});
flush();
assert.equal(vm.runInContext('query', context), 'AI');
assert.equal(renders, 4);
let prevented = 0;
input.onkeydown({key:'Enter', preventDefault() {prevented++;}});
assert.equal(renders, 5, '回车应主动重新搜索');
assert.equal(prevented, 1);
input.oncompositionstart();
input.onkeydown({key:'Enter', isComposing:true});
assert.equal(renders, 5, '输入法选字回车不能触发搜索');
input.value = 'bi'; input.oncompositionend();
input.value = '笔试'; input.selectionStart = 2;
input.oninput({isComposing:false});
flush();
assert.equal(vm.runInContext('query', context), '笔试', '读取选字完成后的最终文字');
assert.equal(renders, 6, '输入完整笔试后无需删除即可筛选');
input.value = '笔'; input.selectionStart = 1; input.oninput({}); flush();
assert.equal(vm.runInContext('query', context), '笔');
vm.runInContext(source.slice(source.indexOf('function matches('), source.indexOf('function visibleSteps(')), context);
context.item = {company:'示例公司', category:'AI面试', notes:'已确认参加'};
for (const term of ['面试', ' AI面试 ', '已确认', '示例']) {
  context.term = term;
  assert.equal(vm.runInContext('query=term; matches(item)', context), true);
}
assert.equal(vm.runInContext("query='无关关键词'; matches(item)", context), false);
console.log('搜索检查通过：中文组词、选字、尾随事件、粘贴、清空及英文输入');
