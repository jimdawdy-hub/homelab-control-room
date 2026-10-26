(() => {
  'use strict';
  let snapshot = null, thermalHistory = null, pending = false, historyAt = 0;
  let dashboard = {title: 'Homelab Control Room', services: []};
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock = value => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  function temperatureChart(host, sensor, measuredAt) {
    const end = Date.now(), start = end - 24 * 3600000;
    const series = thermalHistory?.hosts?.find(item => item.name === host.name)?.series?.find(item => item.id === sensor.id);
    const samples = new Map((series?.points || []).map(([at, value]) => [Date.parse(at), value]));
    if (finite(sensor.value_c) && Number.isFinite(Date.parse(measuredAt))) samples.set(Date.parse(measuredAt), sensor.value_c);
    const points = [...samples]
      .filter(([at, value]) => Number.isFinite(at) && finite(value) && at >= start && at <= end)
      .sort((a, b) => a[0] - b[0]);
    const values = points.map(point => point[1]);
    const low = Math.floor(Math.min(...values, finite(sensor.value_c) ? sensor.value_c : 30) / 10) * 10 - 10;
    const high = Math.max(low + 20, Math.ceil(Math.max(...values, finite(sensor.value_c) ? sensor.value_c : 50) / 10) * 10 + 10);
    const x = at => 42 + (at - start) / (end - start) * 300;
    const y = value => 108 - (value - low) / (high - low) * 92;
    const segments = [];
    for (const point of points) {
      if (!segments.length || point[0] - segments.at(-1).at(-1)[0] > 25 * 60000) segments.push([]);
      segments.at(-1).push(point);
    }
    const lines = segments.map(segment => {
      const coordinates = segment.map(([at, value]) => x(at).toFixed(1) + "," + y(value).toFixed(1)).join(" ");
      return '<polygon class="area" points="' + x(segment[0][0]).toFixed(1) + ',108 ' + coordinates + ' ' + x(segment.at(-1)[0]).toFixed(1) + ',108"/>' +
        '<polyline class="line" points="' + coordinates + '"/>';
    }).join("");
    const dots = points.map(([at, value]) => '<circle class="latest" cx="' + x(at).toFixed(1) + '" cy="' + y(value).toFixed(1) +
      '" r="2.5"><title>' + escape(clock(at) + " · " + value.toFixed(1) + " °C") + '</title></circle>').join("");
    const ticks = [low, (low + high) / 2, high].map(value => '<line class="grid" x1="42" x2="342" y1="' + y(value) + '" y2="' + y(value) +
      '"/><text x="35" y="' + (y(value) + 4) + '" text-anchor="end">' + value + '</text>').join("");
    return '<svg class="chart" viewBox="0 0 360 153" role="img" aria-label="' + escape(sensor.label + ', temperature in degrees Celsius over the last 24 hours. ' + points.length + ' measured samples.') + '">' +
      '<text x="8" y="12">°C</text>' + ticks + lines + dots +
      '<line class="axis" x1="42" x2="342" y1="108" y2="108"/>' +
      [start, (start + end) / 2, end].map((at, index) => '<text x="' + x(at) + '" y="127" text-anchor="' + ["start", "middle", "end"][index] + '">' + escape(clock(at)) + '</text>').join("") +
      '<text x="192" y="146" text-anchor="middle">Time · last 24 hours</text></svg>' +
      '<small class="sample-note">' + (points.length < 2 ? 'Collecting history · ' : '') + points.length + ' samples · every 10 min</small>';
  }

  const bytes = value => {
    if (!finite(value)) return 'Unavailable';
    const units = ['B','KiB','MiB','GiB','TiB']; let i=0;
    while (value>=1024 && i<units.length-1) {value/=1024;i++;}
    return value.toLocaleString(undefined,{maximumFractionDigits:i<3?0:1})+' '+units[i];
  };
  const percent = value => finite(value)?value.toFixed(1)+'%':'Unavailable';
  const uptime = value => finite(value)?Math.floor(value/86400)+'d '+Math.floor(value%86400/3600)+'h '+Math.floor(value%3600/60)+'m':'Unavailable';
  const metric = (label,value,detail='',usage) => `<div class="metric"><span class="metric-label">${escape(label)}</span><strong>${escape(value)}</strong><small>${escape(detail)}</small>${finite(usage)?`<progress max="100" value="${Math.max(0,Math.min(100,usage))}" aria-label="${escape(label)} usage"></progress>`:''}</div>`;
  const memory = value => metric('Memory',bytes(value?.available_bytes)+' available',bytes(value?.total_bytes)+' total',finite(value?.available_bytes)&&value.total_bytes>0?100*(1-value.available_bytes/value.total_bytes):undefined);
  function render() {
    if (!snapshot) return;
    const hosts=snapshot.hosts||[snapshot.host],guests=snapshot.vms||[];
    document.getElementById('summary').innerHTML=metric('Hosts',hosts.filter(h=>!h.error).length+' / '+hosts.length+' available','Configured inventory')+metric('Virtual machines',guests.filter(g=>g.status==='running').length+' running / '+guests.length,'Inventory from host probes')+metric('Sensor history','10-minute samples','24-hour chart window')+metric('Collected',clock(snapshot.generated_at),'Actual collector timestamp');
    document.getElementById('hosts').innerHTML=hosts.map(host=>{
      const head=`<div class="host-top"><h3>${escape(host.name)}</h3><span class="state ${host.error?'stopped':''}">${host.error?'Unavailable':'Collected'}</span></div>`;
      if(host.error) return `<article class="host-card">${head}<p class="host-note">${escape(host.error)}</p></article>`;
      const hba=host.hba;
      const pools=(host.pools||[]).map(p=>`<div class="pool"><div class="pool-line"><span>${escape(p.name)}</span><small>${bytes(p.used_bytes)} / ${bytes(p.total_bytes)} · ${escape(p.health)}</small></div><progress max="100" value="${p.total_bytes>0?100*p.used_bytes/p.total_bytes:0}" aria-label="${escape(p.name)} usage"></progress></div>`).join('');
      const volumes=(host.volumes||[]).map(v=>`<tr><td>${escape(v.name)}</td><td>${bytes(v.used_bytes)}</td><td>${bytes(v.referenced_bytes)}</td><td>${bytes(v.provisioned_bytes)}</td></tr>`).join('');
      return `<article class="host-card">${head}<div class="metric-grid">${metric('Host CPU',percent(host.cpu_percent),host.physical_cores+' cores · '+host.logical_cpus+' threads',host.cpu_percent)}${memory(host.memory)}${metric('Uptime',uptime(host.uptime_seconds))}${hba?metric('HBA temperature',hba.roc_temp_c+' °C','Measured '+clock(hba.measured_at)):''}</div>${pools}${volumes?`<details class="storage-details"><summary><span>ZFS volumes</span></summary><div class="storage-scroll"><table><thead><tr><th>Name</th><th>Accounted</th><th>Referenced</th><th>Virtual size</th></tr></thead><tbody>${volumes}</tbody></table></div><p class="small muted">Rows can share capacity; do not add them together.</p></details>`:''}${(host.sensor_errors||[]).map(e=>`<p class="host-note">${escape(e)}</p>`).join('')}</article>`;
    }).join('');
    const pbs=snapshot.pbs;
    if(pbs) document.getElementById('hosts').innerHTML+=`<article class="host-card"><div class="host-top"><h3>Backup server · ${escape(pbs.node||'local')}</h3><span class="state ${pbs.service_running?'':'stopped'}">${pbs.service_running?'Running':'Unknown or stopped'}</span></div><div class="metric-grid">${(pbs.datastores||[]).map(d=>metric(d.name,bytes(d.available_bytes)+' available',bytes(d.total_bytes)+' total',d.total_bytes>0?100*d.used_bytes/d.total_bytes:undefined)).join('')}</div><p class="host-note">${escape(pbs.error||'Service and datastore status do not prove backup coverage or successful restores.')}</p></article>`;
    document.getElementById('guests').innerHTML=guests.length?guests.map(g=>{
      const live=g.status==='running'&&g.metrics,m=g.metrics;
      const data=live?metric('CPU',percent(m.cpu_percent),m.vcpu_count+' virtual CPUs',m.cpu_percent)+memory(m.memory)+(m.disks||[]).map(d=>metric(d.mountpoint,bytes(d.free_bytes)+' free',bytes(d.total_bytes)+' total',d.total_bytes>0?100*d.used_bytes/d.total_bytes:undefined)).join('')+metric('Uptime',uptime(m.uptime_seconds)):metric('Configured CPU',g.vcpu_count+' virtual CPUs')+metric('Configured memory',bytes(g.allocated_memory_bytes));
      return `<section class="vm-row"><div class="machine-heading"><h3>VM${escape(g.id)} · ${escape(g.name)} · ${escape(g.node)}</h3><span class="state ${live?'':'stopped'}">${escape(g.status)}</span></div><div class="metric-grid">${data}</div>${!live?`<p class="small muted">${escape(g.error||'Guest metrics unavailable while stopped.')}</p>`:''}</section>`;
    }).join(''):'<p class="muted">No virtual machines reported by these hosts.</p>';
    document.getElementById('service-list').innerHTML=dashboard.services.length?dashboard.services.map(s=>`<a class="service-card" href="${escape(s.url)}" target="_blank" rel="noopener noreferrer"><div><h3>${escape(s.name)}</h3><p>${escape(s.description)}</p></div></a>`).join(''):'<p class="muted">Add service bookmarks in your local dashboard configuration.</p>';
    document.getElementById('thermal-hosts').innerHTML=(snapshot.thermal_hosts||[]).map(host=>{
      const cards=host.temperatures.map(sensor=>{
        const stale=host.error||Date.now()-Date.parse(sensor.measured_at)>1200000;
        return `<article class="thermal-card" data-kind="${escape(sensor.kind)}"><div class="thermal-source">${escape(sensor.source)}</div><h4>${escape(sensor.label)}</h4><div class="thermal-value">${finite(sensor.value_c)?sensor.value_c.toFixed(1):'Unavailable'}<span> °C</span></div>${temperatureChart(host,sensor,sensor.measured_at)}<div class="sample-note">${stale?'STALE · ':''}Measured ${escape(clock(sensor.measured_at))}</div></article>`;
      }).join('');
      const fans=(host.fans||[]).map(f=>`<div class="fan"><div><strong>${escape(f.label)}</strong><small>${escape(f.source)}</small></div><div class="fan-value">${finite(f.rpm)?Math.round(f.rpm).toLocaleString():'Unavailable'} <span>RPM</span></div></div>`).join('');
      return `<section class="thermal-host"><div class="machine-heading"><h3>${escape(host.name)}</h3><span class="state ${host.error?'stopped':''}">${host.error?'Last known readings':'10-minute samples'}</span></div><div class="thermal-grid">${cards}</div>${!cards?'<p class="small muted">No temperature readings exposed. Check the host thermal timer.</p>':''}<h4 class="fan-heading">Fan speeds</h4><div class="fan-grid">${fans}</div>${!fans?'<p class="small muted">No fan RPM readings exposed by this host.</p>':''}${(host.errors||[]).map(e=>`<p class="host-note">${escape(e)}</p>`).join('')}</section>`;
    }).join('');
  }
  async function get(path) {
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(!response.ok) throw new Error('Unavailable');
    return response.json();
  }
  async function refresh() {
    if(pending||document.hidden) return;
    pending=true;
    const status=document.getElementById('update-status');
    try {
      const next=await get('/metrics.json');
      if(!Array.isArray(next.vms)||!Number.isFinite(Date.parse(next.generated_at))) throw new Error('Invalid snapshot');
      snapshot=next;
      if(Date.now()-historyAt>600000) {
        try {thermalHistory=await get('/thermal-history.json');historyAt=Date.now();}catch{historyAt=0;}
      }
      status.textContent=(next.stale?'STALE · ':'')+'Collected '+new Date(next.generated_at).toLocaleString()+' · resources every minute · thermals every 10 minutes';
    } catch {status.textContent='Collection unavailable. '+(snapshot?'Showing the last snapshot; retrying automatically.':'Configure your hosts and run the collector, then refresh.');}
    finally {pending=false;render();}
  }
  if(typeof document==='undefined') {if(typeof module!=='undefined') module.exports={escape,bytes,metric,temperatureChart};return;}
  document.getElementById('history-range').remove();
  document.querySelector('.range-label').textContent='Sensor history · last 24 hours';
  document.getElementById('theme').addEventListener('click',event=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';event.currentTarget.textContent=dark?'Light mode':'Dark mode';});
  document.getElementById('density').addEventListener('click',event=>{const compact=document.documentElement.dataset.density!=='compact';document.documentElement.dataset.density=compact?'compact':'comfortable';event.currentTarget.setAttribute('aria-pressed',String(compact));});
  document.getElementById('refresh').addEventListener('click',refresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  get('/dashboard.json').then(config=>{dashboard=config;document.title=config.title;document.getElementById('overview-title').textContent=config.title;render();}).catch(()=>{});
  refresh();setInterval(refresh,30000);
})();
