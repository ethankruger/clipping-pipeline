import base64
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state'
PLATFORMS=('instagram','tiktok','youtube')

def _load_dotenv():
    env_file = ROOT / '.env'
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()


def config():
    c=read(ROOT/'config.json')
    if c['publish_mode'] not in ('preview','computer','external','buffer') or c['editor'] not in ('claude','codex'):
        raise ValueError('Check editor and publish_mode')
    if not 1<=c['target']<=15 or not c['target']<=c['review_budget']<=100 or not 1<=c['top_per_channel']<=25:
        raise ValueError('Check target, review_budget and top_per_channel')
    if not 1<=c['spacing_minutes']<=15: raise ValueError('spacing_minutes must be 1–15')
    return c


def env(key):
    v=os.environ.get(key,'').strip()
    if not v: raise ValueError('Missing '+key)
    return v


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def write(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=p.parent,mode='w',suffix='.tmp',encoding='utf-8',delete=False) as f:
        json.dump(obj,f,ensure_ascii=False,indent=2)
        tmp=Path(f.name)
    tmp.replace(p)


def db():
    STATE.mkdir(exist_ok=True)
    conn=sqlite3.connect(STATE/'ledger.sqlite',timeout=30)
    conn.row_factory=sqlite3.Row
    conn.execute("""create table if not exists posts (
      k text primary key, day text, platform text, source text, event text, state text,
      payload text, receipt text default '', owner text default '',
      unique(platform,source), unique(platform,event))""")
    conn.execute("""create table if not exists candidates (
      id text primary key, creator text, title text, game text, url text,
      views integer, duration real, vod text, offset integer, window text,
      local_score real default 0, local_reason text default '',
      discovered_at text, status text default 'new')""")
    return conn


