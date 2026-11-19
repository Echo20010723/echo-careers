import io,json,re,hashlib
from pathlib import Path
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from .errors import AppError
from .store import uid,now,dumps

PDF_SECTION_HEADING = re.compile(
    r'(?:教育(?:背景|经历)?|工作(?:经历|经验)|实习(?:经历|经验)|项目(?:经历|经验|介绍)|'
    r'专业技能|技术栈|求职(?:意向|目标)|education|work experience|professional experience|'
    r'employment|projects?|(?:technical )?skills)[：:]?', re.I,
)


def _pdf_page_text(page):
    plain = page.extract_text() or ''
    lines = [line.strip() for line in plain.splitlines() if line.strip()]

    def heading_score(rows):
        # Standalone headings should be followed by text. Some PDFs render headings
        # correctly but place their drawing commands after the body in the text stream.
        return sum(bool(PDF_SECTION_HEADING.fullmatch(line))
                   and i + 1 < len(rows) and not PDF_SECTION_HEADING.fullmatch(rows[i + 1])
                   for i, line in enumerate(rows))

    orphan = any(PDF_SECTION_HEADING.fullmatch(line) and
                 (i + 1 == len(lines) or PDF_SECTION_HEADING.fullmatch(lines[i + 1]))
                 for i, line in enumerate(lines))
    if orphan:
        try:
            layout = page.extract_text(extraction_mode='layout') or ''
        except (PdfReadError, ValueError, TypeError, KeyError, IndexError):
            return plain
        visual = [re.sub(r'[ \t]+', ' ', line.strip()) for line in layout.splitlines() if line.strip()]
        if (heading_score(visual) > heading_score(lines)
                and len(re.sub(r'\s', '', layout)) >= .9*len(re.sub(r'\s', '', plain))):
            return '\n'.join(visual)
    return plain


def digest(text): return hashlib.sha256(text.encode('utf-8')).hexdigest()
def read_resume(name,content):
    if len(content)>5*1024*1024: raise AppError('INPUT_TOO_LARGE')
    suffix=Path(name).suffix.lower()
    try:
        if suffix=='.pdf':
            reader=PdfReader(io.BytesIO(content))
            if len(reader.pages)>50: raise AppError('INPUT_TOO_LARGE')
            text='\n'.join(_pdf_page_text(page) for page in reader.pages)
            if len(re.sub(r'\s','',text))<30: raise AppError('PDF_NO_TEXT')
        elif suffix in {'.txt','.md'}: text=content.decode('utf-8-sig')
        else: raise AppError('INVALID_INPUT')
    except AppError: raise
    except Exception: raise AppError('INVALID_INPUT') from None
    if not text.strip(): raise AppError('INVALID_INPUT')
    if len(text)>40000: raise AppError('INPUT_TOO_LARGE')
    return text.strip()
def split_semantic(text,source):
    parts=re.split(r'(?m)(?=^#{1,4} |^(?:教育|项目|技能|实习|工作|个人|荣誉)[^\n]{0,25}$)',text)
    chunks=[]
    for part in parts:
        part=part.strip()
        if not part: continue
        for start in range(0,len(part),1000):
            chunks.append({'id':digest(part+str(start))[:20],'title':part.splitlines()[0][:80],'text':part[start:start+1000],'source_file':source,'category':'uploaded','needs_current_confirmation':False,'note':'仅代表上传材料自述；不扩展为未写明的成果或承诺'})
    return chunks
class Resumes:
    def __init__(self,store,settings): self.store,self.settings=store,settings
    def seed(self):
        if self.store.one('SELECT id FROM resumes LIMIT 1'): return
        root=self.settings.root
        local = root/'knowledge_base/resume/resume_chunks.jsonl'
        if self.settings.use_demo_resume or not local.is_file():
            sample = root/'app/static/resume-demo.md'
            text = sample.read_text(encoding='utf-8-sig').strip()
            self.save('脱敏演示简历', text, split_semantic(text, sample.name), str(sample), resume_id='demo-resume')
            return
        chunks=[json.loads(x) for x in local.read_text(encoding='utf-8').splitlines() if x.strip()]
        # One canonical fact representation. PDF is retained as source attachment, not indexed again.
        text='\n\n'.join(c['text']+'\n边界：'+c.get('note','') for c in chunks)
        source_pdf = next(iter(sorted((root/'knowledge_base').glob('*.pdf'))), None)
        self.save('材料简历（原始事实切片）', text, chunks, str(source_pdf) if source_pdf else '', resume_id='provided-resume')
    def save(self,name,text,chunks,path,resume_id=None,select=True):
        rid=resume_id or uid(); version=digest(text)
        with self.store.tx() as db:
            db.execute('INSERT INTO resumes VALUES(?,?,?,?,?,?,?)',(rid,name,version,text,dumps(chunks),path,now()))
        if not select:
            return {'id':rid, 'name':name}
        self.select(rid)
        return self.current()
    def upload(self,name,content,*,select=True):
        text=read_resume(name,content)
        directory=self.settings.data_dir/'resumes'; directory.mkdir(parents=True,exist_ok=True)
        path=directory/(uid()+Path(name).suffix.lower()); path.write_bytes(content)
        return self.save(Path(name).name,text,split_semantic(text,Path(name).name),str(path),select=select)
    def select(self,rid):
        if not self.store.one('SELECT id FROM resumes WHERE id=?',(rid,)): raise AppError('RESUME_NOT_FOUND')
        with self.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('current_resume',dumps(rid)))
            db.execute("UPDATE outbox SET status='cancelled' WHERE status IN ('draft','queued','blocked') AND resume_id<>?",(rid,))
    def current(self):
        row=self.store.one('SELECT * FROM resumes WHERE id=?',(self.store.get('current_resume'),))
        if not row: raise AppError('RESUME_NOT_FOUND')
        row['chunks']=json.loads(row['chunks']); return row
