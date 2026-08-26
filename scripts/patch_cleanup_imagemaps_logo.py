from pathlib import Path
from bs4 import BeautifulSoup
import json,re,urllib.parse,shutil,subprocess,html

ROOT=Path('/mnt/data/radlines-finalfix/radlines-astro-converted')
PAGES=ROOT/'src/data/pages.json'
pages=json.loads(PAGES.read_text())

# Merge missing non-redirect pages from the raw-wikitext conversion as fallback.
oldraw=Path('/mnt/data/oldraw/radlines-astro-converted/src/data/pages.json')
old=json.loads(oldraw.read_text())
existing_titles={p['title'] for p in pages}
merged=[]
for p in old:
    if p['title'] not in existing_titles and not p.get('redirect'):
        pages.append(p); existing_titles.add(p['title']); merged.append(p['title'])

# Ensure About is present and routeable.
if 'Radlines:About' not in existing_titles:
    # fallback from any source if possible
    q=next((p for p in old if p['title']=='Radlines:About'),None)
    if q:
        pages.append(q); existing_titles.add(q['title']); merged.append(q['title'])

# Redirect map from DB parser output; include imagemap-only semantic aliases.
out=subprocess.check_output(['python','/mnt/data/parse_redirects.py'],text=True)
redirects={}
for line in out.splitlines()[1:]:
    if '=>' in line:
        a,b=line.split('=>',1); redirects[a.strip()]=b.strip()
redirects.update({
    'Male reproductive system':'Reproductive system',
    'Female reproductive system':'Reproductive system',
})

def resolve_title(title):
    frag=''
    if '#' in title:
        title,frag=title.split('#',1)
    cur=title.replace('_',' '); seen=set()
    while cur in redirects and cur not in seen:
        seen.add(cur); nxt=redirects[cur]
        if '#' in nxt:
            cur,nf=nxt.split('#',1)
            if not frag: frag=nf
        else: cur=nxt
    return cur + (('#'+frag) if frag else '')

def slug(title):
    frag=''
    if '#' in title:
        title,frag=title.split('#',1)
    s='/' + urllib.parse.quote(title.replace(' ','_'),safe=':_()-,.')
    if frag: s += '#' + urllib.parse.quote(frag.replace(' ','_'),safe=':_()-,.')
    return s

# Add uploaded logo and hip image.
shutil.copy2('/mnt/data/Logo favicon(1).svg', ROOT/'public/media/Radlines_logo.svg')
shutil.copy2('/mnt/data/X-ray_of_measurements_in_hip_dysplasia,_with_numbers.jpg', ROOT/'public/media/X-ray_of_measurements_in_hip_dysplasia,_with_numbers.jpg')

# Preserve Main map source before page cleanup.
main_page=next(p for p in pages if p['title']=='Main')
main_soup=BeautifulSoup(main_page['html'],'html.parser')
main_map=None
for m in main_soup.find_all('map'):
    name=m.get('name','')
    img=main_soup.find('img',attrs={'usemap':'#'+name})
    if img and 'Anatomy_image_for_main_menu.png' in img.get('src',''):
        main_map=m; break

# Commons rewrite helpers.
thumb_re=re.compile(r'/wikipedia/commons/thumb/[0-9a-f]/[0-9a-f]{2}/([^/]+)/',re.I)
def infer_file(src,anchor=None):
    if anchor:
        m=re.search(r'commons\.wikimedia\.org/wiki/File:([^?#]+)',anchor)
        if m:return urllib.parse.unquote(m.group(1)).replace(' ','_')
    m=thumb_re.search(src)
    if m:return urllib.parse.unquote(m.group(1))
    m=re.search(r'/wikipedia/commons/[0-9a-f]/[0-9a-f]{2}/([^/?#]+)',src,re.I)
    if m:return urllib.parse.unquote(m.group(1))
    return None
def commons_src(fname,width):
    u='https://commons.wikimedia.org/wiki/Special:Redirect/file/'+urllib.parse.quote(fname,safe='()_,-.')
    if width: u += '?width='+str(width)
    return u

