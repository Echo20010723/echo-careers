(() => {
  const get = id => document.getElementById(id);
  const api = async (path, options = {}) => {
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const missingOfferRoute = response.status === 404 && path.startsWith('/api/discovery/offerdao/search');
      const error = Error(data.error?.message || (missingOfferRoute
        ? '当前运行的后端没有 Offer岛接口，请重新启动工作台。'
        : '操作失败，请检查输入后重试。'));
      error.status = response.status;
      error.retryAfter = Number(response.headers.get('Retry-After')) || 0;
      throw error;
    }
    return data;
  };
  const post = (path, data) => api(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
  });
  const error = message => notice(message, true);
  const key = text => text.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
  const modeNames = {online:'在线搜索', demo:'脱敏演示', import:'本地 JSON 导入', paste:'粘贴岗位正文', source_page:'Offer岛本页分析'};
  const platformNames = {himalayas:'Himalayas', offerdao:'Offer岛', greenhouse:'Greenhouse', careerjet:'Careerjet 中国', boss:'旧 BOSS 批次'};
  const evaluationNames = {local_rules:'本地规则', local_fallback:'模型不可用 · 本地规则', model_with_rules:'模型提取 · 规则分档', model:'模型建议'};
  const gradeNames = ['夯', '顶级', '人上人', 'npc', '拉完了'];
  const stages = {queued:'排队中', fetching:'正在获取岗位', deduplicating:'正在去重', analyzing:'正在分析', complete:'已完成', partial:'部分岗位失败', failed:'任务失败'};
  const desktop = matchMedia('(min-width: 821px)');
  const terminal = stage => ['complete','partial','failed','interrupted'].includes(stage);
  let agentId = null, agentTimer = null, listScroll = 0;
  let reanalysis = null;
  let sourceRevision = 0;
  let resumeLoad = 0, resumeBusy = false, searchBusy = false, resumeVersion = '';
  let resumeId = null, resumeName = '', resumeKind = null, taskId = null, batch = null, selectedJob = null, pollTimer = null;

  let offerPage = null, offerAbort = null, offerRevision = 0, offerRetryAt = 0, offerReady;
  const applicationDrafts = new Map();
  let detailMotion, lastDetailSnapshot;
  let offerPreview = false, offerSupported;
  let manualOfferQuery = false, manualOfferType = false;
  const browsingOffers = () => offerPreview;
  const offerSource = () => get('discovery-mode').value === 'online' && get('discovery-platform').value === 'offerdao';

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

  const entryMotion = matchMedia('(prefers-reduced-motion: no-preference)');
  let intakeHandoff;

  function stopIntakeHandoff(restoreFocus = false) {
    const handoff = intakeHandoff;
    if (!handoff) return;
    intakeHandoff = null;
    cancelAnimationFrame(handoff.frame);
    for (const animation of handoff.animations) animation.cancel();
    handoff.layer.remove();
    handoff.discovery.classList.remove('intake-preparing');
    if (restoreFocus && !handoff.discovery.querySelector('.discovery-workspace').contains(document.activeElement)) {
      focusWorkspace(handoff.discovery);
    }
  }

  function focusWorkspace(discovery) {
    if (!discovery.classList.contains('active') || !discovery.classList.contains('workspace-open')) return;
    const title = !desktop.matches ? get('mobile-search-toggle') : get('discovery-settings-title');
    title.focus({preventScroll:true});
  }

  function pinIntakeSnapshot(node, rect) {
    Object.assign(node.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'});
  }

  function openWorkspace(focus = false, trigger) {
    const discovery = get('discovery');
    if (!focus || !trigger || !discovery.classList.contains('active') || discovery.classList.contains('workspace-open') ||
        !entryMotion.matches || !Element.prototype.animate) {
      showWorkspace(discovery,focus);
      return;
    }
    if (intakeHandoff) return;
    const rect = trigger.getBoundingClientRect();
    if (!rect.width) { showWorkspace(discovery,focus); return; }
    const flight = el('div','简历已就绪','intake-flight');
    pinIntakeSnapshot(flight, rect);
    const layer = el('div',undefined,'intake-overlay');
    layer.inert = true;
    layer.setAttribute('aria-hidden','true');
    const hero = trigger.closest('.landing-copy');
    let copy;
    if (hero) {
      copy = el('div',undefined,'landing-copy intake-copy');
      for (const child of hero.querySelectorAll('h1, .landing-intro')) {
        const clone = child.cloneNode(true);
        clone.removeAttribute('id');
        copy.append(clone);
      }
      pinIntakeSnapshot(copy, hero.getBoundingClientRect());
      layer.append(copy);
    }
    layer.append(flight);
    document.body.append(layer);

    const handoff = {layer,discovery,animations:[],frame:0};
    intakeHandoff = handoff;
    discovery.classList.add('intake-preparing');
    showWorkspace(discovery,false);
    window.scrollTo({top:0,behavior:'instant'});
    const destination = get('workspace-resume');
    const grid = discovery.querySelector('.discovery-workspace-grid');
    const duration = desktop.matches ? 460 : 410;
    const ease = 'cubic-bezier(.32,.02,.24,1)';
    handoff.frame = requestAnimationFrame(() => {
      handoff.frame = requestAnimationFrame(() => {
        if (intakeHandoff !== handoff) return;
        const target = destination.getBoundingClientRect();
        const x = target.left-rect.left;
        const y = target.top-rect.top;
        const sx = target.width/rect.width;
        const sy = target.height/rect.height;
        handoff.animations = [
          flight.animate([
            {transform:'none',opacity:1},
            {transform:'translate('+x*.70+'px,'+y*.70+'px) scale('+(.56+.32*sx)+')',opacity:1,offset:.62},
            {transform:'translate('+x+'px,'+y+'px) scale('+sx+','+sy+')',opacity:.3,offset:.92},
            {transform:'translate('+x+'px,'+y+'px) scale('+sx+','+sy+')',opacity:0},
          ],{duration,easing:ease,fill:'both'}),
          grid.animate([{opacity:0,transform:'translateY(10px)'},{opacity:1,transform:'none'}],
            {delay:Math.round(duration*.32),duration:Math.round(duration*.65),easing:'ease-out',fill:'both'}),
          destination.animate([{opacity:0},{opacity:1}],
            {delay:Math.round(duration*.77),duration:Math.round(duration*.23),fill:'both'}),
        ];
        if (copy) handoff.animations.push(copy.animate(
          [{opacity:1},{opacity:0,transform:'translateY(-24px)'}],
          {duration:Math.round(duration*.58),easing:'ease-out',fill:'both'},
        ));
        discovery.classList.remove('intake-preparing');
        Promise.allSettled(handoff.animations.map(animation=>animation.finished)).then(() => {
          if (intakeHandoff === handoff) stopIntakeHandoff(true);
        });
      });
    });
  }

  function showWorkspace(discovery,focus) {
    get('discovery-intake').classList.add('landing-motion-ready');
    discovery.classList.add('workspace-open');
    syncResultVisibility();
    if (focus) {
      focusWorkspace(discovery);
      window.scrollTo({top:0,behavior:'instant'});
    }
  }

  const activeResumeKey = 'discovery-resume-ref';
  const personalResumeKey = 'discovery-personal-resume';
  let verifiedPersonalResume = null;

  function readResumeRef(key) {
    try {
      const ref = JSON.parse(sessionStorage.getItem(key));
      if (typeof ref?.id === 'string' && typeof ref.name === 'string' && ref.id && ref.name) return ref;
    } catch {}
    return null;
  }

  function savedResumeKind(ref) {
    if (Object.hasOwn(ref, 'kind')) return ref.kind;
    // Migrate references saved before the example and personal flows had separate records.
    if (ref.id === 'demo-resume' || ref.name === '脱敏示例.md' || ref.name === '脱敏演示简历') return 'example';
    return ref.id === 'provided-resume' ? null : 'personal';
  }

  function syncLandingActions() {
    get('discovery-return').hidden = !verifiedPersonalResume;
  }

  function syncResultVisibility() {
    get('discovery-result').hidden = !(taskId || agentId || get('discovery').classList.contains('workspace-open'));
  }
  function layoutModeChanged() {
    syncResultVisibility();
    get('discovery-filter-options').open = desktop.matches;
  }
  desktop.addEventListener('change', layoutModeChanged);
  layoutModeChanged();
  const discoveryPanel = get('discovery');
  new MutationObserver(() => {
    if (!discoveryPanel.classList.contains('active')) stopIntakeHandoff();
  }).observe(discoveryPanel,{attributes:true,attributeFilter:['class']});
  entryMotion.addEventListener('change', () => {
    if (!entryMotion.matches) stopIntakeHandoff(true);
  });
  function expandSearch(open) {
    discoveryPanel.classList.toggle('search-collapsed', !open);
    get('mobile-search-toggle').setAttribute('aria-expanded', String(open));
  }
  get('mobile-search-toggle').onclick = () => expandSearch(discoveryPanel.classList.contains('search-collapsed'));
  get('mobile-list-back').onclick = () => {
    if (!get('discovery-resume-panel').hidden) toggleResumePanel(false, false);
    discoveryPanel.classList.remove('mobile-reading');
    const active = get('discovery-list').querySelector('[aria-pressed=true]');
    active?.focus({preventScroll:true});
    requestAnimationFrame(() => window.scrollTo(0, listScroll));
  };
  get('discovery-use-example').onclick = event =>
    loadResume(() => post('/api/discovery/example', {}), {kind:'example', trigger:event.currentTarget});
  const uploadButton = get('discovery-open-upload');
  const fileInput = get('discovery-resume');
  let pickingFromHero = false;
  uploadButton.onclick = () => {
    if (resumeBusy) return;
    pickingFromHero = true;
    fileInput.value = '';
    fileInput.click();
  };
  fileInput.addEventListener('cancel', () => { pickingFromHero = false; });
  fileInput.addEventListener('change', () => {
    if (!pickingFromHero) return;
    pickingFromHero = false;
    const file = fileInput.files[0];
    if (file) loadResume(() => uploadResumeFile(file), {kind:'personal', trigger:uploadButton});
  });
  get('discovery-show-example').onclick = event => {
    event.preventDefault();
    const heading = get('sample-title');
    heading.focus({preventScroll:true});
    heading.scrollIntoView({block:'start', behavior:entryMotion.matches ? 'smooth' : 'instant'});
  };
  get('landing-title').addEventListener('animationend', event => {
    if (event.target.matches('.title-tail, .title-tail > :last-child')) {
      get('discovery-intake').classList.add('landing-motion-ready');
    }
  });

  const dropZone = get('resume-drop-zone');
  dropZone.addEventListener('dragover', event => {
    if (!event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = resumeBusy ? 'none' : 'copy';
    if (!resumeBusy) dropZone.classList.add('is-dragover');
  });
  dropZone.addEventListener('dragleave', event => {
    if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove('is-dragover');
  });
  dropZone.addEventListener('drop', event => {
    event.preventDefault();
    dropZone.classList.remove('is-dragover');
    if (resumeBusy) return;
    if (event.dataTransfer.files.length !== 1) {
      const status = get('manual-resume-profile');
      status.classList.add('error');
      status.textContent = '请一次拖放一份简历。';
      return;
    }
    fileInput.files = event.dataTransfer.files;
    fileInput.focus({preventScroll:true});
  });

  api('/health').then(info => {
    if (info.public) {
      document.body.classList.add('public-workbench');
      document.body.dataset.publicModel = info.model_mode;
      get('mode-badge').textContent = info.model_mode === 'mock' ? '公开演示' : '公开体验';
    } else {
      get('mode-badge').textContent = info.model_mode === 'mock' ? '本机演示' : '本机工作台';
    }
    const banner = get('deployment-banner');
    banner.hidden = false;
    const onlineOption = get('agent-source').querySelector('option[value="online"]');
    if (info.agent_provider) {
      onlineOption.textContent = info.agent_provider === 'cloudflare' ? 'Himalayas 在线岗位 · Cloudflare Agent' : 'Himalayas 在线岗位 · 模型决策';
      get('agent-source').value = 'online';
    } else {
      onlineOption.textContent = 'Himalayas 在线岗位 · 规则决策';
    }
    const mode = info.agent_provider === 'cloudflare' ? 'Cloudflare Workers AI · 在线岗位 Agent 已配置'
      : info.model_mode === 'mock' ? '演示模式 · 无需模型密钥' : (info.model_configured === false || info.simulated_model)
      ? '模型未配置 · 服务端需设置 OPENAI_API_KEY，在线 Agent 会使用规则降级' : '真实模型模式 · 岗位 Agent 有独立预算';
    banner.textContent = mode + (info.public ? ' ｜公开体验 · 访客数据独立，重启或休眠后可能清空。投递由你决定。' : '');
  }).catch(() => {});

  get('discovery-back').onclick = () => {
    stopIntakeHandoff();
    discoveryPanel.classList.remove('workspace-open','mobile-reading');
    syncLandingActions();
    syncResultVisibility();
    (verifiedPersonalResume ? get('discovery-return') : get('discovery-open-upload')).focus({preventScroll:true});
    window.scrollTo({top:0,behavior:'instant'});
  };
  document.querySelector('.brand').addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!discoveryPanel.classList.contains('workspace-open')) return;
    event.preventDefault();
    tab('discovery');
    get('discovery-back').click();
  });
  get('discovery-return').onclick = async event => {
    if (resumeBusy || !verifiedPersonalResume) return;
    const button = event.currentTarget;
    const restored = await loadResume(() => verifiedPersonalResume, {restoring:true, kind:'personal', trigger:button});
    if (restored) openWorkspace(true, button);
    else get('discovery-open-upload').focus({preventScroll:true});
  };
  get('discovery-browse').onclick = () => {
    tab('discovery');
    get('discovery-settings').hidden = false;
    get('discovery-platform').value = 'offerdao';
    setMode('online');
    openWorkspace();
    expandSearch(true);
    get('offerdao-query').focus();
  };

  function evidenceItem(item) {
    return el('li', '第 '+item.line+' 行 · '+item.quote);
  }

  async function loadResume(task, {restoring = false, inline = false, kind = null, trigger, statusId} = {}) {
    if (resumeBusy) return false;
    const revision = ++resumeLoad;
    resumeBusy = true;
    trigger?.setAttribute('aria-busy','true');
    const profile = get(statusId || (kind === 'example' ? 'example-profile' : 'discovery-profile'));
    profile.classList.remove('error');
    profile.textContent = restoring ? (trigger ? '正在恢复简历…' : '') : '正在解析简历并生成职位词…';
    const buttons = document.querySelectorAll('#discovery-resume-form button, #discovery-paste-form button, #discovery-resume-panel button, #discovery-use-example, #discovery-return, #discovery-open-upload');
    let resume;
    const submit = get('discovery-task-form').querySelector('[type=submit]');
    buttons.forEach(button => button.disabled = true);
    submit.disabled = true;
    try {
      resume = await task();
      const result = await api('/api/discovery/resumes/'+encodeURIComponent(resume.id)+'/terms');
      if (revision !== resumeLoad) return false;
      if (result.resume_id !== resume.id || !result.resume_version) throw Error('无法确认简历版本，请重试。');
      const keepRanking = inline && Boolean(batch?.resume);
      const crossingExample = !keepRanking && resumeId && resumeId !== resume.id &&
        (resumeKind === 'example') !== (kind === 'example');
      const changed = resumeId !== resume.id || resumeVersion !== result.resume_version;
      resumeId = resume.id;
      resumeVersion = result.resume_version;
      resumeName = resume.name;
      resumeKind = kind;
      if (changed) applicationDrafts.clear();
      if (crossingExample) {
        invalidateOffers();
        clearTimeout(pollTimer);
        clearTimeout(agentTimer);
        taskId = agentId = null;
        batch = null;
        sessionStorage.removeItem('discovery-task');
        sessionStorage.removeItem('discovery-agent');
        showOfferPanel(false);
        get('agent-trace').hidden = true;
        get('discovery-attribution').hidden = true;
        resetResultPane();
        get('discovery-round').hidden = true;
        get('discovery-status').textContent = '请选择搜索条件后开始搜索。';
      }
      if (changed && !restoring && !keepRanking) {
        sessionStorage.removeItem(searchDraftKey());
        const currentPage = offerSource() ? offerPage : null;
        invalidateOffers();
        if (currentPage) {
          offerPage = currentPage;
          renderOfferPage(currentPage, 'complete');
          rankOfferPage(currentPage);
        } else {
          const query = get('offerdao-query');
          const type = get('offerdao-type');
          const values = [query.value,type.value];
          get('discovery-task-form').reset();
          if (manualOfferQuery) query.value = values[0];
          if (manualOfferType) type.value = values[1];
          setMode('online');
        }
      }
      if (!keepRanking) {
        get('discovery-terms').replaceChildren();
        get('discovery-add').disabled = false;
      }
      const saved = restoring ? readSearchDraft() : null;
      if (!keepRanking) {
        (saved?.terms || result.suggestions.map(item=>item.term)).forEach(addTerm);
        if (!result.suggestions.length) addTerm();
      }
      if (saved) {
        manualOfferQuery = saved.manualOfferQuery ?? Boolean(saved.fields['offerdao-query']);
        manualOfferType = saved.manualOfferType ?? Boolean(saved.fields['offerdao-type']);
        setMode(['import','paste'].includes(saved.mode) ? 'online' : saved.mode);
        for (const [id,value] of Object.entries(saved.fields)) {
          if (draftFields.includes(id)) get(id).value = value;
        }
      }
      const roles = get('occupation-directions');
      roles.replaceChildren(...result.suggestions.map(item=>el('li', [item.kind, item.term,
        '第 '+item.line+' 行 · '+item.quote, ...(item.needs || []),
        ...(item.references || []).map(ref=>'模板 '+ref.id+' '+ref.title)].filter(Boolean).join(' · '))));
      get('occupation-suggestions').hidden = !roles.childElementCount;
      get('discovery-settings').hidden = false;
      profile.textContent = '';
      get('workspace-analysis-mode').textContent = evaluationNames[result.mode] || '本地规则';
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
      const ref = {id:resume.id,name:resume.name,kind};
      sessionStorage.setItem(activeResumeKey, JSON.stringify(ref));
      if (kind === 'personal') {
        verifiedPersonalResume = ref;
        sessionStorage.setItem(personalResumeKey, JSON.stringify(ref));
      }
      setMode(get('discovery-mode').value);
      if (keepRanking) renderJobs();
      updateResumeStatus();
      if (!restoring) openWorkspace(!inline, trigger);
      syncLandingActions();
      return true;
    } catch (e) {
      if (revision !== resumeLoad) return false;
      profile.textContent = '';
      if (inline) {
        const message = get('discovery-inline-error');
        message.textContent = e.message;
        message.hidden = false;
      } else if (!restoring) {
        profile.classList.add('error');
        profile.textContent = e.message;
      } else {
        const expired = [404, 410, 422].includes(e.status);
        if (expired && readResumeRef(activeResumeKey)?.id === resume?.id) sessionStorage.removeItem(activeResumeKey);
        if (kind === 'personal') {
          verifiedPersonalResume = null;
          if (expired && readResumeRef(personalResumeKey)?.id === resume?.id) sessionStorage.removeItem(personalResumeKey);
          syncLandingActions();
        }
        if (trigger) {
          profile.classList.add('error');
          profile.textContent = expired ? '上次简历已失效，请重新上传。' : '暂时无法恢复简历，可以重新上传或刷新后再试。';
        }
      }
      return false;
    } finally {
      trigger?.removeAttribute('aria-busy');
      if (revision === resumeLoad) {
        resumeBusy = false;
        buttons.forEach(button => button.disabled = false);
        submit.disabled = searchBusy;
      }
    }
  }

  get('upload-form').addEventListener('resume-uploaded', event => {
    loadResume(() => event.detail, {restoring:true, kind:'personal'});
  });

  get('discovery-resume-form').onsubmit = event => {
    event.preventDefault();
    const file = get('discovery-resume').files[0];
    if (!file) return;
    loadResume(() => uploadResumeFile(file),{kind:'personal', trigger:event.submitter, statusId:'manual-resume-profile'});
  };
  get('discovery-paste-form').onsubmit = event => {
    event.preventDefault();
    loadResume(() => post('/api/discovery/resume-text',{text:get('discovery-resume-text').value}),{kind:'personal', trigger:event.submitter, statusId:'manual-resume-profile'});
  };
  function uploadResumeFile(file, endpoint = '/api/resumes') {
    const form = new FormData();
    form.append('file', file);
    return api(endpoint, {method:'POST', body:form});
  }

  function setResumeFilename(details, name) {
    const summary = details.querySelector('summary');
    if (summary.textContent === name) return;
    details.open = false;
    summary.textContent = name;
    summary.title = name;
    details.querySelector('p').textContent = name;
  }

  function updateResumeStatus() {
    const status = get('workspace-resume');
    setResumeFilename(get('workspace-resume-file'), resumeName);
    status.hidden = !resumeName;
    const ranked = batch?.resume;
    const hasRanking = Boolean(ranked);
    const updated = Boolean(resumeId && hasRanking && (ranked.id !== resumeId || ranked.version !== resumeVersion));
    get('discovery-attach-resume').textContent = resumeId ? '更换简历' : '上传简历';
    get('discovery-resume-panel-title').textContent = resumeId ? '更换简历' : '上传简历';
    get('discovery-resume-name').textContent = resumeName ? '当前文件 · '+resumeName : '尚未上传简历';
    get('discovery-reading-tools').hidden = !hasRanking;
    get('discovery-resume-update').hidden = !hasRanking;
    get('discovery-resume-status').textContent = updated ? '简历已更新，当前结果仍基于上一份简历' : '';
    const ranking = get('discovery-ranking-resume');
    ranking.hidden = !hasRanking;
    setResumeFilename(ranking, hasRanking ? '本轮使用 · '+ranked.name : '');
    const reanalyze = get('discovery-reanalyze');
    const running = reanalysis?.id === taskId;
    reanalyze.hidden = !(updated && batch.jobs.length && terminal(batch.stage));
    reanalyze.disabled = running;
    reanalyze.textContent = running ? '正在用新简历分析…' : agentId ? '用新简历重跑 Agent' : '用新简历重新分析';
  }

  function toggleResumePanel(open, returnFocus = true) {
    const panel = get('discovery-resume-panel');
    const trigger = get('discovery-attach-resume');
    panel.hidden = !open;
    get('discovery-result').classList.toggle('resume-panel-open', open);
    trigger.setAttribute('aria-expanded', String(open));
    if (open) {
      if (!desktop.matches) panel.scrollIntoView({block:'nearest'});
      get('discovery-inline-resume').focus({preventScroll:true});
    } else {
      panel.reset();
      get('discovery-inline-error').hidden = true;
      if (returnFocus) trigger.focus();
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
      if (await loadResume(() => uploadResumeFile(file, '/api/discovery/resumes'), {inline:true, kind:'personal'})) {
        trigger.disabled = false;
        toggleResumePanel(false);
      }
    } finally { trigger.disabled = false; }
  };
  get('discovery-reanalyze').onclick = async event => {
    if (resumeBusy || searchBusy || reanalysis?.id === taskId) return;
    const button = event.currentTarget;
    if (agentId) { get('agent-form').requestSubmit(); return; }
    const sourceTask = taskId;
    button.disabled = true;
    try {
      const task = await post('/api/discovery/tasks/'+sourceTask+'/reanalyze', {resume_id:resumeId});
      if (taskId === sourceTask) {
        clearTimeout(pollTimer);
        taskId = task.id;
        reanalysis = {id:task.id, previousTask:sourceTask};
        updateResumeStatus();
        get('discovery-status').textContent = '正在用新简历分析，暂时显示上轮结果。';
        poll();
      }
    } catch (e) { error(e.message); }
    finally { button.disabled = reanalysis?.id === taskId; }
  };

  const draftFields = ['discovery-platform','offerdao-query','offerdao-city','offerdao-type','discovery-job-type','discovery-country','discovery-city','discovery-limit',
    'discovery-months','discovery-available','discovery-days','discovery-graduation','discovery-degree'];
  const searchDraftKey = () => resumeKind === 'example' ? 'discovery-example-search-draft' : 'discovery-search-draft';
  function readSearchDraft() {
    try {
      const draft = JSON.parse(sessionStorage.getItem(searchDraftKey()));
      return draft?.id === resumeId && draft.version === resumeVersion ? draft : null;
    } catch { return null; }
  }
  function saveSearchDraft() {
    if (!resumeId || resumeBusy) return;
    sessionStorage.setItem(searchDraftKey(), JSON.stringify({
      id:resumeId, version:resumeVersion, mode:get('discovery-mode').value,
      manualOfferQuery, manualOfferType,
      terms:[...get('discovery-terms').querySelectorAll('input')].map(input=>input.value),
      fields:Object.fromEntries(draftFields.map(id=>[id,get(id).value])),
    }));
  }
  get('discovery-task-form').addEventListener('input', saveSearchDraft);
  get('discovery-add').onclick = () => { addTerm(); saveSearchDraft(); };
  function recommendOfferQuery() {
    const input = get('offerdao-query');
    const type = get('offerdao-type');
    if (input.value || manualOfferQuery) return;
    const term = get('discovery-terms').querySelector('input')?.value.trim();
    if (!term) return;
    if (/(?:实习|\bintern(?:ship)?\b)/i.test(term) && !type.value && !manualOfferType) type.value = '实习';
    if (/\bagents?\b|智能体/i.test(term)) input.value = 'Agent';
    else if (/大模型|\bllms?\b|生成式\s*AI/i.test(term)) input.value = '大模型';
    else input.value = term.replace(/(?:实习生?|intern(?:ship)?)$/i,'').trim();
  }

  function setMode(mode) {
    if (get('discovery-mode').value !== mode) sourceRevision++;
    const online = mode === 'online';
    get('discovery-mode').value = mode;
    const offer = offerSource();
    if (!offer && (browsingOffers() || offerPage)) {
      invalidateOffers();
      showOfferPanel(false);
      batch = null;
      get('discovery-status').textContent = '请选择搜索条件后开始搜索。';
      get('discovery-attribution').hidden = true;
    }
    get('discovery-platform-field').hidden = !online;
    get('discovery-source-static').hidden = online;
    get('offerdao-fields').hidden = !offer;
    get('offerdao-query').required = offer;
    get('discovery-remote-terms').hidden = offer;
    if (offer) recommendOfferQuery();
    get('discovery-file-field').hidden = mode !== 'import';
    get('discovery-job-file').required = mode === 'import';
    get('discovery-jd-field').hidden = mode !== 'paste';
    get('discovery-jd').required = mode === 'paste';
    get('discovery-limit').disabled = mode === 'paste' || offer;
    get('discovery-limit-field').hidden = offer;
    get('discovery-online').hidden = online;
    get('discovery-source-label').textContent = online ? 'Himalayas · 远程' : mode === 'import' ? '本地文件' : mode === 'paste' ? '手动粘贴' : '脱敏演示';
    get('discovery-city-field').hidden = online;
    get('discovery-country-field').hidden = !online || offer;
    get('discovery-type-field').hidden = !online || offer;
    get('discovery-submit').textContent = {
      online:resumeId ? '搜索并排名' : '搜索岗位', import:'导入并排名', paste:'分析岗位正文', demo:'运行脱敏演示',
    }[mode];
    get('discovery-mode-note').textContent = offer
      ? (offerSupported === false ? '当前运行的后端缺少 Offer岛接口，请重启工作台。'
        : offerReady === false ? 'Offer岛尚未配置个人 API Key，可切换来源或粘贴 JD。'
        : resumeId ? '搜索已审核岗位，并自动匹配本页岗位。' : '可先浏览岗位，添加简历后自动匹配本页。')
      : online
      ? '搜索 Himalayas 公开远程岗位。国家/地区用于筛选允许远程工作的范围。'
      : mode === 'import' ? '读取你选择的本地文件，按职位词筛选。'
      : mode === 'paste' ? '使用当前简历和粘贴的正文分析单个岗位。' : '使用内置虚构岗位。';
    for (const id of ['discovery-platform', 'offerdao-type', 'discovery-job-type']) {
      get(id).dispatchEvent(new Event('picker-sync'));
    }
  }
  function showOfferPanel(show) {
    offerPreview = show;
    if (!show) get('offerdao-pagination').hidden = true;
    get('offerdao-return').hidden = show || !offerPage;
    if (show) {
      get('discovery-result').hidden = false;
      discoveryPanel.classList.remove('mobile-reading');
    }
  }

  function renderOfferPage(page, stage, message) {
    showOfferPanel(true);
    get('agent-trace').hidden = true;
    selectedJob = null;
    for (const name of ['filter-grade', 'filter-city', 'filter-term']) get(name).value = '';
    get('filter-unknown').checked = false;
    const jobs = (page?.jobs || []).map(job => ({...job, local_job_id:job.source_id, matched_terms:[]}));
    batch = {
      platform:'offerdao', mode:'online', stage, jobs, terms:[], counts:{},
      message:message || (jobs.length ? '来源共 '+page.total+' 张卡片 · 本页 '+jobs.length+' 个岗位'
        : '本页无符合条件的岗位。可缩短关键词或放宽条件后重新搜索。'),
    };
    updateOfferPagination(page);
    get('discovery-list').scrollTop = 0;
    update();
  }

  function updateOfferPagination(page) {
    get('offerdao-prev').disabled = !page || page.offset === 0;
    get('offerdao-next').disabled = !page || page.offset + page.limit >= page.total;
    get('offerdao-pagination').hidden = get('offerdao-prev').disabled && get('offerdao-next').disabled;
  }

  function invalidateOffers() {
    offerRevision++;
    offerAbort?.abort();
    offerAbort = null;
    if (offerPreview || offerPage || batch?.platform === 'offerdao') {
      clearTimeout(pollTimer);
      taskId = null;
      sessionStorage.removeItem('discovery-task');
      renderOfferPage(null, 'idle', '请按当前条件重新搜索。');
    }
    offerPage = null;
    get('offerdao-return').hidden = true;
  }

  function offerLink(url, text) {
    if (!url || !/^https?:\/\//i.test(url)) return null;
    const link = el('a', text);
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  }

  function availability() {
    return {
      internship_months:get('discovery-months').valueAsNumber || null,
      available_from:get('discovery-available').value || null,
      weekly_days:get('discovery-days').valueAsNumber || null,
    };
  }

  async function rankOfferPage(page) {
    if (!resumeId || !page.jobs.length) return;
    const revision = offerRevision;
    const currentResume = resumeId;
    get('discovery-status').textContent = batch.message+' · 正在提交本页分析…';
    try {
      const q = get('offerdao-query').value;
      const rankingTerm = q.trim();
      const task = await post('/api/discovery/tasks', {
        resume_id:currentResume, platform:'offerdao', mode:'source_page',
        terms:rankingTerm.length >= 2 && rankingTerm.length <= 48 && !/[。！？；;]/.test(rankingTerm) ? [rankingTerm] : [],
        source_query:q, source_employment_type:get('offerdao-type').value || null,
        jobs:page.jobs.slice(0,100), source_total:page.total, source_offset:page.offset, source_limit:page.limit,
        job_type:get('offerdao-type').value === '实习' ? 'internship' : 'any',
        city:get('offerdao-city').value.trim() || null,
        ...availability(),
        degree:get('discovery-degree').value || null,
        graduation_year:get('discovery-graduation').valueAsNumber || null,
      });
      if (revision === offerRevision && currentResume === resumeId && offerSource()) {
        watch(task.id, false, true, true);
      }
    } catch (problem) {
      if (revision === offerRevision) get('discovery-status').textContent = batch.message+' · '+problem.message;
    }
  }

  async function searchOffers(offset = 0) {
    const q = get('offerdao-query').value;
    if (!q.trim()) return;
    clearTimeout(pollTimer);
    clearTimeout(agentTimer);
    taskId = agentId = null;
    sessionStorage.removeItem('discovery-task');
    sessionStorage.removeItem('discovery-agent');
    if (offerSupported === false || offerReady === false || Date.now() < offerRetryAt) {
      const message = offerSupported === false ? '当前运行的后端没有 Offer岛接口，请重新启动工作台。'
        : offerReady === false ? 'Offer岛尚未配置个人 API Key（OFFERDAO_API_KEY）。可切换来源或粘贴 JD。'
        : 'Offer岛查询受限，请在 '+Math.ceil((offerRetryAt-Date.now())/1000)+' 秒后手动重试。';
      renderOfferPage(null, 'failed', message);
      return;
    }
    const limit = offset ? offerPage?.limit : 12;
    if (!limit) return;
    offerAbort?.abort();
    const controller = new AbortController();
    offerAbort = controller;
    const revision = ++offerRevision;
    const params = new URLSearchParams({q, limit, offset});
    saveSearchDraft();
    const location = get('offerdao-city').value.trim();
    const employmentType = get('offerdao-type').value;
    if (location) params.set('location', location);
    if (employmentType) params.set('employment_type', employmentType);
    renderOfferPage(null, 'fetching', '正在搜索岗位…');
    try {
      const page = await api('/api/discovery/offerdao/search?'+params, {signal:controller.signal});
      if (revision !== offerRevision) return;
      offerPage = page;
      renderOfferPage(page, 'complete');
      rankOfferPage(page);
      if (!offset && !desktop.matches) {
        expandSearch(false);
        get('discovery-result').scrollIntoView({block:'start'});
      }
    } catch (problem) {
      if (revision !== offerRevision) return;
      offerPage = null;
      if (problem.status === 429) offerRetryAt = Date.now()+1000*problem.retryAfter;
      renderOfferPage(null, 'failed', problem.message+' 可切换来源或粘贴 JD。');
    } finally {
      if (revision === offerRevision) offerAbort = null;
    }
  }

  get('offerdao-prev').onclick = () => { if (offerPage && !offerAbort) searchOffers(Math.max(0,offerPage.offset-offerPage.limit)); };
  get('offerdao-next').onclick = () => { if (offerPage && !offerAbort) searchOffers(offerPage.offset+offerPage.limit); };
  get('offerdao-return').onclick = () => {
    if (!offerPage) return;
    clearTimeout(pollTimer);
    taskId = null;
    renderOfferPage(offerPage, 'complete');
  };
  get('discovery-platform').onchange = () => {
    sourceRevision++;
    invalidateOffers();
    clearTimeout(pollTimer);
    clearTimeout(agentTimer);
    taskId = agentId = null;
    sessionStorage.removeItem('discovery-task');
    sessionStorage.removeItem('discovery-agent');
    setMode('online');
    if (offerSource()) renderOfferPage(null,'idle','请按当前条件搜索。');
    saveSearchDraft();
  };
  get('offerdao-query').addEventListener('input', () => { manualOfferQuery = true; });
  get('offerdao-type').addEventListener('input', () => { manualOfferType = true; });
  for (const id of ['offerdao-query','offerdao-city','offerdao-type','discovery-degree',
    'discovery-months','discovery-available','discovery-days','discovery-graduation']) {
    get(id).addEventListener('input', () => { if (offerSource()) invalidateOffers(); });
  }
  api('/api/discovery/sources').then(data => {
    const source = data.sources.find(item=>item.id==='offerdao');
    offerSupported = Boolean(source);
    offerReady = source?.ready;
    if (offerSource()) setMode('online');
  }).catch(()=>{});

  get('discovery-import').onclick = () => setMode('import');
  get('discovery-paste-jd').onclick = () => {
    setMode('paste');
    get('discovery-jd').focus();
  };
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

  function showDetail(job, animate = false) {
    const root = get('discovery-detail');
    root.hidden = false;
    const snapshot = JSON.stringify([resumeId,resumeVersion,taskId,batch?.stage,job]);
    if (snapshot === lastDetailSnapshot) return;
    lastDetailSnapshot = snapshot;
    const detail = el('div',undefined,'detail-evidence');
    const heading = el('div',undefined,'detail-heading');
    heading.append(el('h3',job.title || '未提供岗位名'));
    const analysis = job.analysis;
    const origin = platformNames[job.source];
    const metadata = el('div', undefined, 'discovery-job-meta muted');
    for (const value of [job.company, job.team, job.city, job.employment_type, job.salary_raw, job.published_at && '发布于 '+job.published_at, origin && '来源 · '+origin,
      job.detail_status === 'detail' ? '已读取详情' : '仅列表 · 完整 JD 待核对'].filter(Boolean)) {
      metadata.append(el('span', value));
    }
    heading.append(metadata);
    let application;
    if (['offerdao','himalayas'].includes(job.source)) {
      const draftKey = JSON.stringify([resumeId,resumeVersion,job.source,job.source_id || job.url,job.title,job.jd,job.jd_summary,job.contact_emails,job.application_url,job.url,job.original_source_url]);
      if (!applicationDrafts.has(draftKey)) {
        if (applicationDrafts.size >= 12) {
          const unused = [...applicationDrafts].find(([, panel]) =>
            !panel.querySelector('.discovery-mail-editor')?.childElementCount && !panel.dataset.statementOpen && !panel.querySelector('button:disabled'));
          if (unused) applicationDrafts.delete(unused[0]);
        }
        applicationDrafts.set(draftKey,applicationPanel(job));
      }
      application = applicationDrafts.get(draftKey);
    }
    const openJD = root.dataset.jobKey === jobKey(job) && root.querySelector('.job-description')?.open;
    root.dataset.jobKey = jobKey(job);
    if (application && application.parentElement === root) {
      for (const child of [...root.children]) if (child !== application) child.remove();
      application.before(heading);
      application.after(detail);
    } else {
      root.replaceChildren(heading,...(application ? [application] : []),detail);
    }
    if (animate && desktop.matches && matchMedia('(prefers-reduced-motion: no-preference)').matches) {
      detailMotion?.cancel();
      detailMotion = root.animate([{opacity:.68,transform:'translateY(3px)'},{opacity:1,transform:'none'}],
        {duration:150,easing:'ease-out'});
    }
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
    if (!analysis) {
      if (job.source === 'offerdao') {
        detail.append(el('p',job.error || (resumeId ? '待匹配 · 可先浏览原文' : '添加简历后可对本页岗位排名。')));
        if (!resumeId) {
          const add = el('button','添加简历后排名','secondary');
          add.type = 'button';
          add.onclick = () => toggleResumePanel(true);
          detail.append(add);
        }
        appendJobSource(detail, job, openJD);
      } else {
        detail.append(el('p',job.error || '等待分析'));
      }
      return;
    }
    const strip = el('div',undefined,'match-strip');
    strip.append(el('span','本轮档位 '+analysis.grade+' · 证据分 '+analysis.match_score+'/100',
      'grade grade-'+analysis.rank));
    const unknown = analysis.conditions.filter(item => item.status === '待确认').length;
    const conflicts = analysis.conditions.filter(item => item.status === '冲突').length;
    if (unknown || conflicts) strip.append(el('span',[unknown && '待核对 '+unknown+' 项',
      conflicts && '冲突 '+conflicts+' 项'].filter(Boolean).join(' · '),'condition-count'));
    detail.append(strip,el('p',analysis.reason),el('p',evaluationNames[analysis.evaluation_mode],'muted'));
    const groups = [
      ['硬条件核对', analysis.conditions.map(item=>item.name+' · '+item.status+(item.evidence?' · '+item.evidence:''))],
      ['最相关简历证据', analysis.evidence.map(item=>'第 '+item.line+' 行 · '+item.quote)],
      ['关键缺口', analysis.gaps],
      ['岗位核心职责', analysis.duties],
      ['必需技能与要求', analysis.required],
      ['加分技能', analysis.bonus],
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
    if (batch?.mode === 'source_page' && terminal(batch.stage) && taskId && batch.resume?.id === resumeId) {
      const rerun = el('button','重跑此岗位','secondary');
      rerun.type = 'button';
      rerun.onclick = async () => {
        const current = taskId;
        const revision = offerRevision;
        rerun.disabled = true;
        try {
          await post('/api/discovery/tasks/'+current+'/retry?job_id='+encodeURIComponent(job.local_job_id),{});
          if (taskId === current && revision === offerRevision) watch(current,false,true,true);
        } catch (problem) { error(problem.message); }
        finally { rerun.disabled = false; }
      };
      detail.append(rerun);
    }
    detail.append(draftPanel(job));
    appendJobSource(detail, job, openJD);
  }

  function appendJobSource(detail, job, open = false) {
    const jd = el('details',undefined,'job-description');
    jd.open = Boolean(open);
    jd.append(el('summary',job.jd?'查看 JD 原文':'查看岗位摘要'),el('pre',job.jd || job.jd_summary || '未提供岗位描述'));
    detail.append(jd);
    if (job.source === 'offerdao') return;
    const url = job.url || job.source_page_url;
    if (url && /^https?:\/\//i.test(url)) {
      const link = el('a',job.url ? '打开岗位原页' : '打开来源列表页');
      link.href=url; link.target='_blank'; link.rel='noopener noreferrer';
      detail.append(link);
    }
    if (job.source !== 'himalayas' && job.application_url && job.application_url !== url && /^https?:\/\//i.test(job.application_url)) {
      const apply = el('a','打开官方申请入口','button-link secondary');
      apply.href = job.application_url; apply.target = '_blank'; apply.rel = 'noopener noreferrer';
      detail.append(' ',apply);
    }
  }

  const draftReasons = {
    not_configured:'Workers AI 尚未配置，可编辑基础草稿。',
    no_evidence:'缺少匹配证据，可补充简历或手动修改。',
    question_needs_input:'这个问题需要你提供具体或已确认的信息，可手动补充。',
    app_daily_limit:'本站今日生成次数已达上限，可继续编辑，或在 UTC 零点后重新生成。',
    app_hourly_limit:'当前会话的小时上限已到，可继续编辑，稍后再重新生成。',
    upstream_quota:'Cloudflare 免费额度已用完，可继续编辑，或等 Cloudflare 额度重置。',
    upstream_limit:'Cloudflare 暂时限流，请稍后再重新生成。',
    busy:'生成服务正忙，可继续编辑，稍后再试。',
    timeout:'Workers AI 响应超时，可使用基础草稿或重新生成。',
    provider_error:'Workers AI 暂不可用，可继续编辑基础草稿。',
    invalid_output:'模型正文或证据引用未通过校验，可继续编辑基础草稿。',
  };

  function applicationAnchor(job) {
    try {
      const target = new URL(job.application_url);
      if (!['https:','http:'].includes(target.protocol) || target.username || target.password) return null;
      const link = offerLink(job.application_url,'前往申请');
      link.className = 'button-link';
      return link;
    } catch { return null; }
  }

  function webApplicationPanel(job) {
    const panel = el('section',undefined,'discovery-application');
    const actions = el('div',undefined,'actions');
    const direct = applicationAnchor(job);
    if (direct) actions.append(direct);
    else {
      const original = offerLink(job.url,'查看原帖');
      if (original) actions.append(original);
    }
    const toggle = el('button','生成申请陈述','secondary');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded','false');
    actions.append(toggle);

    const form = el('form',undefined,'discovery-statement');
    form.hidden = true;
    const language = el('select');
    for (const [value,name] of [['en','English'],['zh','中文']]) {
      const option = el('option',name);
      option.value = value;
      language.append(option);
    }
    const question = el('textarea');
    question.rows = 2;
    question.maxLength = 800;
    question.placeholder = '例如：Why are you a good fit for this role?';
    const limit = el('input');
    limit.type = 'number';
    limit.min = '1';
    limit.max = '500';
    limit.step = '1';
    const label = (name,input) => {
      const wrapper = el('label',name);
      wrapper.append(input);
      return wrapper;
    };
    const limitCaption = el('span');
    const limitLabel = label('',limit);
    limitLabel.prepend(limitCaption);
    const status = el('p',undefined,'muted');
    status.setAttribute('role','status');
    const output = el('div',undefined,'discovery-mail-editor');
    const body = el('textarea');
    body.rows = 8;
    const stats = el('p',undefined,'muted');
    const basis = el('p',undefined,'muted');
    const stateLabel = el('p',undefined,'muted');
    const submit = el('button','生成陈述');
    submit.type = 'submit';
    const copy = el('button','复制陈述','secondary');
    const apply = el('button','采用新草稿','secondary');
    copy.type = apply.type = 'button';
    const controls = el('div',undefined,'actions');
    controls.append(copy,apply);
    output.append(stateLabel,label('申请陈述',body),stats,basis,controls);
    form.append(label('语言',language),label('招聘方问题（选填）',question),limitLabel,submit,status,output);
    panel.append(el('h4','投递方式'),actions,form);
    const slots = new Map();
    const inputsByLanguage = new Map();
    let currentLanguage = language.value;

    const options = () => ({
      language:language.value, question:question.value.trim(), limit:limit.valueAsNumber || null,
    });
    const key = () => JSON.stringify(options());
    const count = value => language.value === 'zh' ? Array.from(value.replace(/\s/g,'')).length
      : value.trim() ? value.trim().split(/\s+/).length : 0;
    function render() {
      const slot = slots.get(key());
      const available = Boolean(slot?.draft);
      output.hidden = !available;
      submit.disabled = Boolean(slot?.pending);
      submit.textContent = available && !slot.pending ? '重新生成' : '生成陈述';
      limitCaption.textContent = language.value === 'zh' ? '最多字符数（选填）' : '最多英文词数（选填）';
      status.textContent = slot?.status || '';
      if (!available) return;
      if (body.value !== slot.body) body.value = slot.body;
      stateLabel.textContent = slot.edited ? '已编辑' : slot.draft.mode;
      const size = count(slot.body);
      const max = options().limit;
      stats.textContent = size+(language.value === 'zh' ? ' 字符' : ' 词')+
        (max ? ' / '+max+(size > max ? ' · 超过填写的限制' : '') : '');
      const evidence = slot.draft.evidence.map(item =>
        '简历第 '+item.line+' 行'+(item.transferable ? '（可迁移）' : '')+' · '+item.topics.join('、'));
      const duties = slot.draft.duties.map(terms => terms.join('、'));
      basis.textContent = [duties.length ? '岗位职责 · '+duties.join('；') : '', ...evidence].filter(Boolean).join(' ｜ ');
      apply.hidden = !slot.proposal;
    }
    toggle.onclick = () => {
      form.hidden = !form.hidden;
      if (!form.hidden) panel.dataset.statementOpen = 'true';
      toggle.setAttribute('aria-expanded',String(!form.hidden));
      if (!form.hidden) render();
    };
    language.onchange = () => {
      inputsByLanguage.set(currentLanguage, [question.value,limit.value]);
      currentLanguage = language.value;
      [question.value,limit.value] = inputsByLanguage.get(currentLanguage) || ['',''];
      render();
    };
    question.oninput = limit.oninput = render;
    body.oninput = () => {
      const slot = slots.get(key());
      slot.body = body.value;
      slot.edited = true;
      render();
    };
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(body.value);
        status.textContent = '已复制陈述';
      } catch { status.textContent = '无法访问剪贴板，请选中文本复制。'; }
    };
    function replace(slot,draft) {
      Object.assign(slot,{draft,body:draft.answer,edited:false,proposal:null});
    }
    apply.onclick = () => {
      const slot = slots.get(key());
      if (slot?.proposal) {
        replace(slot,slot.proposal);
        slot.status = '已采用新草稿。';
        render();
      }
    };
    form.onsubmit = async event => {
      event.preventDefault();
      if (!resumeId) { toggleResumePanel(true); return; }
      const chosen = key();
      const slot = slots.get(chosen) || {};
      if (slot.pending) return;
      const selectedResume = resumeId;
      const selectedVersion = resumeVersion;
      const refresh = Boolean(slot.draft);
      const request = {resume_id:selectedResume,application_job:job,statement:options()};
      const sameResume = () => resumeId === selectedResume && resumeVersion === selectedVersion;
      const show = () => { if (sameResume() && key() === chosen) render(); };
      slots.set(chosen,slot);
      Object.assign(slot,{pending:true,proposal:null,status:'正在准备申请陈述…'});
      show();
      try {
        if (!refresh) {
          const preview = await post('/api/discovery/drafts',{...request,preview:true});
          if (!sameResume()) return;
          replace(slot,preview);
        }
        slot.status = '正在生成。可先编辑或复制基础草稿。';
        show();
        const draft = await post('/api/discovery/drafts',refresh ?
          {...request,regeneration_id:crypto.randomUUID()} : request);
        if (!sameResume()) return;
        if (!slot.edited) replace(slot,draft);
        else if (!draft.fallback_reason) slot.proposal = draft;
        slot.status = draft.fallback_reason ? draftReasons[draft.fallback_reason] || '可编辑基础草稿。'
          : slot.edited ? '新草稿已就绪，当前编辑已保留。' : '申请陈述已就绪。';
      } catch (problem) { slot.status = problem.message; }
      finally { slot.pending = false; show(); }
    };
    render();
    return panel;
  }


  function applicationPanel(job) {
    if (job.source === 'himalayas') return webApplicationPanel(job);
    const panel = el('section', undefined, 'discovery-application');
    panel.append(el('h4','投递方式'));
    const addresses = job.contact_emails || [];
    const actions = el('div', undefined, 'actions');
    const direct = !addresses.length && applicationAnchor(job);
    const original = offerLink(job.url,'查看原帖');
    const source = offerLink(job.original_source_url,'打开发布方来源');
    if (direct) {
      actions.append(...[direct,original,source].filter(Boolean));
      panel.append(actions);
      return panel;
    }
    const status = el('p', undefined, 'muted');
    status.setAttribute('role','status');
    const copy = async (value, label) => {
      try {
        await navigator.clipboard.writeText(value);
        status.textContent = '已复制'+label;
      } catch {
        status.textContent = '无法访问剪贴板，请选中文本复制。';
      }
    };
    const button = (label, action, variant = 'secondary') => {
      const control = el('button',label,variant);
      control.type = 'button';
      control.onclick = action;
      return control;
    };
    const recipient = el(addresses.length > 1 ? 'select' : addresses.length ? 'output' : 'input');
    const recipientLabel = el('label','收件人');
    if (addresses.length > 1) {
      for (const address of addresses) {
        const option = el('option',address);
        option.value = address;
        recipient.append(option);
      }
    } else if (addresses.length) {
      recipient.value = addresses[0];
    } else {
      recipient.type = 'email';
      recipient.maxLength = 254;
      recipient.placeholder = '可从原帖核对后填写';
      panel.append(el('p','岗位未提供可展示的招聘邮箱，可去原帖核对。','muted'));
    }
    recipientLabel.append(recipient);
    const signature = el('input');
    signature.maxLength = 80;
    signature.placeholder = '只使用你在此确认的姓名';
    const signatureLabel = el('label','签名（选填）');
    signatureLabel.append(signature);
    const editor = el('div', undefined, 'discovery-mail-editor');
    let modified = false, active = false, proposal = null;
    const mode = el('p',undefined,'muted');
    const subject = el('input');
    const subjectLabel = el('label','主题');
    subjectLabel.append(subject);
    const body = el('textarea');
    body.rows = 8;
    const bodyLabel = el('label','正文');
    bodyLabel.append(body);
    const guidance = el('div',undefined,'muted');
    const controls = el('div', undefined, 'actions');
    const client = el('a','打开邮件客户端','button-link secondary');
    const updateLink = () => {
      const address = recipient.value.trim();
      const valid = /^[^\s,@?&#]+@[^\s,@?&#]+\.[^\s,@?&#]+$/.test(address);
      if (!valid) client.removeAttribute('href');
      else client.href = 'mailto:'+encodeURIComponent(address).replace('%40','@')+
        '?subject='+encodeURIComponent(subject.value)+'&body='+encodeURIComponent(body.value);
      client.setAttribute('aria-disabled',String(!valid));
    };
    const reasons = draftReasons;
    function fill(draft) {
      modified = false;
      mode.textContent = draft.mode;
      subject.value = draft.subject;
      body.value = draft.answer;
      guidance.replaceChildren(el('p','建议附件名 · '+draft.attachment_name),
        el('p','发送前请手动附上简历，并核对收件人和原帖的附件要求。'));
      for (const item of draft.evidence || []) {
        guidance.append(el('p','简历第 '+item.line+' 行 · '+item.topics.join('、')));
      }
      for (const hint of draft.guidance) guidance.append(el('p',hint));
      updateLink();
    }
    for (const field of [subject,body]) field.addEventListener('input',() => {
      modified = true;
      mode.textContent = '已编辑';
      updateLink();
    });
    recipient.addEventListener('input',updateLink);
    const apply = button('采用新草稿', () => {
      if (proposal) {
        fill(proposal);
        proposal = null;
        apply.hidden = true;
        status.textContent = '已采用新草稿。';
      }
    });
    apply.hidden = true;
    const regenerate = button('重新生成', () => generate(true));
    controls.append(button('复制主题',() => copy(subject.value,'主题')),
      button('复制正文',() => copy(body.value,'正文')),client,regenerate,apply);
    async function generate(refresh) {
      if (!resumeId) { toggleResumePanel(true); return; }
      if (active) return;
      if (!refresh && editor.childElementCount) { body.focus(); return; }
      if (!addresses.length && !recipient.reportValidity()) return;
      const generationResume = resumeId;
      const generationVersion = resumeVersion;
      const stillCurrent = () => generationResume === resumeId && generationVersion === resumeVersion;
      const inputs = {resume_id:generationResume, application_job:job, ...availability(),
        recipient:recipient.value.trim() || null, signature:signature.value.trim() || null};
      active = true;
      compose.disabled = regenerate.disabled = true;
      proposal = null;
      apply.hidden = true;
      status.textContent = '正在准备邮件…';
      try {
        if (!editor.childElementCount) {
          const preview = await post('/api/discovery/drafts', {...inputs,preview:true});
          if (!stillCurrent()) return;
          fill(preview);
          editor.append(mode,subjectLabel,bodyLabel,controls,guidance);
          signatureLabel.hidden = true;
        }
        status.textContent = '正在生成正文。现在可以编辑或复制基础草稿。';
        const draft = await post('/api/discovery/drafts', refresh ?
          {...inputs,regeneration_id:crypto.randomUUID()} : inputs);
        if (!stillCurrent()) return;
        if (modified && !draft.fallback_reason) {
          proposal = draft;
          apply.hidden = false;
        } else if (!modified) {
          fill(draft);
        }
        status.textContent = draft.fallback_reason
          ? reasons[draft.fallback_reason] || '可继续编辑基础草稿。'
          : modified ? '新草稿已就绪，当前编辑已保留。' : '模型草稿已就绪。';
      } catch (problem) { if (stillCurrent()) status.textContent = problem.message; }
      finally {
        active = false;
        compose.disabled = regenerate.disabled = false;
      }
    }
    const compose = button('写投递邮件', () => generate(false), addresses.length ? '' : 'secondary');

    actions.append(compose);
    if (addresses.length) {
      actions.append(button('复制邮箱',() => copy(recipient.value,'邮箱')));
      actions.append(...[original,source].filter(Boolean));
      panel.append(recipientLabel,signatureLabel,actions,editor,status);
    } else {
      const links = el('div',undefined,'actions');
      links.append(...[original,source].filter(Boolean));
      const manual = el('details');
      manual.append(el('summary','手动填写邮箱'),recipientLabel,signatureLabel,actions,editor,status);
      panel.append(links,manual);
    }
    return panel;
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

  const jobKey = job => job.source === 'offerdao' ? job.source_id : job.local_job_id;

  function offerAdjustments() {
    const current = get('offerdao-query').value.trim();
    const type = get('offerdao-type');
    const employment = type.value || (/(?:实习|\bintern(?:ship)?\b)/i.test(current) ? '实习' : '');
    const actions = el('div',undefined,'actions');
    for (const keyword of ['大模型','Agent']) {
      if (keyword.toLowerCase() === current.toLowerCase() && employment === type.value) continue;
      const adjust = el('button','试搜「'+keyword+'」'+(employment ? ' · '+employment : ''),'secondary');
      adjust.type = 'button';
      adjust.onclick = () => {
        manualOfferQuery = manualOfferType = true;
        get('offerdao-query').value = keyword;
        type.value = employment;
        invalidateOffers();
        searchOffers();
      };
      actions.append(adjust);
    }
    return actions;
  }

  function waitingRows() {
    return Array.from({length:3},() => {
      const placeholder = el('div',undefined,'result-placeholder');
      placeholder.setAttribute('aria-hidden','true');
      return placeholder;
    });
  }

  function renderJobs() {
    const area = get('discovery-list');
    if (!batch) return;
    const jobs = batch.jobs.filter(job => (
      (!get('filter-grade').value || job.analysis?.grade === get('filter-grade').value) &&
      (!get('filter-city').value || (job.city || (job.remote ? '远程' : '待确认')) === get('filter-city').value) &&
      (!get('filter-term').value || job.matched_terms.includes(get('filter-term').value)) &&
      (!get('filter-unknown').checked || job.analysis?.conditions.some(item => item.status === '待确认'))
    ));
    if (batch.mode !== 'source_page' || terminal(batch.stage)) {
      jobs.sort((a,b)=>(a.analysis?.rank ?? 9)-(b.analysis?.rank ?? 9) ||
        (b.analysis?.match_score ?? 0)-(a.analysis?.match_score ?? 0));
    }
    const scroll = area.scrollTop;
    area.replaceChildren();
    if (!jobs.length) {
      get('discovery-detail').hidden = true;
      area.append(el('p',batch.jobs.length ? '没有符合当前筛选条件的岗位。' :
        batch.stage === 'failed' ? '本轮没有可显示的岗位。请查看上方失败原因。' :
        terminal(batch.stage) ? '本轮没有匹配岗位。可调整搜索词或城市。' : batch.stage === 'idle' ? '等待搜索。' : '等待后台获取岗位。','empty'));
      if (offerPreview && batch.platform === 'offerdao' && batch.stage === 'complete' && !batch.jobs.length && offerPage) {
        area.append(offerAdjustments());
      }
      if (['queued','fetching','deduplicating','analyzing'].includes(batch.stage)) area.append(...waitingRows());
      return;
    }
    area.append(el('p','显示 '+jobs.length+' / '+batch.jobs.length+' 条','muted'));
    const active = jobs.find(job => jobKey(job) === selectedJob) || jobs[0];
    selectedJob = jobKey(active);
    const mixedSources = new Set(batch.jobs.map(job=>job.source).filter(Boolean)).size > 1;
    for(const job of jobs) {
      const card = el('button',undefined,'discovery-job');
      card.setAttribute('aria-pressed', String(job === active));
      card.type = 'button';
      const label = el('span',job.analysis?.grade || (job.error?'分析失败':'待匹配'),'grade grade-'+(job.analysis?.rank ?? 'none'));
      const heading = el('span',undefined,'discovery-job-heading');
      heading.append(el('strong',job.title || '未提供岗位名'),label);
      card.append(heading,
        el('span',[job.company,job.city || (job.remote ? '远程' : null),mixedSources ? platformNames[job.source] : null].filter(Boolean).join(' · '),'muted'),
        el('span',job.analysis?.reason || job.error || (offerPreview && !resumeId ? '添加简历后可匹配' : '等待分析'),'discovery-job-reason'));
      card.onclick = () => {
        area.querySelector('[aria-pressed="true"]')?.setAttribute('aria-pressed','false');
        card.setAttribute('aria-pressed','true');
        selectedJob = jobKey(job);
        showDetail(job,true);
        const detail = get('discovery-detail');
        if (desktop.matches) detail.scrollTop = 0;
        else {
          listScroll = window.scrollY;
          discoveryPanel.classList.add('mobile-reading');
          get('mobile-list-back').focus({preventScroll:true});
          get('workspace-resume').closest('.workspace-top').scrollIntoView({block:'start'});
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
    const offer = batch.platform === 'offerdao' || jobs.some(job => job.source === 'offerdao');
    const pageProgress = batch.mode === 'source_page' && batch.query_conditions
      ? (batch.query_conditions.source_total == null ? '' : '来源共 '+batch.query_conditions.source_total+' 张卡片 · ')+
        '本页 '+jobs.length+
        ' 个岗位 · 已完成匹配 '+counts.analyzed+'/'+jobs.length+(counts.failed ? ' · 失败 '+counts.failed : '')+' · '
      : '';
    const current = counts.current_term ? ' · '+counts.current_term : '';
    const sourceHint = batch.mode === 'online' && batch.error && !jobs.length
      ? ' · 可在岗位原页复制正文，通过「更多方式 → 粘贴 JD」分析。'
      : '';
    get('discovery-result-title').textContent = '岗位结果 '+jobs.length;
    get('discovery-round').hidden = offerPreview;
    get('discovery-status').textContent = offerPreview ? batch.message : (agentId ? 'Agent · ' : '')+(batch.reused_candidates ? '使用新简历重评原岗位 · 筛选词来自原搜索 · ' : '')+
      (offer ? 'Offer岛 · '+pageProgress : batch.mode === 'online' ? '' : modeNames[batch.mode]+' · ')+ (stages[stage] || ({running:'正在执行', waiting:'等待回答', interrupted:'本轮中断'}[stage] || stage))+current+(batch.error?' · '+batch.error:'')+sourceHint;
    const parts = [
      ['获取',counts.fetched],['命中',counts.matched],['去重后',counts.unique],
      ['本轮候选',counts.selected],['已分析',counts.analyzed],['失败',counts.failed],
    ];
    get('discovery-overview').textContent = parts.filter(([,count])=>count!==undefined).map(([label,count])=>label+' '+count).join(' · ')+
      (terminal(stage) && (counts.selected ?? 0) < limit ? ' · 候选少于目标 '+limit+' 条'+
        (batch.mode === 'online' && stage === 'complete' ? '（本轮符合条件的岗位不足）' : '') : '');
    const attribution = get('discovery-attribution');
    attribution.textContent = offer ? 'Offer岛' : 'Himalayas';
    attribution.href = offer ? 'https://offerdao.ai/' : 'https://himalayas.app/';
    attribution.setAttribute('aria-label', '岗位来源 '+attribution.textContent);
    attribution.hidden = !offer && !(batch.mode === 'online' && batch.platform === 'himalayas') && !jobs.some(job => job.source === 'himalayas');
    get('discovery-queries').textContent = Object.entries(counts.queries || {})
      .filter(([entered,actual]) => entered !== actual)
      .map(([entered,actual])=>entered+' → '+actual).join(' · ');
    get('discovery-filters').hidden = offerPreview || !jobs.length;
    get('discovery-retry').hidden = offerPreview || Boolean(agentId) || !['partial','failed'].includes(stage);
    const exportLink = get('discovery-export');
    exportLink.hidden = offerPreview || !terminal(stage);
    exportLink.setAttribute('aria-label','导出 JSON（'+jobs.length+' 条岗位）');
    exportLink.href = agentId ? '/api/discovery/agent/'+agentId+'/export' : '/api/discovery/tasks/'+taskId+'/export';
    const distribution = batch.grade_distribution || gradeNames.map(grade => ({
      grade, count: jobs.filter(job => job.analysis?.grade === grade).length,
    }));
    get('discovery-grade-note').textContent = '档位依据岗位要求、已确认条件和简历证据，部分档位可能为空。';
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

  function keepPreviousRanking(message) {
    taskId = reanalysis.previousTask;
    reanalysis = null;
    update();
    error(message);
  }

  async function poll() {
    const requestedId = taskId;
    try {
      const result = await api('/api/discovery/tasks/'+requestedId+'?wait_ms=750');
      if (requestedId !== taskId) return;
      if (reanalysis?.id === requestedId) {
        if (!terminal(result.stage)) {
          pollTimer = setTimeout(poll,0);
          return;
        }
        if (!['complete','partial'].includes(result.stage) || !result.counts.analyzed) {
          keepPreviousRanking(result.error || '重新分析未完成，仍然显示上轮结果。');
          return;
        }
        reanalysis = null;
        sessionStorage.setItem('discovery-task',requestedId);
      }
      if (result.mode === 'source_page' && !result.jobs.length && offerPage && !terminal(result.stage)) {
        pollTimer = setTimeout(poll,0);
        return;
      }
      batch = result;
      update();
      if (!terminal(batch.stage)) pollTimer = setTimeout(poll,0);
    } catch (e) {
      if (requestedId !== taskId) return;
      if ([404,422].includes(e.status)) {
        if (reanalysis?.id === requestedId) {
          keepPreviousRanking('重新分析的批次已失效，仍然显示上轮结果。');
          return;
        }
        get('discovery-status').textContent = '该批次已失效，请重新搜索。';
        sessionStorage.removeItem('discovery-task');
        return;
      }
      get('discovery-status').textContent = '暂时无法读取进度，正在重新连接…';
      pollTimer = setTimeout(poll,2500);
    }
  }
  function resetResultPane(waiting = false, keepSelection = false) {
    if (!keepSelection) selectedJob = null;
    batch = null;
    get('discovery-result-title').textContent = '岗位结果 0';
    get('discovery-list').replaceChildren(...(waiting ? waitingRows() : []));
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

  function watch(id, reset, keepSelection = false, keepOffer = false) {
    reanalysis = null;
    if (!keepOffer) {
      if (offerAbort) invalidateOffers();
      showOfferPanel(false);
    } else {
      offerPreview = false;
    }
    clearTimeout(agentTimer);
    agentId = null;
    sessionStorage.removeItem('discovery-agent');
    get('agent-trace').hidden = true;
    if (!keepOffer) discoveryPanel.classList.remove('mobile-reading');
    expandSearch(false);
    clearTimeout(pollTimer);
    taskId = id;
    sessionStorage.setItem('discovery-task',id);
    if (reset) resetResultPane(true, keepSelection);
    get('discovery-result').hidden = false;
    get('discovery-round').hidden = false;
    updateResumeStatus();
    if (!keepOffer) get('discovery-status').textContent = '任务已接收，正在排队…';
    poll();
  }

  get('discovery-task-form').onsubmit = async event => {
    event.preventDefault();
    if (resumeBusy || searchBusy) return;
    if (offerSource()) {
      if (!offerAbort) searchOffers();
      return;
    }
    if (!resumeId) { error('请先上传简历或选用示例简历。'); return; }
    const submittedResume = resumeId;
    const submittedRevision = resumeLoad;
    const submittedSource = sourceRevision;
    const submit = event.submitter;
    searchBusy = true;
    submit.disabled = true;
    get('discovery-status').textContent = '正在提交分析…';
    saveSearchDraft();
    try {
      const terms = [...new Map([...get('discovery-terms').querySelectorAll('input')]
        .map(input=>input.value.trim()).filter(Boolean).map(term=>[key(term),term])).values()];
      const mode = get('discovery-mode').value;
      const pasted = mode === 'paste';
      const limit = pasted ? 1 : get('discovery-limit').valueAsNumber;
      if ((!terms.length && !pasted) || terms.length > 5 || terms.some(term=>term.length < 2 || term.length > 48 || /[。！？；;]/.test(term))) throw Error('请输入 1–5 个不同的短职位词，每个 2–48 字。');
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw Error('候选数量必须是 1–100 的整数。');
      const sourceOptions = mode === 'online' ? {
        job_type:get('discovery-job-type').value,
        country:get('discovery-country').value.toUpperCase() || null,
      } : {};
      let jobs = [];
      if (pasted) {
        const jd = get('discovery-jd').value.trim();
        if (!jd) throw Error('请粘贴岗位正文。');
        jobs = [{title:'手动粘贴的岗位', jd}];
      }
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
        ...availability(),
        degree:get('discovery-degree').value || null,
        graduation_year:get('discovery-graduation').valueAsNumber || null,
      });
      if (submittedRevision !== resumeLoad || submittedResume !== resumeId || submittedSource !== sourceRevision) return;
      watch(task.id,true);
      if (!desktop.matches) get('discovery-result').scrollIntoView({block:'start'});
    } catch (e) {error(e.message);}
    finally { searchBusy=false; submit.disabled=resumeBusy; }
  };
  get('discovery-retry').onclick = async () => {
    if (reanalysis?.id === taskId) return;
    const button = get('discovery-retry');
    button.disabled=true;
    try {const task = await post('/api/discovery/tasks/'+taskId+'/retry',{}); watch(task.id,false,true,Boolean(offerPage));}
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
      live:run.provider === 'cloudflare' ? 'Cloudflare Workers AI · Himalayas 在线岗位' : '真实模型驱动 · Himalayas 在线岗位',
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
      const run = await api('/api/discovery/agent/'+id+'?wait_ms=650');
      if (id !== agentId) return;
      renderAgent(run);
      if (!terminal(run.status) && run.status !== 'waiting') agentTimer = setTimeout(pollAgent, 0);
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

  function showAgent(id, {restoring = false} = {}) {
    if (offerAbort || offerPage) invalidateOffers();
    showOfferPanel(false);
    clearTimeout(pollTimer);
    clearTimeout(agentTimer);
    taskId = null;
    agentId = id;
    batch = null;
    selectedJob = null;
    sessionStorage.removeItem('discovery-task');
    sessionStorage.setItem('discovery-agent', id);
    if (!restoring) openWorkspace();
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

  const previousResume = readResumeRef(activeResumeKey);
  const personalResume = readResumeRef(personalResumeKey);
  const initialResume = personalResume || previousResume;
  const initialKind = personalResume ? 'personal' : initialResume && savedResumeKind(initialResume);
  const preferPersonal = personalResume && previousResume && previousResume.id !== personalResume.id;
  const restoringResume = initialResume && loadResume(() => initialResume, {restoring:true, kind:initialKind});
  if (preferPersonal && savedResumeKind(previousResume) === 'example') {
    sessionStorage.removeItem('discovery-task');
    sessionStorage.removeItem('discovery-agent');
  }
  const recentAgent = !preferPersonal && sessionStorage.getItem('discovery-agent');
  if (recentAgent) showAgent(recentAgent,{restoring:true});
  const recent = !preferPersonal && sessionStorage.getItem('discovery-task');
  if (recent) {
    api('/api/discovery/tasks/'+recent).then(async result=>{
      await restoringResume;
      if (taskId || agentId || recent !== sessionStorage.getItem('discovery-task')) return;
      taskId = recent;
      batch = result;
      if (result.mode === 'source_page' && result.query_conditions.source_limit) {
        const options = result.query_conditions;
        offerPage = {jobs:result.jobs, total:options.source_total, offset:options.source_offset, limit:options.source_limit};
        get('discovery-platform').value = 'offerdao';
        get('offerdao-query').value = options.source_query || result.terms[0] || '';
        get('offerdao-city').value = options.city || '';
        get('offerdao-type').value = options.source_employment_type || (options.job_type === 'internship' ? '实习' : '');
        setMode('online');
        updateOfferPagination(offerPage);
      }
      update();
      if (!resumeId && result.resume && !initialResume) loadResume(() => result.resume, {restoring:true});
      if (!terminal(result.stage)) poll();
    }).catch(()=>sessionStorage.removeItem('discovery-task'));
  }
})();
