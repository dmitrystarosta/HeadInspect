(() => {
  const STORAGE_KEY = "headinspect_analytics_consent";
  const METRIKA_ID = 111906427;

  function hasConsent() {
    try { return localStorage.getItem(STORAGE_KEY) === "accepted"; }
    catch (_) { return false; }
  }

  function saveConsent() {
    try { localStorage.setItem(STORAGE_KEY, "accepted"); } catch (_) {}
  }

  function loadMetrika() {
    if (window.__headinspectMetrikaLoaded) return;
    window.__headinspectMetrikaLoaded = true;
    (function(m,e,t,r,i,k,a){
      m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
      m[i].l=1*new Date();
      for (var j=0;j<document.scripts.length;j++){if(document.scripts[j].src===r){return;}}
      k=e.createElement(t);a=e.getElementsByTagName(t)[0];k.async=1;k.src=r;a.parentNode.insertBefore(k,a);
    })(window,document,"script","https://mc.yandex.ru/metrika/tag.js?id="+METRIKA_ID,"ym");

    ym(METRIKA_ID,"init",{ssr:true,webvisor:true,clickmap:true,ecommerce:"dataLayer",referrer:document.referrer,url:location.href,accurateTrackBounce:true,trackLinks:true});
  }

  function showNotice() {
    if (document.querySelector(".analytics-notice")) return;
    const notice=document.createElement("aside");
    notice.className="analytics-notice";
    notice.setAttribute("aria-label","Уведомление об аналитике");
    notice.innerHTML=`<div class="analytics-notice-mark"></div><p>Мы используем cookie и Яндекс Метрику для анализа работы сайта. Нажимая «Принять», вы соглашаетесь на обработку данных для веб-аналитики.</p><div class="analytics-notice-actions"><button class="primary-btn analytics-accept" type="button">Принять</button><a class="analytics-more" href="/privacy/">Подробнее</a></div>`;
    document.body.appendChild(notice);
    notice.querySelector(".analytics-accept")?.addEventListener("click",()=>{saveConsent();loadMetrika();notice.remove();});
  }

  function init(){ if(hasConsent()) loadMetrika(); else showNotice(); }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",init); else init();
})();
