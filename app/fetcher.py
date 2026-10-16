import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit, urljoin

import httpx
import trafilatura
from lxml import etree, html


def extract_readable(body, url):
    # Keep structural paragraph/BR boundaries, but flatten inline styling first.
    # Some extractors otherwise emit a newline on both sides of every italic/link.
    tree = html.fromstring(body)
    for node in tree.iter():
        if node.text:
            node.text = re.sub(r'\s+', ' ', node.text)
        if node.tail:
            node.tail = re.sub(r'\s+', ' ', node.tail)
    etree.strip_tags(tree, 'a', 'span', 'em', 'strong', 'i', 'b', 'u', 'small', 'mark', 'font')
    prepared = etree.tostring(tree, encoding='unicode', method='html')
    options = dict(url=url, output_format='json', with_metadata=True, include_comments=False, include_tables=False)
    extracted = trafilatura.extract(prepared, favor_precision=True, **options)
    if not extracted:
        extracted = trafilatura.extract(prepared, **options)
    if not extracted:
        raise ValueError('No readable article found. This page may require login or JavaScript; paste its text instead.')
    doc = json.loads(extracted)
    paragraphs = [p.strip() for p in doc.get('text', '').splitlines() if p.strip()]
    if sum(map(len, paragraphs)) < 100:
        raise ValueError('Not enough article text found. Paste the article text instead.')
    return {'title': doc.get('title') or urlsplit(url).hostname, 'byline': doc.get('author') or '', 'url': url, 'paragraphs': paragraphs}


async def public_address(url):
    parts = urlsplit(url)
    if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Use a public http:// or https:// article URL.')
    if parts.port not in (None, 80, 443):
        raise ValueError('Only standard web ports are supported.')
    addresses = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM), timeout=10)
    ips = [a[4][0] for a in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError('Article URLs must point to a public website, not a local network address.')
    return ips[0]


async def fetch_article(url):
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            # Pin the validated public IP to prevent a second DNS lookup/rebinding.
            ip = await public_address(url)
            parsed = httpx.URL(url)
            target = parsed.copy_with(host=ip)
            headers = {'Host': parsed.netloc.decode(), 'User-Agent': 'Readroom/0.1 (personal article reader)', 'Accept': 'text/html'}
            async with client.stream('GET', target, headers=headers, extensions={'sni_hostname': parsed.host}) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('The website returned an empty redirect.')
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                if not any(t in response.headers.get('content-type', '').lower() for t in ('text/html', 'application/xhtml')):
                    raise ValueError('This URL is not an HTML article. Paste its text instead.')
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 3_000_000:
                        raise ValueError('The page is too large. Paste the article text instead.')
                break
        else:
            raise ValueError('Too many website redirects.')
    return await asyncio.to_thread(extract_readable, bytes(body), url)
