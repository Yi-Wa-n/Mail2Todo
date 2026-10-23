import json
import unittest
from tools.audit_deadlines import audit_schema,validate_audit
from backend.jobs.local_gate import response_schema


class DeadlineAuditTests(unittest.TestCase):
    def test_complete_coverage_and_source_validation(self):
        schema=audit_schema(response_schema()['properties']['decisions']['items']['properties']['deadline_rules'])
        packet={'new_mail':[{'id':'a','body':'请在三天内补齐资料'}]}
        rule={'kind':'other','label':'补齐资料','action':'三天内补齐资料','source_text':'请在三天内补齐资料',
              'anchor':'received_date','anchor_explicit':False,'days':3,'unit':'calendar_days'}
        decision={'id':'a','needs_action':True,'deadline_rules':[rule],'already_recorded':[False],'evidence_note':'待补期限'}
        self.assertEqual([decision],validate_audit(json.dumps({'decisions':[decision]}),packet,schema))
        for bad in ({**decision,'id':'b'},{**decision,'already_recorded':[]},{**decision,'needs_action':False},
                    {**decision,'deadline_rules':[{**rule,'source_text':'邮件没有的文本'}]}):
            with self.assertRaises(ValueError):validate_audit(json.dumps({'decisions':[bad]}),packet,schema)

    def test_full_body_redaction_does_not_truncate_deadlines(self):
        from backend.mail.core import safe_text
        body='说明'*10000+'请在三个工作日内提交材料 https://example.invalid/private 密码：abc123'
        cleaned=safe_text(body,maximum=None)
        self.assertIn('请在三个工作日内提交材料',cleaned)
        self.assertNotIn('example.invalid',cleaned);self.assertNotIn('abc123',cleaned)


if __name__=='__main__':unittest.main()
