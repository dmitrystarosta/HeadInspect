(() => {
  const footer = document.getElementById("site-footer");
  if (!footer) return;

  footer.innerHTML = `
    <div class="shell footer-inner">
      <div class="footer-main">
        <div class="footer-about">
          <a class="brand footer-brand" href="/" aria-label="HeadInspect — на главную">
            <img class="brand-logo" src="/headinspect-logo-36.webp" srcset="/headinspect-logo-36.webp 1x, /headinspect-logo-72.webp 2x" width="36" height="36" alt="" aria-hidden="true">
            <span>HeadInspect</span>
          </a>
          <p>Массовая проверка технических данных сайта.</p>
        </div>
        <div class="footer-link-groups">
          <div class="footer-link-group">
            <span class="footer-link-label">Проверки</span>
            <div class="footer-links">
              <a href="/open-graph/">Open Graph</a>
              <a href="/meta/">Meta</a>
              <a href="/canonical/">Canonical</a>
              <a href="/schema/">Schema</a>
              <a href="/images/">Images</a>
              <a href="/sitemap/">Sitemap</a>
            </div>
          </div>
          <div class="footer-link-group">
            <span class="footer-link-label">Ссылки</span>
            <div class="footer-links">
              <a href="https://github.com/dmitrystarosta/HeadInspect" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
              <a href="https://t.me/headinspect" target="_blank" rel="noopener noreferrer">Telegram ↗</a>
              <a href="https://vk.ru/headinspect" target="_blank" rel="noopener noreferrer">ВКонтакте ↗</a>
            </div>
          </div>
          <div class="footer-link-group">
            <span class="footer-link-label">Документы</span>
            <div class="footer-links">
              <a href="/privacy/">Политика обработки персональных данных</a>
            </div>
          </div>
        </div>
      </div>
      <aside class="other-projects" aria-label="Другие проекты">
        <div class="other-projects-label">Другие проекты</div>
        <a class="project-card" href="https://belyjspisok.ru/" target="_blank" rel="noopener noreferrer">
          <img src="/belyjspisok-logo-56.webp" srcset="/belyjspisok-logo-56.webp 1x, /belyjspisok-logo-112.webp 2x" width="56" height="56" alt="Логотип «Белый список?»">
          <span class="project-card-copy"><strong>Белый список?</strong><small>Проверка доступности сайтов при ограничениях мобильного интернета.</small><span class="project-url">belyjspisok.ru ↗</span></span>
        </a>
      </aside>
      <small class="copyright">© 2026 Dmitry Starosta</small>
    </div>`;
})();
