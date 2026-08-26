from pathlib import Path
import re, html, json, shutil, os, urllib.parse

SRC=Path('/mnt/data/radlines_build/home/radviser/www/www')
DB=SRC/'DB.sql'
OUT=Path('/mnt/data/radlines-astro-converted')
if OUT.exists(): shutil.rmtree(OUT)
(OUT/'src/components').mkdir(parents=True)
(OUT/'src/layouts').mkdir(parents=True)
(OUT/'src/pages').mkdir(parents=True)
(OUT/'src/data').mkdir(parents=True)
(OUT/'public/media').mkdir(parents=True)

# ---------- MySQL dump parsing ----------
def mysql_unescape(s):
    out=[]; i=0
    mp={'0':'\0','b':'\b','n':'\n','r':'\r','t':'\t','Z':'\x1a'}
    while i < len(s):
        c=s[i]
        if c=='\\' and i+1<len(s):
            n=s[i+1]
            out.append(mp.get(n,n)); i+=2
        else:
            out.append(c); i+=1
    return ''.join(out)

def parse_values(blob):
    rows=[]; i=0; n=len(blob)
    while i<n:
        while i<n and blob[i] not in '(': i+=1
        if i>=n: break
        i+=1; row=[]
        while i<n:
            while i<n and blob[i].isspace(): i+=1
            if i<n and blob[i]=="'":
                i+=1; buf=[]
                while i<n:
                    if blob[i]=='\\' and i+1<n:
                        buf.append(blob[i]); buf.append(blob[i+1]); i+=2
                    elif blob[i]=="'":
                        i+=1; break
                    else: buf.append(blob[i]); i+=1
                val=mysql_unescape(''.join(buf))
            else:
                start=i; depth=0
                while i<n:
                    c=blob[i]
                    if c=='(' : depth+=1
                    elif c==')':
                        if depth==0: break
                        depth-=1
                    if depth==0 and c in ',)': break
                    i+=1
                tok=blob[start:i].strip()
                if tok=='NULL': val=None
                else:
                    try: val=int(tok)
                    except:
                        try: val=float(tok)
                        except: val=tok
            row.append(val)
            while i<n and blob[i].isspace(): i+=1
            if i<n and blob[i]==',': i+=1; continue
            if i<n and blob[i]==')': i+=1; break
            if i>=n: break
        rows.append(row)
    return rows

def get_insert(table):
    marker=f"INSERT INTO `{table}` VALUES "
    text=DB.read_text('utf-8', errors='replace')
    rows=[]
    search_from=0
    while True:
        p=text.find(marker, search_from)
        if p<0: break
        p += len(marker)
        i=p; in_str=False; esc=False; q=len(text)
        while i < len(text):
            c=text[i]
            if in_str:
                if esc:
                    esc=False
                elif c=='\\':
                    esc=True
                elif c=="'":
                    in_str=False
            else:
                if c=="'":
                    in_str=True
                elif c==';':
                    q=i; break
            i+=1
        rows.extend(parse_values(text[p:q]))
        search_from=q+1
    return rows

pages_rows=get_insert('page')
rev_rows=get_insert('revision')
text_rows=get_insert('text')
texts={int(r[0]):r[1] for r in text_rows}
revs={int(r[0]):r for r in rev_rows}

pages=[]
for r in pages_rows:
    pid,ns,title,isredir,latest = int(r[0]), int(r[1]), str(r[2]), int(r[4]), int(r[9])
    if ns != 0: continue
    rev=revs.get(latest)
    if not rev: continue
    tid=int(rev[2]); wt=texts.get(tid,'')
    pages.append({'id':pid,'title':title.replace('_',' '),'rawTitle':title,'redirect':bool(isredir),'wikitext':wt})

# ---------- Media copy / lookup ----------
media_lookup={}
imgroot=SRC/'images'
for p in imgroot.rglob('*'):
    if not p.is_file(): continue
    rel=p.relative_to(imgroot)
    if any(x in rel.parts for x in ('thumb','archive','deleted','temp')): continue
    if p.name=='index.html' or p.name.startswith('.'): continue
    # Copy flattened. If collision, keep first then hashed-ish path name.
    dest=OUT/'public/media'/p.name
    if not dest.exists():
        try: shutil.copy2(p,dest)
        except: continue
    media_lookup[p.name.replace('_',' ').lower()]='/media/'+urllib.parse.quote(p.name)
    media_lookup[p.name.lower()]='/media/'+urllib.parse.quote(p.name)

