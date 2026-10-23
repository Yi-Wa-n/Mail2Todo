import datetime as dt
from email.message import EmailMessage
import json
from pathlib import Path
import tempfile
import unittest
from backend.mail import core
from backend.mail.task_links import task_id,validate_link
from backend.storage import repository as repo
from backend.jobs.local_gate import review_packet


class TaskLinkTests(unittest.TestCase):
    def test_reminder_shares_calendar_and_completion_but_preserves_both_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=core.open_db(Path(tmp)/'test.sqlite3');repo.initialize(db)
            ids=[]
            for n in range(2):
                m=EmailMessage();m['Subject']='考试通知' if n==0 else '笔试提醒';m['Message-ID']=f'<{n}@example.invalid>';m.set_content('请按原邮件完成同一场笔试')
                core.store_message(db,m.as_bytes(),'INBOX','1',str(n),received=dt.datetime(2026,10,20+n,12,tzinfo=core.TZ))
                key=next(r[0] for r in db.execute('SELECT id FROM mails') if r[0] not in ids);ids.append(key)
                item={'id':key,'relevant':True,'requires_action':True,'company':'测试集团','role':'待核实','category':'笔试','status':'待处理','next_action':'完成笔试','evidence':'同一场笔试','event_at':None,'deadline':'2026-10-23T23:59:00+08:00','notes':f'备注{n}','important':True}
                if n:item['same_task_as']=ids[0]
                core.ingest(db,[item]);repo.sync_local(db)
            state=repo.state(db);self.assertEqual(2,len(state['emails']));self.assertEqual(1,sum(s['active'] for s in state['steps']))
            reminder=next(m for m in state['emails'] if m['id']==ids[1]);repo.mutate(db,'email',{'id':reminder['id'],'version':reminder['version'],'done':True})
            state=repo.state(db);self.assertTrue(all(m['done'] for m in state['emails']));self.assertEqual({'备注0','备注1'},{m['notes'] for m in state['emails']})
            with self.assertRaisesRegex(ValueError,'记录已变化'):
                repo.mutate(db,'email',{'id':reminder['id'],'version':reminder['version'],'done':False})
            original=next(m for m in state['emails'] if m['id']==ids[0]);repo.mutate(db,'email',{'id':original['id'],'version':original['version'],'done':False})
            self.assertTrue(all(not m['done'] for m in repo.state(db)['emails']))
            packet=review_packet(db,[{'id':'new','subject':'名称缩写提醒','received':'2026-10-23T12:00:00+08:00','body':'请按前次通知完成'}])
            self.assertIn(ids[0],{m['id'] for m in packet['related_existing_tasks']})
            step=next(s for s in repo.state(db)['steps'] if s['active'])
            repo.mutate(db,'step',{'id':step['id'],'version':step['version'],'done':True})
            self.assertTrue(all(m['done'] for m in repo.state(db)['emails']))
            # A later cancellation of the original also retires the reminder.
            stored=json.loads(db.execute('SELECT payload FROM records WHERE mail_id=?',(ids[1],)).fetchone()[0])
            stored.pop('same_task_as');stored['requires_action']=False;stored['resolves']=[ids[0]]
            core.ingest(db,[stored]);repo.sync_local(db)
            self.assertFalse(any(s['active'] for s in repo.state(db)['steps']))
            db.close()

    def test_rejects_self_future_different_category_and_cycles(self):
        a={'id':'a','received':'2026-10-20','category':'笔试'};b={'id':'b','received':'2026-10-21','category':'笔试','same_task_as':'a'}
        validate_link(b,{'a':a})
        for item,context in [({**b,'same_task_as':'b'},{'b':b}),({**a,'same_task_as':'b'},{'b':b}),({**b,'category':'面试'},{'a':a})]:
            with self.assertRaises(ValueError):validate_link(item,context)
        with self.assertRaises(ValueError):task_id('a',{'a':{**a,'same_task_as':'b'},'b':b})
        validate_link({**b,'manual_values':{'通知类型':'笔试'}},{'a':a})
        with self.assertRaisesRegex(ValueError,'人工日程修订'):
            validate_link({**b,'manual_values':{'截止时间':'2026-10-25T12:00:00+08:00'}},{'a':a})

    def test_independent_rounds_do_not_merge_without_model_link(self):
        from backend.mail.steps import project
        from backend.storage.repository import values
        items=[{'id':str(n),'company':'同公司','role':'开发','category':'面试','next_action':'参加面试','evidence':'独立轮次','received':f'2026-10-2{n}','event_at':None,'deadline':None,'requires_action':True} for n in (1,2)]
        self.assertEqual(2,len(project(items,values)))

    def test_original_and_reminder_in_same_scan_batch_share_one_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=core.open_db(Path(tmp)/'test.sqlite3');repo.initialize(db)
            ids=[]
            for n in range(2):
                m=EmailMessage();m['Subject']=f'同批通知{n}';m.set_content(f'通知正文{n}')
                core.store_message(db,m.as_bytes(),'INBOX','1',str(n),received=dt.datetime(2026,10,20+n,12,tzinfo=core.TZ))
                ids.append(next(r[0] for r in db.execute('SELECT id FROM mails') if r[0] not in ids))
            base={'relevant':True,'requires_action':True,'company':'测试集团','role':'开发','category':'笔试','status':'待处理','next_action':'完成笔试','evidence':'同一笔试提醒','event_at':None,'deadline':'2026-10-23T23:59:00+08:00','important':True}
            # Model output order need not equal chronological order.
            core.ingest(db,[{**base,'id':ids[1],'same_task_as':ids[0]},{**base,'id':ids[0]}]);repo.sync_local(db)
            self.assertEqual(1,sum(s['active'] for s in repo.state(db)['steps']))
            db.close()

    def test_link_proposal_is_rechecked_with_both_full_bodies_and_can_be_rejected(self):
        from backend.jobs.local_gate import confirm_task_links
        from backend.models.review_audit import AuditedDecisions
        with tempfile.TemporaryDirectory() as tmp:
            db=core.open_db(Path(tmp)/'test.sqlite3');repo.initialize(db);ids=[]
            for n in range(2):
                m=EmailMessage();m['Subject']=f'邀请{n}';m.set_content(f'Dear candidate {n}\n'+'正文'*10000+'\n结尾依据')
                core.store_message(db,m.as_bytes(),'INBOX','1',str(n),received=dt.datetime(2026,10,20+n,12,tzinfo=core.TZ))
                ids.append(next(r[0] for r in db.execute('SELECT id FROM mails') if r[0] not in ids))
            core.ingest(db,[{'id':ids[0],'relevant':True,'requires_action':True,'company':'测试集团','role':'开发','category':'笔试','status':'待处理','next_action':'完成笔试','evidence':'邀请','event_at':None,'deadline':None,'important':True}])
            batch=[m for m in core.pending(db) if m['id']==ids[1]]
            def reviewer(packet,config):
                self.assertIn('结尾依据',packet['new_mail'][0]['body'])
                self.assertIn('结尾依据',packet['related_existing_tasks'][0]['body'])
                self.assertTrue(packet['full_body_required_for_reminder_links'])
                return AuditedDecisions([{'id':ids[1],'same_task_as':None}],'confirmed')
            result=confirm_task_links(db,batch,AuditedDecisions([{'id':ids[1],'same_task_as':ids[0]}],'proposed'),{},reviewer)
            self.assertIsNone(result[0]['same_task_as']);self.assertEqual(2,result.model_calls)
            db.close()


if __name__=='__main__':unittest.main()
