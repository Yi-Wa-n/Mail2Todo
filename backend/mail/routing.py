"""Conservative exclusions only; absence of a recruitment word is never evidence."""
import re


def skip_reason(mail):
    subject, body = mail['subject'], mail['body']
    # Action/stage evidence overrides receipt, report, promotion and account titles.
    stages = r'测评|笔试|面试|体检|背调|背景调查|入职|聘用|录用|报到|三方协议|劳动合同'
    if re.search(r'邀请.{0,20}(?:'+stages+r')|(?:'+stages+r').{0,12}(?:邀请|通知|安排|预约|确认)|改期|取消|未通过|录用|补充材料|更新简历|签约|签署|预约截止', subject):
        return None
    if re.search(r'(?:请(?:您|你|于|在)?|务必|需要您|需要你)[\s\S]{0,100}(?:完成(?:在线|线上)?(?:'+stages+r')|参加(?:在线|线上)?(?:'+stages+r')|确认面试|确认参加|预约|补交|补充材料|更新简历|签署|上传.{0,15}(?:报告|材料)|提交.{0,15}(?:报告|材料))', body):
        return None
    if re.search(r'验证码|邮箱验证|邮箱认证|账号激活|账户已绑定|注册成功', subject):
        return 'account_notice'
    if re.search(r'满意度|招聘体验问卷|面试体验问卷|面试报告|测评报告', subject):
        return 'report_or_feedback'
    if re.search(r'投递成功|申请成功|投递回执|简历已收到|已收到您的(?:简历|申请)|感谢.{0,12}(?:投递|申请)|application received', subject, re.I):
        return 'receipt'
    if re.search(r'智联推荐|岗位推荐|职位推荐|\(AD\)|宣讲会|招聘会|直播|火热来袭|招聘.{0,10}(?:正式启动|火热开启)|邀请您投递简历', subject):
        return 'promotion'
    return None
