"""Render untrusted Markdown with HTML disabled; no CDN, image fetches or scripts.

The browser adds code-copy controls and evidence links AFTER parsing this HTML.
Stored HTML is not trusted: history is re-rendered from its original Markdown.
"""
from html import escape
import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

MAX_MARKDOWN_CHARS = 60000


def render_markdown(text: str) -> str:
    parser = MarkdownIt('js-default', {'html': False, 'linkify': False, 'breaks': False})
    original_validate = parser.validateLink

    def safe_link(value: str) -> bool:
        # Only external HTTP(S) links and local fragment anchors. No file:, data:,
        # javascript:, protocol-relative URLs, repository paths or credential URLs.
        if not original_validate(value):
            return False
        try:
            url = urlsplit(value)
            return value.startswith('#') or (url.scheme.lower() in {'http', 'https'}
                    and bool(url.hostname) and not url.username and not url.password)
        except ValueError:
            return False

    parser.validateLink = safe_link

    def image(renderer, tokens, idx, options, env):
        return '<span class="image-omitted">[图片未加载：' + escape(tokens[idx].content) + ']</span>'

    def link_open(renderer, tokens, idx, options, env):
        token = tokens[idx]
        href = token.attrGet('href') or ''
        token.attrs = {'href': href, 'rel': 'noopener noreferrer', 'target': '_blank'}
        return renderer.renderToken(tokens, idx, options, env)

    def fence(renderer, tokens, idx, options, env):
        token = tokens[idx]
        info = token.info.strip().split(maxsplit=1)
        language = info[0].lower() if info and re.fullmatch(r'[a-zA-Z0-9_+.-]{1,24}', info[0]) else 'text'
        return f'<pre><code class="language-{escape(language, quote=True)}">{escape(token.content)}</code></pre>\n'

    def cell(renderer, tokens, idx, options, env):
        # Inline styles are disallowed by our CSP. Convert Markdown table alignment
        # to fixed CSS classes instead of allowing arbitrary style attributes.
        token = tokens[idx]
        style = token.attrGet('style') or ''
        token.attrs = {}
        for align in ('left', 'center', 'right'):
            if style == f'text-align:{align}':
                token.attrSet('class', f'align-{align}')
        return renderer.renderToken(tokens, idx, options, env)

    parser.add_render_rule('image', image)
    parser.add_render_rule('link_open', link_open)
    parser.add_render_rule('fence', fence)
    parser.add_render_rule('th_open', cell)
    parser.add_render_rule('td_open', cell)
    return parser.render(text[:MAX_MARKDOWN_CHARS])
