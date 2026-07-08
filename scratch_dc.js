
class Component extends DCLogic {
  constructor(props){
    super(props);
    this.H = 48;
    this.cores = 8;
    this._lid = 0;
    this.LOG = {
      'traefik':[['info','level=info msg="Configuration reloaded from provider docker"'],['info','level=info msg="Creating server" entryPoint=websecure']],
      'postgres':[['info','LOG:  checkpoint complete: wrote 214 buffers (1.3%)'],['warn','WARNING:  autovacuum of table "public.events" is taking long'],['info','LOG:  database system is ready to accept connections']],
      'redis':[['info','Background saving started by pid 41'],['info','DB saved on disk'],['info','1 changes in 900 seconds. Saving...']],
      'grafana':[['info','logger=context msg="Request Completed" status=200 dur=8ms'],['warn','logger=plugins msg="Plugin update available" id=piechart']],
      'prometheus':[['info','msg="Completed loading of configuration file" totalDuration=41ms'],['warn','msg="Error on ingesting out-of-order samples" num_dropped=2']],
      'minio':[['info','API: SYSTEM.internal PutObject bucket=backups OK'],['info','Healing: scan complete for 1 object']],
      'worker-queue':[['info','job=email.send status=done dur=42ms'],['error','job=report.build status=failed retry=2 err="timeout"'],['info','job=thumb.generate status=done dur=118ms']],
      'nginx-proxy':[['info','GET /healthz 200 1ms'],['info','GET /api/v1/stats 200 6ms client=10.0.0.4']],
    };
    const rnd = n => Math.random()*n;
    const hex = n => Array.from({length:n},()=>'0123456789abcdef'[Math.floor(Math.random()*16)]).join('');
    const base = [
      ['traefik','traefik:v3.0',true,6,48,512,120,80],
      ['postgres','postgres:16-alpine',true,14,310,1024,60,220],
      ['redis','redis:7-alpine',true,4,64,256,40,55],
      ['grafana','grafana/grafana:11.1.0',true,9,220,512,90,70],
      ['prometheus','prom/prometheus:v2.53',true,22,640,2048,140,110],
      ['minio','minio/minio:RELEASE.2024',true,5,180,1024,75,320],
      ['worker-queue','app/worker:1.8.2',true,31,410,1024,50,95],
      ['nginx-proxy','nginx:1.27-alpine',true,2,28,128,110,60],
      ['backup-cron','app/backup:0.9',false,0,0,256,0,0],
      ['vpn-gateway','linuxserver/wireguard',false,0,0,128,0,0],
    ];
    const H = this.H;
    const containers = base.map(b=>{
      const [name,image,running,cpu,mem,memLimit,rx,tx]=b;
      const seed = v => Array.from({length:H},()=> running ? Math.max(0, v*(0.7+rnd(0.6))) : 0);
      return {
        id: hex(12), name, image, running, memLimit,
        cpu0:running?cpu:0, mem0:running?mem:0, rx0:running?rx:0, tx0:running?tx:0,
        cpu:running?cpu:0, mem:running?mem:0, rx:running?rx:0, tx:running?tx:0,
        block: running ? 8+rnd(30) : 0,
        pids: running ? Math.floor(4+rnd(40)) : 0,
        restarts: running ? Math.floor(rnd(3)) : Math.floor(1+rnd(4)),
        uptimeSec: running ? Math.floor(600+rnd(90000)) : 0,
        exitedAgo: running ? 0 : Math.floor(30+rnd(4000)),
        exitCode: running ? 0 : (Math.random()<0.5?0:1),
        cpuH:seed(cpu), memH:seed(mem), rxH:seed(rx), txH:seed(tx), blkH:seed(rx*1.4),
      };
    });
    this.state = {
      containers,
      host:{
        cpuH: Array.from({length:H},()=>20+rnd(30)),
        ramH: Array.from({length:H},()=>44+rnd(20)),
        netH: Array.from({length:H},()=>250+rnd(400)),
        diskH: Array.from({length:H},()=>16+rnd(6)),
      },
      logs: this.seedLogs(containers),
    };
  }

  componentDidMount(){ this.connectLive(); this.scrollLogs(); }
  componentWillUnmount(){ clearInterval(this._t); }
  componentDidUpdate(prev){
    if(prev.tickMs !== this.props.tickMs) this.startTimer();
    this.scrollLogs();
  }
  startTimer(){ clearInterval(this._t); this._t = setInterval(()=>this.tick(), this.props.tickMs || 1500); }
  scrollLogs(){ document.querySelectorAll('[data-logstream]').forEach(el=>{ el.scrollTop = el.scrollHeight; }); }

