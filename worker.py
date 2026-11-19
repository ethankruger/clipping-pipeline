import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import time as clock
import urllib.request
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from common import ROOT, STATE, config, db, env, gql, channels, verify_channels, write, read, twitch, gem, helix, req, storage, host
from pipeline import shortlist, brief, produce, scout_local
from publish import claim, complete, buffer_send, enqueue, directions, init_ops
from browser_ops import operate, login
from edit import run

load_dotenv(ROOT/'.env')
STAGES=('scout','brief','edit','plan')
lock=threading.Lock()
production=threading.Lock()
browser_lock=threading.Lock()
active=set()


def batch(job):
    if not re.fullmatch(r'(test|daily)-\d{4}-\d{2}-\d{2}(?:-\d{2})?',job): raise ValueError('Invalid batch ID')
    parts=job.split('-')
    day=date.fromisoformat('-'.join(parts[1:4]))
    return STATE/'batches'/job,day


def step(job,stage):
    folder,day=batch(job); folder.mkdir(parents=True,exist_ok=True)
    if stage not in STAGES: raise ValueError('Unknown stage')
    marker=folder/f'{stage}.status.json'
    with lock:
        if marker.exists(): return read(marker)
        key=(job,stage)
        if key in active: return {'state':'running','job':job,'stage':stage}
        idx=STAGES.index(stage)
        if idx and (not (prev:=folder/f'{STAGES[idx-1]}.status.json').exists() or read(prev)['state']!='done'):
            raise ValueError('Previous stage incomplete')
        active.add(key)
    def work():
        try:
            with production:
                c=config()
                if job.startswith('test-'):
                    c.update(target=1,review_budget=min(3,c['review_budget']))
                if stage=='plan': result=enqueue(c,folder,day,job.startswith('test-'))
                else: result={'scout':shortlist,'brief':brief,'edit':produce}[stage](c,folder)
                out={'state':'done','job':job,'stage':stage,'result':result}
        except Exception as e:
            out={'state':'failed','job':job,'stage':stage,'error':str(e)[:750]}
        finally:
            with lock:
                try: write(marker,out)
                finally: active.discard(key)
    threading.Thread(target=work,daemon=True).start()
    return {'state':'running','job':job,'stage':stage}


def tick():
    c=config()
    if c['publish_mode'] in ('preview','external'): return {'state':c['publish_mode']}
    if not browser_lock.acquire(False): return {'state':'busy'}
    def dispatch():
        try:
            # Re-evaluate urgency between every job. Stop after a small burst; next n8n tick resumes.
            for _ in range(8):
                p=claim(c)
                if not p: return
                try:
                    if p['action']=='schedule' and p['mode']=='buffer': result=buffer_send(p)
                    elif p['action']=='submit':
                        from browser_ops import local_submit_rewards; result=local_submit_rewards(p)
                    else: result=operate(p)
                    complete(p['key'],p['claim'],result)
                except Exception as err:
                    complete(p['key'],p['claim'],{'result':'uncertain','evidence':str(err)[:600] or type(err).__name__})
        finally: browser_lock.release()
    threading.Thread(target=dispatch,daemon=True).start()
    return {'state':'monitor started'}


def status():
    with db() as con:
        return {'mode':config()['publish_mode'],'counts':{r[0]:r[1] for r in con.execute('select state,count(*) from posts group by state')},
                'recent':[dict(r) for r in con.execute("select k,day,platform,state,receipt,json_extract(payload,'$.public_url') as public_url,json_extract(payload,'$.published_at') as published_at,json_extract(payload,'$.campaign') as campaign from posts order by rowid desc limit 60")],
                'jobs':[read(p) for p in sorted((STATE/'batches').glob('*/*.status.json'),key=lambda x:x.stat().st_mtime,reverse=True)[:10]]}


