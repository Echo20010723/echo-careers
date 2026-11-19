(() => {
  const get = id => document.getElementById(id);
  const api = async (path, options = {}) => {
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const error = Error(data.error?.message || '操作失败，请检查输入后重试。');
      error.status = response.status;
      throw error;
    }
    return data;
  };
  const post = (path, data) => api(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
  });
  const error = message => notice(message, true);
  const key = text => text.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
  const modeNames = {online:'在线搜索', demo:'脱敏演示', import:'本地 JSON 导入'};
  const platformNames = {himalayas:'Himalayas', greenhouse:'Greenhouse', careerjet:'Careerjet 中国', boss:'旧 BOSS 批次'};
  const evaluationNames = {local_rules:'本地规则', local_fallback:'模型不可用 · 本地规则', model_with_rules:'模型提取 · 规则分档', model:'模型建议'};
  const gradeNames = ['夯', '顶级', '人上人', 'npc', '拉完了'];
  const stages = {queued:'排队中', fetching:'正在获取岗位', deduplicating:'正在去重', analyzing:'正在分析', complete:'已完成', partial:'部分岗位失败', failed:'任务失败'};
  const desktop = matchMedia('(min-width: 821px)');
  const terminal = stage => ['complete','partial','failed','interrupted'].includes(stage);
  let agentId = null, agentTimer = null, listScroll = 0;
  let resumeLoad = 0, resumeBusy = false, searchBusy = false, resumeVersion = '';
  let resumeId = null, resumeName = '', taskId = null, batch = null, selectedJob = null, pollTimer = null;

  const addTerm = (term = '') => {
    if (get('discovery-terms').childElementCount >= 5) return;
    const row = el('div', undefined, 'term-row');
    const input = el('input');
    input.value = term;
    input.maxLength = 48;
    input.setAttribute('aria-label', '职位搜索词');
    const remove = el('button', undefined, 'secondary term-remove');
    const icon = el('span', '×', 'term-remove-icon');
    icon.setAttribute('aria-hidden', 'true');
    remove.append(el('span', '删除', 'term-remove-label'), icon);
    remove.title = '删除';
    remove.type = 'button';
    remove.onclick = () => {row.remove(); get('discovery-add').disabled = false; saveSearchDraft();};
    row.append(input, remove);
    get('discovery-terms').append(row);
    get('discovery-add').disabled = get('discovery-terms').childElementCount === 5;
  };

  function openWorkspace(focus = false) {
    const discovery = get('discovery');
    discovery.classList.add('workspace-open');
    syncResultVisibility();
    get('discovery-return').hidden = false;
    if (focus && desktop.matches && discovery.classList.contains('active')) {
      const title = get('discovery-settings').hidden
        ? get('discovery-back') : get('discovery-settings-title');
      title.focus({preventScroll:true});
      window.scrollTo({top:0, behavior:'instant'});
    }
  }

  function syncResultVisibility() {
    get('discovery-result').hidden = !(taskId || agentId || get('discovery').classList.contains('workspace-open'));
  }
  desktop.addEventListener('change', syncResultVisibility);
  const discoveryPanel = get('discovery');
  function expandSearch(open) {
    discoveryPanel.classList.toggle('search-collapsed', !open);
    get('mobile-search-toggle').setAttribute('aria-expanded', String(open));
  }
  get('mobile-search-toggle').onclick = () => expandSearch(discoveryPanel.classList.contains('search-collapsed'));
  get('mobile-list-back').onclick = () => {
    discoveryPanel.classList.remove('mobile-reading');
    const active = get('discovery-list').querySelector('[aria-pressed=true]');
    active?.focus({preventScroll:true});
    requestAnimationFrame(() => window.scrollTo(0, listScroll));
  };
  get('discovery-use-example').onclick = () => loadResume(() => post('/api/discovery/example', {}));
  api('/health').then(info => {
    if (info.public) {
      document.body.classList.add('public-workbench');
      document.body.dataset.publicModel = info.model_mode;
      get('mode-badge').textContent = info.model_mode === 'mock' ? '公开演示' : '公开体验';
    }
    const banner = get('deployment-banner');
    banner.hidden = false;
    const mode = info.model_mode === 'mock' ? '演示模式 · 无需模型密钥' : (info.model_configured === false || info.simulated_model)
      ? '模型未配置 · 服务端需设置 OPENAI_API_KEY，在线 Agent 会使用规则降级' : '真实模型模式 · 岗位 Agent 有独立预算';
    banner.textContent = mode + (info.public ? ' ｜公开体验 · 访客数据独立，重启或休眠后可能清空。投递由你决定。' : '');
  }).catch(() => {});

  get('discovery-back').onclick = () => {
    get('discovery').classList.remove('workspace-open');
    syncResultVisibility();
    get('discovery-resume').focus({preventScroll:true});
    window.scrollTo({top:0, behavior:'instant'});
  };
  get('discovery-return').onclick = () => openWorkspace(true);

  function evidenceItem(item) {
    return el('li', '第 '+item.line+' 行 · '+item.quote);
  }

  async function loadResume(task, {restoring = false, inline = false} = {}) {
    const revision = ++resumeLoad;
    resumeBusy = true;
    const profile = get('discovery-profile');
    profile.textContent = '正在读取简历并生成职位词…';
    const buttons = document.querySelectorAll('#discovery-resume-form button, #discovery-paste-form button, #discovery-resume-panel button, #discovery-use-example');
    const submit = get('discovery-task-form').querySelector('[type=submit]');
    buttons.forEach(button => button.disabled = true);
    submit.disabled = true;
    try {
      const resume = await task();
      const result = await api('/api/discovery/resumes/'+encodeURIComponent(resume.id)+'/terms');
      if (revision !== resumeLoad || result.resume_id !== resume.id) return false;
      const changed = resumeId !== resume.id || resumeVersion !== result.resume_version;
      resumeId = resume.id;
      resumeVersion = result.resume_version;
      resumeName = resume.name;
      if (changed && !restoring) {
        sessionStorage.removeItem('discovery-search-draft');
        get('discovery-task-form').reset();
        setMode('online');
      }
      get('discovery-terms').replaceChildren();
      get('discovery-add').disabled = false;
      const saved = restoring ? readSearchDraft() : null;
      (saved?.terms || result.suggestions.map(item=>item.term)).forEach(addTerm);
      if (saved) {
        setMode(saved.mode === 'import' ? 'online' : saved.mode);
        for (const [id,value] of Object.entries(saved.fields)) {
          if (draftFields.includes(id)) get(id).value = value;
        }
      }
      const roles = get('occupation-directions');
      roles.replaceChildren(...result.suggestions.map(item=>el('li', [item.kind, item.term,
        '第 '+item.line+' 行 · '+item.quote, ...(item.needs || []),
        ...(item.references || []).map(ref=>'模板 '+ref.id+' '+ref.title)].filter(Boolean).join(' · '))));
      get('occupation-suggestions').hidden = !roles.childElementCount;
      if (!result.suggestions.length) addTerm();
      get('discovery-settings').hidden = false;
      profile.textContent = resume.name+' · '+(evaluationNames[result.mode] || '本地规则');
      get('discovery-terms-hint').textContent = result.needs_input ? '简历证据不足以生成三个职位词，可编辑或补填。' : '可调整建议，去重后最多五个。';
      const summary = get('discovery-summary');
      summary.replaceChildren();
      const suggestions = el('ul');
      for(const item of result.suggestions) suggestions.append(el('li', item.term+' · 第 '+item.line+' 行 · '+item.quote));
      if(suggestions.childElementCount) summary.append(el('h3','职位词依据'),suggestions);
      for(const [category, items] of Object.entries(result.summary)){
        if (!items.length) continue;
        const list = el('ul');
        list.append(...items.map(evidenceItem));
        summary.append(el('h3', category), list);
      }
      get('discovery-evidence').hidden = !summary.childElementCount;
      sessionStorage.setItem('discovery-resume-ref', JSON.stringify({id:resume.id,name:resume.name}));
      openWorkspace(!restoring && !inline);
      updateResumeStatus();
      return true;
    } catch (e) {
      if (revision !== resumeLoad) return false;
      profile.textContent = resumeName || '未能处理这份简历，请查看提示。';
      if (inline) {
        const message = get('discovery-inline-error');
        message.textContent = e.message;
        message.hidden = false;
      } else if (!restoring) {
        error(e.message);
      } else {
        sessionStorage.removeItem('discovery-resume-ref');
      }
      return false;
    } finally {
      if (revision === resumeLoad) {
        resumeBusy = false;
        buttons.forEach(button => button.disabled = false);
        submit.disabled = searchBusy;
      }
    }
  }

  get('discovery-resume-form').onsubmit = event => {
    event.preventDefault();
    const file = get('discovery-resume').files[0];
    if (!file) return;
    loadResume(() => uploadResumeFile(file));
  };
  get('discovery-paste-form').onsubmit = event => {
    event.preventDefault();
    loadResume(() => post('/api/discovery/resume-text',{text:get('discovery-resume-text').value}));
  };
  function uploadResumeFile(file, endpoint = '/api/resumes') {
    const form = new FormData();
    form.append('file', file);
    return api(endpoint, {method:'POST', body:form});
  }

  function updateResumeStatus() {
    const ranked = batch?.resume;
    const hasRanking = Boolean(ranked);
    const updated = Boolean(resumeId && hasRanking && ranked.id !== resumeId);
    get('discovery-attach-resume').textContent = resumeId ? '更换简历' : '上传简历';
    get('discovery-resume-panel-title').textContent = resumeId ? '更换简历' : '上传简历';
    get('discovery-resume-name').textContent = resumeName ? '当前文件 · '+resumeName : '尚未上传简历';
    get('discovery-resume-update').hidden = !resumeId;
    get('discovery-resume-status').textContent = updated
      ? '简历已更新，当前结果仍基于上一份简历' : hasRanking ? '' : '简历已就绪 · '+resumeName;
    const ranking = get('discovery-ranking-resume');
    ranking.hidden = !hasRanking;
    ranking.textContent = hasRanking ? '本轮使用 · '+ranked.name : '';
    get('discovery-reanalyze').hidden = !(updated && batch.jobs.length && terminal(batch.stage));
    get('discovery-reanalyze').textContent = agentId ? '用新简历重跑 Agent' : '用新简历重新分析';
  }

  function toggleResumePanel(open) {
    const panel = get('discovery-resume-panel');
    const trigger = get('discovery-attach-resume');
    panel.hidden = !open;
    get('discovery-result').classList.toggle('resume-panel-open', open);
    if (open && !desktop.matches) get('discovery-resume-panel').scrollIntoView({block:'nearest'});
    trigger.setAttribute('aria-expanded', String(open));
    if (open) get('discovery-inline-resume').focus();
    else {
      panel.reset();
      get('discovery-inline-error').hidden = true;
      trigger.focus();
    }
  }
  get('discovery-attach-resume').onclick = () => toggleResumePanel(get('discovery-resume-panel').hidden);
  get('discovery-close-resume').onclick = () => toggleResumePanel(false);
  get('discovery-resume-panel').onsubmit = async event => {
    event.preventDefault();
    const file = get('discovery-inline-resume').files[0];
    if (!file) return;
    const trigger = get('discovery-attach-resume');
    trigger.disabled = true;
    get('discovery-inline-error').hidden = true;
    try {
      if (await loadResume(() => uploadResumeFile(file, '/api/discovery/resumes'), {inline:true})) {
        trigger.disabled = false;
        toggleResumePanel(false);
      }
    } finally { trigger.disabled = false; }
  };
  get('discovery-reanalyze').onclick = async event => {
    if (resumeBusy || searchBusy) return;
    const button = event.currentTarget;
    if (agentId) { get('agent-form').requestSubmit(); return; }
    const sourceTask = taskId;
    button.disabled = true;
    try {
      const task = await post('/api/discovery/tasks/'+sourceTask+'/reanalyze', {resume_id:resumeId});
      if (taskId === sourceTask) watch(task.id, true, true);
    } catch (e) { error(e.message); }
    finally { button.disabled = false; }
  };

  const draftFields = ['discovery-job-type','discovery-country','discovery-city','discovery-limit',
    'discovery-months','discovery-available','discovery-days','discovery-graduation'];
  function readSearchDraft() {
    try {
      const draft = JSON.parse(sessionStorage.getItem('discovery-search-draft'));
      return draft?.id === resumeId && draft.version === resumeVersion ? draft : null;
    } catch { return null; }
  }
  function saveSearchDraft() {
    if (!resumeId || resumeBusy) return;
    sessionStorage.setItem('discovery-search-draft', JSON.stringify({
      id:resumeId, version:resumeVersion, mode:get('discovery-mode').value,
      terms:[...get('discovery-terms').querySelectorAll('input')].map(input=>input.value),
      fields:Object.fromEntries(draftFields.map(id=>[id,get(id).value])),
    }));
  }
  get('discovery-task-form').addEventListener('input', saveSearchDraft);
  get('discovery-add').onclick = () => { addTerm(); saveSearchDraft(); };
  function setMode(mode) {
    const online = mode === 'online';
    get('discovery-mode').value = mode;
    get('discovery-file-field').hidden = mode !== 'import';
    get('discovery-job-file').required = mode === 'import';
    get('discovery-online').hidden = online;
    get('discovery-source-label').textContent = online ? 'Himalayas · 远程' : mode === 'import' ? '本地文件' : '脱敏演示';
    get('discovery-city-field').hidden = online;
    get('discovery-country-field').hidden = !online;
    get('discovery-type-field').hidden = !online;
    get('discovery-submit').textContent = {
      online:'搜索岗位并排名', import:'导入并排名', demo:'运行脱敏演示',
    }[mode];
    get('discovery-mode-note').textContent = online
      ? '搜索 Himalayas 公开远程岗位。国家/地区用于筛选允许远程工作的范围。'
      : mode === 'import' ? '读取你选择的本地文件，按职位词筛选。' : '使用内置虚构岗位。';
  }
  get('discovery-import').onclick = () => setMode('import');
  get('discovery-demo').onclick = () => {setMode('demo'); get('discovery-task-form').requestSubmit(get('discovery-submit'));};
  get('discovery-online').onclick = () => setMode('online');
  const preferenceIds = ['discovery-city','discovery-country',
    'discovery-job-type','discovery-months','discovery-available','discovery-days','discovery-graduation','discovery-limit'];
  for (const id of preferenceIds) {
    const input = get(id);
    const saved = localStorage.getItem(id);
    if (saved !== null && (input.tagName !== 'SELECT' || [...input.options].some(option=>option.value === saved))) input.value = saved;
    input.addEventListener('change',() => localStorage.setItem(id,input.value));
  }
  setMode('online');

  function replaceOptions(id, items) {
    const select = get(id);
    const current = select.value;
    const keep = select.options[0];
    select.replaceChildren(keep);
    for (const [value, label] of items) {
      const option = el('option', label);
      option.value = value;
      select.append(option);
    }
    if ([...select.options].some(option => option.value === current)) select.value = current;
  }

  function showDetail(job) {
    const detail = get('discovery-detail');
    detail.hidden = false;
    detail.replaceChildren(el('h3',job.title || '未提供岗位名'));
    const analysis = job.analysis;
    const origin = platformNames[job.source];
    const metadata = el('div', undefined, 'discovery-job-meta muted');
    for (const value of [job.company, job.city, job.salary_raw, origin && '来源 · '+origin,
      job.detail_status === 'detail' ? '已读取详情' : '仅列表 · 完整 JD 待核对'].filter(Boolean)) {
      metadata.append(el('span', value));
    }
    detail.append(metadata);
    if (job.retrieved_by?.length) detail.append(el('p','来源检索词 · '+job.retrieved_by.join('、'),'muted'));
    if (job.lexical_matches?.length) detail.append(el('p','标题或 JD 文本命中 · '+job.lexical_matches.join('、'),'muted'));
    if (job.remote) {
      const locations = job.location_restrictions || [];
      const timezones = job.timezone_restrictions || [];
      const restrictions = el('div', undefined, 'discovery-job-meta discovery-remote');
      restrictions.append(el('span',locations.length ? '允许远程工作的地区：'+locations.join('、') : '远程 · 来源未列出国家限制'));
      if (timezones.length) restrictions.append(el('span','时区：'+timezones.join('、')));
      detail.append(restrictions);
    }
    if (!analysis) { detail.append(el('p',job.error || '等待分析')); return; }
    detail.append(el('p','本轮档位 '+analysis.grade+' · 证据分 '+analysis.match_score+'/100'),
      el('p',analysis.reason), el('p',evaluationNames[analysis.evaluation_mode],'muted'));
    const groups = [
      ['岗位核心职责', analysis.duties],
      ['必需技能与要求', analysis.required],
      ['加分技能', analysis.bonus],
      ['最相关简历证据', analysis.evidence.map(item=>'第 '+item.line+' 行 · '+item.quote)],
      ['关键缺口', analysis.gaps],
      ['硬条件核对', analysis.conditions.map(item=>item.name+' · '+item.status+(item.evidence?' · '+item.evidence:''))],
      ['向 HR 确认', analysis.questions],
    ];
    for(const [title, items] of groups){
      detail.append(el('h4',title));
      if(!items.length){detail.append(el('p','暂无可核实信息','muted')); continue;}
      const list = el('ul');
      list.append(...items.map(item=>el('li',item)));
      detail.append(list);
    }
    detail.append(el('h4','下一步投递建议'),el('p',analysis.recommendation));
    detail.append(draftPanel(job));
    const jd = el('details');
    jd.append(el('summary',job.jd?'查看 JD 原文':'查看岗位摘要'),el('pre',job.jd || job.jd_summary || '未提供岗位描述'));
    detail.append(jd);
    const url = job.url || job.source_page_url;
    if (url && /^https?:\/\//i.test(url)) {
      const link = el('a',job.url ? '打开岗位原页' : '打开来源列表页');
      link.href=url; link.target='_blank'; link.rel='noopener noreferrer';
      detail.append(link);
    }
    if (job.application_url && job.application_url !== url && /^https?:\/\//i.test(job.application_url)) {
      const apply = el('a','打开官方申请入口','button-link secondary');
      apply.href = job.application_url; apply.target = '_blank'; apply.rel = 'noopener noreferrer';
      detail.append(' ',apply);
    }
  }

  function draftPanel(job) {
    const panel = el('details');
    const question = el('textarea');
    question.rows = 2;
    question.maxLength = 1500;
    question.placeholder = '例如：招聘方问我做过哪些 FastAPI 项目？';
    const label = el('label','招聘方的问题');
    label.append(question);
    const create = el('button','生成回复草稿','secondary');
    create.type = 'button';
    const output = el('div');
    output.setAttribute('aria-live','polite');
    panel.append(el('summary','生成 HR 回复草稿'),label,create,output);
    create.onclick = async () => {
      if (!question.value.trim() || !resumeId) return;
      create.disabled = true;
      output.textContent = '正在检索当前简历…';
      try {
        const draft = await post('/api/discovery/drafts', {resume_id:resumeId, question:question.value.trim(), job_title:job.title || ''});
        output.replaceChildren(el('p',draft.mode,'muted'),el('p',draft.answer));
        for (const evidence of draft.facts) output.append(el('p',evidence.source+' · '+evidence.quote,'muted'));
        for (const example of draft.examples) output.append(el('p','历史问答参考 · '+example.question,'muted'));
        if (!draft.facts.length) output.append(el('p','当前简历没有命中证据。','muted'));
        if (draft.can_approve) {
          const approve = el('button','确认并存为历史问答','secondary');
          approve.type = 'button';
          approve.onclick = async () => {
            try {
              await post('/api/discovery/drafts/'+draft.id+'/approve', {});
              approve.textContent = '已保存';
              approve.disabled = true;
            } catch (e) { error(e.message); }
          };
          output.append(approve);
        }
      } catch (e) { output.textContent = e.message; }
      finally { create.disabled = false; }
    };
    return panel;
  }

  function renderJobs() {
    const area = get('discovery-list');
    if (!batch) return;
    const jobs = batch.jobs.filter(job => (
      (!get('filter-grade').value || job.analysis?.grade === get('filter-grade').value) &&
      (!get('filter-city').value || (job.city || (job.remote ? '远程' : '待确认')) === get('filter-city').value) &&
      (!get('filter-term').value || job.matched_terms.includes(get('filter-term').value)) &&
      (!get('filter-unknown').checked || job.analysis?.conditions.some(item => item.status === '待确认'))
    )).sort((a,b)=>(a.analysis?.rank ?? 9)-(b.analysis?.rank ?? 9) ||
      (b.analysis?.match_score ?? 0)-(a.analysis?.match_score ?? 0));
    const scroll = area.scrollTop;
    area.replaceChildren();
    if (!jobs.length) {
      get('discovery-detail').hidden = true;
      area.append(el('p',batch.jobs.length ? '没有符合当前筛选条件的岗位。' :
        batch.stage === 'failed' ? '本轮没有可显示的岗位。请查看上方失败原因。' :
        terminal(batch.stage) ? '本轮没有匹配岗位。可调整搜索词或城市。' : '等待后台获取岗位。','empty'));
      return;
    }
    area.append(el('p','显示 '+jobs.length+' / '+batch.jobs.length+' 条','muted'));
    const active = jobs.find(job => job.local_job_id === selectedJob) || jobs[0];
    selectedJob = active.local_job_id;
    for(const job of jobs) {
      const card = el('button',undefined,'discovery-job');
      card.setAttribute('aria-pressed', String(job === active));
      card.type = 'button';
      const label = el('span',job.analysis?.grade || (job.error?'分析失败':'待分析'),'grade grade-'+(job.analysis?.rank ?? 'none'));
      const heading = el('span',undefined,'discovery-job-heading');
      heading.append(el('strong',job.title || '未提供岗位名'),label);
      card.append(heading,
        el('span',[job.company,job.city || (job.remote ? '远程' : null),platformNames[job.source],job.detail_status === 'list_only' ? '仅列表' : null].filter(Boolean).join(' · '),'muted'),
        el('span',job.analysis?.reason || job.error || '等待分析','discovery-job-reason'));
      card.onclick = () => {
        area.querySelector('[aria-pressed="true"]')?.setAttribute('aria-pressed','false');
        card.setAttribute('aria-pressed','true');
        selectedJob = job.local_job_id;
        showDetail(job);
        const detail = get('discovery-detail');
        if (desktop.matches) detail.scrollTop = 0;
        else {
          listScroll = window.scrollY;
          discoveryPanel.classList.add('mobile-reading');
          get('mobile-list-back').focus({preventScroll:true});
          get('discovery-result').scrollIntoView({block:'start'});
        }
      };
      area.append(card);
    }
    showDetail(active);
    area.scrollTop = scroll;
  }

  function refreshHistory() {
    let history = [];
    try { history = JSON.parse(sessionStorage.getItem('discovery-history')) || []; } catch {}
    if (taskId && batch?.resume && terminal(batch.stage)) {
      const item = {id:taskId, label:batch.resume.name+' · '+(batch.terms[0] || '岗位批次')};
      history = [item, ...history.filter(row=>row.id!==item.id)].slice(0,8);
      sessionStorage.setItem('discovery-history', JSON.stringify(history));
    }
    const select = get('discovery-history');
    const start = el('option','选择批次');
    start.value = '';
    select.replaceChildren(start,...history.map(item=>{
      const option = el('option',item.label);
      option.value = item.id;
      return option;
    }));
    select.value = taskId || '';
    get('discovery-history-field').hidden = history.length < 2 && !(agentId && history.length);
  }
  get('discovery-history').onchange = event => {
    if (event.target.value && event.target.value !== taskId) watch(event.target.value, true);
  };

  function update() {
    const {stage,limit,counts,jobs} = batch;
    const current = counts.current_term ? ' · '+counts.current_term : '';
    get('discovery-result-title').textContent = '岗位结果 '+jobs.length;
    get('discovery-round').hidden = false;
    get('discovery-status').textContent = (agentId ? 'Agent · ' : '')+(batch.reused_candidates ? '使用新简历重评原岗位 · 筛选词来自原搜索 · ' : '')+
      (batch.mode === 'online' ? '' : modeNames[batch.mode]+' · ')+ (stages[stage] || ({running:'正在执行', waiting:'等待回答', interrupted:'本轮中断'}[stage] || stage))+current+(batch.error?' · '+batch.error:'');
    const parts = [
      ['获取',counts.fetched],['命中',counts.matched],['去重后',counts.unique],
      ['本轮候选',counts.selected],['已分析',counts.analyzed],['失败',counts.failed],
    ];
    get('discovery-overview').textContent = parts.filter(([,count])=>count!==undefined).map(([label,count])=>label+' '+count).join(' · ')+
      (terminal(stage) && (counts.selected ?? 0) < limit ? ' · 候选少于目标 '+limit+' 条'+
        (batch.mode === 'online' && stage === 'complete' ? '（本轮符合条件的岗位不足）' : '') : '');
    get('discovery-attribution').hidden = !(batch.mode === 'online' && batch.platform === 'himalayas') && !jobs.some(job => job.source === 'himalayas');
    get('discovery-queries').textContent = Object.entries(counts.queries || {})
      .filter(([entered,actual]) => entered !== actual)
      .map(([entered,actual])=>entered+' → '+actual).join(' · ');
    get('discovery-filters').hidden = !jobs.length;
    get('discovery-retry').hidden = Boolean(agentId) || !['partial','failed'].includes(stage);
    const exportLink = get('discovery-export');
    exportLink.hidden = !terminal(stage);
    exportLink.setAttribute('aria-label','导出 JSON（'+jobs.length+' 条岗位）');
    exportLink.href = agentId ? '/api/discovery/agent/'+agentId+'/export' : '/api/discovery/tasks/'+taskId+'/export';
    const distribution = batch.grade_distribution || gradeNames.map(grade => ({
      grade, count: jobs.filter(job => job.analysis?.grade === grade).length,
    }));
    get('discovery-grade-note').textContent = counts.analyzed >= 5
      ? '档位按本轮相对匹配度分布。投递建议依据岗位要求与简历证据。'
      : '已分析岗位不足 5 条，部分档位可能为空。';
    replaceOptions('filter-grade', distribution.map(({grade,count}) => [grade,grade+'（'+count+'）']));
    const gradeFilter = get('filter-grade');
    const filters = get('discovery-grade-distribution');
    filters.replaceChildren(...[{grade:'', count:jobs.length}, ...distribution].map(({grade,count},index) => {
      const item = el('li');
      const button = el('button', (grade || '全部')+' '+count, 'grade grade-'+(index-1));
      button.type = 'button';
      button.value = grade;
      button.onclick = () => {
        gradeFilter.value = gradeFilter.value === grade ? '' : grade;
        updateGradeButtons();
        renderJobs();
      };
      item.append(button);
      return item;
    }));
    updateGradeButtons();
    replaceOptions('filter-city',[...new Set(jobs.map(job=>job.city || (job.remote ? '远程' : '待确认')))].sort().map(city=>[city,city]));
    replaceOptions('filter-term',batch.terms.map(term=>[term,term]));
    renderJobs();
    updateResumeStatus();
    refreshHistory();
  }

  function updateGradeButtons() {
    const value = get('filter-grade').value;
    get('discovery-grade-distribution').querySelectorAll('button').forEach(button =>
      button.setAttribute('aria-pressed', String(button.value === value)));
  }

  async function poll() {
    const requestedId = taskId;
    try {
      const result = await api('/api/discovery/tasks/'+requestedId);
      if (requestedId !== taskId) return;
      batch = result;
      update();
      if (!terminal(batch.stage)) pollTimer = setTimeout(poll,750);
    } catch (e) {
      if (requestedId !== taskId) return;
      if ([404,422].includes(e.status)) {
        get('discovery-status').textContent = '该批次已失效，请重新搜索。';
        sessionStorage.removeItem('discovery-task');
        return;
      }
      get('discovery-status').textContent = '暂时无法读取进度，正在重新连接…';
      pollTimer = setTimeout(poll,2500);
    }
  }
  function watch(id, reset, keepSelection = false) {
    clearTimeout(agentTimer);
    agentId = null;
    sessionStorage.removeItem('discovery-agent');
    get('agent-trace').hidden = true;
    discoveryPanel.classList.remove('mobile-reading');
    expandSearch(false);
    clearTimeout(pollTimer);
    taskId = id;
    sessionStorage.setItem('discovery-task',id);
    if (reset) {
      if (!keepSelection) selectedJob = null;
      batch = null;
      get('discovery-result-title').textContent = '岗位结果 0';
      get('discovery-list').replaceChildren();
      get('discovery-list').scrollTop = 0;
      get('discovery-detail').hidden = true;
      get('discovery-overview').textContent = '';
      get('discovery-grade-note').textContent = '';
      get('discovery-grade-distribution').replaceChildren(...gradeNames.map((grade,index) => el('li',grade+' 0','grade grade-'+index)));
      get('discovery-queries').textContent = '';
      get('discovery-attribution').hidden = true;
      for (const id of ['discovery-filters','discovery-export','discovery-retry']) get(id).hidden = true;
      for(const name of ['filter-grade','filter-city','filter-term']) get(name).value='';
      get('filter-unknown').checked=false;
    }
    get('discovery-result').hidden = false;
    get('discovery-round').hidden = false;
    updateResumeStatus();
    get('discovery-status').textContent = '任务已接收，正在排队…';
    poll();
  }

  get('discovery-task-form').onsubmit = async event => {
    event.preventDefault();
    if (!resumeId || resumeBusy || searchBusy) return;
    const submittedResume = resumeId;
    const submittedRevision = resumeLoad;
    const submit = event.submitter;
    searchBusy = true;
    submit.disabled = true;
    saveSearchDraft();
    try {
      const terms = [...new Map([...get('discovery-terms').querySelectorAll('input')]
        .map(input=>input.value.trim()).filter(Boolean).map(term=>[key(term),term])).values()];
      const limit = get('discovery-limit').valueAsNumber;
      if (!terms.length || terms.length > 5 || terms.some(term=>term.length < 2 || term.length > 48 || /[。！？；;]/.test(term))) throw Error('请输入 1–5 个不同的短职位词，每个 2–48 字。');
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw Error('候选数量必须是 1–100 的整数。');
      const mode = get('discovery-mode').value;
      const sourceOptions = mode === 'online' ? {
        job_type:get('discovery-job-type').value,
        country:get('discovery-country').value.toUpperCase() || null,
      } : {};
      let jobs = [];
      if (mode === 'import') {
        const file = get('discovery-job-file').files[0];
        if (!file || file.size > 5 * 1024 * 1024) throw Error('请选择不超过 5 MB 的岗位 JSON。');
        let data;
        try { data = JSON.parse((await file.text()).replace(/^\uFEFF/,'')); }
        catch { throw Error('文件不是有效的 JSON。'); }
        jobs = Array.isArray(data) ? data : data?.jobs;
        if (!jobs && Array.isArray(data?.records)) {
          jobs = data.records.filter(row => row && typeof row.job_title === 'string' && row.job_title.trim() &&
            typeof row.jd_text === 'string' && row.jd_text.trim());
        }
        if (!Array.isArray(jobs) || !jobs.length || jobs.length > 1000) throw Error('文件须包含 1–1000 条岗位。');
      }
      const task = await post('/api/discovery/tasks',{
        resume_id:submittedResume, platform:'himalayas', mode, limit, terms, jobs, ...sourceOptions,
        city:mode === 'online' ? null : get('discovery-city').value.trim() || null,
        internship_months:get('discovery-months').valueAsNumber || null,
        available_from:get('discovery-available').value || null,
        graduation_year:get('discovery-graduation').valueAsNumber || null,
        weekly_days:get('discovery-days').valueAsNumber || null,
      });
      if (submittedRevision !== resumeLoad || submittedResume !== resumeId) return;
      watch(task.id,true);
      if (!desktop.matches) get('discovery-result').scrollIntoView({block:'start'});
    } catch (e) {error(e.message);}
    finally { searchBusy=false; submit.disabled=resumeBusy; }
  };
  get('discovery-retry').onclick = async () => {
    const button = get('discovery-retry');
    button.disabled=true;
    try {const task = await post('/api/discovery/tasks/'+taskId+'/retry',{}); watch(task.id,false);}
    catch (e) {error(e.message);}
    finally {button.disabled=false;}
  };
  for (const id of ['filter-grade','filter-city','filter-term','filter-unknown']) get(id).onchange = () => {
    updateGradeButtons();
    renderJobs();
  };
  function renderAgent(run) {
    const trace = get('agent-trace');
    trace.hidden = false;
    trace.querySelector('summary').textContent = 'Agent 执行记录 · '+run.events.length+' 步';
    if (run.status === 'waiting') trace.open = true;
    else if (terminal(run.status)) trace.open = false;
    get('agent-mode').textContent = {
      demo:'演示模式 · 虚构岗位与可控规则',
      live:'真实模型驱动 · Himalayas 在线岗位',
      rule_fallback:'规则降级 · Himalayas 在线岗位',
    }[run.mode];
    get('agent-latest').textContent = run.stop_reason || run.events.at(-1)?.summary || '正在准备 Agent 任务…';
    get('agent-events').replaceChildren(...run.events.map(event => el('li', event.summary)));
    const eligibility = (run.inspected || []).filter(item => item.degree_quote || item.degree_preferred);
    get('agent-evidence').hidden = !eligibility.length;
    get('agent-eligibility').replaceChildren(...eligibility.map(item =>
      el('li', item.title+' · '+(item.degree_quote || item.degree_preferred))));
    get('agent-answer').hidden = !run.question;
    get('agent-question').textContent = run.question?.text || '';
    const degree = run.question?.field === 'degree';
    get('agent-answer-degree').hidden = !degree;
    get('agent-answer-value').hidden = degree;
    get('agent-question').htmlFor = degree ? 'agent-answer-degree' : 'agent-answer-value';
    batch = {
      stage:run.status, limit:0, counts:{analyzed:run.recommendations.length}, terms:[],
      mode:run.mode === 'demo' ? 'demo' : 'online', platform:run.mode === 'demo' ? 'synthetic' : 'himalayas',
      jobs:run.recommendations.map(job => ({...job, matched_terms:job.search_terms || []})),
      resume:run.resume || {id:run.resume_id, name:run.resume_id === resumeId ? resumeName : '本轮简历'},
    };
    update();
  }

  async function pollAgent() {
    const id = agentId;
    try {
      const run = await api('/api/discovery/agent/'+id);
      if (id !== agentId) return;
      renderAgent(run);
      if (!terminal(run.status) && run.status !== 'waiting') agentTimer = setTimeout(pollAgent, 650);
    } catch (e) {
      if (id !== agentId) return;
      if (e.status === 404) {
        agentId = null;
        sessionStorage.removeItem('discovery-agent');
        get('agent-trace').hidden = true;
        if (!resumeId) discoveryPanel.classList.remove('workspace-open');
        syncResultVisibility();
        error('本轮 Agent 数据已失效，请重新选择或上传简历。');
      } else {
        get('agent-latest').textContent = e.message;
        agentTimer = setTimeout(pollAgent, 2500);
      }
    }
  }

  function showAgent(id) {
    clearTimeout(pollTimer);
    clearTimeout(agentTimer);
    taskId = null;
    agentId = id;
    batch = null;
    selectedJob = null;
    sessionStorage.removeItem('discovery-task');
    sessionStorage.setItem('discovery-agent', id);
    openWorkspace();
    discoveryPanel.classList.remove('mobile-reading');
    expandSearch(false);
    get('agent-trace').hidden = false;
    get('agent-trace').open = true;
    get('agent-latest').textContent = '正在准备 Agent 任务…';
    get('discovery-export').hidden = true;
    get('discovery-list').replaceChildren();
    get('discovery-detail').hidden = true;
    for (const filter of ['filter-grade','filter-city','filter-term']) get(filter).value = '';
    get('filter-unknown').checked = false;
    pollAgent();
  }

  get('agent-form').onsubmit = async event => {
    event.preventDefault();
    const button = event.submitter || get('agent-form').querySelector('button[type=submit]');
    button.disabled = true;
    try {
      const run = await post('/api/discovery/agent', {
        resume_id:resumeId, goal:get('agent-goal').value.trim(), source:get('agent-source').value,
        target:get('agent-target').valueAsNumber, country:get('discovery-country').value.trim().toUpperCase() || null,
        degree:get('agent-degree').value || null, job_type:get('discovery-job-type').value, weekly_days:get('discovery-days').valueAsNumber || null,
        internship_months:get('discovery-months').valueAsNumber || null,
        graduation_year:get('discovery-graduation').valueAsNumber || null,
        available_from:get('discovery-available').value || null,
        terms:[...get('discovery-terms').querySelectorAll('input')].map(input => input.value.trim()).filter(Boolean),
      });
      showAgent(run.run_id);
      if (!desktop.matches) get('discovery-result').scrollIntoView({block:'start'});
    } catch (e) { error(e.message); }
    finally { button.disabled = false; }
  };

  async function continueAgent(answer) {
    try {
      await post('/api/discovery/agent/'+agentId+'/answer', {answer});
      get('agent-answer').hidden = true;
      pollAgent();
    } catch (e) { error(e.message); }
  }
  get('agent-answer').onsubmit = event => {
    event.preventDefault();
    continueAgent(get('agent-answer-degree').hidden ? get('agent-answer-value').value : get('agent-answer-degree').value);
  };
  get('agent-skip-answer').onclick = () => continueAgent('unknown');

  let restoringResume = false;
  try {
    const previousResume = JSON.parse(sessionStorage.getItem('discovery-resume-ref'));
    if (previousResume?.id) {
      restoringResume = true;
      loadResume(() => previousResume, {restoring:true});
    }
  } catch {
    sessionStorage.removeItem('discovery-resume-ref');
  }
  const recentAgent = sessionStorage.getItem('discovery-agent');
  if (recentAgent) showAgent(recentAgent);
  const recent = sessionStorage.getItem('discovery-task');
  if (recent) {
    api('/api/discovery/tasks/'+recent).then(result=>{
      if (taskId || agentId || recent !== sessionStorage.getItem('discovery-task')) return;
      taskId = recent;
      batch = result;
      openWorkspace();
      update();
      if (!restoringResume && result.resume) loadResume(() => result.resume, {restoring:true});
      if (!terminal(result.stage)) poll();
    }).catch(()=>sessionStorage.removeItem('discovery-task'));
  }
})();
