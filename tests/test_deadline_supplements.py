from email.message import EmailMessage
from pathlib import Path
import tempfile
import unittest
from backend.mail import core
from backend.mail.deadlines import calculated_steps
from backend.mail.deadline_supplements import supplement_key
from backend.storage import repository as repo


class SupplementTests(unittest.TestCase):
    def test_single_completed_undated_step_gains_time_without_losing_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            db=core.open_db(Path(folder)/'ledger.sqlite3');repo.initialize(db)
            mail=EmailMessage();mail['Subject']='补充材料';mail.set_content('收到后48小时内提交')
            core.store_message(db,mail.as_bytes(),'INBOX','1','1')
            key=db.execute('SELECT id FROM mails').fetchone()[0]
            item={'id':key,'relevant':True,'requires_action':True,'company':'测试公司','role':'开发','category':'补充材料','status':'已处理',
                  'next_action':'提交资料','evidence':'原文要求','event_at':None,'deadline':None,'important':False,'manual_completed':True}
            core.ingest(db,[item]);repo.sync_local(db)
            original=repo.state(db)['steps'][0]
            rule={'kind':'other','label':'提交资料','action':'收到后48小时内提交','source_text':'收到后48小时内提交','days':48,'unit':'hours','anchor':'received_date','anchor_explicit':True}
            step=calculated_steps([rule],'2026-10-23T12:00:00+08:00')[0];step['supplement_id']=supplement_key(rule)
            item.update(additional_date_steps=[step],additional_completion_snapshot=True)
            with db:db.execute('UPDATE records SET payload=? WHERE mail_id=?',(core.canonical(item),key))
            repo.sync_local(db);after=repo.state(db)
            self.assertEqual(1,len(after['steps']));self.assertEqual(original['id'],after['steps'][0]['id'])
            self.assertEqual('2026-10-25T12:00:00+08:00',after['steps'][0]['at'])
            self.assertTrue(after['steps'][0]['done']);self.assertTrue(after['emails'][0]['done'])
            db.close()

    def test_preserves_precise_exam_and_completion_when_adding_unknown_trigger(self):
        with tempfile.TemporaryDirectory() as folder:
            db=core.open_db(Path(folder)/'ledger.sqlite3');repo.initialize(db)
            mail=EmailMessage();mail['Subject']='测试公司笔试';mail.set_content('请参加笔试')
            core.store_message(db,mail.as_bytes(),'INBOX','1','1')
            key=db.execute('SELECT id FROM mails').fetchone()[0]
            item={'id':key,'relevant':True,'requires_action':True,'company':'测试公司','role':'开发','category':'笔试','status':'待处理',
                  'next_action':'参加笔试','evidence':'邀请','event_at':'2026-10-28T14:00:00+08:00','deadline':None,'important':True}
            core.ingest(db,[item]);repo.sync_local(db)
            before=repo.state(db);original=before['steps'][0]
            rule={'kind':'other','label':'提交补充资料','action':'收到后3天内提交资料','source_text':'3天内提交资料','days':3,'unit':'calendar_days','anchor':'other_event','anchor_explicit':True}
            step=calculated_steps([rule],'2026-10-23T12:00:00+08:00')[0];step['supplement_id']=supplement_key(rule)
            item.update(additional_date_steps=[step],additional_completion_snapshot=False)
            with db:db.execute('UPDATE records SET payload=? WHERE mail_id=?',(core.canonical(item),key))
            repo.sync_local(db);after=repo.state(db)
            exact=next(s for s in after['steps'] if s['id']==original['id'])
            self.assertEqual(original['at'],exact['at']);self.assertEqual(original['done'],exact['done'])
            self.assertEqual(2,len(after['steps']));self.assertEqual(1,sum(s['at'] is None for s in after['steps']))
            db.close()


if __name__=='__main__':unittest.main()
