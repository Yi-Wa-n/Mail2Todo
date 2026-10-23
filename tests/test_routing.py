import json
from email.message import EmailMessage
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from backend.mail import core
from backend.jobs import local_gate as gate
from backend.storage import repository


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=core.open_db(Path(self.temp.name)/'ledger.sqlite3')
        repository.initialize(self.db)

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def mail(self,subject,body,uid):
        message=EmailMessage();message['Subject']=subject
        message['Message-ID']=f'<{uid}@example.invalid>'
        message.set_content(body)
        added,candidate=core.store_message(self.db,message.as_bytes(),'INBOX','1',uid)
        key=self.db.execute('SELECT mail_id FROM sources WHERE uid=?',(uid,)).fetchone()[0]
        return key,candidate

    def test_unknown_mail_without_recruitment_words_is_routed(self):
        key,candidate=self.mail('后续安排','请阅读附件并办理相应手续','1')
        self.assertTrue(candidate)
        self.assertEqual(0,gate.local_filter(self.db))
        self.assertEqual([key],[m['id'] for m in core.pending(self.db)])

    def test_legacy_keyword_miss_is_recovered_and_clear_exclusion_logged(self):
        key,_=self.mail('某公司体检通知','请在三个工作日内完成体检，取得报告后一天内上传','1')
        excluded,_=self.mail('一次性验证码','此验证码仅用于登录','2')
        self.db.execute('UPDATE mails SET candidate=0');self.db.commit()
        self.assertEqual(1,gate.local_filter(self.db))
        self.assertEqual([key],[m['id'] for m in core.pending(self.db)])
        self.assertEqual('account_notice',self.db.execute('SELECT reason FROM local_filter_log WHERE mail_id=?',(excluded,)).fetchone()[0])

    def test_stage_action_overrides_receipt_and_report_titles(self):
        for title in ['投递成功','测评报告','岗位推荐','邮箱验证']:
            self.assertIsNone(gate.skip_reason({'subject':title,'body':'请在三个工作日内完成体检，并上传体检报告。'}))
        for title in ['某公司体检通知','入职安排通知','背景调查确认','劳动合同签署通知']:
            self.assertIsNone(gate.skip_reason({'subject':title,'body':'具体内容见附件'}))

    def test_review_failure_remains_pending_and_prior_decision_not_repeated(self):
        old,_=self.mail('旧安排','请查阅附件','1')
        core.ingest(self.db,[{'id':old,'relevant':False}])
        new,_=self.mail('后续安排','请查阅附件','2')
        self.db.execute('UPDATE mails SET candidate=0 WHERE id=?',(new,));self.db.commit()
        reviewer=Mock(side_effect=RuntimeError('synthetic failure'))
        with patch.object(gate,'RUNTIME',Path(self.temp.name)):
            with self.assertRaises(RuntimeError):
                gate.run_once(self.db,{'model_provider':'codebuddy_cn'},scanner=lambda *_:{'new_messages':0},reviewer=reviewer,credentials_loader=lambda:{})
        packet=reviewer.call_args.args[0]
        self.assertEqual([new],[m['id'] for m in packet['new_mail']])
        self.assertEqual([new],[m['id'] for m in core.pending(self.db)])

    def test_second_pass_does_not_call_model_again(self):
        key,_=self.mail('后续安排','请查阅附件','1')
        reviewer=Mock(return_value=[{'id':key,'relevant':False}])
        with patch.object(gate,'RUNTIME',Path(self.temp.name)):
            for _ in range(2):
                gate.run_once(self.db,{'model_provider':'codebuddy_cn'},scanner=lambda *_:{'new_messages':0},reviewer=reviewer,credentials_loader=lambda:{})
        self.assertEqual(1,reviewer.call_count)


if __name__=='__main__':unittest.main()
