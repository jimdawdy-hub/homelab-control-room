const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const data=require('./demo/app.js');
test('sample dataset contains the intended ordered thermal summary',()=>{
 assert.equal(data.thermal[0].sensors.length,11);
 assert.equal(data.thermal[1].sensors.length,2);
 assert.deepEqual(data.thermal[0].sensors.map(s=>s[0]),['hba','cpu1-package','cpu1-bmc','cpu2-package','cpu2-bmc','gpu','pch','system','peripheral','dimm','optane']);
 assert.equal(data.thermal[0].fans.length,3);
});
test('simulated history is deterministic, finite, and spaced ten minutes apart',()=>{
 const points=data.samples(45,2,1800000000000);
 assert.equal(points.length,145);
 assert.deepEqual(points,data.samples(45,2,1800000000000));
 assert.ok(points.every(([,value])=>Number.isFinite(value)&&value>0&&value<100));
 assert.equal(points[1][0]-points[0][0],600000);
});
test('both chart windows include labeled time and temperature axes',()=>{
 const end=1800000000000,points=data.samples(45,2,end);
 for(const hours of [6,24]) {
  const html=data.chart(points,hours,end,'CPU package');
  assert.match(html,/°C/);assert.ok(html.includes('Time · last '+hours+' hours'));
  assert.equal((html.match(/class="point"/g)||[]).length,hours*6+1);
  assert.doesNotMatch(html,/NaN|Infinity/);
 }
});
test('public assets have no private identifiers, addresses, external assets, or network integrations',()=>{
 const assets=['index.html','styles.css','app.js'].map(name=>fs.readFileSync('demo/'+name,'utf8')).join('\n');
 assert.doesNotMatch(assets,/https?:\/\/|\b(?:\d{1,3}\.){3}\d{1,3}\b|\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon/i);
 assert.match(assets,/Fictional hosts and simulated readings/);
 assert.ok(assets.includes('sample data'));
});
test('the complete page renders and local theme, density and history controls work',()=>{
 const elements={};
 for(const id of ['summary','hosts','guests','service-list','thermal-hosts','history-range','theme','density','refresh','update-status']) {
  elements[id]={innerHTML:'',textContent:'',value:'24',events:{},attributes:{},addEventListener(name,fn){this.events[name]=fn;},setAttribute(name,value){this.attributes[name]=value;}};
 }
 const root={dataset:{theme:'dark'}};
 const scope=vm.createContext({Date,document:{documentElement:root,getElementById:id=>elements[id]},localStorage:{getItem:()=>null,setItem(){}}});
 vm.runInContext(fs.readFileSync('demo/app.js','utf8'),scope);
 assert.equal((elements['thermal-hosts'].innerHTML.match(/class="thermal-card"/g)||[]).length,13);
 assert.equal((elements['thermal-hosts'].innerHTML.match(/class="fan"/g)||[]).length,3);
 assert.equal((elements.guests.innerHTML.match(/class="vm-row"/g)||[]).length,8);
 elements.theme.events.click();assert.equal(root.dataset.theme,'light');
 elements.density.events.click({currentTarget:elements.density});assert.equal(root.dataset.density,'compact');
 elements['history-range'].value='6';elements['history-range'].events.change();
 assert.match(elements['thermal-hosts'].innerHTML,/Time · last 6 hours/);
 assert.equal((elements['thermal-hosts'].innerHTML.match(/class="point"/g)||[]).length,13*37);
 const before=elements['thermal-hosts'].innerHTML;elements.refresh.events.click();
 assert.notEqual(elements['thermal-hosts'].innerHTML,before);
});
