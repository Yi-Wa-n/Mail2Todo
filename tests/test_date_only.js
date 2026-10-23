const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/web/app.js'),'utf8');
const context=vm.createContext({Date});
vm.runInContext(source.slice(source.indexOf('function fmt('),source.indexOf('function color(')),context);
assert.equal(vm.runInContext('fmt("2026-10-28")',context),'2026-10-28');
assert.equal(vm.runInContext('dayKey("2026-10-28")',context),'2026-10-28');
assert.equal(vm.runInContext('fmt("2026-10-28T14:30:00+08:00")',context),'2026-10-28 14:30');
function node(tag,text){return {tag,text:text||'',children:[],dataset:{},append(...items){this.children.push(...items);}};}
const render=vm.createContext({Date,month:'2026-10',el:node,button:(text)=>node('button',text),color:e=>e,
 checkbox:()=>node('input'),fmt:context.fmt,openStep(){},change(){},data:{emails:[]}});
vm.runInContext(source.slice(source.indexOf('function calendarDay('),source.indexOf('function captureCalendarPosition(')),render);
const text=vm.runInContext(`JSON.stringify(calendarDay(new Date('2026-10-28T00:00:00Z'),new Map([['2026-10-28',[{at:'2026-10-28',date_only:true,estimated:true,company:'测试公司',label:'完成体检',role:'测试岗位',action:'完成体检',source:'mail',done:false}]]]),true))`,render);
assert.ok(text.includes('推算日期'));assert.ok(text.includes('无指定时刻'));assert.ok(!text.includes('00:00'));
console.log('日期显示检查通过：日期精度、北京时间、日历推算标签，不补造00:00');