page_titles={p['title'].lower():p['title'] for p in pages}

def slug_for(title):
    title=title.replace(' ','_')
    return '/' + urllib.parse.quote(title, safe=':_()-,.')

def link_internal(target,label=None):
    target=target.strip()
    if target.startswith('#'):
        href=target
    else:
        href=slug_for(target)
    lab=label if label is not None else target.replace('_',' ')
    return f'<a href="{html.escape(href,quote=True)}">{lab}</a>'

def commons_file_url(name):
    return 'https://commons.wikimedia.org/wiki/File:' + urllib.parse.quote(name.replace(' ','_'), safe='()_,-.')

def commons_media_url(name, width=None):
    # Special:Redirect/file lets a normal <img> display a Commons-hosted file in-site.
    # Supplying width asks Commons for a thumbnail rather than the full original.
    base = 'https://commons.wikimedia.org/wiki/Special:Redirect/file/' + urllib.parse.quote(name.replace(' ','_'), safe='()_,-.')
    if width:
        return base + '?width=' + str(int(width))
    return base

# protect tags that should not be parsed
PROTECTED={}
def protect(m):
    key=f'@@PROT{len(PROTECTED)}@@'; tag=m.group(1).lower(); inner=m.group(2)
    if tag=='code':
        # Explicitly escape braces too, preventing Astro expression interpretation if this HTML is later inlined.
        esc=html.escape(inner, quote=False).replace('{','&#123;').replace('}','&#125;')
        val=f'<code>{esc}</code>'
    else:
        val=html.escape(inner, quote=False)
    PROTECTED[key]=val; return key

def restore_protected(s):
    for k,v in PROTECTED.items(): s=s.replace(k,v)
    return s

def split_template_parts(t):
    parts=[]; buf=[]; square=0; angle=0
    i=0
    while i < len(t):
        if t.startswith('[[',i): square+=1; buf.append('[['); i+=2; continue
        if t.startswith(']]',i) and square: square-=1; buf.append(']]'); i+=2; continue
        c=t[i]
        if c=='|' and square==0:
            parts.append(''.join(buf)); buf=[]
        else: buf.append(c)
        i+=1
    parts.append(''.join(buf))
    return parts

def parse_template(t):
    parts=[x.strip() for x in split_template_parts(t)]
    name=parts[0].strip().lower().replace('_',' ')
    args={}; pos=[]
    for x in parts[1:]:
        if '=' in x:
            k,v=x.split('=',1); args[k.strip().lower()]=v.strip()
        else: pos.append(x)
    if name in ('reflist','references'): return '<div class="references-placeholder"></div>'
    if name=='visible anchor': return html.escape(pos[0] if pos else '')
    if name=='further':
        v=pos[0] if pos else args.get('1','')
        return f'<p class="further">Further information: {link_internal(v)}</p>' if v else ''
    if name=='authors':
        vals=[]
        for k,v in sorted(args.items()):
            if k.startswith('author') and v: vals.append(render_inline(v))
        return '<div class="authors"><strong>Authors:</strong> '+', '.join(vals)+'</div>' if vals else ''
    if name.startswith('cite web') or name=='cite web':
        title=args.get('title') or args.get('website') or args.get('url') or 'Source'
        url=args.get('url','')
        return f'<cite>{f"<a href={json.dumps(url)}>{html.escape(title)}</a>" if url else html.escape(title)}</cite>'
    if name in ('cite journal','cite book'):
        title=args.get('title','Source'); url=args.get('url') or args.get('doi')
        if url and not url.startswith('http') and args.get('doi'): url='https://doi.org/'+url
        return f'<cite>{f"<a href={json.dumps(url)}>{html.escape(title)}</a>" if url else html.escape(title)}</cite>'
    if 'imagestack' in name or name in ('image stack','scrollable ct','scrollable image stack'):
        # Prefer an explicitly named image/file if present; otherwise link to Commons search.
        candidates=[]
        for x in pos:
            m=re.search(r'(?:File:|Image:)?([^|]+\.(?:jpg|jpeg|png|gif|tif|tiff))',x,re.I)
            if m: candidates.append(m.group(1).strip())
        if candidates:
            url=commons_file_url(candidates[0])
        else:
            url='https://commons.wikimedia.org/wiki/Special:MediaSearch?type=image&search='+urllib.parse.quote(name)
        return f'<div class="ct-stack-link"><strong>Scrollable CT/image stack:</strong> <a href="{html.escape(url,quote=True)}" target="_blank" rel="noopener">view on Wikimedia Commons</a>.</div>'
    # Common wrappers whose first positional text is useful
    if name in ('nowrap','small','em') and pos: return render_inline(pos[0])
    # Hide licensing/infrastructure templates that don't add useful article prose
    infrastructure=('pd-','licens','documentation','navbar','navbox','nospam','own','information','cc-','template')
    if any(name.startswith(x) for x in infrastructure): return ''
    # Unknown templates: keep a readable compact marker in source data but don't expose raw braces.
    return ''