class API(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def do_POST(self):
        try:
            if not hmac.compare_digest(self.headers.get('X-Worker-Key',''),env('WORKER_TOKEN')):
                self.reply(403,{'error':'Invalid worker credential'}); return
            size=int(self.headers.get('Content-Length',0))
            if not 0<=size<=16384: raise ValueError('Invalid input size')
            body=json.loads(self.rfile.read(size) or b'{}'); path=self.path
            import pathlib
            paused = (pathlib.Path(__file__).resolve().parent / 'state' / 'paused.flag').exists()
            if path=='/step':
                if paused and not str(body.get('job','')).startswith('test-'):
                    result={'state':'error','error':'Pipeline is paused via Control Panel'}
                else:
                    result=step(body['job'],body['stage'])
            elif path=='/tick':
                result={'state':'paused'} if paused else tick()
            elif path=='/status': result=status()
            elif path=='/scout-local':
                if paused:
                    result={'state':'paused','fetched':0,'inserted':0}
                else:
                    try:
                        import cleanup; cleanup.prune_batches(days=3)
                    except Exception:
                        pass
                    result=scout_local(config())
            elif path=='/operator':
                if body['action']=='status': result=status()
                elif config()['publish_mode']!='external': raise ValueError('External claims require external mode')
                elif body['action']=='next':
                    p=claim(config(),external=True)
                    result={'job':({k:v for k,v in p.items() if k!='file'}|{'instructions':directions(p)}) if p else None}
                elif body['action']=='record':
                    result=complete(body['key'],body['claim'],body['report'])
                else: raise ValueError('Unknown operator action')
            else: self.reply(404,{'error':'Unknown route'}); return
            self.reply(200,result)
        except Exception as e: self.reply(400,{'error':str(e)[:650]})

    def reply(self,code,body):
        data=json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)


