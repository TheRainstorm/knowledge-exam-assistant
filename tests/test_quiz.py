import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import base64
from pathlib import Path
from kexam.quiz import QuizAPI, QuizError, validate_question, validate_answer


class QuizTests(unittest.TestCase):
    def question(self):
        return {'stem':'哪项不正确？','type':'single','options':[{'label':'A','text':'选项一'},{'label':'B','text':'选项二'}], 'unreadable':[], 'keywords':['主题']}

    def answer(self):
        return {'status':'supported','selected':['A'],'citations':['H1'],'reason':'见 H1','options':[{'label':'A','verdict':'correct','reason':'依据 H1'},{'label':'B','verdict':'incorrect','reason':'依据 H1'}]}

    def test_unreadable_rejected(self):
        q=self.question();q['unreadable']=['否定词看不清']
        with self.assertRaises(QuizError): validate_question(q)

    def test_duplicate_options_rejected(self):
        q=self.question();q['options'][1]['label']='A'
        with self.assertRaises(QuizError): validate_question(q)

    def test_invented_citation_rejected(self):
        a=self.answer();a['citations']=['H99']
        with self.assertRaises(QuizError): validate_answer(a,self.question(),{'H1'})

    def test_missing_evidence_downgraded(self):
        a=self.answer();a['citations']=[]
        self.assertEqual(validate_answer(a,self.question(),{'H1'})['status'],'needs_review')

    def test_single_cannot_select_two(self):
        a=self.answer();a['selected']=['A','B']
        with self.assertRaises(QuizError): validate_answer(a,self.question(),{'H1'})

    def test_disagreement_downgraded(self):
        a=self.answer();a['selected']=['B']
        self.assertEqual(validate_answer(a,self.question(),{'H1'})['status'],'needs_review')

    def test_unknown_type_downgraded(self):
        q=self.question();q['type']='unknown'
        self.assertEqual(validate_answer(self.answer(),q,{'H1'})['status'],'needs_review')

    def test_restart_does_not_replay(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            api.save({'id':'a'*24,'status':'running','items':[]})
            api=QuizAPI(d,None,None)
            self.assertEqual(api.load('a'*24)['status'],'interrupted')

    def test_source_ids_are_server_assigned(self):
        def slides(*a,**kw):return {'rows':[{'id':'untrusted-id','kind':'asr','image':'assets/a.png','text':'证据','handbook_url':'/handbook#block-1'}]}
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,slides,lambda *a,**kw:{'rows':[]})
            _,e=api.retrieve(self.question(),'')
            self.assertEqual([r['id'] for r in e],['P1','H1'])
            self.assertEqual([r['kind'] for r in e],['ppt','handbook'])

    def test_failed_item_does_not_stop_batch(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            job={'id':'b'*24,'status':'running','course':'','items':[]}
            folder=api.folder/job['id'];folder.mkdir()
            for i in range(2):
                (folder/f'{i}.png').write_bytes(b'fixture')
                job['items'].append({'index':i+1,'file':f'{i}.png','status':'queued'})
            answers=iter([QuizError('模拟单题失败'),self.question(),self.answer(),self.answer()])
            def call(*args):
                value=next(answers)
                if isinstance(value,Exception):raise value
                return value
            api.call_model=call
            api.retrieve=lambda *args:([],[{'id':'H1','kind':'handbook','text':'依据'}])
            api.lock.acquire();api.run(job)
            self.assertEqual([i['status'] for i in job['items']],['failed','complete'])
            self.assertFalse(api.lock.locked())

    def test_oversized_image_count_rejected_before_processing(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);api.config=lambda:{}
            with self.assertRaises(QuizError):api.submit({'images':[{}]*101})

    def test_one_image_multiple_questions_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            job={'id':'c'*24,'status':'running','course':'','items':[{'index':1,'source_image':1,'file':'1.png','status':'queued'}]}
            folder=api.folder/job['id'];folder.mkdir();(folder/'1.png').write_bytes(b'fixture')
            bad=self.question();bad['unreadable']=['缺少半个选项']
            api.call_model=lambda *args:{'questions':[self.question(),bad,self.question()]}
            api.retrieve=lambda *args:([],[{'id':'H1','kind':'handbook','text':'证据'}])
            api.solve=lambda *args:self.answer()
            api.lock.acquire();api.run(job)
            self.assertEqual([i['status'] for i in job['items']],['complete','failed','complete'])
            self.assertEqual([i['index'] for i in job['items']],[1,2,3])
            self.assertEqual([i['source_image'] for i in job['items']],[1,1,1])

    def test_checked_marks_never_enter_solver(self):
        q=self.question();q['selected']=['B'];q['options'][0]['checked']=True
        clean=validate_question(q)
        self.assertNotIn('selected',clean)
        self.assertNotIn('checked',clean['options'][0])

    def test_solver_does_not_receive_original_question_image(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);requests=[]
            def call(instruction,content,tokens):requests.append(content);return self.answer()
            api.call_model=call
            api.solve(self.question(),[{'id':'H1','kind':'handbook','text':'证据'}])
            self.assertEqual(len(requests),2)
            self.assertTrue(all(part['type']=='text' for req in requests for part in req))

    def test_inference_never_upgrades_evidence_support(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            value=self.answer();value['citations']=[]
            api.call_model=lambda *args:value
            result=api.infer_candidate(self.question(),self.answer())
            self.assertEqual(result['status'],'needs_review')
            self.assertEqual(result['basis'],'model_inference')
            self.assertEqual(result['citations'],[])

    def test_inference_cannot_invent_course_citation(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);api.call_model=lambda *args:self.answer()
            with self.assertRaises(QuizError):api.infer_candidate(self.question(),self.answer())

    def test_inference_answer_consistency(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            value=self.answer();value['citations']=[];value['selected']=['B']
            api.call_model=lambda *args:value
            with self.assertRaises(QuizError):api.infer_candidate(self.question(),self.answer())

    def test_inference_failure_keeps_evidence_review(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            job={'id':'d'*24,'status':'running','course':'','allow_inference':True,'items':[{'index':1,'file':'1.png','status':'queued'}]}
            folder=api.folder/job['id'];folder.mkdir();(folder/'1.png').write_bytes(b'fixture')
            api.call_model=lambda *args:self.question()
            api.retrieve=lambda *args:([],[])
            answer=self.answer();answer['status']='needs_review';answer['selected']=[]
            api.solve=lambda *args:answer
            def fail(*args):raise QuizError('API unavailable')
            api.infer_candidate=fail
            api.lock.acquire();api.run(job)
            self.assertEqual(job['items'][0]['status'],'complete')
            self.assertEqual(job['items'][0]['answer']['status'],'needs_review')
            self.assertEqual(job['items'][0]['answer']['inference_error'],'API unavailable')

    def test_export_labels_inference(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);a=self.answer();a['status']='needs_review'
            a['inference']={**self.answer(),'citations':[],'basis':'model_inference'}
            exported=api.export({'items':[{'index':1,'question':self.question(),'answer':a}]})
            self.assertIn('模型推断候选（无充分课程依据，需复核）',exported)

    def test_strict_mode_does_not_call_inference(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            job={'id':'e'*24,'status':'running','course':'','allow_inference':False,'items':[{'index':1,'file':'1.png','status':'queued'}]}
            folder=api.folder/job['id'];folder.mkdir();(folder/'1.png').write_bytes(b'fixture')
            api.call_model=lambda *args:self.question();api.retrieve=lambda *args:([],[])
            a=self.answer();a['status']='needs_review';api.solve=lambda *args:a
            def unexpected(*args):raise AssertionError('strict mode requested inference')
            api.infer_candidate=unexpected
            api.lock.acquire();api.run(job)
            self.assertNotIn('inference',job['items'][0]['answer'])
            self.assertNotIn('inference_error',job['items'][0]['answer'])

    def test_parallel_questions_keep_order_and_evidence_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            questions=[]
            for i in range(5):
                q=self.question();q['stem']='question-'+str(i);questions.append(q)
            job={'id':'f'*24,'status':'running','course':'','items':[{'index':1,'file':'1.png','status':'queued'}]}
            folder=api.folder/job['id'];folder.mkdir();(folder/'1.png').write_bytes(b'fixture')
            api.call_model=lambda *args:{'questions':questions}
            barrier=threading.Barrier(3,timeout=3)
            count=0;count_lock=threading.Lock()
            def retrieve(q,*args):
                nonlocal count
                with count_lock:count+=1;n=count
                if n<=3:barrier.wait()
                return [],[{'id':'H1','kind':'handbook','text':q['stem']}]
            def solve(q,e):
                a=self.answer();a['reason']=e[0]['text'];return a
            api.retrieve=retrieve;api.solve=solve
            api.lock.acquire();api.run(job)
            self.assertEqual(job['status'],'complete')
            self.assertTrue(all(i['status']=='complete' for i in job['items']))
            self.assertEqual([i['index'] for i in job['items']],[1,2,3,4,5])
            self.assertEqual([i['answer']['reason'] for i in job['items']],['question-'+str(i) for i in range(5)])
            self.assertEqual(api.load(job['id']),job)

    def test_model_requests_are_bounded_and_slots_return_after_error(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None)
            api.model_slots=threading.BoundedSemaphore(3)
            ready=threading.Event();release=threading.Event();guard=threading.Lock()
            active=0;peak=0
            def request(instruction,*args):
                nonlocal active,peak
                with guard:
                    active+=1;peak=max(peak,active)
                    if active==3:ready.set()
                try:
                    if not release.wait(3):raise RuntimeError('test gate timeout')
                    if instruction=='0':raise QuizError('simulated provider error')
                    return instruction
                finally:
                    with guard:active-=1
            api._call_model=request
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures=[executor.submit(api.call_model,str(i),'',1) for i in range(8)]
                try:self.assertTrue(ready.wait(2));self.assertEqual(peak,3)
                finally:release.set()
                failures=0
                for future in futures:
                    try:future.result()
                    except QuizError:failures+=1
            self.assertEqual(failures,1);self.assertEqual(peak,3)
            self.assertEqual(api.call_model('next','',1),'next')

    def test_freed_worker_starts_next_before_slow_question_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);third_started=threading.Event()
            questions=[]
            for i in range(4):
                q=self.question();q['stem']=str(i);questions.append(q)
            job={'id':'1'*24,'status':'running','course':'','question_concurrency':2,'items':[{'index':1,'file':'1.png','status':'queued'}]}
            folder=api.folder/job['id'];folder.mkdir();(folder/'1.png').write_bytes(b'fixture')
            api.call_model=lambda *args:{'questions':questions};api.retrieve=lambda *args:([],[])
            def solve(q,e):
                if q['stem']=='0' and not third_started.wait(3):raise RuntimeError('scheduler waited for slow group')
                if q['stem']=='2':third_started.set()
                return self.answer()
            api.solve=solve;api.lock.acquire();api.run(job)
            self.assertTrue(third_started.is_set())
            self.assertTrue(all(i['status']=='complete' for i in job['items']))

    def test_answering_overlaps_next_image_recognition(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);started=threading.Event();calls=0
            job={'id':'2'*24,'status':'running','course':'','items':[]}
            folder=api.folder/job['id'];folder.mkdir()
            for i in range(2):
                (folder/f'{i}.png').write_bytes(b'fixture');job['items'].append({'index':i+1,'file':f'{i}.png','status':'queued'})
            def recognize(*args):
                nonlocal calls
                calls+=1
                if calls==2 and not started.wait(3):raise RuntimeError('all recognition blocked answering')
                return self.question()
            def solve(*args):started.set();return self.answer()
            api.call_model=recognize;api.retrieve=lambda *args:([],[]);api.solve=solve
            api.lock.acquire();api.run(job)
            self.assertTrue(all(i['status']=='complete' for i in job['items']))

    def test_upload_more_than_five_images_with_configured_concurrency(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);api.config=lambda:{'model':'test','endpoint':'http://localhost/test'}
            image={'data_url':'data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*30).decode()}
            with patch('kexam.quiz.threading.Thread'):
                result=api.submit({'images':[image]*6,'concurrency':4})
            saved=api.load(result['id']);api.lock.release()
            self.assertEqual(saved['image_count'],6);self.assertEqual(saved['question_concurrency'],4)

    def test_invalid_concurrency_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            api=QuizAPI(d,None,None);api.config=lambda:{}
            for concurrency in [True,0,9,2.5,'3']:
                with self.assertRaises(QuizError):api.submit({'images':[],'concurrency':concurrency})


if __name__=='__main__':unittest.main()
