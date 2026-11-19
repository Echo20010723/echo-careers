import re
from datetime import datetime,timezone

def current_text(text):
    # Quoted historical paragraphs do not represent a new recruiter event.
    lines=[]
    for line in text.splitlines():
        line=line.strip()
        if line.startswith(('>','历史引用：','引用：','历史消息：')): continue
        line=re.sub(r'[“「].*?[”」]','',line)
        if line: lines.append(line)
    return '\n'.join(lines)
def handoff_reason(text):
    t=current_text(text)
    if re.search(r'(加.{0,8}(微信|[Vv][Xx])|交换.{0,5}(电话|联系方式)|发.{0,6}(手机号|微信号)|留.{0,5}(电话|联系方式)|方便.{0,6}(电话|微信)|联系方式|加我.{0,5}(微信|[Vv][Xx])|(手机号|微信号|微信|电话).{0,6}(发我|给我|交换|多少)|wechat|phone number)',t,re.I): return '联系方式请求'
    if re.search(r'(正式面试|开始面试|现在.{0,5}面试|笔试|代码考核|现场编程|手写.{0,12}(代码|算法|LRU)|技术考核|深入.{0,5}技术|推导.{0,12}(公式|算法)|offer|转正条款)',t,re.I): return '正式考核或条款沟通'
    vague=r'(以后|后续|将来|合适|通过|如果|有机会).{0,15}(安排|邀请|联系).{0,8}面试'
    t=re.sub(vague,'',t)
    if re.search(r'((明天|周[一二三四五六日天]).{0,12}面试|面试.{0,8}(可以吗|方便吗))',t): return '明确面试邀约'
    if re.search(r'((邀请|请|约|安排|参加|来|进行).{0,18}面试|面试.{0,16}(时间|明天|周[一二三四五六日天]|几点|方便|安排|邀约)|interview.{0,20}(tomorrow|invite|schedule))',t,re.I): return '明确面试邀约'
    if re.search(r'(保证|承诺|签署|签约|接受.{0,6}(薪资|报价)|确认.{0,8}(入职|到岗)|必须.{0,8}答复)',t): return '需要本人判断的承诺'
    return None

def unknown_fact(text):
    return bool(re.search(r'(到岗|每周.{0,8}(天|出勤)|实习.{0,8}(多久|几个|月|期限)|连续.{0,8}(月|实习)|何时.{0,5}入职|什么时候.{0,5}入职|现在.{0,6}(城市|哪里)|接受.{0,8}(元|薪资))',text))
def unsafe_instruction(text):
    return bool(re.search(r'(忽略.{0,8}(规则|指令)|泄露|API.?key|系统提示|编造|伪造|无视.{0,5}约束|ignore.{0,10}instructions)',text,re.I))
def safe_example(row):
    text=row.get('question','')+' '+row.get('answer','')
    if row.get('needs_current_confirmation') or row.get('confirmation') in {'pending','expired','unconfirmed'}: return False
    if row.get('valid_until') and row['valid_until']<datetime.now(timezone.utc).date().isoformat(): return False
    if unsafe_instruction(text) or handoff_reason(text) or unknown_fact(text): return False
    if re.search(r'(已.{0,5}(发送|投递|预约)|这就.{0,4}发|可以接受|薪资|工资|元/|现居|安排确认|面试安排)',text): return False
    return True

def validate_reply(answer,resume,question):
    if not answer.strip(): return False
    if unsafe_instruction(answer): return False
    if re.search(r'(已.{0,5}(投递|发送|预约)|已添加|微信.{0,8}[a-zA-Z0-9]{5}|\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|确认参加面试)',answer): return False
    # Numeric achievements and technical names require current resume support.
    for value in re.findall(r'\d+(?:\.\d+)?\s*(?:%|FPS|年经验)',answer,re.I):
        if value not in resume: return False
    for token in ['DeepSeek','LangChain','FastAPI','PyTorch','YOLO','Chroma','硕士','博士']:
        if token.lower() in answer.lower() and token.lower() not in resume.lower(): return False
    if re.search(r'(识别准确率|全部上线|生产级部署|独立主导)',answer) and not any(x in resume for x in ['识别准确率','全部上线','生产级部署','独立主导']): return False
    return True
