from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os, subprocess, time, socket, platform, psutil, json
from datetime import datetime, timedelta
from collections import deque

app = FastAPI()
HISTORY_FILE = "/app/logs/history.json"
EXTERNAL_DISK_PATH = os.getenv("EXTERNAL_DISK_PATH", "/media/hs/HDD")

history = deque(maxlen=720)

def save_history(data):
    try:
        point = {"ts": data["timestamp"], "cpu": data["cpu"]["usage"], "temp": data["cpu"]["temp"], 
                 "ram": data["ram"]["usage"], "disk_sys": data["disk"]["system"]["percent"], 
                 "disk_ext": data["disk"]["external"]["percent"] if data["disk"]["external"]["mounted"] else 0}
        history.append(point)
        if len(history) % 10 == 0:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, "w") as f:
                json.dump(list(history), f)
    except Exception as e:
        print(f"History save error: {e}")

def load_history():
    global history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                history = deque(data, maxlen=720)
            print(f"✅ Loaded {len(history)} history points")
    except:
        pass

def cpu_temp():
    try:
        for z in range(5):
            p = f"/sys/class/thermal/thermal_zone{z}/temp"
            if os.path.exists(p):
                t = int(open(p).read().strip()) / 1000
                if 20 < t < 100: return round(t, 1)
    except: pass
    return 0.0

def cpu_info():
    try:
        freq = psutil.cpu_freq()
        return {"usage": psutil.cpu_percent(interval=0.1), "temp": cpu_temp(), 
                "cores": psutil.cpu_count(logical=True), "freq": round(freq.current/1000,2) if freq else 0}
    except: return {"usage":0,"temp":0,"cores":0,"freq":0}

def ram_info():
    try:
        m = psutil.virtual_memory()
        return {"usage": round(m.percent,1), "total_gb": round(m.total/1e9,1), 
                "used_gb": round(m.used/1e9,1), "free_gb": round(m.available/1e9,1)}
    except: return {"usage":0,"total_gb":0,"used_gb":0,"free_gb":0}

def disk_info():
    try: 
        s = psutil.disk_usage("/")
        sys_disk = {"percent": round(s.percent,1), "total_gb": round(s.total/1e9,1), 
                    "used_gb": round(s.used/1e9,1), "free_gb": round(s.free/1e9,1)}
    except: sys_disk = {"percent":0,"total_gb":0,"used_gb":0,"free_gb":0}
    ext = {"percent":0,"total":"-","used":"-","free":"-","mounted":False}
    try:
        if os.path.exists(EXTERNAL_DISK_PATH):
            st = os.statvfs(EXTERNAL_DISK_PATH)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            ext = {"percent": round(used/total*100,1) if total>0 else 0, 
                   "total": f"{round(total/1e9,1)}G", "used": f"{round(used/1e9,1)}G", 
                   "free": f"{round(free/1e9,1)}G", "mounted": True}
    except: pass
    return {"system": sys_disk, "external": ext}

def net_info():
    ifs = {}
    try:
        r = subprocess.run("ip -o link show 2>/dev/null | grep -E '^[0-9]+: (enp|eth)' | awk -F': ' '{print $2}'", 
                          shell=True, capture_output=True, text=True, timeout=3)
        for iface in [i.strip() for i in r.stdout.split("\n") if i.strip()]:
            ip = subprocess.run(f"ip addr show {iface} 2>/dev/null | grep 'inet ' | awk '{{print $2}}' | cut -d'/' -f1", 
                               shell=True, capture_output=True, text=True, timeout=2).stdout.strip() or "-"
            rx=tx=0
            for k in ["rx","tx"]:
                p = f"/sys/class/net/{iface}/statistics/{k}_bytes"
                if os.path.exists(p):
                    try:
                        v = int(open(p).read().strip())
                        if k=="rx": rx=v
                        else: tx=v
                    except: pass
            ifs[iface] = {"ip": ip, "rx_mb": round(rx/1024/1024,2), "tx_mb": round(tx/1024/1024,2)}
    except: pass
    return ifs

def sys_info():
    try:
        b = datetime.fromtimestamp(psutil.boot_time())
        u = timedelta(seconds=time.time()-psutil.boot_time())
        load = psutil.getloadavg()
        return {"hostname": socket.gethostname(), "kernel": platform.release(), 
                "boot": b.strftime("%d.%m.%Y %H:%M"), "uptime": str(u).split(".")[0], 
                "load": [round(x,2) for x in load]}
    except: return {"hostname":"-","kernel":"-","boot":"-","uptime":"-","load":[0,0,0]}

