"""MP3 Maker helper - private, local-only. Makes the MP3/OGG audio for the MP3Maker.html page.
It runs on YOUR computer at http://127.0.0.1:8765 and nothing is put online.

FIRST TIME: install Python once (python.org, tick "Add Python to PATH"), then in File Explorer double-click
THIS file (MP3Maker.py). It installs two small free tools, sets up the page's "Start helper" button (and, if you
say yes, starts itself when you sign in to Windows), and opens the app. After that you just open MP3Maker.html.
To undo the setup:  py MP3Maker.py remove

WHERE THE COURSE PLAYERS COME FROM (no copies needed):
  - MP3Maker.html opened from your GitHub Pages site: it reads the players from that same repo, automatically.
  - Otherwise: GITHUB_REPO below, or the folder this file sits in.
  - Or use "Open a file from this computer" on the page."""
import asyncio, json, os, subprocess, sys, tempfile, threading, time, wave, webbrowser
import re, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------- SETTINGS -----------------------------
GITHUB_REPO   = ''    # e.g. 'yourname/yourrepo'. Empty = use the folder this file is in.
GITHUB_BRANCH = ''    # empty = the repo's default branch
SUBFOLDER     = ''    # players are in the root. Only fill this if they are in a folder, e.g. 'arch'
MIN_KB        = 150   # GitHub mode only: ignore .html files smaller than this (they are not course players)
EXTRA_ORIGINS = []    # only if your player site has its own domain, e.g. ['https://apps.example.com']
# --------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, SUBFOLDER) if SUBFOLDER else HERE
SR, PORT = 24000, 8765
MARK = b'/*@@data-start*/'

def need(mod, pkg):
    try: __import__(mod)
    except ImportError:
        print(f'Installing {pkg} (first run only)...')
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', pkg])
need('edge_tts', 'edge-tts'); need('imageio_ffmpeg', 'imageio-ffmpeg')
import edge_tts, imageio_ffmpeg
FF = imageio_ffmpeg.get_ffmpeg_exe()
_voices = None

def voices():
    global _voices
    if _voices is None:
        _voices = [{'n': v['ShortName'], 'l': v['Locale'], 'g': v['Gender']}
                   for v in asyncio.run(edge_tts.list_voices())]
    return _voices

# ---------------------------- course source ----------------------------
def http_get(url):
    h = {'User-Agent': 'MP3Maker'}
    tok = os.environ.get('MP3MAKER_TOKEN')          # only for a private repo; never put a token in a file
    if tok: h['Authorization'] = 'token ' + tok
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r: return r.read()

_cache = {}   # name -> (time, bytes)
def gh_path(name):
    return urllib.parse.quote((SUBFOLDER.strip('/') + '/' if SUBFOLDER else '') + name)

def list_courses(repo=''):
    repo = repo or GITHUB_REPO
    if repo:
        url = f'https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(SUBFOLDER.strip("/"))}'
        if GITHUB_BRANCH: url += '?ref=' + urllib.parse.quote(GITHUB_BRANCH)
        items = json.loads(http_get(url))
        return sorted(f['name'] for f in items if f['type'] == 'file' and f['name'].lower().endswith('.html') and f['name'] != 'MP3Maker.html'
                      and f.get('size', 0) >= MIN_KB * 1024)
    out = []
    for f in os.listdir(LOCAL):
        if not f.lower().endswith('.html') or f == 'MP3Maker.html': continue
        p = os.path.join(LOCAL, f)
        key = ('L', f, os.path.getmtime(p))
        if key not in _cache:
            _cache[key] = (0, MARK in open(p, 'rb').read())
        if _cache[key][1]: out.append(f)           # only real course players (they carry the data block)
    return sorted(out)

def get_course(name, repo=''):
    name = os.path.basename(name)
    repo = repo or GITHUB_REPO
    if repo:
        hit = _cache.get((repo, name))
        if hit and time.time() - hit[0] < 60: return hit[1]
        body = http_get(f'https://raw.githubusercontent.com/{repo}/{GITHUB_BRANCH or "HEAD"}/{gh_path(name)}')
        _cache[(repo, name)] = (time.time(), body); return body
    return open(os.path.join(LOCAL, name), 'rb').read()