def doctor():
    c=config(); failed=[]
    def check(name,fn):
        try: print(name,'PASS',fn() or '')
        except Exception as err: failed.append(name); print(name,'FAIL',str(err)[:450])
    def tools():
        for name in ('ffmpeg','ffprobe',c['editor']):
            if not shutil.which(name): raise ValueError('Missing '+name)
        args=[c['editor'],'auth','status'] if c['editor']=='claude' else ['codex','login','status']
        run([shutil.which(args[0]),*args[1:]],timeout=25)
        run(['ffmpeg','-version']); run([sys.executable,'-m','yt_dlp','--version'])
    check('Editor and local tools',tools)
    check('Twitch',lambda:', '.join(u['login'] for u in helix('users',{'login':c['creators']},twitch())))
    check('Gemini text/model',lambda:gem(c,'Return {"test":"ok"}'))
    check('Local n8n bridge key',lambda: 'configured' if len(env('WORKER_TOKEN'))>=32 else (_ for _ in ()).throw(ValueError('Key too short')))
    if c['publish_mode'] in ('buffer','external') or os.getenv('BUFFER_TOKEN'):
        check('Buffer organizations',lambda:gql('{account{organizations{id name}}}')['account']['organizations'])
        if os.getenv('BUFFER_ORG_ID'):
            check('Buffer channel inventory',channels)
            if c['publish_mode']=='buffer': check('Target channel matches',lambda:list(verify_channels(c)))
    if c['publish_mode'] in ('buffer','external') or os.getenv('S3_BUCKET'):
        check('Media bucket',lambda:storage().head_bucket(Bucket=env('S3_BUCKET')) and 'accessible')
    if c['publish_mode'] in ('computer','buffer') or os.getenv('OPENAI_API_KEY'):
        import urllib.parse
        model=os.getenv('OPERATOR_MODEL','gpt-6.1-sol')
        check('OpenAI operator model access',lambda:req('https://api.openai.com/v1/models/'+urllib.parse.quote(model,safe=''),headers={'Authorization':'Bearer '+env('OPENAI_API_KEY')})['id'])
    print('Mode:',c['publish_mode'],'| Approved creator rules:',[k for k,v in c['rules'].items() if v.get('approved')])
    return 1 if failed else 0


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('command',choices=['serve','doctor','login','status','retry-stage','resolve','media-test','computer-test','run-day','editor-test','gemini-models','promote','scout-local'])
    ap.add_argument('args',nargs='*'); opt=ap.parse_args()
    if opt.command=='doctor': return doctor()
    if opt.command=='editor-test':
        from pipeline import editor
        work=STATE/'editor-test'; work.mkdir(parents=True,exist_ok=True)
        out=editor(config(),'Return exactly JSON {"status":"ok"} without markdown.',work)
        if out.get('status')!='ok': raise ValueError('Incorrect editor reply')
        print('PASS: actual headless editor call'); return
    if opt.command=='gemini-models':
        data=req('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000',headers={'x-goog-api-key':env('GEMINI_API_KEY')})
        print('\n'.join(m['name'].split('/')[-1] for m in data['models'] if 'generateContent' in m.get('supportedGenerationMethods',[]))); return
    if opt.command=='scout-local':
        print(json.dumps(scout_local(config()),indent=2)); return
    if opt.command=='run-day':
        day=date.fromisoformat(opt.args[0]); job='daily-'+day.isoformat()
        for stage in STAGES:
            while True:
                reply=req('http://127.0.0.1:'+os.getenv('WORKER_PORT','8785')+'/step',headers={'X-Worker-Key':env('WORKER_TOKEN')},data={'job':job,'stage':stage})
                if reply['state']=='failed': raise ValueError(reply['error'])
                if reply['state']=='done': print(stage,reply['result']); break
                clock.sleep(15)
        return
    if opt.command=='login': login(); return
    if opt.command=='status': print(json.dumps(status(),indent=2)); return
    if opt.command=='media-test':
        f=Path(opt.args[0]); print(host(f,'kreegs/test-'+secrets.token_hex(12)+'.mp4')); return
    if opt.command=='computer-test':
        from browser_ops import smoke
        print(smoke()); return
    if opt.command=='promote':
        folder,day=batch('daily-'+opt.args[0])
        marker=folder/'plan.status.json'; old=read(marker)
        if not old.get('result',{}).get('preview') or config()['publish_mode']=='preview':
            raise ValueError('Choose a live mode and a completed daily preview')
        if any(r['rule']!=config()['rules'].get(r['creator'],{}) for r in read(folder/'manifest.json')):
            raise ValueError('Campaign rules changed after QA: rerender and recheck a new batch with current rules')
        result=enqueue(config(),folder,day,False); write(marker,dict(old,result=result)); print(result); return
    if opt.command=='retry-stage':
        folder,_=batch(opt.args[0]); stage=opt.args[1]
        if stage not in STAGES: raise ValueError('Bad stage')
        marker=folder/f'{stage}.status.json'
        if read(marker)['state']!='failed': raise ValueError('Only a failed stage can be reset')
        marker.unlink(); print('Retry the original n8n execution with the same job date.'); return
    if opt.command=='resolve':
        key,state,evidence=opt.args
        if state not in ('ready','scheduled','live','submitted','already_submitted','blocked'): raise ValueError('Bad recovery state')
        with db() as con:
            init_ops(con)
            r=con.execute('select state,payload from posts where k=?',(key,)).fetchone()
            if not r or r['state'] not in ('uncertain','blocked','working_submit','working_verify','working_schedule','check_manually'): raise ValueError('Inspect current state before recovery')
            if state in ('live','submitted','already_submitted') and not read_payload_url(r): raise ValueError('Record verified publication first by restoring scheduled and running verify')
            if not evidence.strip(): raise ValueError('Explicit site evidence required')
            con.execute("update posts set state=?,owner='',receipt=? where k=?",(state,evidence,key))
            con.execute('insert into audit(k,stamp,phase,evidence) values(?,?,?,?)',(key,datetime.now(timezone.utc).isoformat(),state,evidence))
        return
    if len(env('WORKER_TOKEN'))<32: raise ValueError('WORKER_TOKEN needs at least 32 characters')
    STATE.mkdir(exist_ok=True)
    server=ThreadingHTTPServer((os.getenv('BIND_HOST','127.0.0.1'),int(os.getenv('WORKER_PORT','8785'))),API)
    # Previous in-flight actions may already have reached Buffer or Content Rewards.
    with db() as con:
        con.execute("update posts set state='uncertain',owner='',receipt='Worker restarted during action. Reconcile against live site.' where state like 'working_%' and json_extract(payload,'$.mode')!='external'")
    print('Kreegs worker ready',server.server_address,flush=True); server.serve_forever()


def read_payload_url(row): return json.loads(row['payload']).get('public_url')


if __name__=='__main__': sys.exit(main())
