"""DVD AutoRip – vložíš disk, appka ho vyripuje (MakeMKV), překóduje (HandBrake),
pojmenuje přes TMDB, uloží do složky Jellyfinu a pošle notifikaci do Home Assistantu.

Spuštění:  pip install flask   →   python app.py   →   http://localhost:8080
Nastavení: vedle app.py vytvoř config.json (stačí přepsat jen to, co se liší).
"""
import ctypes, json, os, queue, re, shutil, subprocess, threading, time, urllib.parse, urllib.request, uuid
from pathlib import Path
from flask import Flask, jsonify, request

CFG = {
    "drive": "D",
    "tmp": r"C:\rip\tmp",
    "out": r"\\HOMEASSISTANT\media\Jellyfin\Filmy",
    "out_kids": r"\\HOMEASSISTANT\media\Jellyfin\Pohádky",  # pohádky / animované / rodinné (podle žánru z TMDB)
    "makemkv": r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",
    "handbrake": r"C:\Program Files\HandBrake\HandBrakeCLI.exe",
    "preset": "H.265 MKV 576p25",
    "minlength": 2400,          # vteřiny; kratší tituly (bonusy) se přeskočí
    "dynamic_minlength": True,   # automaticky odvodit minimální délku z disku    "tmdb_key": "",             # API klíč z themoviedb.org (zdarma)
    "omdb_key": "",             # API klíč z omdbapi.com (zdarma)
    "tmdb_lang": "cs-CZ",
    "ha_webhook": "",           # např. http://homeassistant.local:8123/api/webhook/dvd_rip
    "jellyfin_url": "",         # např. http://192.168.1.50:8096
    "jellyfin_key": "",         # API klíč: Jellyfin → Dashboard → API Keys
    "port": 8080,
    "debug": True,            # zobrazit /debug a /api/debug s chybami a logy
}
cfg_file = Path(__file__).with_name("config.json")
if cfg_file.exists():
    _raw = cfg_file.read_text(encoding="utf-8").strip()
    if _raw:
        CFG.update(json.loads(_raw))

app = Flask(__name__)
jobs, enc_q = [], queue.Queue()
state = {"label": None}
DEBUG = []  # poslední chyby/události pro /debug


def dlog(msg, **extra):
    import datetime
    DEBUG.insert(0, {"time": datetime.datetime.now().isoformat(timespec="seconds"), "msg": str(msg), **extra})
    del DEBUG[50:]


# ---------- pomocné funkce ----------
def disc_label():
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(f"{CFG['drive']}:\\", buf, 261, None, None, None, None, 0)
    return buf.value if ok else None


def eject():
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f"open {CFG['drive']}: type cdaudio alias cd", None, 0, None)
    mci("set cd door open", None, 0, None)
    mci("close cd", None, 0, None)


