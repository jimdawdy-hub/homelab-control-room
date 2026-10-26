const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/app.js','utf8');

async function page(fail=false) {
 const elements={};
 for(const id of ['summary','hosts','guests','service-list','thermal-hosts','theme','density','refresh','update-status','overview-title','history-range'])
  elements[id]={innerHTML:'',textContent:'',remove(){},addEventListener(){},setAttribute(){}};
 const host=name=>({name,cpu_percent:10,physical_cores:4,logical_cpus:8,memory:{total_bytes:100,available_bytes:40},uptime_seconds:100,pools:[],volumes:[]});
 const snapshot={generated_at:new Date().toISOString(),stale:false,hosts:[host('node-a'),host('node-b')],
  vms:[{id:17,node:'node-a',name:'Example guest',status:'stopped',vcpu_count:2,allocated_memory_bytes:2048}],
  thermal_hosts:[{name:'node-a',temperatures:[{id:'cpu',label:'CPU #1 · Package',source:'coretemp',kind:'cpu',value_c:40,measured_at:new Date().toISOString()}],fans:[],errors:[]}]};
 const scope=vm.createContext({Date,AbortSignal,document:{hidden:false,documentElement:{dataset:{theme:'dark'}},getElementById:id=>elements[id],querySelector:()=>({}),addEventListener(){}},setInterval(){},fetch:async path=>{
  if(fail&&path==='/metrics.json')throw new Error('offline');
  return {ok:true,json:async()=>path==='/metrics.json'?snapshot:path==='/dashboard.json'?{title:'My Lab',services:[]}:{hosts:[]}};
 }});
 vm.runInContext(source,scope);
 await new Promise(setImmediate);
 return elements;
}
test('live UI renders arbitrary host names, actual sensor data and stopped guest capacity',async()=>{
 const elements=await page();
 assert.match(elements.hosts.innerHTML,/node-a/);assert.match(elements.hosts.innerHTML,/node-b/);
 assert.match(elements.guests.innerHTML,/VM17/);assert.match(elements.guests.innerHTML,/Configured CPU/);
 assert.match(elements['thermal-hosts'].innerHTML,/CPU #1 · Package/);
 assert.match(elements['thermal-hosts'].innerHTML,/40\.0/);
 assert.equal(elements['overview-title'].textContent,'My Lab');
 assert.match(elements['update-status'].textContent,/Collected/);
});
test('missing live collection never falls back to invented demo metrics',async()=>{
 const elements=await page(true);
 assert.match(elements['update-status'].textContent,/Collection unavailable/);
 assert.equal(elements.hosts.innerHTML,'');assert.equal(elements['thermal-hosts'].innerHTML,'');
});
