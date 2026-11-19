"""Local fixture only. No accounts, network, or publishing."""
import json
import tempfile
import unittest
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import common, worker
from publish import enqueue,claim,complete,valid_url
from edit import render,run,probe

class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name); p=patch('common.STATE',self.folder/'state'); p.start(); self.addCleanup(p.stop)
        self.c=common.read(common.ROOT/'config.example.json'); self.c['publish_mode']='computer'
    def test_workflows(self):
        for f in common.ROOT.glob('*.n8n.json'):
            w=common.read(f); names={n['name'] for n in w['nodes']}; self.assertFalse(w['active'])
            for src,edge in w['connections'].items():
                self.assertIn(src,names)
                for port in edge['main']:
                    for hop in port: self.assertIn(hop['node'],names)
    def test_week(self):
        for i in range(7):
            d=date(2027,3,8)+timedelta(days=i)
            for plat in common.PLATFORMS:
                ts=[common.slots(self.c,d,plat,x,datetime(2027,1,1,tzinfo=timezone.utc)) for x in range(1,16)]
                self.assertEqual(len(set(ts)),15)
                self.assertEqual(ts[0].hour,self.c['anchors'][plat][i][0])
                self.assertTrue(all(v.date()==d for v in ts))
        # Compare the same clock time across the March DST boundary.
        a=common.slots(self.c,date(2027,3,7),'youtube',1,datetime(2027,1,1,tzinfo=timezone.utc))
        b=common.slots(self.c,date(2027,3,14),'youtube',1,datetime(2027,1,1,tzinfo=timezone.utc))
        self.assertNotEqual(a.utcoffset(),b.utcoffset())
    def test_lifecycle(self):
        day=date.today()+timedelta(days=2)
        rule={'approved':True,'platforms':['instagram'],'joined':True,'paid':True,'campaign':'https://contentrewards.com/c/campaigns/test','valid_through':day.isoformat()}
        self.c['rules']['lacy']=rule
        row={'rank':1,'id':'source','url':'https://clips.twitch.tv/example','creator':'lacy','event':'unique','file':'file.mp4','rule':rule,'edit':dict(title='A',description='B',instagram='C',tiktok='D'),'qa':{'pass':True,'eligible_platforms':['instagram']}}
        common.write(self.folder/'manifest.json',[row])
        enqueue(self.c,self.folder,day,True); self.assertIsNone(claim(self.c))
        enqueue(self.c,self.folder,day,False); enqueue(self.c,self.folder,day,False)
        job=claim(self.c); self.assertEqual(job['action'],'schedule'); self.assertIsNone(claim(self.c))
        result=complete(job['key'],job['claim'],dict(result='scheduled',evidence='receipt')); self.assertEqual(result['state'],'scheduled')
        now=datetime.now(timezone.utc)
        with common.db() as con:
            p=json.loads(con.execute('select payload from posts').fetchone()[0]); p['due']=(now-timedelta(minutes=4)).isoformat()
            con.execute('update posts set payload=?',(json.dumps(p),))
        v=claim(self.c); self.assertEqual(v['action'],'verify')
        complete(v['key'],v['claim'],{'result':'live','url':'https://www.instagram.com/reel/abc/','published_at':(now-timedelta(minutes=2)).isoformat(),'evidence':'permalink checked'})
        s=claim(self.c);self.assertEqual(s['action'],'submit')
        complete(s['key'],s['claim'],{'result':'submitted','evidence':'Pending review row'})
        self.assertIsNone(claim(self.c))
        with self.assertRaises(ValueError): complete(s['key'],s['claim'],{'result':'submitted','evidence':'again'})
        with common.db() as con:
            self.assertEqual(con.execute('select count(*) from submissions').fetchone()[0],1)
            p=json.loads(con.execute('select payload from posts').fetchone()[0]);p['published_at']=(now-timedelta(minutes=31)).isoformat()
            con.execute('delete from submissions')
            con.execute("update posts set payload=?,state='live'",(json.dumps(p),))
        self.assertIsNone(claim(self.c))
        with common.db() as con: self.assertEqual(con.execute('select state from posts').fetchone()[0],'expired')
        self.assertFalse(valid_url('https://www.tiktok.com/@wrong/video/123','tiktok','kreegs.replays'))
    def test_video(self):
        src=self.folder/'sample.mp4'
        run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=12','-f','lavfi','-i','sine=frequency=400','-t','2','-c:v','libx264','-c:a','aac',str(src)])
        for layout in ['wide','portrait','stack']:
            dest=self.folder/(layout+'.mp4')
            render(src,dest,{'start':0,'end':1.5,'layout':layout,'box':[0,0,1,1],'box2':[0,0,1,1],'captions':[{'s':0.1,'e':0.9,'text':'Sample caption'}],'hook':'A question?'},{'overlay':'Sample disclosure'},'Arial')
            self.assertTrue(dest.exists())
        from pipeline import proxy
        proxy(dest,self.folder/'proxy.mp4')
        self.assertGreater(float(probe(self.folder/'proxy.mp4')['format']['duration']),1)

if __name__=='__main__': unittest.main(verbosity=2)