def notify(msg):
    if not CFG["ha_webhook"]:
        return
    try:
        req = urllib.request.Request(CFG["ha_webhook"], json.dumps({"message": msg}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def refresh_jellyfin():
    if not (CFG["jellyfin_url"] and CFG["jellyfin_key"]):
        return
    try:
        req = urllib.request.Request(CFG["jellyfin_url"].rstrip("/") + "/Library/Refresh", method="POST",
                                     headers={"X-Emby-Token": CFG["jellyfin_key"]})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def guess_title(label):
    q = re.sub(r"[_.]+", " ", label)
    q = re.sub(r"\b(disc|disk|dvd|cd)\s*\d*\b", "", q, flags=re.I).strip().title()
    # OMDb (The Open Movie Database)
    if CFG.get("omdb_key"):
        try:
            # nejprve přímé vyhledání podle názvu
            url = "http://www.omdbapi.com/?" + urllib.parse.urlencode(
                {"apikey": CFG["omdb_key"], "t": q})
            data = json.load(urllib.request.urlopen(url, timeout=10))
            if data.get("Response") == "True":
                title = data.get("Title") or q
                year = (data.get("Year") or "")[:4]
                genres = (data.get("Genre") or "").lower()
                kids = ("animation" in genres) or ("family" in genres) or ("children" in genres)
                return (f"{title} ({year})" if year else title), kids
            # fallback: vyhledat v seznamu a vzít první výsledek
            url = "http://www.omdbapi.com/?" + urllib.parse.urlencode(
                {"apikey": CFG["omdb_key"], "s": q})
            data = json.load(urllib.request.urlopen(url, timeout=10))
            if data.get("Response") == "True" and data.get("Search"):
                imdb_id = data["Search"][0].get("imdbID")
                if imdb_id:
                    url = "http://www.omdbapi.com/?" + urllib.parse.urlencode(
                        {"apikey": CFG["omdb_key"], "i": imdb_id})
                    data = json.load(urllib.request.urlopen(url, timeout=10))
                    if data.get("Response") == "True":
                        title = data.get("Title") or q
                        year = (data.get("Year") or "")[:4]
                        genres = (data.get("Genre") or "").lower()
                        kids = ("animation" in genres) or ("family" in genres) or ("children" in genres)
                        return (f"{title} ({year})" if year else title), kids
        except Exception:
            pass
    # TMDB
    if CFG["tmdb_key"]:
        try:
            url = "https://api.themoviedb.org/3/search/movie?" + urllib.parse.urlencode(
                {"api_key": CFG["tmdb_key"], "query": q, "language": CFG["tmdb_lang"]})
            res = json.load(urllib.request.urlopen(url, timeout=10))["results"]
            if res:
                year = (res[0].get("release_date") or "")[:4]
                kids = bool({16, 10751} & set(res[0].get("genre_ids", [])))  # 16 = animovaný, 10751 = rodinný
                return (f"{res[0]['title']} ({year})" if year else res[0]["title"]), kids
        except Exception:
            pass
    return (q or label), False


def fail(job, e):
    job["status"], job["error"] = "error", str(e)
    dlog(f"fail job={job.get('id')} label={job.get('label')}: {e}", job=job.get("id"))
    notify(f"Chyba u disku {job['label']}: {e}")


def dynamic_minlength():
    """Zjistí délky titulů na disku a odvodí rozumné minimum (aby se neřezaly epizody seriálu,
    ale přeskočily bonusy). Vrací vteřiny pro --minlength."""
    try:
        p = subprocess.Popen(
            [CFG["makemkv"], "-r", "info", "disc:0", "--minlength=0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        lengths = {}
        for line in p.stdout:
            m = re.match(r'TINFO:(\d+),9,\d+,"(\d+):(\d+):(\d+)"', line)
            if m:
                tid = int(m.group(1))
                sec = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
                lengths[tid] = sec
        p.wait()
        if not lengths:
            return int(CFG.get("minlength", 0) or 0)
        maxdur = max(lengths.values())
        # 20 % nejdelšího titulu, min. 60 s; nikdy ne víc než konfigurované maximum
        ml = max(60, int(maxdur * 0.2))
        cap = int(CFG.get("minlength", 0) or 0)
        if cap:
            ml = min(ml, cap)
        return ml
    except Exception:
        return int(CFG.get("minlength", 0) or 0)


# ---------- ripování ----------
def rip(label):
    job = dict(id=uuid.uuid4().hex[:8], label=label, title=label, status="rip", progress=0, error=None)
    jobs.insert(0, job)
    work = Path(CFG["tmp"]) / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    job["work"] = str(work)
    try:
        job["title"], job["kids"] = guess_title(label)
        if CFG.get("dynamic_minlength"):
            minlength = dynamic_minlength()
        else:
            minlength = int(CFG.get("minlength", 0) or 0)
        job["minlength"] = minlength
        p = subprocess.Popen(
            [CFG["makemkv"], "-r", "--progress=-same", "mkv", "disc:0", "all", str(work),
             f"--minlength={minlength}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        log_lines = []
        for line in p.stdout:
            log_lines.append(line.rstrip("\n"))
            m = re.match(r"PRGV:(\d+),(\d+),(\d+)", line)
            if m and int(m[3]):
                job["progress"] = int(int(m[2]) / int(m[3]) * 100)
        rc = p.wait()
        try:
            (work / "makemkv.log").write_text("\n".join(log_lines), encoding="utf-8", errors="replace")
        except Exception:
            pass
        if rc != 0:
            tail = "\n".join(log_lines[-15:])
            raise RuntimeError(f"MakeMKV skončil s chybou (kód {rc})\n{tail}")
        if not list(work.glob("*.mkv")):
            raise RuntimeError("Na disku není titul delší než minimální délka")
        job["status"], job["progress"] = "queued", 0
        enc_q.put(job)
    except Exception as e:
        fail(job, e)
    eject()


def watcher():
    last = None
    while True:
        label = disc_label()
        state["label"] = label
        if label and label != last:
            last = label
            rip(label)
        elif not label:
            last = None
        time.sleep(5)


# ---------- překódování ----------
def encoder():
    while True:
        job = enc_q.get()
        job["status"], job["progress"] = "encode", 0
        try:
            src = max(Path(job["work"]).glob("*.mkv"), key=lambda f: f.stat().st_size)  # hlavní film
            name = re.sub(r'[<>:"/\\|?*]', "", job["title"]).strip()
            dest = Path(CFG["out_kids"] if job.get("kids") else CFG["out"]) / name
            dest.mkdir(parents=True, exist_ok=True)
            p = subprocess.Popen(
                [CFG["handbrake"], "-i", str(src), "-o", str(dest / f"{name}.mkv"),
                 "--preset", CFG["preset"], "--all-audio", "--all-subtitles"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            buf = ""
            while True:
                chunk = p.stdout.read1(256)
                if not chunk:
                    break
                buf = (buf + chunk.decode(errors="ignore"))[-300:]
                m = re.findall(r"(\d+\.\d+) %", buf)
                if m:
                    job["progress"] = int(float(m[-1]))
            if p.wait() != 0:
                raise RuntimeError("HandBrake skončil s chybou")
            shutil.rmtree(job["work"], ignore_errors=True)
            job["status"], job["progress"] = "done", 100
            refresh_jellyfin()
            notify(f"Hotovo: {name}")
        except Exception as e:
            fail(job, e)


# ---------- API ----------
@app.get("/api/jobs")
def api_jobs():
    return jsonify(drive=state["label"], letter=CFG["drive"],
                   jobs=[{k: v for k, v in j.items() if k != "work"} for j in jobs[:30]])


@app.post("/api/jobs/<jid>/title")
def api_title(jid):
    title = (request.json or {}).get("title", "").strip()
    for j in jobs:
        if j["id"] == jid and title and j["status"] in ("rip", "queued"):
            j["title"] = title
            return jsonify(ok=True)
    return jsonify(ok=False), 400


@app.get("/api/debug")
def api_debug():
    if not CFG.get("debug", False):
        return jsonify(ok=False, error="debug disabled"), 403
    return jsonify(ok=True, state=state, jobs=jobs[:50], debug=DEBUG)


@app.get("/debug")
def debug_page():
    if not CFG.get("debug", False):
        return "debug disabled", 403
    rows = "".join(
        f"<tr><td>{d['time']}</td><td><pre>{d['msg']}</pre></td></tr>" for d in DEBUG)
    return f"""<!doctype html><meta charset=utf-8><title>Debug</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:20px}} table{{width:100%;border-collapse:collapse}} td{{border:1px solid #333;padding:8px;vertical-align:top}} pre{{white-space:pre-wrap;word-break:break-word}}</style>
<h1>Debug</h1>
<p><a href=/api/debug>JSON</a> | <a href=/>App</a></p>
<table><tr><th>čas</th><th>zpráva</th></tr>{rows}</table>"""


@app.get("/")
def index():
    return PAGE


PAGE = """<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DVD AutoRip</title>
<style>
:root{--bg:#eef1f4;--card:#fff;--ink:#1b2430;--mute:#66727f;--acc:#0b6e6e;--track:#d5dbe2;--ok:#2f7d4f;--err:#b3392f}
@media(prefers-color-scheme:dark){:root{--bg:#141a21;--card:#1d252e;--ink:#e4e9ee;--mute:#8d99a6;--acc:#4fc3c3;--track:#2c3641;--ok:#6cc08b;--err:#ef7d72}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 "Segoe UI",system-ui,sans-serif}
main{max-width:680px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:1.6rem;font-weight:700;margin:0 0 4px}
.drive{display:flex;align-items:center;gap:10px;color:var(--mute);margin-bottom:24px}
.disc{width:18px;height:18px;border-radius:50%;border:4px solid var(--mute);background:var(--bg)}
.disc.in{border-color:var(--acc);animation:spin 3s linear infinite;border-top-color:transparent}
@keyframes spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.disc.in{animation:none}}
.job{background:var(--card);border-radius:10px;padding:14px 16px;margin-bottom:12px;border-left:5px solid var(--acc)}
.job.done{border-color:var(--ok)}.job.error{border-color:var(--err)}.job.queued{border-color:var(--mute)}
.top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.title{font-weight:600;font-size:1.05rem;word-break:break-word}
.edit{background:none;border:0;color:var(--acc);cursor:pointer;font:inherit;padding:0}
.edit:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.meta{color:var(--mute);font-size:.88rem;margin-top:2px}
.bar{height:8px;background:var(--track);border-radius:4px;margin-top:10px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--acc);transition:width .4s}
.job.done .bar i{background:var(--ok)}.job.error .bar{display:none}
.err{color:var(--err);margin-top:8px;font-size:.92rem}
.empty{color:var(--mute);padding:20px 0}
</style></head><body><main>
<h1>DVD AutoRip</h1>
<div class="drive"><span id="disc" class="disc"></span><span id="drive"></span></div>
<div id="list"></div>
</main>
<script>
const T={rip:"Ripuju z disku",queued:"Čeká na překódování",encode:"Překódovávám do H.265",done:"Hotovo, je ve Jellyfinu",error:"Chyba"};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function rename(id,old){
  const t=prompt("Název filmu (např. Matrix (1999))",old);
  if(t&&t.trim()) await fetch("/api/jobs/"+id+"/title",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:t})});
  load();
}
async function load(){
  try{
    const d=await (await fetch("/api/jobs")).json();
    document.getElementById("disc").className="disc"+(d.drive?" in":"");
    document.getElementById("drive").textContent="Mechanika "+d.letter+": "+(d.drive?"disk "+d.drive:"vlož disk");
    document.getElementById("list").innerHTML=d.jobs.length?d.jobs.map(j=>`
      <div class="job ${j.status}">
        <div class="top"><span class="title">${esc(j.title)}</span>
        ${j.status==="rip"||j.status==="queued"?`<button class="edit" data-id="${j.id}" data-t="${esc(j.title)}">Přejmenovat</button>`:""}</div>
        <div class="meta">${T[j.status]}${j.status==="rip"||j.status==="encode"?" · "+j.progress+" %":""} · disk ${esc(j.label)}</div>
        <div class="bar"><i style="width:${j.progress}%"></i></div>
        ${j.error?`<div class="err">${esc(j.error)}</div>`:""}
      </div>`).join(""):'<div class="empty">Zatím nic. Vlož DVD a o zbytek se postará appka.</div>';
    document.querySelectorAll(".edit").forEach(b=>b.onclick=()=>rename(b.dataset.id,b.dataset.t));
  }catch(e){}
}
load();setInterval(load,2000);
</script></body></html>"""


if __name__ == "__main__":
    ctypes.windll.kernel32.SetErrorMode(1)  # žádné Windows dialogy při prázdné mechanice
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=encoder, daemon=True).start()
    app.run(host="0.0.0.0", port=CFG["port"])