def replace_templates(s):
    # Repeatedly resolve non-nested templates; nested remnants are removed later.
    for _ in range(12):
        new=re.sub(r'\{\{([^{}]*)\}\}', lambda m: parse_template(m.group(1)), s)
        if new==s: break
        s=new
    s=re.sub(r'\{\{.*?\}\}','',s,flags=re.S)
    return s

def render_inline(s):
    if not s: return ''
    # protected tokens already HTML; escape plain text carefully by transforming syntax before broad escaping
    s=re.sub(r"'''(.*?)'''", r'<strong>\1</strong>', s, flags=re.S)
    s=re.sub(r"''(.*?)''", r'<em>\1</em>', s, flags=re.S)
    # External links [url label]
    def ext(m):
        url=m.group(1); lab=m.group(2) or url
        return f'<a href="{html.escape(url,quote=True)}" target="_blank" rel="noopener">{lab}</a>'
    s=re.sub(r'\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\]', ext, s)
    # Internal links
    def il(m):
        inner=m.group(1)
        if inner.lower().startswith('category:'): return ''
        if inner.lower().startswith(('file:','image:')): return m.group(0)
        if '|' in inner:
            target,lab=inner.split('|',1)
        else: target,lab=inner,inner.replace('_',' ')
        return link_internal(target,lab)
    s=re.sub(r'\[\[([^\]]+)\]\]', il, s)
    return s

def render_file(spec):
    parts=[x.strip() for x in spec.split('|')]
    fname=re.sub(r'^(?:File|Image):','',parts[0],flags=re.I).strip()
    opts=parts[1:]
    caption=''; link_target=None; width=None
    for o in opts:
        if o.lower().startswith('link='):
            link_target=o.split('=',1)[1].strip()
        else:
            wm=re.match(r'^(\d+)(?:x\d+)?px$',o,re.I)
            if wm:
                width=int(wm.group(1))
            elif not re.match(r'^(thumb|thumbnail|left|right|center|frameless|frame|border|upright(?:=.*)?|alt=.*)$',o,re.I):
                caption=o
    alt=html.escape(re.sub("<.*?>","",caption or fname),quote=True)
    local=media_lookup.get(fname.lower()) or media_lookup.get(fname.replace('_',' ').lower())
    if local:
        width_attr=f' width="{width}"' if width else ''
        img=f'<img src="{html.escape(local,quote=True)}" alt="{alt}" loading="lazy"{width_attr}>'
        if link_target:
            img=f'<a href="{html.escape(slug_for(link_target),quote=True)}">{img}</a>'
        cap=f'<figcaption>{render_inline(caption)}</figcaption>' if caption else ''
        return f'<figure>{img}{cap}</figure>'

    # Ordinary Commons-hosted files should still be displayed inside Radlines.
    # This mirrors MediaWiki's shared-repository behavior without copying the file locally.
    file_page=commons_file_url(fname)
    media_src=commons_media_url(fname, width or 640)
    width_attr=f' width="{width}"' if width else ''
    img=f'<img src="{html.escape(media_src,quote=True)}" alt="{alt}" loading="lazy" referrerpolicy="no-referrer"{width_attr}>'
    if link_target:
        href=slug_for(link_target)
        img=f'<a href="{html.escape(href,quote=True)}">{img}</a>'
    else:
        img=f'<a href="{html.escape(file_page,quote=True)}" target="_blank" rel="noopener">{img}</a>'
    cap=f'<figcaption>{render_inline(caption)}</figcaption>' if caption else ''
    return f'<figure class="commons-media">{img}{cap}</figure>'

def render_gallery(m):
    body=m.group(1)
    items=[]
    for line in body.splitlines():
        line=line.strip()
        if not line or not re.match(r'^(?:File|Image):',line,re.I): continue
        items.append(render_file(line))
    return '<div class="gallery">'+''.join(items)+'</div>'

