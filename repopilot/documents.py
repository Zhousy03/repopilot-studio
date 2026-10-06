"""Fetch user-selected official documentation only; never arbitrary model URLs."""
import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit
import httpx

HOSTS={'docs.python.org','docs.langchain.com','reference.langchain.com','fastapi.tiangolo.com',
       'developer.mozilla.org','docs.github.com','docs.docker.com','www.postgresql.org',
       'www.typescriptlang.org','nodejs.org','docs.pydantic.dev','docs.pytest.org'}

class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','nav','noscript'}: self.hidden+=1
        if tag in {'p','div','br','li','pre','h1','h2','h3','h4'} and not self.hidden: self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style','nav','noscript'} and self.hidden: self.hidden-=1
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)

async def fetch_document(url: str) -> dict:
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in HOSTS or u.port not in {None,443} or u.username or u.password:
        raise ValueError('仅支持设置中列出的官方文档 HTTPS 地址；不支持任意网址')
    addresses=await asyncio.to_thread(socket.getaddrinfo,u.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('拒绝私网文档目标')
    async with httpx.AsyncClient(timeout=25,follow_redirects=False,trust_env=True) as client:
        async with client.stream('GET',url,headers={'User-Agent':'RepoPilot-Studio/1.1'}) as r:
            r.raise_for_status()
            if r.is_redirect: raise ValueError('文档发生重定向，请填写最终官方页面 URL')
            if not any(x in r.headers.get('content-type','') for x in ('text/html','text/plain','text/markdown')):
                raise ValueError('只读取 HTML、文本或 Markdown 文档')
            raw=bytearray()
            async for block in r.aiter_bytes():
                raw.extend(block)
                if len(raw)>2_000_000: raise ValueError('文档超过读取上限')
    text=raw.decode('utf-8',errors='replace')
    if 'text/html' in r.headers.get('content-type',''):
        parser=Text(); parser.feed(text); text=''.join(parser.parts)
    return {'url':url,'content':text[:16000],'source':'official_document','truncated':len(text)>16000}
