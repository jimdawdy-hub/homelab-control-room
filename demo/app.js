(() => {
  'use strict';
  const minute = 60000;
  let revision = 0;
  const hosts = [
    {name:'compute-01',role:'Primary virtualization node',cpu:21.4,memory:128,available:74,cores:16,threads:32,uptime:'12d 6h 18m',pools:[['vm-pool',4.8,12],['scratch',1.2,4]]},
    {name:'storage-01',role:'Backup & monitoring node',cpu:8.6,memory:64,available:42,cores:12,threads:24,uptime:'18d 3h 42m',pools:[['system',.12,1],['backup-pool',6.3,16]]}
  ];
  const guests = [
    [101,'Operations','compute-01',true,4,8,12.8,3.4,'48 GiB free','3d 4h'],
    [102,'Application server','compute-01',true,8,32,18.2,11.6,'180 GiB free','8d 2h'],
    [103,'Database','compute-01',true,8,48,24.6,28.4,'620 GiB free','8d 2h'],
    [201,'Monitoring','storage-01',true,2,4,3.2,1.1,'22 GiB free','12d 6h'],
    [202,'Media services','storage-01',true,4,8,8.1,2.6,'1.2 TiB free','6d 9h'],
    [301,'Linux sandbox','compute-01',false,4,8],
    [302,'Test database','compute-01',false,4,16],
    [303,'Build runner','compute-01',false,4,8]
  ];
  const services = [
    ['RP','Reverse proxy','Local service routing','Available'],['UP','Uptime monitor','Service checks and response history','Available'],
    ['DH','Drive health','Disk health and SMART monitoring','Available'],['LG','Log viewer','Centralized application logs','Available'],
    ['MD','Media server','Home media library','Available'],['AU','Automation','Scheduled lab workflows','Idle']
  ];
  const thermal = [
    {name:'compute-01',sensors:[
      ['hba','HBA controller','hba','Chip sensor',61],
      ['cpu1-package','CPU #1 · Package','cpu','Package sensor',47],['cpu1-bmc','CPU #1 · BMC temperature','cpu','BMC',44],
      ['cpu2-package','CPU #2 · Package','cpu','Package sensor',50],['cpu2-bmc','CPU #2 · BMC temperature','cpu','BMC',46],
      ['gpu','GPU temperature','gpu','GPU sensor',54],['pch','PCH','board','BMC',39],['system','System','board','BMC',29],
      ['peripheral','Peripheral','board','BMC',34],['dimm','DIMM average','memory','Average · 4 sample bank sensors',42],
      ['optane','Optane average','memory','Average · 4 sample bank sensors',44]
    ],fans:[['CPU fan','BMC',1920],['Chassis fan','BMC',1480],['Peripheral fan','BMC',1240]]},
    {name:'storage-01',sensors:[['package','CPU #1 · Package','cpu','Package sensor',39],['core-max','CPU #1 · Hottest core','cpu','Maximum · 12 sample core sensors',37]],fans:[]}
  ];
  const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time = value => new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  const metric = (label,value,detail,usage) => `<div class="metric"><span class="metric-label">${escape(label)}</span><strong>${escape(value)}</strong><small>${escape(detail)}</small>${Number.isFinite(usage)?`<progress max="100" value="${usage}" aria-label="${escape(label)} usage"></progress>`:''}</div>`;
  function samples(base,phase,end) {
    return Array.from({length:145},(_,index)=>{
      const offset=index/144;
      const wave=1.8*Math.sin(offset*12+phase)+.8*Math.sin(offset*43+phase*.4);
      const workload=4.5*Math.exp(-Math.pow((offset-.62-phase*.008)/.075,2));
      return [end-(144-index)*10*minute,Math.round((base+wave+workload)*10)/10];
    });
  }
  function chart(points,hours,end,label) {
    const start=end-hours*60*minute;
    points=points.filter(([at])=>at>=start);
    const values=points.map(([,value])=>value);
    const low=Math.floor((Math.min(...values)-4)/5)*5;
    const high=Math.max(low+15,Math.ceil((Math.max(...values)+4)/5)*5);
    const x=at=>42+(at-start)/(end-start)*300;
    const y=value=>108-(value-low)/(high-low)*92;
    const coords=points.map(([at,value])=>`${x(at).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
    const grid=[low,(low+high)/2,high].map(value=>`<line class="grid" x1="42" x2="342" y1="${y(value)}" y2="${y(value)}"/><text x="35" y="${y(value)+4}" text-anchor="end">${value}</text>`).join('');
    const ticks=[start,(start+end)/2,end].map((at,index)=>`<text x="${x(at)}" y="127" text-anchor="${['start','middle','end'][index]}">${escape(time(at))}</text>`).join('');
    const dots=points.map(([at,value])=>`<circle class="point" cx="${x(at).toFixed(1)}" cy="${y(value).toFixed(1)}" r="5"><title>${escape(time(at))} · ${value.toFixed(1)} °C · sample data</title></circle>`).join('');
    const last=points.at(-1);
    return `<svg class="chart" viewBox="0 0 360 153" role="img" aria-label="${escape(label)}: simulated ${hours}-hour temperature history in degrees Celsius"><text x="8" y="12">°C</text>${grid}<polygon class="area" points="42,108 ${coords} 342,108"/><polyline class="line" points="${coords}"/>${dots}<circle class="latest" cx="${x(last[0])}" cy="${y(last[1])}" r="3"/><line class="axis" x1="42" x2="342" y1="108" y2="108"/>${ticks}<text x="192" y="146" text-anchor="middle">Time · last ${hours} hours</text></svg>`;
  }
  function renderThermals() {
    const end=Math.floor(Date.now()/(10*minute))*10*minute;
    const hours=Number(document.getElementById('history-range').value);
    document.getElementById('thermal-hosts').innerHTML=thermal.map((host,hostIndex)=>{
      const cards=host.sensors.map(([id,label,kind,source,base],index)=>{
        const data=samples(base,index*.9+hostIndex*2+revision*.15,end);
        const value=data.at(-1)[1];
        return `<article class="thermal-card" data-kind="${kind}"><div class="thermal-source">${escape(kind.toUpperCase()+' · '+source)}</div><h4>${escape(label)}</h4><div class="thermal-value">${value.toFixed(1)}<span> °C</span></div>${chart(data,hours,end,label)}<div class="sample-note">${hours*6+1} simulated samples · every 10 min</div></article>`;
      }).join('');
      const fans=host.fans.map(([label,source,rpm])=>`<div class="fan"><div><strong>${escape(label)}</strong><small>${escape(source)}</small></div><div class="fan-value">${rpm.toLocaleString()} <span>RPM</span></div></div>`).join('');
      return `<section class="thermal-host" aria-label="${escape(host.name)} temperatures and fans"><div class="machine-heading"><h3>${escape(host.name)}</h3><span class="state">Sample readings</span></div><div class="thermal-grid">${cards}</div><h4 class="fan-heading">Fan speeds</h4>${fans?`<div class="fan-grid">${fans}</div>`:'<p class="small muted">No fan RPM readings in this sample configuration.</p>'}</section>`;
    }).join('');
  }
  function render() {
    document.getElementById('summary').innerHTML=metric('Cluster nodes','2 online','Example virtualization cluster')+metric('Virtual machines','5 running / 8','3 stopped guests')+metric('Backup datastore','9.7 TiB available','16 TiB sample capacity',39.4)+metric('Sensor history','10-minute samples','24-hour demo window');
    document.getElementById('hosts').innerHTML=hosts.map(host=>`<article class="host-card"><div class="host-top"><div><h3>${host.name}</h3><div class="node-label">${host.role}</div></div><span class="state">Online</span></div><div class="metric-grid">${metric('Host CPU',host.cpu+'%',`${host.cores} cores · ${host.threads} threads`,host.cpu)}${metric('Memory',host.available+' GiB free',host.memory+' GiB total',100*(1-host.available/host.memory))}${metric('Uptime',host.uptime,'Example host uptime')}${metric('HBA temperature',host.name==='compute-01'?samples(61,revision*.15,Math.floor(Date.now()/(10*minute))*10*minute).at(-1)[1].toFixed(1)+' °C':'Not installed',host.name==='compute-01'?'Sample chip reading':'No controller in this example')}</div>${host.pools.map(([name,used,total])=>`<div class="pool"><div class="pool-line"><span>${name}</span><small>${used} / ${total} TiB used · healthy</small></div><progress max="100" value="${100*used/total}" aria-label="${name} usage"></progress></div>`).join('')}</article>`).join('')+
      `<article class="host-card"><div class="host-top"><div><h3>Backup server</h3><div class="node-label">Snapshot archive · storage-01</div></div><span class="state">Available</span></div><div class="metric-grid">${metric('Datastore','9.7 TiB free','16 TiB total',39.4)}${metric('Last backup','4 hours ago','Simulated successful task')}${metric('Retention','90 days','Example history policy')}${metric('Next window','02:00','Example schedule')}</div><p class="host-note">Read-only sample status. No backup jobs or real systems are connected to this demonstration.</p></article>`;
    document.getElementById('guests').innerHTML=guests.map(([id,name,node,running,cpus,memory,cpu,used,disk,uptime])=>`<section class="vm-row"><div class="machine-heading"><h3>VM${id} · ${name} <span class="muted">· ${node}</span></h3><span class="state ${running?'':'stopped'}">${running?'Running':'Stopped'}</span></div><div class="metric-grid">${running?metric('Processor',cpu+'%',cpus+' virtual CPUs',cpu)+metric('Memory',(memory-used).toFixed(1)+' GiB free',memory+' GiB assigned',used/memory*100)+metric('Root disk',disk,'Example guest filesystem')+metric('Uptime',uptime,'Simulated guest uptime'):metric('Configured CPU',cpus+' virtual CPUs','Assigned capacity')+metric('Configured memory',memory+' GiB','Assigned capacity')}</div>${running?'':'<p class="small muted">Live usage is unavailable while the example guest is stopped.</p>'}</section>`).join('');
    document.getElementById('service-list').innerHTML=services.map(([mark,name,description,state])=>`<article class="service-card"><span class="service-mark" aria-hidden="true">${mark}</span><div><h3>${name}</h3><p>${description}</p><span class="state ${state==='Idle'?'stopped':''}">${state}</span></div></article>`).join('');
    renderThermals();
    document.getElementById('update-status').textContent='Demo generated '+new Date().toLocaleTimeString()+'. All metrics and history are simulated.';
  }
  if(typeof document==='undefined') {
    if(typeof module!=='undefined') module.exports={hosts,guests,thermal,samples,chart,metric};
    return;
  }
  try {document.documentElement.dataset.theme=localStorage.getItem('homelab-demo-theme')||'dark';} catch {}
  const theme=document.getElementById('theme');
  const syncTheme=()=>{theme.textContent=document.documentElement.dataset.theme==='dark'?'Light mode':'Dark mode';};
  theme.addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';try{localStorage.setItem('homelab-demo-theme',document.documentElement.dataset.theme);}catch{}syncTheme();});
  document.getElementById('density').addEventListener('click',event=>{const compact=document.documentElement.dataset.density!=='compact';document.documentElement.dataset.density=compact?'compact':'comfortable';event.currentTarget.setAttribute('aria-pressed',String(compact));event.currentTarget.textContent=compact?'Comfortable view':'Compact view';});
  document.getElementById('refresh').addEventListener('click',()=>{revision++;render();});
  document.getElementById('history-range').addEventListener('change',renderThermals);
  syncTheme();render();
})();
