/* The server renders Markdown with raw HTML disabled. Defense in depth:
   parse in an inert template, allow only fixed tags/attrs, then decorate text.
   Never interpret model output as raw HTML; never load Markdown images. */
(function () {
  'use strict';
  const allowed = new Set(['P','BR','HR','H1','H2','H3','H4','H5','H6','UL','OL','LI',
    'STRONG','EM','S','BLOCKQUOTE','PRE','CODE','A','TABLE','THEAD','TBODY','TR','TH','TD','SPAN']);
  function safeLink(value) {
    if (value.startsWith('#')) return true;
    try {
      const u = new URL(value);
      return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password;
    } catch (_) { return false; }
  }
  function fragment(html) {
    const template = document.createElement('template');
    template.innerHTML = html;
    for (const node of [...template.content.querySelectorAll('*')]) {
      if (!allowed.has(node.tagName)) { node.remove(); continue; }
      const href = node.getAttribute('href') || '', cls = node.getAttribute('class') || '';
      const start = node.getAttribute('start');
      for (const attr of [...node.attributes]) node.removeAttribute(attr.name);
      if (node.tagName === 'A' && safeLink(href)) {
        node.setAttribute('href', href); node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
      if (node.tagName === 'CODE' && /^language-[a-zA-Z0-9_+.-]{1,24}$/.test(cls)) node.className = cls;
      if (['TH','TD'].includes(node.tagName) && /^align-(left|right|center)$/.test(cls)) node.className = cls;
      if (node.tagName === 'SPAN' && cls === 'image-omitted') node.className = cls;
      if (node.tagName === 'OL' && /^\d{1,8}$/.test(start || '')) node.setAttribute('start', start);
    }
    return template.content;
  }
  function render(target, html, {labels = [], onCitation = () => {}, copy = () => {}, provisional = false} = {}) {
    target.replaceChildren(fragment(html));
    // Only link evidence markers in prose. Never mutate code strings, links or HTML.
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement.closest('pre,code,a,button') && /\[E\d+\]/.test(node.nodeValue)) texts.push(node);
    }
    const permitted = new Set(labels);
    for (const node of texts) {
      const part = document.createDocumentFragment(); let last = 0;
      for (const match of node.nodeValue.matchAll(/\[(E\d+)\]/g)) {
        part.append(document.createTextNode(node.nodeValue.slice(last, match.index)));
        if (permitted.has(match[1])) {
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'citation-link'; button.textContent = match[1];
          button.setAttribute('aria-label', `查看源码证据 ${match[1]}`);
          button.addEventListener('click', () => onCitation(match[1])); part.append(button);
        } else part.append(document.createTextNode(match[0]));
        last = match.index + match[0].length;
      }
      part.append(document.createTextNode(node.nodeValue.slice(last))); node.replaceWith(part);
    }
    for (const pre of [...target.querySelectorAll('pre')]) {
      const code = pre.querySelector('code'); if (!code) continue;
      const box = document.createElement('div'); box.className = 'md-code';
      const head = document.createElement('div'); head.className = 'md-code-head';
      const lang = document.createElement('span'); lang.textContent = code.className.replace('language-', '') || 'text';
      const button = document.createElement('button'); button.type = 'button'; button.className = 'code-copy';
      button.textContent = '复制代码'; button.disabled = provisional;
      button.title = provisional ? '草稿完成检查后可复制' : '复制此代码块';
      button.addEventListener('click', () => copy(code.textContent));
      head.append(lang, button); pre.before(box); box.append(head, pre);
    }
    for (const table of [...target.querySelectorAll('table')]) {
      const box = document.createElement('div'); box.className = 'md-table-wrap';
      box.tabIndex = 0; box.setAttribute('aria-label', '表格，可横向滚动');
      table.before(box); box.append(table);
    }
  }
  window.RepoMarkdown = {render, fragment};
})();
