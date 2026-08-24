const $=(s,c=document)=>c.querySelector(s);const $$=(s,c=document)=>[...c.querySelectorAll(s)];
const toggle=$('.menu-toggle'),nav=$('.main-nav');
toggle?.addEventListener('click',()=>{const o=nav.classList.toggle('open');toggle.setAttribute('aria-expanded',String(o))});
$$('.main-nav a').forEach(a=>a.addEventListener('click',()=>{nav.classList.remove('open');toggle?.setAttribute('aria-expanded','false')}));
$$('.faq-list details').forEach(d=>d.addEventListener('toggle',()=>{if(d.open)$$('.faq-list details').forEach(o=>{if(o!==d)o.open=false})}));

// Keep one audit alive while the user switches between HeadInspect modules.
(()=>{const current=new URL(location.href);const job=current.searchParams.get('job');if(!job)return;const url=current.searchParams.get('url');const params=new URLSearchParams({job});if(url)params.set('url',url);const auditPaths=new Set(['/','/open-graph/','/meta/','/canonical/','/schema/','/images/','/sitemap/']);$$('a[href]').forEach(a=>{let target;try{target=new URL(a.getAttribute('href'),location.origin)}catch{return}if(target.origin!==location.origin||!auditPaths.has(target.pathname))return;params.forEach((v,k)=>target.searchParams.set(k,v));a.href=target.pathname+'?'+target.searchParams.toString()+target.hash})})();
