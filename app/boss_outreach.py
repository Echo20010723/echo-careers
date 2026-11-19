"""Initial inquiries use explicit job facts and no availability/qualification promises."""
import re
from .policy import unsafe_instruction

def is_demo_payload(job):
    return (job.get('source') or '').startswith('mock') or (job.get('title') or '').startswith('界面验收 · ')

def inquiry_candidate(job,preferences):
    if is_demo_payload(job):return False,'测试或演示记录不能进入真实沟通'
    title=job.get('title') or '';text=job.get('raw_jd') or ''
    if unsafe_instruction(title+' '+text):return False,'岗位内容含指令，需复核'
    if '实习' not in title and '实习' not in (job.get('employment_type') or ''):return False,'不是明确实习岗位'
    if re.search(r'销售|获客|招聘|猎头|HR|主播|直播带货|兼职|代运营',title,re.I):return False,'岗位主体不是目标AI研发或产品工作'
    if not re.search(r'AI|人工智能|智能体|Agent|大模型|LLM|机器学习|算法|RAG',title,re.I):return False,'缺少目标AI岗位信息'
    if not re.search(r'开发|研发|产品|工程|应用|算法|模型|智能体|Agent|RAG|运维',title+' '+text,re.I):return False,'职责关联不足'
    if re.search(r'仅全职|不接受实习|只招全职|已取得博士|仅博士',text):return False,'存在明确资格冲突'
    city=job.get('location')
    if city and not any(c in city for c in preferences.get('cities',[])) and not ('远程' in text and preferences.get('remote_acceptable')):return False,'城市不在偏好范围'
    return True,'AI实习职责相关；仅初步询问，不承诺到岗、出勤或期限'

def inquiry_text(job):
    title=(job.get('title') or 'AI实习').strip()
    return f'您好，我正在寻找AI相关实习机会，看到贵司的{title}岗位，想进一步了解工作内容与实习要求，方便沟通吗？'