def render_table(block):
    rows=[]; cur=[]
    for line in block.splitlines()[1:]:
        line=line.strip()
        if line.startswith('|}'): break
        if line.startswith('|-'):
            if cur: rows.append(cur); cur=[]
        elif line.startswith('!'):
            cells=[x.strip() for x in line[1:].split('!!')]; cur.extend([('th',render_inline(x)) for x in cells])
        elif line.startswith('|'):
            cells=[x.strip() for x in line[1:].split('||')]; cur.extend([('td',render_inline(x)) for x in cells])
    if cur: rows.append(cur)
    if not rows: return ''
    out=['<div class="table-wrap"><table>']
    for row in rows:
        out.append('<tr>'); out.extend(f'<{t}>{v}</{t}>' for t,v in row); out.append('</tr>')
    out.append('</table></div>')
    return ''.join(out)

def render_wikitext(wt,title):
    global PROTECTED
    PROTECTED={}
    if not wt: return ''
    # redirects
    m=re.match(r'\s*#REDIRECT\s*\[\[([^\]]+)\]\]',wt,re.I)
    if m:
        return f'<p>This page redirects to {link_internal(m.group(1))}.</p>'
    s=wt
    s=re.sub(r'<!--.*?-->','',s,flags=re.S)
    s=re.sub(r'<(code|nowiki)\b[^>]*>(.*?)</\1>',protect,s,flags=re.S|re.I)
    # Scrollable stack templates need special recognition before generic templates.
    s=re.sub(r'\{\{\s*Imagestack\b(.*?)\}\}',lambda m: parse_template('Imagestack|'+m.group(1)),s,flags=re.S|re.I)
    # Galleries
    s=re.sub(r'<gallery\b[^>]*>(.*?)</gallery>',render_gallery,s,flags=re.S|re.I)
    # refs
    refs=[]
    def refrep(m):
        content=m.group(1).strip(); refs.append(content)
        return f'<sup class="ref">[{len(refs)}]</sup>'
    s=re.sub(r'<ref\b[^>]*>(.*?)</ref>',refrep,s,flags=re.S|re.I)
    s=re.sub(r'<ref\b[^>]*/>','',s,flags=re.I)
    s=replace_templates(s)
    s=re.sub(r'\[\[Category:[^\]]+\]\]','',s,flags=re.I)
    # Files outside galleries
    s=re.sub(r'\[\[((?:File|Image):[^\n]*?)\]\]',lambda m: render_file(m.group(1)),s,flags=re.I)
    # Wiki tables
    s=re.sub(r'\{\|.*?\n\|\}',lambda m: render_table(m.group(0)),s,flags=re.S)
    # headings
    s=re.sub(r'^======\s*(.*?)\s*======\s*$',r'<h6>\1</h6>',s,flags=re.M)
    s=re.sub(r'^=====\s*(.*?)\s*=====\s*$',r'<h5>\1</h5>',s,flags=re.M)
    s=re.sub(r'^====\s*(.*?)\s*====\s*$',r'<h4>\1</h4>',s,flags=re.M)
    s=re.sub(r'^===\s*(.*?)\s*===\s*$',r'<h3>\1</h3>',s,flags=re.M)
    s=re.sub(r'^==\s*(.*?)\s*==\s*$',r'<h2>\1</h2>',s,flags=re.M)
    # strip old presentational wrappers and tags, preserve simple useful tags
    s=re.sub(r'</?(?:div|span|font)\b[^>]*>','',s,flags=re.I)
    s=re.sub(r'<br\s*/?>','\n',s,flags=re.I)
    s=re.sub(r'__\w+__','',s)
    # lists and paragraphs line-by-line
    lines=s.splitlines(); out=[]; list_stack=[]; para=[]
    def flush_para():
        nonlocal para
        txt=' '.join(x.strip() for x in para).strip()
        if txt: out.append('<p>'+render_inline(txt)+'</p>')
        para=[]
    def close_lists():
        nonlocal list_stack
        while list_stack:
            out.append(f'</{list_stack.pop()}>')
    for line in lines:
        st=line.strip()
        if not st:
            flush_para(); close_lists(); continue
        if st.startswith('<h') or st.startswith('<div class="gallery') or st.startswith('<figure') or st.startswith('<div class="table-wrap') or st.startswith('<p class="missing-media') or st.startswith('<div class="ct-stack-link'):
            flush_para(); close_lists(); out.append(render_inline(st)); continue
        lm=re.match(r'^([*#:;]+)\s*(.*)$',st)
        if lm:
            flush_para(); marks=lm.group(1); content=lm.group(2)
            typ='ol' if marks[0]=='#' else 'ul'
            if not list_stack or list_stack[-1]!=typ:
                close_lists(); out.append(f'<{typ}>'); list_stack.append(typ)
            out.append('<li>'+render_inline(content)+'</li>'); continue
        para.append(st)
    flush_para(); close_lists()
    body='\n'.join(out)
    if refs:
        body += '<section class="references"><h2>References</h2><ol>' + ''.join('<li>'+render_inline(replace_templates(r))+'</li>' for r in refs) + '</ol></section>'
    body=restore_protected(body)
    body=re.sub(r'\[\[((?:File|Image):[^\n]*?)\]\]',lambda m: render_file(m.group(1)),body,flags=re.I)
    # Legacy stack instructions are obsolete after replacing stacks with Commons links.
    if 'ct-stack-link' in body:
        body=re.sub(r'(?i)To move through the images,.*?(?:stack\.|loaded\.)','',body)
    # Final cleanup of raw brace expressions and obsolete inputboxes
    body=re.sub(r'<inputbox>.*?</inputbox>','',body,flags=re.S|re.I)
    return body