  connectLive(){
    const apiHost = this.props.apiHost || location.hostname || '127.0.0.1';
    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const httpProtocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const wsUrl = this.props.wsUrl || `${wsProtocol}//${apiHost}:8000/ws/snapshot`;
    const snapshotUrl = this.props.snapshotUrl || `${httpProtocol}//${apiHost}:8000/api/snapshot`;
    let socket;
    let pollTimer = null;

    const startPolling = () => {
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        try {
          const res = await fetch(snapshotUrl);
          if (res.ok) this.applySnapshot(await res.json());
        } catch (err) {
          // stay silent; next poll will retry
        }
      }, this.props.tickMs || 1500);
    };

    const stopPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const connect = () => {
      try {
        socket = new WebSocket(wsUrl);
      } catch (err) {
        startPolling();
        return;
      }
      socket.onopen = () => stopPolling();
      socket.onmessage = (event) => {
        this.applySnapshot(JSON.parse(event.data));
      };
      socket.onclose = () => {
        startPolling();
        setTimeout(connect, this.props.tickMs || 1500);
      };
      socket.onerror = () => socket.close();
    };

    connect();
  }

  applySnapshot(snapshot){
    const H = this.H;
    const pushH = (arr, v) => { const n = (arr || []).concat([v]); return n.length > H ? n.slice(n.length - H) : n; };

    this.setState(s=>{
      // --- host ---
      const prevHost = s.host || {};
      const host = {
        cpuH: pushH(prevHost.cpuH, snapshot.host.cpu_pct),
        ramH: pushH(prevHost.ramH, snapshot.host.mem_pct),
        netH: pushH(prevHost.netH, snapshot.host.net_rate_kbps),
        diskH: pushH(prevHost.diskH, snapshot.host.disk_pct),
      };

      // --- containers: merge by id, keep existing history ---
      const byId = new Map((s.containers || []).map(c => [c.id, c]));
      const containers = snapshot.containers.map(c => {
        const existing = byId.get(c.id) || {
          id: c.id,
          memLimit: c.mem_limit_mb,
          cpu0: c.cpu_pct, mem0: c.mem_used_mb, rx0: c.net_rx_kbps, tx0: c.net_tx_kbps,
          cpuH: [], memH: [], rxH: [], txH: [], blkH: [],
        };
        return {
          ...existing,
          name: c.name,
          image: c.image,
          running: c.state === 'running',
          memLimit: c.mem_limit_mb,
          exitCode: c.exit_code,
          uptimeSec: c.uptime_sec,
          exitedAgo: c.exited_ago_sec,
          restarts: c.restarts,
          pids: c.pids,
          cpu: c.cpu_pct,
          mem: c.mem_used_mb,
          rx: c.net_rx_kbps,
          tx: c.net_tx_kbps,
          block: c.block_kbps,
          cpuH: pushH(existing.cpuH, c.cpu_pct),
          memH: pushH(existing.memH, c.mem_used_mb),
          rxH: pushH(existing.rxH, c.net_rx_kbps),
          txH: pushH(existing.txH, c.net_tx_kbps),
          blkH: pushH(existing.blkH, c.block_kbps),
        };
      });

      // --- events -> log lines ---
      const logs = (snapshot.events || []).reduce((acc, e) => acc.concat([{
        id: this._lid++,
        time: (e.ts || '').slice(11, 19),
        level: e.level,
        name: e.container,
        msg: e.message,
      }]), s.logs || []).slice(-220);

      return { containers, host, logs };
    });
  }

  jit(v,target,noise,min,max){
    let n = v + (target-v)*0.14 + (Math.random()-0.5)*noise;
    return Math.min(max, Math.max(min, n));
  }
  fmtMem(mb){ return mb>=1024 ? (mb/1024).toFixed(2)+' GiB' : mb.toFixed(0)+' MiB'; }
  fmtRate(kb){ return kb>=1024 ? (kb/1024).toFixed(1)+' MB/s' : kb.toFixed(0)+' kB/s'; }
  fmtUp(s){ s=Math.floor(s); const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),ss=Math.floor(s%60);
    if(d>0) return d+'d '+h+'h'; if(h>0) return h+'h '+m+'m'; if(m>0) return m+'m '+ss+'s'; return ss+'s'; }

  seedLogs(containers){ this._lid=0; let logs=[]; const run=containers.filter(c=>c.running); for(let i=0;i<16;i++) logs=this.appendLog(logs,run); return logs; }
  appendLog(logs, run){
    if(!run.length) return logs;
    const c = run[Math.floor(Math.random()*run.length)];
    const pool = this.LOG[c.name] || [['info','heartbeat ok']];
    const pick = pool[Math.floor(Math.random()*pool.length)];
    const d = new Date();
    const t = [d.getHours(),d.getMinutes(),d.getSeconds()].map(x=>String(x).padStart(2,'0')).join(':');
    return logs.concat([{id:this._lid++, time:t, level:pick[0], name:c.name, msg:pick[1]}]).slice(-220);
  }

  tick(){
    this.setState(s=>{
      const push=(a,v)=>{ const n=a.slice(1); n.push(v); return n; };
      const last=a=>a[a.length-1];
      const containers = s.containers.map(c=>{
        if(!c.running) return {...c, exitedAgo:c.exitedAgo+ (this.props.tickMs||1500)/1000};
        const cpu=this.jit(c.cpu,c.cpu0,c.cpu0*0.6+2,0,c.cpu0*3+6);
        const mem=this.jit(c.mem,c.mem0,c.mem0*0.05+4,0,c.memLimit);
        const rx=this.jit(c.rx,c.rx0,c.rx0*0.5+18,0,c.rx0*4+60);
        const tx=this.jit(c.tx,c.tx0,c.tx0*0.5+18,0,c.tx0*4+60);
        const pids=Math.max(1,Math.round(this.jit(c.pids,c.pids,1.4,1,300)));
        const blk=this.jit(c.block,c.rx0*1.4+10,20,0,c.rx0*5+80);
        return {...c, cpu,mem,rx,tx,pids, block:blk, uptimeSec:c.uptimeSec+(this.props.tickMs||1500)/1000,
          cpuH:push(c.cpuH,cpu), memH:push(c.memH,mem), rxH:push(c.rxH,rx), txH:push(c.txH,tx), blkH:push(c.blkH,blk)};
      });
      const host={
        cpuH: push(s.host.cpuH, this.jit(last(s.host.cpuH),30,9,3,96)),
        ramH: push(s.host.ramH, this.jit(last(s.host.ramH),52,5,20,92)),
        netH: push(s.host.netH, this.jit(last(s.host.netH),420,220,20,1500)),
        diskH: push(s.host.diskH, this.jit(last(s.host.diskH),17,1,10,95)),
      };
      const run = containers.filter(c=>c.running);
      let logs = s.logs;
      if(Math.random()<0.85) logs = this.appendLog(logs, run);
      if(Math.random()<0.3)  logs = this.appendLog(logs, run);
      return {containers, host, logs};
    });
  }

  spark(arr){
    const max=Math.max.apply(null,arr.concat([1])), min=Math.min.apply(null,arr.concat([0]));
    const rng=(max-min)||1;
    const pts=arr.map((v,i)=>{ const x=(i/(arr.length-1))*100; const y=33-((v-min)/rng)*30; return x.toFixed(1)+','+y.toFixed(1); });
    const line=pts.join(' ');
    return {line, area:line+' 100,34 0,34'};
  }

  renderVals(){
    const s=this.state, H=this.H;
    const GREEN=this.props.accent||'#46e08a', AMBER='#e8b23a', RED='#e8555f', DIM='#5f7a6e';
    const clr=v=> v<60?GREEN : v<85?AMBER : RED;
    const zero=new Array(H).fill(0);

    const mk=(arr,isPct,sub2)=>{ const v=arr[arr.length-1], sp=this.spark(arr); const pct=isPct?v:Math.min(100,v/14);
      return {disp:isPct?Math.round(v)+'%':this.fmtRate(v), color:isPct?clr(v):GREEN, line:sp.line, area:sp.area,
        ringOffset:(251.3*(1-pct/100)).toFixed(1), sub2:sub2||'' }; };
    const host={
      cpu:mk(s.host.cpuH,true,'host · '+this.cores+' cores'),
      ram:mk(s.host.ramH,true,Math.round(s.host.ramH[H-1])+'% of total'),
      disk:mk(s.host.diskH,true,'/var/lib/docker'),
      net:mk(s.host.netH,false),
    };

    const containers = s.containers.map(c=>{
      const memPct = c.memLimit ? (c.mem/c.memLimit*100) : 0;
      const sp = this.spark(c.running?c.cpuH:zero);
      return {
        id:c.id, name:c.name, image:c.image, running:c.running,
        dot: c.running?GREEN:(c.exitCode===0?DIM:RED),
        statusText: c.running ? 'up '+this.fmtUp(c.uptimeSec)
                    : 'exited ('+c.exitCode+') '+this.fmtUp(c.exitedAgo)+' ago',
        statusColor: c.running?GREEN:DIM,
        nameColor: c.running?'#e6f4ec':'#5f7a6e',
        cpuStr: c.cpu.toFixed(1)+'%', cpuPct: Math.min(100,c.cpu).toFixed(1), cpuColor: c.running?clr(c.cpu):DIM,
        memStr: this.fmtMem(c.mem), memPct: Math.min(100,memPct).toFixed(1), memColor: c.running?clr(memPct):DIM,
        netStr: '↓'+this.fmtRate(c.rx)+'  ↑'+this.fmtRate(c.tx),
        line: sp.line, area: sp.area, lineColor: c.running?GREEN:'#2a3d34',
        cardBorder: '#1c2823',
        cardOpacity: c.running?'1':'.55',
      };
    });

    const logs = s.logs.map(l=>({ id:l.id, time:l.time, level:l.level.toUpperCase(),
      levelColor: l.level==='error'?RED : l.level==='warn'?AMBER : GREEN, name:l.name, msg:l.msg }));

    const running = s.containers.filter(c=>c.running).length;
    return {
      host, containers, logs,
      accent:GREEN, accentGlow:GREEN+'80', scanlineOpacity: (this.props.scanlines!==false)?'.05':'0',
      hostLabel: (this.props.hostname||'homelab')+' · engine 27.1.1',
      runningStr: running+'/'+s.containers.length,
      tickLabel: ((this.props.tickMs||1500)/1000).toFixed(1)+'s',
    };
  }
}