def wan_ip():
    try: return subprocess.run("curl -s --connect-timeout 2 https://api.ipify.org", 
                              shell=True, capture_output=True, text=True, timeout=3).stdout.strip() or "-"
    except: return "-"

@app.get("/health")
async def health(): return {"status":"ok"}

@app.get("/mon/data")
async def mon_data():
    net = net_info()
    lans = sorted([k for k in net if k.startswith(("enp","eth"))])
    data = {"timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "cpu": cpu_info(), "ram": ram_info(), 
            "disk": disk_info(), "network": {"wan_ip": wan_ip(), "lan1": net.get(lans[0]) if len(lans)>0 else None, 
            "lan2": net.get(lans[1]) if len(lans)>1 else None}, "system": sys_info()}
    save_history(data)
    return data

@app.get("/mon/history")
async def get_history():
    h = list(history)
    return {"labels": [p["ts"] for p in h], "cpu": [p["cpu"] for p in h], "temp": [p["temp"] for p in h],
            "ram": [p["ram"] for p in h], "disk_sys": [p["disk_sys"] for p in h], "disk_ext": [p["disk_ext"] for p in h]}

@app.get("/")
async def mon_dash():
    return HTMLResponse(content="""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>🖥️ Server Monitor</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,sans-serif;background:linear-gradient(135deg,#667eea,#764ba2);padding:15px;color:#333}.container{max-width:1400px;margin:0 auto}.header{background:rgba(255,255,255,0.95);padding:15px 20px;border-radius:12px;margin-bottom:15px;display:flex;justify-content:space-between;align-items:center}.header h1{color:#667eea;font-size:1.5em}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-bottom:15px}.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-bottom:15px}.card{background:white;padding:20px;border-radius:10px;box-shadow:0 4px 12px rgba(0,0,0,0.1)}.card h3{color:#667eea;margin:0 0 12px;font-size:1.2em;border-bottom:2px solid #f0f0f0;padding-bottom:10px}.links{display:flex;gap:10px;margin:10px 0}.link-btn{flex:1;padding:8px 12px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;text-decoration:none;border-radius:6px;font-size:0.9em;text-align:center;transition:opacity 0.2s}.link-btn:hover{opacity:0.9}.stat{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #eee;font-size:0.95em}.stat:last-child{border-bottom:none}.stat-l{color:#666}.stat-v{font-weight:600}.progress{background:#e0e0e0;border-radius:6px;height:8px;margin:8px 0;overflow:hidden}.progress-bar{height:100%;background:#667eea;border-radius:6px;transition:width 0.3s}.progress-bar.high{background:#f5576c}.progress-bar.medium{background:#4facfe}.chart-container{position:relative;height:300px;margin-top:15px}.net-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.net-c{background:#f5f7fa;padding:12px;border-radius:6px;border-left:4px solid #667eea}.net-c strong{display:block;color:#667eea;margin-bottom:8px;font-size:1.1em}.detail{font-size:0.9em;color:#666;margin-top:3px}@media(max-width:1024px){.grid-3{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}@media(max-width:768px){.header{flex-direction:column;gap:10px;text-align:center}.links{flex-direction:column}}
</style></head><body><div class="container"><div class="header"><h1>🖥️ Server Monitor</h1><span id="ts" style="font-size:0.9em;color:#666"></span></div><div class="grid"><div class="card" style="grid-column:1/-1"><h3>💻 Система</h3><div class="links"><a href="https://processes.cloudpub.ru/" class="link-btn">📊 Процессы</a><a href="https://network.cloudpub.ru/" class="link-btn">🌐 Сеть</a></div><div class="stat"><span class="stat-l">Хост:</span><span class="stat-v" id="hn">—</span></div><div class="stat"><span class="stat-l">Загрузка:</span><span class="stat-v" id="bt">—</span></div><div class="stat"><span class="stat-l">Uptime:</span><span class="stat-v" id="up">—</span></div><div class="stat"><span class="stat-l">WAN:</span><span class="stat-v" id="wan">—</span></div></div></div><div class="grid-3"><div class="card"><h3>🔥 CPU</h3><div class="stat"><span class="stat-l">Загрузка:</span><span class="stat-v" id="cu">—</span></div><div class="progress"><div id="cb" class="progress-bar" style="width:0%"></div></div><div class="chart-container" id="cpuChart"></div><div class="stat"><span class="stat-l">Температура:</span><span class="stat-v" id="ct">—</span></div><div class="chart-container" id="tempChart"></div></div><div class="card"><h3>💾 RAM</h3><div class="stat"><span class="stat-l">Использовано:</span><span class="stat-v" id="ru">—</span></div><div class="progress"><div id="rb" class="progress-bar" style="width:0%"></div></div><div class="detail" id="rd">—</div><div class="chart-container" id="ramChart"></div></div><div class="card"><h3>💿 Диски</h3><div class="stat"><span class="stat-l">Система:</span><span class="stat-v" id="ds">—</span></div><div class="progress"><div id="db" class="progress-bar" style="width:0%"></div></div><div class="detail" id="dd">—</div><div class="stat"><span class="stat-l">Внешний:</span><span class="stat-v" id="de">—</span></div><div class="progress"><div id="deb" class="progress-bar" style="width:0%"></div></div><div class="detail" id="ded">—</div><div class="chart-container" id="diskChart"></div></div></div><div class="grid"><div class="card" style="grid-column:1/-1"><h3>🌐 Сеть</h3><div class="net-grid"><div class="net-c"><strong>LAN 1</strong><div class="stat"><span class="stat-l">IP:</span><span class="stat-v" id="l1i">—</span></div><div class="stat"><span class="stat-l">RX:</span><span class="stat-v" id="l1r">—</span></div><div class="stat"><span class="stat-l">TX:</span><span class="stat-v" id="l1t">—</span></div></div><div class="net-c"><strong>LAN 2</strong><div class="stat"><span class="stat-l">IP:</span><span class="stat-v" id="l2i">—</span></div><div class="stat"><span class="stat-l">RX:</span><span class="stat-v" id="l2r">—</span></div><div class="stat"><span class="stat-l">TX:</span><span class="stat-v" id="l2t">—</span></div></div></div></div></div></div><script>
let historyLoaded=false;
function fb(v){if(!v||v=="-")return"-";var n=parseFloat(v);return isNaN(n)?"-":n>=1024?(n/1024).toFixed(1)+" GB":n.toFixed(1)+" MB"}
function gc(p){return p>=80?"#f5576c":p>=50?"#4facfe":"#667eea"}

function createPlotlyChart(divId,color,title,yMax=100){
    const layout={
        margin:{t:20,r:20,b:40,l:50},
        paper_bgcolor:"rgba(0,0,0,0)",
        plot_bgcolor:"rgba(0,0,0,0)",
        xaxis:{
            type:"date",
            tickformat:"%H:%M:%S",
            gridcolor:"rgba(0,0,0,0.1)",
            showgrid:true,
            nticks:8
        },
        yaxis:{
            range:[0,yMax],
            gridcolor:"rgba(0,0,0,0.1)",
            showgrid:true
        },
        showlegend:false
    };
    const config={responsive:true,displayModeBar:false};
    Plotly.newPlot(divId,[{
        x:[],
        y:[],
        type:"scatter",
        mode:"lines",
        line:{color:color,width:2},
        fill:"tozeroy",
        fillcolor:color.replace(")",",0.2)").replace("#","rgba(").split(",").map((v,i)=>i===3?parseFloat(v):v).join(",")
    }],layout,config);
}

function initCharts(){
    createPlotlyChart("cpuChart","#667eea","CPU Load",100);
    createPlotlyChart("tempChart","#f5576c","Temperature",100);
    createPlotlyChart("ramChart","#667eea","RAM Usage",100);
    
    const diskLayout={
        margin:{t:20,r:20,b:40,l:50},
        paper_bgcolor:"rgba(0,0,0,0)",
        plot_bgcolor:"rgba(0,0,0,0)",
        xaxis:{type:"date",tickformat:"%H:%M:%S",gridcolor:"rgba(0,0,0,0.1)",showgrid:true,nticks:8},
        yaxis:{range:[0,100],gridcolor:"rgba(0,0,0,0.1)",showgrid:true},
        showlegend:true
    };
    Plotly.newPlot("diskChart",[
        {x:[],y:[],type:"scatter",mode:"lines",line:{color:"#667eea",width:2},fill:"tozeroy",name:"Система"},
        {x:[],y:[],type:"scatter",mode:"lines",line:{color:"#f5576c",width:2},fill:"tozeroy",name:"Внешний"}
    ],diskLayout,{responsive:true,displayModeBar:false});
}

async function loadHistory(){
    if(historyLoaded)return;
    try{
        const r=await fetch("/mon/history");
        const d=await r.json();
        if(d.labels&&d.labels.length>1){
            const times=d.labels.map(ts=>new Date(ts.split(".").reverse().join("-")+" "+ts.split(" ")[1]));
            
            Plotly.extendTraces("cpuChart",{x:[[...times]],y:[[...d.cpu]]},[0],60);
            Plotly.extendTraces("tempChart",{x:[[...times]],y:[[...d.temp]]},[0],60);
            Plotly.extendTraces("ramChart",{x:[[...times]],y:[[...d.ram]]},[0],60);
            Plotly.extendTraces("diskChart",{x:[[...times],[...times]],y:[[...d.disk_sys],[...d.disk_ext]]},[0,1],60);
            
            historyLoaded=true;
            console.log("✅ History loaded:",d.labels.length,"points");
        }
    }catch(e){console.log("History error:",e)}
}

function updateCharts(data){
    const now=new Date();
    if(historyLoaded){
        Plotly.extendTraces("cpuChart",{x:[[now]],y:[[data.cpu.usage]]},[0],60);
        Plotly.extendTraces("tempChart",{x:[[now]],y:[[data.cpu.temp]]},[0],60);
        Plotly.extendTraces("ramChart",{x:[[now]],y:[[data.ram.usage]]},[0],60);
        Plotly.extendTraces("diskChart",{x:[[now],[now]],y:[[data.disk.system.percent],[data.disk.external.mounted?data.disk.external.percent:0]]},[0,1],60);
    }
}

async function load(){
    try{
        const r=await fetch("/mon/data"),d=await r.json();
        document.getElementById("ts").textContent=d.timestamp;
        document.getElementById("hn").textContent=d.system.hostname;
        document.getElementById("bt").textContent=d.system.boot;
        document.getElementById("up").textContent=d.system.uptime;
        document.getElementById("wan").textContent=d.network.wan_ip;
        const cu=d.cpu.usage;document.getElementById("cu").textContent=cu+"%";
        document.getElementById("cb").style.width=cu+"%";document.getElementById("cb").style.background=gc(cu);
        document.getElementById("ct").textContent=d.cpu.temp+"°C";
        const ru=d.ram.usage;document.getElementById("ru").textContent=ru+"%";
        document.getElementById("rb").style.width=ru+"%";document.getElementById("rb").style.background=gc(ru);
        document.getElementById("rd").textContent=d.ram.used_gb+"GB / "+d.ram.total_gb+"GB";
        if(d.disk.system){const s=d.disk.system;document.getElementById("ds").textContent=s.percent+"%";document.getElementById("db").style.width=s.percent+"%";document.getElementById("db").style.background=gc(s.percent);document.getElementById("dd").textContent=s.used_gb+"GB / "+s.total_gb+"GB"}
        if(d.disk.external&&d.disk.external.mounted){const e=d.disk.external;document.getElementById("de").textContent=e.percent+"%";document.getElementById("deb").style.width=e.percent+"%";document.getElementById("deb").style.background=gc(e.percent);document.getElementById("ded").textContent=e.used+" / "+e.total+" ("+e.free+" free)"}
        else{document.getElementById("de").textContent="—";document.getElementById("deb").style.width="0%";document.getElementById("ded").textContent="Не смонтирован"}
        if(d.network.lan1){document.getElementById("l1i").textContent=d.network.lan1.ip;document.getElementById("l1r").textContent=fb(d.network.lan1.rx_mb);document.getElementById("l1t").textContent=fb(d.network.lan1.tx_mb)}
        if(d.network.lan2){document.getElementById("l2i").textContent=d.network.lan2.ip;document.getElementById("l2r").textContent=fb(d.network.lan2.rx_mb);document.getElementById("l2t").textContent=fb(d.network.lan2.tx_mb)}
        if(historyLoaded)updateCharts(d);
    }catch(e){console.error(e)}
}

initCharts();
loadHistory().then(()=>{load();setInterval(load,10000)});
</script></body></html>""")

load_history()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