# ------------------------------- audio -------------------------------
async def synth_all(keys, tmp):
    sem = asyncio.Semaphore(5)
    async def one(i, key):
        text, voice, rate = key
        path = os.path.join(tmp, f'c{i}.mp3'); err = None
        async with sem:
            for _ in range(3):
                try:
                    await edge_tts.Communicate(text, voice, rate=rate).save(path)
                    return path
                except Exception as e:
                    err = e; await asyncio.sleep(1)
        raise RuntimeError(f'Voice failed for "{text[:30]}" ({voice}): {err}')
    return await asyncio.gather(*[one(i, k) for i, k in enumerate(keys)])

def render(items, fmt):
    with tempfile.TemporaryDirectory() as tmp:
        keys = []
        for it in items:
            if it['t'] == 's':
                k = (it['text'], it['voice'], it['rate'])
                if k not in keys: keys.append(k)
        pcm = {}
        for k, p in zip(keys, asyncio.run(synth_all(keys, tmp))):
            w = p[:-4] + '.wav'
            subprocess.run([FF, '-y', '-loglevel', 'error', '-i', p, '-ar', str(SR), '-ac', '1', '-f', 'wav', w], check=True)
            with wave.open(w) as r: pcm[k] = r.readframes(r.getnframes())
        joined = os.path.join(tmp, 'all.wav')
        with wave.open(joined, 'wb') as o:
            o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
            for it in items:
                if it['t'] == 's': o.writeframes(pcm[(it['text'], it['voice'], it['rate'])])
                else: o.writeframes(b'\0' * 2 * int(SR * it['ms'] / 1000))
        dest = os.path.join(tmp, 'out.' + fmt)
        codec = ['-c:a', 'libmp3lame', '-q:a', '4'] if fmt == 'mp3' else ['-c:a', 'libvorbis', '-q:a', '4']
        subprocess.run([FF, '-y', '-loglevel', 'error', '-i', joined] + codec + [dest], check=True)
        return open(dest, 'rb').read()

def origin_ok(o):
    if not o or o == 'null': return True            # no Origin = same page; "null" = page opened from a file
    host = urllib.parse.urlparse(o).hostname or ''
    return host in ('127.0.0.1', 'localhost') or host.endswith('.github.io') or o in EXTRA_ORIGINS

REPO_RE = re.compile(r'^[\w.-]+/[\w.-]+$')
HOME_FALLBACK = '<meta charset="utf-8"><title>MP3 Maker helper</title><body style="font:18px system-ui;padding:30px"><b>MP3 Maker helper is running.</b><p>Go back to your MP3Maker.html page.</p>'

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def cors(self):
        o = self.headers.get('Origin')
        if o and origin_ok(o):
            self.send_header('Access-Control-Allow-Origin', o); self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
    def out(self, code, body, ctype):
        if isinstance(body, str): body = body.encode('utf-8')
        self.send_response(code); self.send_header('Content-Type', ctype); self.cors()
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self):
        if not origin_ok(self.headers.get('Origin')): return self.out(403, 'blocked', 'text/plain')
        self.send_response(204); self.cors()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age', '600'); self.end_headers()
    def do_GET(self):
        if not origin_ok(self.headers.get('Origin')): return self.out(403, 'blocked', 'text/plain')
        u = urllib.parse.urlparse(self.path); p = u.path
        repo = urllib.parse.parse_qs(u.query).get('repo', [''])[0]
        if repo and not REPO_RE.match(repo): repo = ''
        try:
            if p == '/':
                page = os.path.join(HERE, 'MP3Maker.html')
                self.out(200, open(page, 'rb').read() if os.path.exists(page) else HOME_FALLBACK, 'text/html; charset=utf-8')
            elif p == '/api/ping': self.out(200, 'ok', 'text/plain')
            elif p == '/api/voices': self.out(200, json.dumps(voices()), 'application/json')
            elif p == '/api/courses': self.out(200, json.dumps(list_courses(repo)), 'application/json')
            elif p.startswith('/courses/'):
                self.out(200, get_course(urllib.parse.unquote(p[len('/courses/'):]), repo), 'text/html; charset=utf-8')
            else: self.out(404, 'not found', 'text/plain')
        except Exception as e: self.out(500, str(e), 'text/plain')
    def do_POST(self):
        if not origin_ok(self.headers.get('Origin')): return self.out(403, 'blocked', 'text/plain')
        try:
            d = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            fmt = 'ogg' if d.get('format') == 'ogg' else 'mp3'
            self.out(200, render(d['items'], fmt), 'audio/ogg' if fmt == 'ogg' else 'audio/mpeg')
        except Exception as e: self.out(500, str(e), 'text/plain')

