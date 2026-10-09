"""MINARDI PIUME · backoffice QR. Avvio: uvicorn main:app --host 127.0.0.1 --port 8080"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import fitz
import qrcode
from PIL import Image, ImageDraw
try:
    import geoip2.database
    from geoip2.errors import AddressNotFoundError
except ImportError:
    geoip2 = None

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'minardi_qr.sqlite3'
SECRET_PATH = DATA_DIR / 'session_secret.key'
CONFIG_PATH = ROOT / 'config.json'
CONFIG = json.loads(CONFIG_PATH.read_text('utf-8')) if CONFIG_PATH.exists() else {}
BASE_URL = (os.environ.get('BASE_URL') or ('https://' + os.environ['RENDER_EXTERNAL_HOSTNAME'] if os.environ.get('RENDER_EXTERNAL_HOSTNAME') else '') or CONFIG.get('base_url') or 'http://127.0.0.1:8080').rstrip('/')
TZ = ZoneInfo('Europe/Rome')
SITE_URL = 'https://www.minardipiume.it/index-english.html'
GEOIP_PATH = Path(os.environ.get('GEOIP_DB', str(DATA_DIR / 'GeoLite2-City.mmdb')))
PASSWORD_ITERATIONS = 400_000
if not SECRET_PATH.exists():
    SECRET_PATH.write_text(secrets.token_hex(48), 'utf-8')
    try: SECRET_PATH.chmod(0o600)
    except OSError: pass
SESSION_SECRET = SECRET_PATH.read_text('utf-8').strip()


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA busy_timeout=15000')
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE, passhash TEXT NOT NULL, created_at INTEGER NOT NULL,
            password_version INTEGER NOT NULL DEFAULT 0, must_change_password INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS qr_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            event TEXT NOT NULL DEFAULT '', target_url TEXT NOT NULL,
            created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, qr_id INTEGER NOT NULL,
            scanned_at INTEGER NOT NULL, visitor_hash TEXT NOT NULL,
            os TEXT NOT NULL, browser TEXT NOT NULL, device TEXT NOT NULL,
            country TEXT NOT NULL DEFAULT 'Non disponibile',
            city TEXT NOT NULL DEFAULT 'Non disponibile',
            referrer TEXT NOT NULL DEFAULT '', is_demo INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(qr_id) REFERENCES qr_codes(id)
        );
        CREATE INDEX IF NOT EXISTS scans_date_idx ON scans(scanned_at);
        CREATE INDEX IF NOT EXISTS scans_qr_idx ON scans(qr_id, scanned_at);
        ''')
        # Migrazione retrocompatibile per gli account delle versioni precedenti.
        fields = {r['name'] for r in c.execute('PRAGMA table_info(admins)')}
        if 'password_version' not in fields:
            c.execute('ALTER TABLE admins ADD COLUMN password_version INTEGER NOT NULL DEFAULT 0')
        if 'must_change_password' not in fields:
            c.execute('ALTER TABLE admins ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')


def pass_hash(pw: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'


def check_password(pw: str, encoded: str) -> bool:
    try:
        _, n, salt, expected = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), int(n))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False



def bootstrap_production_admin():
    """Crea il primo admin SOLO al primo avvio se fornito da variabili segrete server.

    Mai inserire la password nel repository o in render.yaml.
    """
    email = os.environ.get('BOOTSTRAP_ADMIN_EMAIL', '').strip().lower()
    pw = os.environ.get('BOOTSTRAP_ADMIN_PASSWORD', '')
    if not email and not pw:
        return
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(pw) < 12:
        raise RuntimeError('BOOTSTRAP_ADMIN_EMAIL e BOOTSTRAP_ADMIN_PASSWORD devono essere validi (password di almeno 12 caratteri).')
    with connect() as c:
        if c.execute('SELECT id FROM admins LIMIT 1').fetchone():
            return
        c.execute('INSERT INTO admins(email,passhash,created_at,must_change_password) VALUES(?,?,?,1)',
                  (email,pass_hash(pw),int(datetime.now(timezone.utc).timestamp())))
        if not c.execute('SELECT id FROM qr_codes LIMIT 1').fetchone():
            c.execute('INSERT INTO qr_codes(slug,name,event,target_url,created_at) VALUES(?,?,?,?,?)',
                      ('performance-days-2026','Minardi Piume · Performance Days',
                       'Performance Days · Ottobre 2026',SITE_URL,int(datetime.now(timezone.utc).timestamp())))


def cli_setup(reset=False):
    init_db()
    with connect() as c:
        count = c.execute('SELECT count(*) AS n FROM admins').fetchone()['n']
        if count and not reset:
            print('Amministratore già presente. Usa --reset-password per modificare le credenziali.')
            return
    print('=== MINARDI PIUME · Configurazione amministratore ===')
    email = input('Email amministratore: ').strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        print('Indirizzo email non valido.'); sys.exit(1)
    password = getpass('Nuova password (minimo 12 caratteri): ')
    if len(password) < 12:
        print('Password troppo corta.'); sys.exit(1)
    if password != getpass('Ripeti password: '):
        print('Le password non coincidono.'); sys.exit(1)
    with connect() as c:
        c.execute('DELETE FROM admins') if reset else None
        c.execute('INSERT INTO admins(email,passhash,created_at) VALUES (?,?,?)',
                  (email, pass_hash(password), int(datetime.now(timezone.utc).timestamp())))
        if not c.execute('SELECT id FROM qr_codes LIMIT 1').fetchone():
            c.execute('INSERT INTO qr_codes(slug,name,event,target_url,created_at) VALUES (?,?,?,?,?)',
                      ('performance-days-2026', 'Minardi Piume · Performance Days',
                       'Performance Days · Ottobre 2026', SITE_URL, int(datetime.now(timezone.utc).timestamp())))
    print('Operazione completata. Credenziali salvate localmente nel database.')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('--init','--reset-password'):
        cli_setup(reset=sys.argv[1] == '--reset-password')
    else:
        print('Usa: python main.py --init | --reset-password')
    sys.exit(0)

init_db()
bootstrap_production_admin()
app = FastAPI(title='Minardi Piume · QR Analytics', docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie='minardi_qr_session',
                   max_age=3600 * 10, same_site='lax', https_only=os.environ.get('COOKIE_SECURE') == '1')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
_failed_logins = defaultdict(list)


@app.middleware('http')
async def security_headers(req: Request, call_next):
    res = await call_next(req)
    res.headers['X-Content-Type-Options'] = 'nosniff'
    res.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    res.headers['X-Frame-Options'] = 'DENY'
    if req.url.path.startswith(('/api','/dashboard','/login')):
        res.headers['Cache-Control'] = 'no-store'
    return res


def logged(req: Request):
    uid = req.session.get('user_id')
    if not uid:
        raise HTTPException(401, 'Accedi con un account amministratore.')
    with connect() as c:
        row = c.execute('SELECT password_version FROM admins WHERE id=?', (uid,)).fetchone()
    if not row or req.session.get('password_version',0) != row['password_version']:
        req.session.clear()
        raise HTTPException(401, 'Sessione scaduta. Effettua nuovamente l’accesso.')
    return uid


def csrf(req: Request):
    logged(req)
    token = req.headers.get('X-CSRF-Token','')
    if not token or not hmac.compare_digest(token, req.session.get('csrf','')):
        raise HTTPException(403, 'Richiesta non autorizzata. Aggiorna la pagina.')


def auth_page(req: Request):
    return (ROOT / 'static' / ('index.html' if req.session.get('user_id') else 'login.html')).read_text('utf-8')


@app.get('/', response_class=HTMLResponse)
def home(req: Request):
    return auth_page(req)


@app.get('/dashboard', response_class=HTMLResponse)
def dashboard_page(req: Request):
    return auth_page(req)


@app.get('/login', response_class=HTMLResponse)
def login_page(req: Request):
    return auth_page(req)


class LoginIn(BaseModel):
    email: str
    password: str


@app.post('/api/login')
def login(req: Request, body: LoginIn):
    ip = req.client.host if req.client else 'unknown'
    now = int(datetime.now(timezone.utc).timestamp())
    _failed_logins[ip] = [t for t in _failed_logins[ip] if now - t < 900]
    if len(_failed_logins[ip]) >= 8:
        raise HTTPException(429, 'Troppi tentativi. Riprova tra 15 minuti.')
    with connect() as c:
        user = c.execute('SELECT * FROM admins WHERE email=?', (body.email.strip().lower(),)).fetchone()
    if not user or not check_password(body.password, user['passhash']):
        _failed_logins[ip].append(now)
        raise HTTPException(401, 'Email o password non corretti.')
    _failed_logins.pop(ip, None)
    req.session.clear()
    req.session.update({'user_id': user['id'], 'email': user['email'], 'password_version': user['password_version'], 'csrf': secrets.token_urlsafe(28)})
    return {'ok': True}


@app.post('/api/logout')
def logout(req: Request):
    csrf(req)
    req.session.clear()
    return {'ok': True}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


@app.post('/api/account/password')
def change_password(req: Request, body: PasswordChange):
    csrf(req)
    if body.current_password == body.new_password:
        raise HTTPException(422, 'La nuova password deve essere diversa dalla precedente.')
    with connect() as c:
        user = c.execute('SELECT id,passhash,password_version FROM admins WHERE id=?',
                         (req.session['user_id'],)).fetchone()
        if not user or not check_password(body.current_password,user['passhash']):
            raise HTTPException(403, 'La password corrente non è corretta.')
        next_version = user['password_version'] + 1
        c.execute('UPDATE admins SET passhash=?,password_version=?,must_change_password=0 WHERE id=?',
                  (pass_hash(body.new_password),next_version,user['id']))
    req.session['password_version'] = next_version
    req.session['csrf'] = secrets.token_urlsafe(28)
    return {'ok': True, 'csrf': req.session['csrf']}


@app.get('/api/health')
def health():
    with connect() as c:
        c.execute('SELECT 1').fetchone()
    return {'status':'ok'}


@app.get('/api/me')
def me(req: Request):
    logged(req)
    with connect() as c:
        row = c.execute('SELECT must_change_password FROM admins WHERE id=?',
                        (req.session['user_id'],)).fetchone()
    return {'email': req.session.get('email'), 'csrf': req.session.get('csrf'),
            'must_change_password': bool(row['must_change_password']),
            'base_url': BASE_URL, 'public_ready': not bool(re.search(r'localhost|127\.0\.0\.1|192\.168\.', BASE_URL, re.I))}


def validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or len(url) > 1500:
        raise HTTPException(422, 'Inserisci un URL completo https:// valido.')
    return url


class QRIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    event: str = Field(default='', max_length=100)
    target_url: str = Field(min_length=8, max_length=1500)


def list_qrs():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM qr_codes ORDER BY created_at DESC,id DESC')]


@app.get('/api/qrs')
def qrs(req: Request):
    logged(req)
    return [{'id': r['id'], 'slug': r['slug'], 'name': r['name'], 'event': r['event'],
             'target_url': r['target_url'], 'active': bool(r['active']), 'created_at': r['created_at'],
             'tracked_url': f'{BASE_URL}/r/{r["slug"]}'} for r in list_qrs()]


@app.post('/api/qrs')
def create_qr(req: Request, body: QRIn):
    csrf(req)
    slug = secrets.token_urlsafe(6).replace('_','a').replace('-','b').lower()
    with connect() as c:
        c.execute('INSERT INTO qr_codes(slug,name,event,target_url,created_at) VALUES (?,?,?,?,?)',
                  (slug, body.name.strip(), body.event.strip(), validate_url(body.target_url), int(datetime.now(timezone.utc).timestamp())))
    return {'ok': True, 'slug': slug}


@app.put('/api/qrs/{slug}')
def modify_qr(slug: str, req: Request, body: QRIn):
    csrf(req)
    with connect() as c:
        row = c.execute('SELECT * FROM qr_codes WHERE slug=?', (slug,)).fetchone()
        if not row: raise HTTPException(404, 'QR non trovato')
        c.execute('UPDATE qr_codes SET name=?,event=?,target_url=? WHERE slug=?',
                  (body.name.strip(), body.event.strip(), validate_url(body.target_url), slug))
    return {'ok': True}


@app.post('/api/qrs/{slug}/status')
def toggle_qr(slug: str, req: Request):
    csrf(req)
    with connect() as c:
        row = c.execute('SELECT active FROM qr_codes WHERE slug=?', (slug,)).fetchone()
        if not row: raise HTTPException(404, 'QR non trovato')
        c.execute('UPDATE qr_codes SET active=? WHERE slug=?', (0 if row['active'] else 1, slug))
    return {'ok': True}


def get_qr(slug: str):
    with connect() as c:
        row = c.execute('SELECT * FROM qr_codes WHERE slug=?', (slug,)).fetchone()
    if not row: raise HTTPException(404, 'Codice QR non trovato')
    return dict(row)


def geo_lookup(ip: str):
    """Geolocalizzazione facoltativa OFFLINE: nessun IP inviato a servizi terzi."""
    if not GEOIP_PATH.exists() or geoip2 is None:
        return None, None
    try:
        if not ipaddress.ip_address(ip).is_global:
            return None, None
        with geoip2.database.Reader(str(GEOIP_PATH)) as reader:
            found = reader.city(ip)
        return found.country.iso_code or None, found.city.name or None
    except Exception:
        return None, None


def parse_agent(agent: str):
    low = agent.lower()
    if 'iphone' in low: os_name='iPhone / iOS'
    elif 'ipad' in low: os_name='iPadOS'
    elif 'android' in low: os_name='Android'
    elif 'windows' in low: os_name='Windows'
    elif 'mac os' in low or 'macintosh' in low: os_name='macOS'
    elif 'linux' in low: os_name='Linux'
    else: os_name='Altro'
    if 'edg/' in low: browser='Microsoft Edge'
    elif 'firefox/' in low or 'fxios/' in low: browser='Firefox'
    elif 'crios/' in low or 'chrome/' in low: browser='Chrome'
    elif 'safari/' in low: browser='Safari'
    else: browser='Altro'
    device = 'Tablet' if ('ipad' in low or 'tablet' in low) else ('Smartphone' if ('mobile' in low or 'iphone' in low or 'android' in low) else 'Computer')
    return os_name,browser,device


@app.api_route('/r/{slug}', methods=['GET','HEAD'])
def track(slug: str, req: Request):
    qr = get_qr(slug)
    if not qr['active']:
        return HTMLResponse('<h2>Questo QR code non è attivo.</h2>', status_code=410)
    agent = req.headers.get('user-agent','')[:512]
    if req.method == 'GET' and not re.search(r'bot|crawler|spider|preview|fetch|headless|telegram|facebookexternalhit', agent, re.I):
        now = datetime.now(timezone.utc)
        ip = req.client.host if req.client else ''
        if os.environ.get('TRUST_PROXY') == '1':
            ip = req.headers.get('x-forwarded-for', ip).split(',')[0].strip()
        # Non memorizza l'IP. Hash pseudonimo con rotazione giornaliera.
        day = now.astimezone(TZ).strftime('%Y-%m-%d')
        visitor_hash = hmac.new(SESSION_SECRET.encode(), f'{day}|{ip}|{agent}'.encode(), hashlib.sha256).hexdigest()
        os_name,browser,device = parse_agent(agent)
        country, city = geo_lookup(ip)
        country = country or req.headers.get('cf-ipcountry', '').strip().upper()
        country = country if re.fullmatch(r'[A-Z]{2}',country) else 'Non disponibile'
        city = city or 'Non disponibile'
        ref = req.headers.get('referer','')
        host = urlparse(ref).hostname or ''
        with connect() as c:
            c.execute('''INSERT INTO scans(qr_id,scanned_at,visitor_hash,os,browser,device,country,city,referrer)
                         VALUES(?,?,?,?,?,?,?,?,?)''',
                      (qr['id'],int(now.timestamp()),visitor_hash,os_name,browser,device,country,city,host[:255]))
    return RedirectResponse(qr['target_url'], status_code=302, headers={'Cache-Control':'no-store, no-cache, must-revalidate'})


def get_filters(period='30', slug='all', start=None, end=None):
    now = datetime.now(TZ)
    if start or end:
        try:
            start_day = date.fromisoformat(start) if start else now.date()-timedelta(days=29)
            end_day = date.fromisoformat(end) if end else now.date()
        except ValueError:
            raise HTTPException(422,'Data non valida')
        if end_day < start_day or (end_day-start_day).days > 3650:
            raise HTTPException(422,'Intervallo date non valido')
    else:
        if period not in ('7','30','90','all'):
            raise HTTPException(422,'Periodo non valido')
        if period == 'all':
            with connect() as db:
                sql = 'SELECT MIN(scanned_at) AS first_ts FROM scans'
                row = db.execute(sql).fetchone()
            start_day = datetime.fromtimestamp(row['first_ts'], TZ).date() if row['first_ts'] else now.date() - timedelta(days=29)
        else:
            start_day = now.date() - timedelta(days=int(period)-1)
        end_day = now.date()
    low = int(datetime.combine(start_day, datetime.min.time(),TZ).timestamp())
    high = int(datetime.combine(end_day+timedelta(days=1),datetime.min.time(),TZ).timestamp())
    qr_id = None
    if slug != 'all': qr_id = get_qr(slug)['id']
    return start_day,end_day,low,high,qr_id


def rows_by_filter(low,high,qr_id):
    sql = '''SELECT s.*, q.name AS qr_name, q.slug AS qr_slug FROM scans s
             JOIN qr_codes q ON s.qr_id=q.id WHERE s.scanned_at >= ? AND s.scanned_at < ?'''
    params=[low,high]
    if qr_id is not None:
        sql += ' AND s.qr_id=?'; params.append(qr_id)
    sql += ' ORDER BY s.scanned_at DESC'
    with connect() as c:
        return [dict(r) for r in c.execute(sql,params)]


def count_named(rows,key):
    counts=Counter((r[key] or 'Non disponibile') for r in rows)
    return [{'name':name,'count':value,'percent': round(value/len(rows)*100) if rows else 0} for name,value in counts.most_common(8)]


@app.get('/api/dashboard')
def dashboard_data(req: Request, period: str='30', slug: str='all', start: str|None=None, end: str|None=None):
    logged(req)
    sd,ed,low,high,qr_id=get_filters(period,slug,start,end)
    rows=rows_by_filter(low,high,qr_id)
    group=defaultdict(lambda: {'scans':0,'unique':set(),'demo':0})
    qr_counts=Counter()
    for r in rows:
        d=datetime.fromtimestamp(r['scanned_at'],TZ).date().isoformat()
        group[d]['scans']+=1
        group[d]['unique'].add(r['visitor_hash'])
        group[d]['demo']+=int(r['is_demo'])
        qr_counts[(r['qr_slug'],r['qr_name'])]+=1
    d=sd
    series=[]
    while d <= ed:
        item=group[d.isoformat()]
        series.append({'date':d.isoformat(),'scans':item['scans'],'unique':len(item['unique'])})
        d+=timedelta(days=1)
    qrs_now=list_qrs()
    unique_count = len({r['visitor_hash'] for r in rows})
    return {
      'start':sd.isoformat(),'end':ed.isoformat(),'total_qrs':len(qrs_now),
      'total_scans':len(rows),'unique_scans':unique_count,
      'demo_scans':sum(int(r['is_demo']) for r in rows),
      'series':series,'systems':count_named(rows,'os'),'browsers':count_named(rows,'browser'),
      'devices':count_named(rows,'device'),'countries':count_named(rows,'country'),
      'cities':count_named(rows,'city'),
      'qr_scans':[{'slug':s,'name':n,'count':v,'percent':round(100*v/len(rows)) if rows else 0} for (s,n),v in qr_counts.most_common()],
      'recent':[{'time':datetime.fromtimestamp(r['scanned_at'],TZ).strftime('%d/%m/%Y %H:%M'),
                 'name':r['qr_name'],'os':r['os'],'browser':r['browser'],'country':r['country'],
                 'demo':bool(r['is_demo'])} for r in rows[:10]]
    }


def safe_csv(s):
    val=str(s)
    return "'"+val if val.lstrip()[:1] in ('=','+','-','@') else val


@app.get('/api/export.csv')
def export_csv(req: Request, period: str='30', slug: str='all', start: str|None=None, end: str|None=None):
    logged(req)
    _,_,low,high,qr_id=get_filters(period,slug,start,end)
    rows=rows_by_filter(low,high,qr_id)
    buffer=io.StringIO()
    out=csv.writer(buffer, delimiter=';')
    out.writerow(['Data ora (Italia)','Codice QR','Evento','Sistema operativo','Browser','Tipo dispositivo','Paese','Città','Demo'])
    for r in rows:
        out.writerow([safe_csv(datetime.fromtimestamp(r['scanned_at'],TZ).strftime('%Y-%m-%d %H:%M:%S')),
                      safe_csv(r['qr_name']),safe_csv(r['qr_slug']),safe_csv(r['os']),safe_csv(r['browser']),
                      safe_csv(r['device']),safe_csv(r['country']),safe_csv(r['city']),int(r['is_demo'])])
    content='\ufeff'+buffer.getvalue()
    return Response(content,media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="minardi-scansioni.csv"'})


def make_qr_png(slug: str, with_logo=True, border=4):
    qr=get_qr(slug)
    code=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, box_size=17, border=border)
    code.add_data(f'{BASE_URL}/r/{qr["slug"]}')
    code.make(fit=True)
    img=code.make_image(fill_color='#19495a',back_color='white').convert('RGB')
    if with_logo:
        src=Image.open(ROOT/'assets'/'logo_qr_originale.png').convert('RGB')
        # Ritocco: cancellazione degli elementi QR del file originale ai bordi del ritaglio.
        # Il marchio rimane al centro di un rettangolo bianco, ben contrastato.
        src=src.crop((10,9,src.width-9,src.height-10))
        w=int(img.width*.18)
        h=int(w*src.height/src.width)
        src=src.resize((w,h),Image.Resampling.LANCZOS)
        x=(img.width-w)//2; y=(img.height-h)//2
        ImageDraw.Draw(img).rounded_rectangle((x-9,y-8,x+w+9,y+h+8),radius=15,fill='white')
        img.paste(src,(x,y))
    buf=io.BytesIO(); img.save(buf,format='PNG',optimize=True)
    return buf.getvalue()


@app.get('/api/qrs/{slug}/image.png')
def qr_image(slug: str,req: Request):
    logged(req)
    return Response(make_qr_png(slug),media_type='image/png',headers={
        'Content-Disposition':f'inline; filename="qr-{slug}.png"'})


@app.get('/api/qrs/{slug}/poster.pdf')
def qr_poster(slug: str,req: Request):
    logged(req)
    template=ROOT/'assets'/'locandina_originale.pdf'
    doc=fitz.open(template)
    page=doc[0]
    # Il rettangolo bianco copre esclusivamente il precedente QR; cornice e testi originali rimangono.
    page.draw_rect(fitz.Rect(107,217,488,588), color=None,fill=(1,1,1),overlay=True)
    page.insert_image(fitz.Rect(120,225,475,580),stream=make_qr_png(slug,border=0),overlay=True)
    data=doc.tobytes(garbage=4,deflate=True)
    doc.close()
    return Response(data,media_type='application/pdf',headers={
        'Content-Disposition':f'attachment; filename="Minardi-Performance-Days-QR-tracciato-{slug}.pdf"'})


@app.post('/api/demo/populate')
def seed_demo(req: Request):
    csrf(req)
    qr=list_qrs()[0] if list_qrs() else None
    if not qr: raise HTTPException(404,'Crea prima un codice QR')
    now=datetime.now(TZ)
    platforms=[('iPhone / iOS','Safari','Smartphone'),('Android','Chrome','Smartphone'),
               ('Windows','Chrome','Computer'),('macOS','Safari','Computer'),('Android','Chrome','Smartphone'),
               ('Windows','Microsoft Edge','Computer')]
    with connect() as c:
        c.execute('DELETE FROM scans WHERE is_demo=1')
        n=0
        for days in range(20):
            for j in range(1+(days*7)%8):
                ts=int((now-timedelta(days=days,hours=(j*3)%15,minutes=j*6)).timestamp())
                os_name,browser,device=platforms[(days+j)%len(platforms)]
                country='IT' if (days+j)%7 else 'DE'
                c.execute('''INSERT INTO scans(qr_id,scanned_at,visitor_hash,os,browser,device,country,city,referrer,is_demo)
                             VALUES(?,?,?,?,?,?,?,?,?,1)''',
                          (qr['id'],ts,f'demo-{days}-{j//2}',os_name,browser,device,country,'Non disponibile',''))
                n+=1
    return {'ok':True,'inserted':n}


@app.post('/api/demo/clear')
def clear_demo(req: Request):
    csrf(req)
    with connect() as c:
        count=c.execute('DELETE FROM scans WHERE is_demo=1').rowcount
    return {'ok':True,'deleted':count}
