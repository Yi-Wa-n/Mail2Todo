import datetime as dt
from email.message import EmailMessage
from pathlib import Path
import tempfile
import unittest
from backend.mail.deadlines import due_date,calculated_steps,enrich,validate_rules
from backend.mail import core
from backend.storage import repository as repo,mail_schedule
from backend.jobs.local_gate import validate_output


class DeadlineTests(unittest.TestCase):
    body='请在三个工作日内完成体检【特殊情况可延长至七天内】，并在取得报告后一天内完成报告上传。'
    received='2026-10-23T14:50:32+08:00'
    rules=[{'kind':'medical_exam','label':'完成体检','action':'完成体检','source_text':'请在三个工作日内完成体检',
            'anchor':'received_date','anchor_explicit':False,'days':3,'unit':'business_days'},
           {'kind':'report_upload','label':'上传体检报告','action':'取得报告后一天内完成报告上传','source_text':'取得报告后一天内完成报告上传',
            'anchor':'report_received','anchor_explicit':True,'days':1,'unit':'calendar_days'}]

    def test_workdays_skip_weekend_and_exclude_receipt_day(self):
        self.assertEqual('2026-10-28',due_date('2026-10-23',3,True))
        self.assertEqual('2026-10-26',due_date('2026-10-23',3))
        self.assertEqual('2026-10-26',due_date('2026-10-24',1,True))

    def test_calendar_overrides_and_year_boundary(self):
        self.assertEqual('2026-10-29',due_date('2026-10-23',3,True,holidays=['2026-10-26']))
        self.assertEqual('2026-10-27',due_date('2026-10-23',3,True,workdays=['2026-10-24']))
        self.assertEqual('2027-01-01',due_date('2026-12-31',1))
        with self.assertRaises(ValueError):due_date('2026-10-23',0)

    def test_hour_deadlines_keep_receipt_time_and_unknown_event_is_not_receipt(self):
        rule={**self.rules[0],'days':48,'unit':'hours','anchor_explicit':True}
        step=calculated_steps([rule],self.received)[0]
        self.assertEqual('2026-10-25T14:50:32+08:00',step['date'])
        self.assertFalse(step['date_only']);self.assertFalse(step['estimated'])
        unknown=calculated_steps([{**rule,'anchor':'other_event'}],self.received)[0]
        self.assertIsNone(unknown['date'])
        with self.assertRaises(ValueError):validate_rules([{**rule,'source_text':'48小时内','unit':'calendar_days','days':2}],'48小时内')

    def test_report_is_separate_trigger_and_no_fabricated_time(self):
        rules=calculated_steps(self.rules,self.received)
        self.assertEqual(2,len(rules))
        self.assertEqual('2026-10-28',rules[0]['date'])
        self.assertTrue(rules[0]['estimated']);self.assertTrue(rules[0]['date_only'])
        self.assertIn('不计收件当日',rules[0]['basis'])
        self.assertIsNone(rules[1]['date'])
        self.assertEqual('report_received',rules[1]['trigger'])
        self.assertEqual('取得报告后一天内完成报告上传',rules[1]['action'])

    def test_only_model_rules_drive_inference_and_evidence_is_checked(self):
        decision={'relevant':True,'requires_action':True,'next_action':'原有事项','deadline':None}
        self.assertEqual(decision,enrich(decision,{'body':self.body,'received':self.received}))
        validate_rules(self.rules,self.body)
        with self.assertRaises(ValueError):validate_rules([{**self.rules[0],'source_text':'原文没有的依据'}],self.body)
        with self.assertRaises(ValueError):validate_rules([{**self.rules[0],'days':True}],self.body)

    def test_date_only_normalization_and_manual_deadline(self):
        self.assertEqual('2026-10-28',repo.timestamp('2026-10-28'))
        self.assertIsNone(repo.timestamp('2026-02-30'))
        v=mail_schedule.normalize({'category':'录用','start':None,'deadline':'2026-10-28','action':'完成体检'})
        self.assertEqual('2026-10-28',v['deadline'])
        with self.assertRaises(ValueError):mail_schedule.normalize({**v,'start':'2026-10-28'})

    def test_model_deadline_rule_schema_accepts_counts_but_rejects_bool_and_range(self):
        from backend.jobs.local_gate import response_schema
        from backend.models.qoder_review import validate
        schema=response_schema()['properties']['decisions']['items']['properties']['deadline_rules']
        validate(self.rules,schema)
        for invalid in (True,0,525601,'3'):
            with self.assertRaises(ValueError):validate([{**self.rules[0],'days':invalid}],schema)

    def test_projection_persists_independent_steps_and_preserves_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            db=core.open_db(Path(folder)/'db.sqlite3');repo.initialize(db)
            message=EmailMessage();message['Subject']='体检通知';message.set_content(self.body)
            core.store_message(db,message.as_bytes(),'INBOX','1','1',received=dt.datetime.fromisoformat(self.received))
            key=db.execute('SELECT id FROM mails').fetchone()[0]
            decision={'id':key,'relevant':True,'requires_action':True,'company':'测试公司','role':'待核实','category':'录用','status':'待处理','next_action':'办理体检和报告上传','evidence':'原文要求体检','event_at':None,'deadline':None,'important':True,'deadline_rules':self.rules}
            item=enrich(decision,{'body':self.body,'received':self.received})
            core.ingest(db,[item]);repo.sync_local(db)
            state=repo.state(db);steps=[s for s in state['steps'] if s['active']]
            self.assertEqual(2,len(steps))
            exam=next(s for s in steps if s['label']=='完成体检')
            report=next(s for s in steps if s['label']=='上传体检报告')
            self.assertEqual('2026-10-28',exam['at']);self.assertTrue(exam['date_only']);self.assertTrue(exam['estimated'])
            self.assertIsNone(report['at'])
            repo.mutate(db,'step',{'id':exam['id'],'version':exam['version'],'done':True})
            repo.sync_local(db)
            after=repo.state(db)
            self.assertTrue(next(s for s in after['steps'] if s['id']==exam['id'])['done'])
            self.assertFalse(next(s for s in after['steps'] if s['id']==report['id'])['done'])
            self.assertFalse(after['emails'][0]['done'])
            mail=after['emails'][0]
            repo.edit_mail_schedule(db,{'op':'save','id':key,'version':mail['version'],'category':'录用','action':mail['action'],'start':None,'deadline':'2026-10-29'})
            override=next(s for s in repo.state(db)['steps'] if s['id']==exam['id'])
            self.assertEqual('2026-10-29',override['at']);self.assertFalse(override['estimated'])
            db.close()


if __name__=='__main__':unittest.main()