converted=[]
for p in pages:
    converted.append({
        'title':p['title'],
        'slug':p['rawTitle'],
        'redirect':p['redirect'],
        'html':render_wikitext(p['wikitext'],p['title'])
    })

(OUT/'src/data/pages.json').write_text(json.dumps(converted,ensure_ascii=False,indent=2))
(OUT/'src/data/site.json').write_text(json.dumps({'siteName':'Radlines - Radiology Guidelines','aboutLabel':'About','aboutHref':'/Radlines:About'},indent=2))

# ---------- Astro project ----------
(OUT/'package.json').write_text('''{
  "name": "radlines-astro",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {"dev":"astro dev","build":"astro build","preview":"astro preview"},
  "dependencies": {"astro":"^5.18.2"}
}\n''')
(OUT/'astro.config.mjs').write_text("import { defineConfig } from 'astro/config';\nexport default defineConfig({ trailingSlash: 'never' });\n")

(OUT/'src/components/SiteHeader.astro').write_text('''---
import site from '../data/site.json';
---
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="/Main">{site.siteName}</a>
    <nav aria-label="Site navigation">
      <a href={site.aboutHref}>{site.aboutLabel}</a>
    </nav>
  </div>
</header>
<style>
.site-header{border-bottom:1px solid #d9dee5;background:#fff;position:sticky;top:0;z-index:20}
.header-inner{max-width:1180px;margin:0 auto;padding:15px 22px;display:flex;align-items:center;justify-content:space-between;gap:24px}
.brand{font-size:1.15rem;font-weight:700;letter-spacing:-.01em;color:#172033;text-decoration:none}
nav a{color:#334155;text-decoration:none;font-weight:500} nav a:hover,.brand:hover{text-decoration:underline}
@media(max-width:600px){.header-inner{padding:13px 16px}.brand{font-size:1rem}}
</style>
''')

