import hashlib,json,math,re,threading
from .store import uid,now
from .audit import REQUEST_ID
from dataclasses import dataclass
from .errors import AppError
from .policy import safe_example
from .resumes import digest

@dataclass
class Document:
    page_content: str
    metadata: dict


class LocalEmbeddings:
    # Offline feature hashing, explicitly simulated. No model downloads or paid calls.
    dimensions=256
    def embed_query(self,text):
        v=[0.0]*self.dimensions
        t=re.sub(r'\s+','',text.lower())
        grams=re.findall(r'[a-z][a-z0-9_+#]+',t)+[t[i:i+2] for i in range(len(t)-1)]
        for gram in grams:
            v[int(hashlib.sha256(gram.encode()).hexdigest()[:8],16)%len(v)]+=1
        norm=math.sqrt(sum(x*x for x in v)) or 1
        return [x/norm for x in v]
    def embed_documents(self,texts): return [self.embed_query(t) for t in texts]
class BudgetEmbeddings:
    def __init__(self,inner,settings,store,audit): self.inner,self.settings,self.store,self.audit=inner,settings,store,audit
    def reserve(self):
        if self.store:
            with self.store.tx() as db:
                if db.execute('SELECT count(*) FROM model_calls').fetchone()[0]>=self.settings.max_calls: raise AppError('BUDGET_EXCEEDED')
                db.execute('INSERT INTO model_calls VALUES(?,?,?)',(uid(),'embedding',now()))
    def validate(self,values):
        if any(len(v)!=self.settings.embedding_dimensions for v in values): raise AppError('RETRIEVAL_UNAVAILABLE')
        return values
    def embed_documents(self,texts):
        self.reserve()
        # Budget each LangChain embedding operation; avoid implicit multi-batch growth.
        if len(texts)>100 or sum(len(t) for t in texts)>120000: raise AppError('INPUT_TOO_LARGE')
        return self.validate(self.inner.embed_documents(texts))
    def embed_query(self,text):
        self.reserve();return self.validate([self.inner.embed_query(text)])[0]
class Rag:
    def __init__(self,settings,store=None,audit=None):
        self.settings=settings
        self.lock=threading.RLock()
        self.embeddings=LocalEmbeddings()
        if settings.model_mode=='live' and not settings.public_mode:
            import os
            if not os.getenv('OPENAI_API_KEY'): raise AppError('SERVICE_NOT_CONFIGURED')
            from langchain_openai import OpenAIEmbeddings
            self.embeddings=OpenAIEmbeddings(model=settings.embedding_model,dimensions=settings.embedding_dimensions,request_timeout=settings.timeout,max_retries=0,chunk_size=100)
            self.embeddings=BudgetEmbeddings(self.embeddings,settings,store,audit)
        self.signature=(('local-hash-256' if settings.public_mode else 'mock-hash-256') if isinstance(self.embeddings, LocalEmbeddings) else settings.embedding_model+'-'+str(settings.embedding_dimensions))
        self.cache={}
    def build(self,resume,force=False):
        with self.lock: return self._build(resume,force)
    def _build(self,resume,force=False):
        qa_text = ''
        if resume['id'] == 'provided-resume' and not self.settings.public_mode:
            manifest_path = self.settings.root/'knowledge_base/ingestion_manifest.json'
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                allowed = {item['file'] for item in manifest['vector_sources']}
                if not {'resume/resume_chunks.jsonl','qa/qa_core.jsonl'}.issubset(allowed):
                    raise AppError('RETRIEVAL_UNAVAILABLE')
                qa_text = (self.settings.root/'knowledge_base/qa/qa_core.jsonl').read_text(encoding='utf-8')
        signature=digest(resume['id']+resume['version']+self.signature+qa_text+'policy-v2')[:24]
        if signature in self.cache and not force: return self.cache[signature],signature
        docs=[]
        for c in resume['chunks']:
            if c.get('needs_current_confirmation'): continue
            docs.append(Document(page_content=c['text'],metadata={'kind':'resume','source':c.get('source_file',resume['name']),'resume_id':resume['id'],'version':resume['version'],'boundary':c.get('note',''),'payload':json.dumps(c,ensure_ascii=False)}))
        for line in qa_text.splitlines():
            q=json.loads(line)
            if safe_example(q): docs.append(Document(page_content=q['question']+' '+q.get('tag',''),metadata={'kind':'qa','source':'qa/qa_core.jsonl','resume_id':resume['id'],'version':resume['version'],'payload':json.dumps(q,ensure_ascii=False)}))
        try:
            if self.settings.vector_backend=='chroma':
                import chromadb
                from langchain_chroma import Chroma
                client=chromadb.PersistentClient(path=str(self.settings.data_dir/'vectors'),settings=chromadb.Settings(anonymized_telemetry=False))
                name='kb-'+signature
                if force:
                    try: client.delete_collection(name)
                    except Exception: pass
                vector=Chroma(client=client,collection_name=name,embedding_function=self.embeddings,collection_metadata={'embedding_model':self.signature,'version':resume['version'],'hnsw:space':'cosine'})
                existing=vector.get()['ids']
                if not existing and docs: vector.add_documents(docs,ids=[digest(d.page_content+d.metadata['kind']) for d in docs])
                value=vector
            else: value=list(zip(docs,self.embeddings.embed_documents([d.page_content for d in docs])))
        except Exception: raise AppError('RETRIEVAL_UNAVAILABLE') from None
        self.cache[signature]=value
        return value,signature
    def search(self,question,resume):
        try:
            vector,signature=self.build(resume)
            if self.settings.vector_backend=='chroma': pairs=vector.similarity_search_with_score(question,k=12)
            else:
                q=self.embeddings.embed_query(question)
                pairs=sorted([(d,1-sum(a*b for a,b in zip(q,v))) for d,v in vector],key=lambda x:x[1])[:12]
            references=[]; facts=[]
            for doc,distance in pairs:
                if distance>.85: continue
                # Lexical gate rejects hashing collisions and unrelated QA in the mock baseline.
                keywords=set(re.findall(r'[a-z][a-z0-9_+#]+|[\u4e00-\u9fff]{2}',question.lower()))
                if isinstance(self.embeddings, LocalEmbeddings) and not any(t in doc.page_content.lower() for t in keywords): continue
                data=json.loads(doc.metadata['payload'])
                if doc.metadata['kind']=='qa' and safe_example(data) and len(references)<3:
                    references.append({**data,'distance':float(distance),'source':doc.metadata['source']})
                elif doc.metadata['kind']=='resume' and len(facts)<4: facts.append(data)
            return {'references':references,'facts':facts,'index_version':signature,'embedding':self.signature,'distance_type':'cosine distance, not calibrated relevance'}
        except AppError: raise
        except Exception: raise AppError('RETRIEVAL_UNAVAILABLE') from None
