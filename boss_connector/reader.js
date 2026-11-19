globalThis.readBossSnapshot = () => {
              const visible=n=>n&&n.getClientRects().length>0;
              const nav=document.querySelector('.user-nav');
              const links=nav?[...nav.querySelectorAll('a')].filter(visible):[];
              const hasPath=path=>links.some(a=>{try{return new URL(a.href,location.href).pathname===path}catch{return false}});
              const authenticated=hasPath('/web/geek/chat')&&hasPath('/web/geek/resume')&&!links.some(a=>/登录\s*[/／]?\s*注册/.test(a.innerText));
              const body=document.body?.innerText||'';
              const safety=/^(安全验证|安全检查|访问受限)/.test(document.title)||/请完成.{0,8}验证|访问异常|账号异常|操作过于频繁/.test(body.slice(0,800));
              const heads=[...document.querySelectorAll('h1,h2,h3,h4,div,span,strong,p')].filter(n=>visible(n)&&n.innerText?.trim()==='职位描述');
              let region=null;
              for(const h of heads){let n=h;while(n&&n!==document.body){const t=n.innerText||'';if(t.includes('立即沟通')&&t.length>=80&&t.length<24000){region=n;break}n=n.parentElement}if(region)break}
              let job=null;
              if(region){
                const text=region.innerText.trim();
                const before=text.split('职位描述')[0].split('\n').map(s=>s.trim()).filter(Boolean);
                const title=before.find(t=>!/^(收藏|立即沟通|分享|举报)$/.test(t)&&!/^\d+(?:\.\d+)?\s*[-~—]/.test(t))||null;
                const salary=(before.join(' ').match(/\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*(?:[kK]|千|万|元)(?:[^\s]{0,8})?/)||[])[0]||null;
                const city=(before.join(' ').match(/上海|北京|苏州|杭州|深圳|成都|重庆|武汉|南京|广州/)||[])[0]||null;
                const experience=(before.join(' ').match(/\d+\s*[-—至]\s*\d+年|\d+年以上|经验不限|在校\/应届|应届生/)||[])[0]||null;
                const education=(before.join(' ').match(/博士|硕士|本科|大专|学历不限/)||[])[0]||null;
                const companyNode=[...region.querySelectorAll('[class]')].find(n=>visible(n)&&typeof n.className==='string'&&n.className.split(' ').includes('company-name'));
                const url=new URL(location.href);const externalId=url.searchParams.get('jobId');
                job={id:externalId,title,company:companyNode?.innerText?.trim()||null,location:city,salary,experience,education,raw_jd:text.slice(0,18000),source:'boss_browser_readonly',source_url:url.origin+url.pathname};
              }
              return {authenticated,safety,job,address:location.origin+location.pathname,loading:body.length<60&&!nav,empty:body.includes('没有找到相关职位')};
            };