(OUT/'src/layouts/RadlinesLayout.astro').write_text('''---
import SiteHeader from '../components/SiteHeader.astro';
const { title } = Astro.props;
---
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="description" content={`Radlines radiology guideline: ${title}`} />
<title>{title === 'Main' ? 'Radlines - Radiology Guidelines' : `${title} | Radlines`}</title>
<style is:global>
:root{font-family:Arial,Helvetica,sans-serif;color:#1f2937;background:#fff;line-height:1.55}
*{box-sizing:border-box} body{margin:0} a{color:#2563a6} img{max-width:100%;height:auto}
main{max-width:1180px;margin:0 auto;padding:28px 22px 60px}.article{max-width:1000px}.article>h1{font-size:2rem;line-height:1.2;margin:0 0 24px;border-bottom:1px solid #d7dde5;padding-bottom:12px}
h2{font-size:1.45rem;margin-top:2rem;border-bottom:1px solid #e5e7eb;padding-bottom:.25rem}h3{font-size:1.2rem;margin-top:1.6rem}p{max-width:78ch}
ul,ol{padding-left:1.6rem}li{margin:.2rem 0}figure{margin:1.2rem 0;display:inline-block;vertical-align:top;max-width:100%}figcaption{font-size:.9rem;color:#4b5563;max-width:45rem}.gallery{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start;margin:1.25rem 0}.gallery figure{margin:0;max-width:340px}.gallery img{max-height:320px;width:auto;object-fit:contain}.authors{background:#f7f8fa;border-left:4px solid #94a3b8;padding:.65rem .85rem;margin:0 0 1.4rem}.further{font-style:italic}.ct-stack-link{padding:1rem;border:1px solid #cbd5e1;background:#f8fafc;border-radius:6px;margin:1rem 0}.missing-media{padding:.55rem .8rem;background:#fff8e6;border-left:3px solid #d7a43b}.missing-media span{color:#6b7280;font-size:.9rem}.table-wrap{overflow-x:auto;margin:1rem 0}table{border-collapse:collapse;min-width:420px}th,td{border:1px solid #cbd5e1;padding:.5rem .65rem;text-align:left}th{background:#f1f5f9}.references{font-size:.92rem}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f1f5f9;padding:.1em .3em;border-radius:3px;white-space:pre-wrap}.ref{font-size:.75em}.legacy-note{font-size:.85rem;color:#64748b;margin-top:3rem;border-top:1px solid #e5e7eb;padding-top:1rem}
@media(max-width:600px){main{padding:22px 16px 48px}.article>h1{font-size:1.65rem}.gallery{display:block}.gallery figure{margin:0 0 18px;max-width:100%}}
</style>
</head>
<body><SiteHeader/><main><article class="article"><slot /></article></main></body>
</html>
''')

(OUT/'src/pages/[...slug].astro').write_text('''---
import RadlinesLayout from '../layouts/RadlinesLayout.astro';
import pages from '../data/pages.json';
export function getStaticPaths() {
  return pages.map((page) => ({ params: { slug: page.slug }, props: { page } }));
}
const { page } = Astro.props;
---
<RadlinesLayout title={page.title}>
  {page.title !== 'Main' && <h1>{page.title}</h1>}
  <div set:html={page.html} />
</RadlinesLayout>
''')

(OUT/'src/pages/index.astro').write_text('''---
return Astro.redirect('/Main');
---
''')

readme=f'''# Radlines Astro conversion\n\nGenerated from the supplied MediaWiki backup.\n\n## Run\n\n```bash\nnpm install\nnpm run dev\n```\n\nThen open `http://localhost:4321/Main`.\n\n## Shared top section\n\nThe site-wide header lives in `src/components/SiteHeader.astro`. The text and About URL are stored in `src/data/site.json`. All pages use `src/layouts/RadlinesLayout.astro`, so changing the header once changes every page.\n\n## Content architecture\n\nThe {len(converted)} main-namespace wiki pages were converted into `src/data/pages.json`. Astro generates static routes through `src/pages/[...slug].astro`.\n\n## Scrollable CT/image stacks\n\nLegacy MediaWiki `Imagestack` content is replaced by a prominent Wikimedia Commons link rather than emulating the old JavaScript viewer.\n\n## Code tags with braces\n\nText inside legacy `<code>` tags is HTML-escaped during conversion, including explicit escaping of `{{` and `}}`, so Astro does not parse brace content as expressions if the content is later inlined into `.astro` files.\n\n## Media\n\nCurrent uploaded files from the MediaWiki `images/` tree were copied to `public/media/`. Deleted files, archived revisions, and generated thumbnails were intentionally excluded. Missing files are linked to their Wikimedia Commons file page.\n\n## Conversion note\n\nThis is a static conversion. MediaWiki editing, talk pages, Lua modules, account management, and other server-side wiki functions are not reproduced. Complex legacy templates are simplified to their useful rendered content where possible.\n'''
(OUT/'README.md').write_text(readme)

# Conversion audit
raw_templates=sum(len(re.findall(r'\{\{',p['wikitext'])) for p in pages)
code_tags=sum(len(re.findall(r'<code\b',p['wikitext'],re.I)) for p in pages)
ct_pages=[p['title'] for p in pages if re.search(r'Imagestack|image\s*stack|scrollable',p['wikitext'],re.I)]
report={'pages':len(converted),'media_files':len(list((OUT/'public/media').iterdir())),'legacy_template_openings':raw_templates,'code_tags':code_tags,'ct_related_pages':ct_pages}
(OUT/'CONVERSION_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