def req(url, *, data=None, headers=None, timeout=70, form=False):
    hdr=dict(headers or {})
    body=None
    if data is not None:
        body=urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        hdr['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
    request=urllib.request.Request(url,data=body,headers=hdr)
    try:
        with urllib.request.urlopen(request,timeout=timeout) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        # Response bodies may echo inputs. Do not log headers, tokens or signed URLs.
        raise RuntimeError(f'{urllib.parse.urlsplit(url).hostname}: HTTP {e.code} {e.reason}') from None


def twitch():
    cid=env('TWITCH_CLIENT_ID')
    tok=req('https://id.twitch.tv/oauth2/token',form=True,data={'client_id':cid,'client_secret':env('TWITCH_CLIENT_SECRET'),'grant_type':'client_credentials'})
    return {'Client-ID':cid,'Authorization':'Bearer '+tok['access_token']}


def helix(resource,params,h):
    return req('https://api.twitch.tv/helix/'+resource+'?'+urllib.parse.urlencode(params,doseq=True),headers=h)['data']


def model_json_object(value, *, source, ranking=False):
    """Accept equivalent model replies and reject ambiguous response shapes."""
    if isinstance(value, list):
        if ranking and all(isinstance(item, str) for item in value):
            value = {'ids': value}
        elif len(value) == 1 and isinstance(value[0], dict):
            value = value[0]
    if not isinstance(value, dict):
        raise ValueError(f'{source}: expected a JSON object, got {type(value).__name__}')
    if ranking and (
        not isinstance(value.get('ids'), list)
        or not all(isinstance(item, str) for item in value['ids'])
    ):
        raise ValueError(f'{source}: expected an ids array containing clip IDs')
    return value


def ollama_json(c, instruction, *, ranking=False):
    base = c.get('ollama_url', 'http://127.0.0.1:11434').rstrip('/')
    model = c.get('ollama_model', 'qwen2.5:3b')
    response = req(
        base + '/api/chat',
        timeout=300,
        data={
            'model': model,
            'stream': False,
            'format': ({'type': 'object', 'properties': {'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['ids']} if ranking else 'json'),
            'options': {'temperature': 0.1, 'num_ctx': 8192},
            'messages': [{'role': 'user', 'content': instruction}],
        },
    )
    content = (response.get('message') or {}).get('content', '').strip()
    if not content:
        raise ValueError(f'Ollama ({model}) returned no usable content')
    source = f'Ollama ({model}) clip ranking' if ranking else f'Ollama ({model}) text response'
    return model_json_object(json.loads(content), source=source, ranking=ranking)


def gem(c,instruction,video=None):
    if video is None and c.get('text_provider', 'gemini') == 'ollama' and '{"test":"ok"}' not in instruction:
        return ollama_json(c, instruction, ranking='Return {"ids":' in instruction)
    model=c['gemini_model']
    if not re.fullmatch(r'[a-zA-Z0-9._-]+',model): raise ValueError('Bad Gemini model ID')
    parts=[{'text':instruction}]
    if video:
        parts.append({'inlineData':{'mimeType':'video/mp4','data':base64.b64encode(video.read_bytes()).decode()}})
    response=req('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
      headers={'x-goog-api-key':env('GEMINI_API_KEY')},timeout=300,
      data={'contents':[{'parts':parts}],'generationConfig':{'responseMimeType':'application/json'}})
    segments=response.get('candidates',[{}])[0].get('content',{}).get('parts',[])
    content=''.join(x.get('text','') for x in segments if not x.get('thought'))
    if not content: raise ValueError('Gemini returned no usable content')
    ranking = 'Return {"ids":' in instruction
    source = 'Gemini clip ranking' if ranking else (
        'Gemini video review' if video else 'Gemini text response'
    )
    return model_json_object(json.loads(content), source=source, ranking=ranking)


def gql(query,variables=None):
    data=req('https://api.buffer.com',headers={'Authorization':'Bearer '+env('BUFFER_TOKEN')},data={'query':query,'variables':variables or {}})
    if data.get('errors'): raise ValueError('Buffer GraphQL: '+' / '.join(x['message'][:250] for x in data['errors']))
    return data['data']


def channels():
    return gql('query($id:OrganizationId!){channels(input:{organizationId:$id}){id name service externalLink displayName serviceId isQueuePaused isDisconnected isLocked}}',{'id':env('BUFFER_ORG_ID')})['channels']


def verify_channels(c):
    remote={x['id']:x for x in channels()}
    for platform,expected in c['channels'].items():
        row=remote.get(expected['id'])
        if not row or row['service'].lower()!=platform or any(row[k] for k in ('isQueuePaused','isDisconnected','isLocked')):
            raise ValueError('Missing, wrong or unavailable Buffer channel: '+platform)
        name=expected['handle'].lstrip('@').lower()
        path=urllib.parse.urlsplit(row.get('externalLink') or '').path.strip('/').lower().lstrip('@')
        match=name in (path,row['name'].lower().lstrip('@'))
        if not match and not (expected.get('service_id') and expected['service_id']==row['serviceId']):
            raise ValueError('Verify handle or configure confirmed native service_id for '+platform)
    return remote


def ollama_local_json(prompt, model='qwen2.5:7b', timeout=120):
    payload = {'model': model, 'prompt': prompt, 'format': 'json', 'stream': False}
    raw = req('http://127.0.0.1:11434/api/generate', data=payload, timeout=timeout)
    return json.loads(raw['response']) if isinstance(raw, dict) else json.loads(json.loads(raw)['response'])


def slots(c,day,platform,rank,now):
    if c.get('rolling_slots'):
        tz = ZoneInfo(c['timezone'])
        plat_offset = {'instagram': 0, 'youtube': 5, 'tiktok': 10}.get(platform, 0)
        spacing = int(c.get('rolling_spacing_minutes', 45))
        return (now + timedelta(minutes=15 + rank * spacing + plat_offset)).astimezone(tz)
    primary,alternate=c['anchors'][platform][day.weekday()]
    step=c['spacing_minutes']
    offset=((rank//2) * (1 if rank%2==0 else -1))*step
    tz=ZoneInfo(c['timezone'])
    for hour in (primary,alternate):
        t=datetime.combine(day,time(hour),tz)+timedelta(minutes=offset)
        if t.date()==day and t>now+timedelta(minutes=3): return t
    return None


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def storage():
    import boto3
    return boto3.client('s3',endpoint_url=env('S3_ENDPOINT'),region_name=os.getenv('S3_REGION','auto'),
                        aws_access_key_id=env('S3_ACCESS_KEY'),aws_secret_access_key=env('S3_SECRET_KEY'))


def host(path,key):
    base=env('MEDIA_BASE_URL').rstrip('/')
    if not base.startswith('https://'): raise ValueError('Media base must be HTTPS')
    storage().upload_file(str(path),env('S3_BUCKET'),key,ExtraArgs={'ContentType':'video/mp4'})
    url=base+'/'+key
    with urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=0-31','User-Agent':'Mozilla/5.0'}),timeout=40) as r:
        if b'ftyp' not in r.read(32): raise ValueError('Media URL does not serve a direct MP4')
    return url