# Available route titles/slugs after merge, excluding redirects as middle pages.
redirect_sources=set(redirects.keys())
# Do not remove aliases not actually DB pages from available titles unless absent naturally.
actual_redirect_sources=set(k for k in redirects if k not in ('Male reproductive system','Female reproductive system'))
pages=[p for p in pages if p['title'] not in actual_redirect_sources or p['title']=='Main']
route_slugs={p['slug'] for p in pages}
route_paths={'/'+urllib.parse.quote(s,safe=':_()-,.') for s in route_slugs}
# Explicit final targets that have anchors.

stats={'desc_removed':0,'edit_removed':0,'area_redirects':0,'link_redirects':0,'selflinks':0,'dead_links_removed':0,'dead_areas_removed':0,'commons_rewritten':0,'thumbs_sized':0}

for p in pages:
    soup=BeautifulSoup(p.get('html',''),'html.parser')

    # Remove MediaWiki ImageMap description/info icon and its wrapper.
    for a in list(soup.find_all('a')):
        if a.get('title')=='About this image' or a.find('img',alt='About this image') or '/extensions/ImageMap/resources/desc-20.png' in str(a):
            par=a.parent
            a.decompose(); stats['desc_removed']+=1
            if par and par.name=='div' and not par.get_text(strip=True) and not par.find(True): par.decompose()
    for img in list(soup.find_all('img')):
        if img.get('alt')=='About this image' or '/extensions/ImageMap/resources/desc-20.png' in img.get('src',''):
            par=img.parent; img.decompose(); stats['desc_removed']+=1
            if par and par.name=='a' and not par.get_text(strip=True) and not par.find(True): par.decompose()

    # Remove all edit-section/template edit controls.
    for el in list(soup.select('.mw-editsection')):
        el.decompose(); stats['edit_removed']+=1
    for a in list(soup.find_all('a',href=True)):
        if 'action=edit' in a['href'] and a.get_text(' ',strip=True).lower()=='edit':
            a.decompose(); stats['edit_removed']+=1

    # Stable Commons image URLs and local URL normalization.
    for img in soup.find_all('img'):
        src=img.get('src','')
        if src.startswith('/media%2F'):
            img['src']='/media/'+urllib.parse.unquote(src[len('/media%2F'):])
            src=img['src']
        if 'upload.wikimedia.org/wikipedia/commons/' in src:
            anc=img.find_parent('a',href=True)
            fname=infer_file(src,anc['href'] if anc else None)
            if fname:
                try:w=int(img.get('width') or 0)
                except:w=0
                req=max(320,min(1600,w*2)) if w else 800
                img['src']=commons_src(fname,req); img.attrs.pop('srcset',None);img['referrerpolicy']='no-referrer';stats['commons_rewritten']+=1

    # Force thumb/caption boxes to image width, not caption width.
    for inner in soup.select('.thumbinner'):
        img=inner.find('img')
        if not img: continue
        w=img.get('width')
        if not w:
            st=img.get('style',''); m=re.search(r'width\s*:\s*(\d+)px',st)
            w=m.group(1) if m else None
        try:w=int(w)
        except:w=0
        if w:
            oldstyle=inner.get('style','').rstrip('; ')
            inner['style']=(oldstyle+';' if oldstyle else '')+f'width:{w}px;box-sizing:content-box;'
            cap=inner.select_one('.thumbcaption')
            if cap:
                cs=cap.get('style','').rstrip('; ')
                cap['style']=(cs+';' if cs else '')+f'width:{w}px;max-width:{w}px;box-sizing:border-box;'
            stats['thumbs_sized']+=1

    current_base='/' + urllib.parse.quote(p['slug'],safe=':_()-,.')

    # Canonicalize ordinary internal links, current-page links, then dead links.
    for a in list(soup.find_all('a',href=True)):
        href=a['href']
        if not href.startswith('/') or href.startswith('//') or href.startswith('/media/') or href.startswith('/extensions/'):
            continue
        # Utility/query links are external legacy behavior; leave unless edit controls handled above.
        if '?' in href: continue
        path,hashmark,frag=href.partition('#')
        raw=urllib.parse.unquote(path.lstrip('/')).replace('_',' ')
        resolved=resolve_title(raw + (('#'+frag) if hashmark else ''))
        new=slug(resolved)
        if new!=href:
            a['href']=new; href=new; stats['link_redirects']+=1
        newpath=href.split('#',1)[0]
        # current article self-link -> visible black non-link anchor
        if newpath==current_base:
            a.attrs.pop('href',None)
            cl=list(a.get('class',[]));
            for c in ('mw-selflink','selflink'):
                if c not in cl: cl.append(c)
            a['class']=cl; stats['selflinks']+=1; continue
        # remove links without final target while preserving children/text
        if newpath not in route_paths and newpath not in ('/Main','/Radlines:About'):
            a.unwrap(); stats['dead_links_removed']+=1

    # Canonicalize HTML imagemap areas too (previous passes only handled <a>).
    for area in soup.find_all('area',href=True):
        href=area['href']
        if not href.startswith('/'): continue
        path,hashmark,frag=href.partition('#')
        raw=urllib.parse.unquote(path.lstrip('/')).replace('_',' ')
        resolved=resolve_title(raw + (('#'+frag) if hashmark else ''))
        new=slug(resolved)
        if new!=href:
            area['href']=new; stats['area_redirects']+=1
        if new.split('#',1)[0] not in route_paths and new.split('#',1)[0] not in ('/Main','/Radlines:About'):
            area.attrs.pop('href',None); stats['dead_areas_removed']+=1

    p['html']=''.join(str(x) for x in soup.contents)

