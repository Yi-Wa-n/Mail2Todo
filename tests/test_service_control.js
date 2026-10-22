const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../app/web/app.js'),'utf8');
const buttons = {'#restart-service':{}, '#exit-service':{}};
let dirty = false, calls = 0, transitions = 0, failure = false;
const messages = [];
const context = vm.createContext({
  document:{querySelector:()=>dirty}, $:id=>buttons[id], tell:message=>messages.push(message),
  api:async()=>{calls++;if(failure)throw Error('正在扫描或保存');return {action:'restart',instance:'old'};},
});
context.transition = () => {transitions++;};
vm.runInContext(`let pendingWrites=0, controlRequested=false, serviceChanging=false, dirtyNotes=false, data={job:{running:false}};
${source.slice(source.indexOf('function hasUnsavedChanges('),source.indexOf('$("#refresh").onclick'))}
serviceTransition=async()=>transition();`,context);
(async()=>{
  vm.runInContext('pendingWrites=1',context);
  await vm.runInContext('controlService("exit")',context);
  assert.equal(calls,0);assert.match(messages.at(-1),/保存/);
  vm.runInContext('pendingWrites=0;data.job.running=true',context);
  await vm.runInContext('controlService("exit")',context);
  assert.equal(calls,0);assert.match(messages.at(-1),/扫描/);
  vm.runInContext('data.job.running=false',context);dirty=true;
  await vm.runInContext('controlService("restart")',context);
  assert.equal(calls,0);assert.match(messages.at(-1),/未保存/);
  dirty=false;failure=true;
  await vm.runInContext('controlService("restart")',context);
  assert.equal(transitions,0);assert.equal(buttons['#restart-service'].disabled,false);
  failure=false;
  await vm.runInContext('controlService("restart")',context);
  assert.equal(calls,2);assert.equal(transitions,1);
  let release;
  const apiContext=vm.createContext({fetch:()=>new Promise(resolve=>release=resolve)});
  vm.runInContext(`let pendingWrites=0,controlRequested=false,serviceChanging=false; const token='test';
${source.slice(source.indexOf('async function api('),source.indexOf('async function refresh('))}`,apiContext);
  const pending=vm.runInContext('api("/api/settings",{})',apiContext);
  assert.equal(vm.runInContext('pendingWrites',apiContext),1);
  release({ok:true,json:async()=>({})});await pending;
  assert.equal(vm.runInContext('pendingWrites',apiContext),0);
  const rejected=vm.runInContext('api("/api/settings",{})',apiContext);
  release({ok:false,json:async()=>({error:'failed'})});await assert.rejects(rejected);
  assert.equal(vm.runInContext('pendingWrites',apiContext),0);
  console.log('服务控制检查通过：扫描、保存、未保存改动保护及请求计数恢复');
})().catch(error=>{console.error(error);process.exitCode=1;});