# ---------------- Windows setup: "Start helper" button + optional start at sign-in ----------------
def pyw():
    exe = sys.executable
    if os.name == 'nt':
        w = os.path.join(os.path.dirname(exe), 'pythonw.exe')       # pythonw = no console window
        if os.path.exists(w): return w
    return exe

def setup_windows():
    import winreg
    script = os.path.abspath(__file__)
    k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\mp3maker')
    winreg.SetValue(k, '', winreg.REG_SZ, 'URL:MP3 Maker')
    winreg.SetValueEx(k, 'URL Protocol', 0, winreg.REG_SZ, '')
    c = winreg.CreateKey(k, r'shell\open\command')
    winreg.SetValue(c, '', winreg.REG_SZ, f'"{pyw()}" "{script}" "%1"')
    print('The "Start helper" button on the page is set up.')
    try: ans = input('Also start the helper automatically when you sign in to Windows? (y/n): ').strip().lower()
    except Exception: ans = 'n'
    if ans.startswith('y'):
        r = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run')
        winreg.SetValueEx(r, 'MP3Maker', 0, winreg.REG_SZ, f'"{pyw()}" "{script}" autostart')
        print('Done: it will start by itself from now on.')

def remove_windows():
    import winreg
    for root, sub in ((winreg.HKEY_CURRENT_USER, r'Software\Classes\mp3maker\shell\open\command'), (winreg.HKEY_CURRENT_USER, r'Software\Classes\mp3maker\shell\open'),
                      (winreg.HKEY_CURRENT_USER, r'Software\Classes\mp3maker\shell'), (winreg.HKEY_CURRENT_USER, r'Software\Classes\mp3maker')):
        try: winreg.DeleteKey(root, sub)
        except OSError: pass
    try:
        r = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(r, 'MP3Maker')
    except OSError: pass
    print('Setup removed.')

def is_set_up():
    try:
        import winreg; winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\mp3maker'); return True
    except Exception: return False

if __name__ == '__main__':
    args = sys.argv[1:]
    quiet = bool(args)                                    # started by the page's button or at sign-in: no window, no browser
    if args and args[0] == 'remove':
        if os.name == 'nt': remove_windows()
        else: print('Nothing to remove on this system.')
        sys.exit()
    try:
        srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)   # 127.0.0.1 = only this computer can reach it
    except OSError:
        if not quiet:
            print('MP3 Maker helper is already running.'); webbrowser.open(f'http://127.0.0.1:{PORT}'); input('Press Enter to close...')
        sys.exit()
    if not quiet:
        if os.name == 'nt' and not is_set_up():
            try: setup_windows()
            except Exception as e: print('Could not set up the Start button:', e)
        threading.Timer(1, lambda: webbrowser.open(f'http://127.0.0.1:{PORT}')).start()
        print(f'MP3 Maker helper running at http://127.0.0.1:{PORT}  (you can close this window to stop it)')
        print('Course players from: ' + (f'GitHub {GITHUB_REPO}' if GITHUB_REPO else 'the repo of the page you open (GitHub Pages), else ' + LOCAL))
    try: srv.serve_forever()
    except Exception as e:
        if not quiet: print('Stopped:', e); input('Press Enter to close...')