# Recompute route paths after dropping redirects and merge.
PAGES.write_text(json.dumps(pages,ensure_ascii=False,indent=2))

# Generate dedicated MainPage from authoritative first imagemap. Put larger regions first,
# smaller/specific regions last so specific hotspots win in SVG painting/hit-testing order.
hotspots=[]
if main_map:
    for a in main_map.find_all('area'):
        coords=[int(x) for x in a.get('coords','').split(',') if x.strip().isdigit()]
        if a.get('shape','rect')!='rect' or len(coords)!=4: continue
        x1,y1,x2,y2=coords; href=a.get('href',''); label=a.get('alt') or a.get('title') or ''
        if href.startswith('/'):
            raw=urllib.parse.unquote(href[1:]).replace('_',' ')
            href=slug(resolve_title(raw))
        # Respect prior rule: don't create a link to a nonexistent target.
        clickable=href and href.split('#',1)[0] in route_paths
        hotspots.append({'label':label,'href':href if clickable else None,'x':x1,'y':y1,'w':x2-x1,'h':y2-y1,'area':(x2-x1)*(y2-y1)})
hotspots.sort(key=lambda h:-h['area'])

hs_lines=[]
for h in hotspots:
    if h['href']:
        hs_lines.append('  '+repr({k:h[k] for k in ('label','href','x','y','w','h')}).replace("'",'"')+',')
# Use JSON-like JS valid objects; Python booleans absent.
hs='\n'.join(hs_lines)
main_component=f'''---
const hotspots = [
{hs}
];
---
<section class="welcome">
  <h1>Radiology guidelines</h1>
  <p>Open access guidelines and reference material for radiologists.</p>
</section>
<div class="main-grid">
  <section class="panel locations">
    <h2>Locations and modalities</h2>
    <div class="anatomy-map-wrap">
      <svg class="anatomy-map" viewBox="0 0 337 1540" role="img" aria-labelledby="anatomy-map-title anatomy-map-desc" preserveAspectRatio="xMidYMin meet">
        <title id="anatomy-map-title">Interactive Radlines anatomy menu</title>
        <desc id="anatomy-map-desc">Select a labeled body region or imaging modality to open its Radlines page.</desc>
        <image href="/media/Anatomy_image_for_main_menu.png" x="0" y="0" width="337" height="1540" />
        {{hotspots.map((h) => (
          <a href={{h.href}} aria-label={{h.label}}>
            <title>{{h.label}}</title>
            <rect class="hotspot" x={{h.x}} y={{h.y}} width={{h.w}} height={{h.h}} rx="1" />
          </a>
        ))}}
      </svg>
      <p class="map-help">Click any labeled area of the image.</p>
    </div>
  </section>
  <div class="side-column">
    <section class="panel"><h2><a href="/Abdomen_and_pelvis">Abdomen and pelvis</a></h2></section>
    <section class="panel"><h2>Emergencies</h2><div class="link-list"><a href="/Contrast_medium_reaction">Contrast medium reaction</a></div></section>
    <section class="panel"><h2><a href="/Radlines:About">About Radlines</a></h2><p>Radlines is an international, collaborative, non-profit, ad-free and open-access radiology reference.</p></section>
  </div>
</div>
<style>
.welcome{{text-align:center;padding:24px 12px 18px}}.welcome h1{{border:0;margin:0 0 6px;font-size:1.9rem}}.welcome p{{margin:0 auto;color:#52606d}}
.main-grid{{display:grid;grid-template-columns:minmax(355px,430px) minmax(280px,1fr);gap:22px;align-items:start;margin:18px auto;max-width:900px}}.side-column{{display:flex;flex-direction:column;gap:18px;position:sticky;top:82px}}
.panel{{border:1px solid #d9e0e8;border-radius:8px;background:#fff;padding:18px}}.panel h2{{font-size:1.1rem;margin:0 0 14px;padding:0 0 9px;border-bottom:1px solid #e4e9ef}}.panel h2 a{{text-decoration:none}}.link-list{{display:flex;flex-direction:column;gap:7px}}.link-list a{{text-decoration:none}}
.locations{{text-align:center}}.anatomy-map-wrap{{width:100%;max-width:380px;margin:0 auto}}.anatomy-map{{display:block;width:100%;height:auto;margin:0 auto}}.hotspot{{fill:transparent;stroke:transparent;stroke-width:1;pointer-events:all}}.hotspot:hover,.hotspot:focus{{fill:rgba(37,99,166,.13);stroke:#2563a6}}.map-help{{margin:.65rem auto 0;color:#64748b;font-size:.84rem;text-align:center}}
@media(max-width:760px){{.main-grid{{grid-template-columns:1fr;max-width:430px}}.side-column{{position:static}}.panel{{padding:14px}}.anatomy-map-wrap{{max-width:337px}}}}
</style>
'''
(ROOT/'src/components/MainPage.astro').write_text(main_component)

