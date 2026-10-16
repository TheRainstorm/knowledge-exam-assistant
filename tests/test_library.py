import json
from pathlib import Path
import tempfile
import unittest
import urllib.request
import urllib.error
import threading
import base64
import io
import re
import time
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image
import numpy as np

from kexam.ingest import chunks, ingest
from kexam.retrieval import build, Search, terms
from kexam.server import make_server
from kexam.storage import safe_child


def config(folder):
    return {"data_dir": str(Path(folder)/"data"), "ingest": {}, "embedding": {"backend":"none"},
            "quiz":{"endpoint":"http://localhost:8000/v1/chat/completions","model":"test"}, "web":{"port":0}}


class LibraryTests(unittest.TestCase):
    def setup_library(self, folder):
        cfg = config(folder)
        source = Path(folder)/"inputs"; source.mkdir()
        (source/"demo.md").write_text("# 虚构\n\n星梭探测器每天备份一次，保留十四天。设备借用必须先提交申请再批准。", encoding="utf-8")
        ingest(cfg, source); build(cfg)
        return cfg, source

    def test_chunk_coverage(self):
        text="0123456789"*500
        parts=list(chunks(text))
        joined=parts[0]+"".join(p[160:] for p in parts[1:])
        self.assertEqual(text,joined)

    def test_chinese_terms(self):
        self.assertIn("备份",terms("每日备份数据"))
        self.assertIn("vrm",terms("VRM规则"))

    def test_import_index_search_and_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, source=self.setup_library(folder)
            search=Search(cfg)
            self.assertIn("十四天", search.query("数据备份保留多久")["rows"][0]["text"])
            first=json.loads((Path(cfg["data_dir"])/"corpus.json").read_text(encoding="utf-8"))
            ingest(cfg,source)
            again=json.loads((Path(cfg["data_dir"])/"corpus.json").read_text(encoding="utf-8"))
            self.assertEqual(first,again)

    def test_stale_corpus_and_embedding_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, source=self.setup_library(folder)
            (source/"new.txt").write_text("新增来源",encoding="utf-8");ingest(cfg,source)
            with self.assertRaises(ValueError):Search(cfg)
            build(cfg);cfg["embedding"]["backend"]="text"
            with self.assertRaises(ValueError):Search(cfg)

    def test_course_filter_and_deleted_source(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg, source=self.setup_library(folder)
            (source/"second.md").write_text("备份保留三天",encoding="utf-8");ingest(cfg,source);build(cfg)
            search=Search(cfg);sid=next(s["id"] for s in search.corpus["sources"] if s["name"]=="second.md")
            found=search.query("备份",course=sid)["rows"]
            self.assertEqual(len(found),1);self.assertIn("三天",found[0]["text"])
            (source/"second.md").unlink();ingest(cfg,source);build(cfg)
            self.assertFalse(Search(cfg).query("备份",course=sid)["rows"])

    def test_image_and_text_use_correct_embedding_modalities(self):
        class Engine:
            def __init__(self):self.calls=[]
            def encode(self,text=None,image=None):
                self.calls.append((text,image));return np.array([1,0],dtype=np.float32) if image else np.array([0,1],dtype=np.float32)
        with tempfile.TemporaryDirectory() as folder:
            cfg,source=self.setup_library(folder)
            Image.new("RGB",(30,30),"blue").save(source/"diagram.png")
            ingest(cfg,source);cfg["embedding"]={"backend":"wemm","model":"fake","revision":"test"}
            engine=Engine();build(cfg,engine)
            self.assertTrue(any(image is not None for _,image in engine.calls))
            self.assertTrue(any(text is not None for text,_ in engine.calls))
            cached=Engine();build(cfg,cached);self.assertEqual(cached.calls,[])
            rows=Search(cfg,engine).query("备份")["rows"];self.assertEqual(len(rows),2)

    def test_safe_path(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):safe_child(folder,"../secret")

    def test_six_images_end_to_end_with_local_fake_provider(self):
        """Real HTTP transport; no real API, credentials, or private knowledge."""
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if '题目转录器' in body['messages'][0]['content']:
                    value={'questions':[{'stem':'设备借用是否必须先提交申请？','type':'single','options':[{'label':'A','text':'是'},{'label':'B','text':'否'}],'keywords':['设备借用'],'unreadable':[]}]}
                else:
                    value={'status':'supported','selected':['A'],'reason':'见 H1','citations':['H1'],'options':[{'label':'A','verdict':'correct','reason':'H1 要求先申请'},{'label':'B','verdict':'incorrect','reason':'H1 不支持'}]}
                response=json.dumps({'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)},'finish_reason':'stop'}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        pt=threading.Thread(target=provider.serve_forever,daemon=True);pt.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                cfg,_=self.setup_library(folder)
                cfg['quiz']['endpoint']=f'http://127.0.0.1:{provider.server_port}/v1/chat/completions'
                cfg['quiz']['default_concurrency']=4
                server=make_server(cfg);st=threading.Thread(target=server.serve_forever,daemon=True);st.start()
                base=f'http://127.0.0.1:{server.server_port}'
                try:
                    with urllib.request.urlopen(base+'/quiz') as r:page=r.read().decode()
                    token=re.search("const TOKEN='([^']+)'",page)[1]
                    out=io.BytesIO();Image.new('RGB',(30,30),'white').save(out,format='PNG')
                    image={'name':'fictional.png','data_url':'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()}
                    req=urllib.request.Request(base+'/api/quiz/submit',data=json.dumps({'images':[image]*6,'concurrency':3,'allow_inference':False}).encode(),headers={'X-Course-Token':token,'Content-Type':'application/json'})
                    with urllib.request.urlopen(req) as r:jobid=json.load(r)['id']
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        with urllib.request.urlopen(base+'/api/quiz/jobs/'+jobid) as r:job=json.load(r)
                        if job['status']!='running':break
                        time.sleep(.01)
                    self.assertEqual(job['status'],'complete');self.assertEqual(len(job['items']),6)
                    self.assertTrue(all(i['answer']['selected']==['A'] for i in job['items']))
                    with urllib.request.urlopen(base+'/quiz/export/'+jobid+'.md') as r:self.assertIn('建议选项：A',r.read().decode())
                finally:server.shutdown();server.server_close();st.join()
        finally:provider.shutdown();provider.server_close();pt.join()

    @unittest.skipUnless(importlib.util.find_spec('pymupdf') and importlib.util.find_spec('docx') and importlib.util.find_spec('cv2'), 'optional document/video modules not installed')
    def test_optional_pdf_docx_video(self):
        import pymupdf
        import cv2
        from docx import Document
        with tempfile.TemporaryDirectory() as folder:
            cfg=config(folder);source=Path(folder)/'inputs';source.mkdir()
            doc=pymupdf.open();page=doc.new_page();page.insert_text((60,60),'Fictional equipment manual');doc.save(source/'sample.pdf');doc.close()
            docx=Document();docx.add_paragraph('Fictional backup retention');docx.save(source/'sample.docx')
            writer=cv2.VideoWriter(str(source/'sample.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),5,(320,180))
            self.assertTrue(writer.isOpened())
            for n in range(20):writer.write(np.full((180,320,3),0 if n<10 else 255,dtype=np.uint8))
            writer.release();cfg['ingest']['sample_seconds']=1
            result=ingest(cfg,source);self.assertEqual(result['sources'],3)
            build(cfg);search=Search(cfg)
            pdf=[r for r in search.rows if r['course']=='sample.pdf'];self.assertEqual(len(pdf),2)
            self.assertTrue(any('Fictional' in r['text'] for r in pdf))
            self.assertEqual(len([r for r in search.rows if r['course']=='sample.mp4']),2)
            sid=next(s['id'] for s in search.corpus['sources'] if s['name']=='sample.mp4')
            server=make_server(cfg,search);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                req=urllib.request.Request(base+'/media/'+sid,headers={'Range':'bytes=0-15'})
                with urllib.request.urlopen(req) as r:
                    self.assertEqual(r.status,206);self.assertEqual(len(r.read()),16)
                    self.assertTrue(r.headers['Content-Range'].startswith('bytes 0-15/'))
                with urllib.request.urlopen(base+'/watch/'+sid+'?t=2') as r:self.assertIn('#t=2.0',r.read().decode())
            finally:server.shutdown();server.server_close();thread.join()

    def test_http_csrf_private_files_and_source_escaping(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg,source=self.setup_library(folder)
            (source/"escape.txt").write_text("<script>alert(1)</script>备份",encoding="utf-8");ingest(cfg,source);build(cfg)
            server=make_server(cfg);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base+"/api/health") as r:self.assertTrue(json.load(r)["ready"])
                with urllib.request.urlopen(base+"/handbook") as r:page=r.read().decode()
                self.assertIn("&lt;script&gt;",page);self.assertNotIn("<script>alert",page)
                for path in ("/files/../../config.toml", "/files/quiz_provider.json"):
                    with self.assertRaises(urllib.error.HTTPError) as raised:urllib.request.urlopen(base+path)
                    self.assertEqual(raised.exception.code,404)
                req=urllib.request.Request(base+"/api/quiz/submit",data=b'{}',headers={"Content-Type":"application/json"})
                with self.assertRaises(urllib.error.HTTPError) as raised:urllib.request.urlopen(req)
                self.assertEqual(raised.exception.code,403)
                req=urllib.request.Request(base+"/api/health",headers={"Host":"evil.invalid"})
                with self.assertRaises(urllib.error.HTTPError) as raised:urllib.request.urlopen(req)
                self.assertEqual(raised.exception.code,403)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=="__main__":unittest.main()