# Shared header with provided logo.
header='''---\nimport site from '../data/site.json';\n---\n<header class="site-header">\n  <div class="header-inner">\n    <a class="brand" href="/Main"><img class="brand-logo" src="/media/Radlines_logo.svg" alt="" aria-hidden="true" /> <span>{site.siteName}</span></a>\n    <nav aria-label="Site navigation"><a href={site.aboutHref}>{site.aboutLabel}</a></nav>\n  </div>\n</header>\n<style>\n.site-header{border-bottom:1px solid #d9dee5;background:#fff;position:sticky;top:0;z-index:20}\n.header-inner{max-width:1180px;margin:0 auto;padding:10px 22px;display:flex;align-items:center;justify-content:space-between;gap:24px}\n.brand{font-size:1.15rem;font-weight:700;letter-spacing:-.01em;color:#172033;text-decoration:none;display:flex;align-items:center;gap:10px}.brand-logo{width:36px;height:36px;flex:0 0 36px;display:block}\nnav a{color:#334155;text-decoration:none;font-weight:500} nav a:hover,.brand:hover{text-decoration:underline}\n@media(max-width:600px){.header-inner{padding:8px 16px}.brand{font-size:1rem}.brand-logo{width:32px;height:32px;flex-basis:32px}}\n</style>\n'''
(ROOT/'src/components/SiteHeader.astro').write_text(header)

# Global style adjustments: current article links black; dt semicolon-subheads bold; thumb rules.
layout=ROOT/'src/layouts/RadlinesLayout.astro'
s=layout.read_text()
insert='''.mw-selflink,.selflink{color:#1f2937!important;text-decoration:none!important;cursor:default}\ndt{font-weight:700}\n.thumbinner{overflow:hidden}.thumbcaption{overflow-wrap:anywhere}\n'''
if '.mw-selflink,.selflink' not in s:
    s=s.replace('/* MediaWiki-rendered article compatibility */','/* MediaWiki-rendered article compatibility */\n'+insert)
layout.write_text(s)

# Update site data.
site={'siteName':'Radlines - Radiology Guidelines','aboutLabel':'About','aboutHref':'/Radlines:About'}
(ROOT/'src/data/site.json').write_text(json.dumps(site,indent=2))

# Reproducibility report.
report={**stats,'fallback_pages_restored':merged,'redirects_known':len(redirects),'main_hotspots_clickable':sum(1 for h in hotspots if h['href']),'main_hotspots_total':len(hotspots),'main_unclickable':[h['label'] for h in hotspots if not h['href']]}
(ROOT/'PATCH_REPORT_2.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
