#!/usr/bin/env python3
"""전국 지역·키워드 선불폰 페이지 생성기.

입력
  data/admin_areas.txt  시도 [시군구] [구] 읍면동 한 줄씩 (전국 행정구역)
  data/stores.csv       방문 개통 매장 (공개 필드만)
  data/keywords.csv     키워드 → 대상 지역
출력
  <슬러그>/index.html   사이트주소/<슬러그>/ 로 열리는 페이지
  sitemap.xml
  data/pages.csv        생성된 페이지 목록 (슬러그, 종류, 지역)

실행: python3 tools/build_regions.py   (저장소 루트에서, 표준 라이브러리만 사용)
"""
import csv
import html
import json
import os
import re
import shutil
from collections import defaultdict, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = 'https://YOUR-DOMAIN'      # 실제 도메인이 정해지면 여기만 바꾸고 다시 실행
UPDATED = '2026년 10월 27일'
UPDATED_ISO = '2026-10-27'
SUFFIX = '선불폰'
MANIFEST = os.path.join(ROOT, 'data', 'pages.csv')

SIDO_SHORT = OrderedDict([
    ('서울특별시', '서울'), ('부산광역시', '부산'), ('대구광역시', '대구'), ('인천광역시', '인천'),
    ('전남광주통합특별시', '전남광주'), ('대전광역시', '대전'), ('울산광역시', '울산'),
    ('세종특별자치시', '세종'), ('경기도', '경기'), ('강원특별자치도', '강원'), ('충청북도', '충북'),
    ('충청남도', '충남'), ('전북특별자치도', '전북'), ('경상북도', '경북'), ('경상남도', '경남'),
    ('제주특별자치도', '제주'),
])
# stores.csv 의 시도 약칭 → 정식 시도명 (광주·전남은 2026-07-01 전남광주통합특별시로 통합)
STORE_SIDO = {'서울': '서울특별시', '부산': '부산광역시', '대구': '대구광역시', '인천': '인천광역시',
              '광주': '전남광주통합특별시', '전남': '전남광주통합특별시', '대전': '대전광역시',
              '울산': '울산광역시', '세종': '세종특별자치시', '경기': '경기도', '강원': '강원특별자치도',
              '충북': '충청북도', '충남': '충청남도', '전북': '전북특별자치도', '경북': '경상북도',
              '경남': '경상남도', '제주': '제주특별자치도'}
# 육지로 맞닿은 시도 (가까운 매장이 같은 시도에 없을 때 찾는 순서)
ADJ = {
    '서울': ['경기', '인천'], '인천': ['서울', '경기'], '경기': ['서울', '인천', '강원', '충북', '충남'],
    '강원': ['경기', '충북', '경북'], '충북': ['경기', '충남', '세종', '대전', '강원', '경북', '전북'],
    '충남': ['세종', '대전', '경기', '충북', '전북'], '세종': ['대전', '충남', '충북'],
    '대전': ['세종', '충남', '충북'], '전북': ['충남', '전남광주', '경남', '충북', '경북'],
    '전남광주': ['전북', '경남'], '경북': ['대구', '울산', '경남', '강원', '충북', '전북'],
    '대구': ['경북', '경남'], '울산': ['부산', '경남', '경북'], '부산': ['경남', '울산'],
    '경남': ['부산', '울산', '대구', '경북', '전남광주', '전북'], '제주': ['전남광주'],
}
METRO = {'부산', '대구', '인천', '대전', '울산'}
GWANGJU_GU = {'동구', '서구', '남구', '북구', '광산구'}   # 전남광주통합특별시 안의 옛 광주광역시 자치구
# 2026-10 이전에 이미 쓰던 주소(옛 region/*.html). 해당 지역 페이지가 이 주소를 그대로 쓰도록 고정한다.
LEGACY_SLUG = {
    ('대전광역시', '동구'): '대전동구선불폰', ('부산광역시', '동구'): '부산동구선불폰',
    ('대구광역시', '동구'): '대구동구선불폰', ('울산광역시', '남구'): '울산남구선불폰',
    ('전남광주통합특별시', '서구'): '광주서구선불폰', ('서울특별시', '중구'): '중구선불폰',
    ('서울특별시', '강서구'): '강서구선불폰',
}


def esc(s):
    return html.escape(s, quote=True)


def strip_unit(name):
    """수원시→수원, 홍천군→홍천 (한 글자가 남으면 그대로)."""
    if len(name) > 2 and name[-1] in '시군':
        return name[:-1]
    return name


class Node:
    def __init__(self, name, parent, level):
        self.name = name
        self.parent = parent
        self.level = level            # 0 시도, 1 시군구, 2 일반구, 3 읍면동
        self.children = OrderedDict()
        self.slug = None
        self.stores = []

    @property
    def path(self):
        n, out = self, []
        while n:
            out.append(n.name)
            n = n.parent
        return tuple(reversed(out))

    @property
    def sido(self):
        n = self
        while n.parent:
            n = n.parent
        return n

    @property
    def sido_short(self):
        return SIDO_SHORT[self.sido.name]

    def ancestors(self):
        n, out = self.parent, []
        while n:
            out.append(n)
            n = n.parent
        return list(reversed(out))

    def label(self):
        """사람이 읽는 짧은 지역명: 서울 광진구 중곡동"""
        parts = [self.sido_short] + [a.name for a in self.ancestors()[1:]] + ([self.name] if self.level else [])
        return ' '.join(parts)

    def short(self):
        return self.name if self.level else self.sido_short

    def leaves(self):
        if not self.children:
            return [self]
        out = []
        for c in self.children.values():
            out.extend(c.leaves())
        return out

    def all_stores(self):
        out = list(self.stores)
        for c in self.children.values():
            out.extend(c.all_stores())
        return out


# ---------------------------------------------------------------- 데이터 읽기
def load_tree():
    sidos = OrderedDict()
    for line in open(os.path.join(ROOT, 'data', 'admin_areas.txt'), encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        t = line.split(' ')
        if t[0] not in sidos:
            sidos[t[0]] = Node(t[0], None, 0)
        node = sidos[t[0]]
        for i, name in enumerate(t[1:]):
            is_last = i == len(t) - 2
            if is_last and name[-1] in '동읍면가':
                level = 3
            elif node.level == 1 and name.endswith('구'):
                level = 2
            else:
                level = 1
            if name not in node.children:
                node.children[name] = Node(name, node, level)
            node = node.children[name]
    return sidos


def find(sidos, path_str):
    parts = path_str.split('>')
    node = sidos.get(parts[0])
    for p in parts[1:]:
        if node is None:
            break
        node = node.children.get(p)
    if node is None:
        raise SystemExit('지역을 찾지 못했습니다: ' + path_str)
    return node


def load_stores(sidos):
    stores = []
    with open(os.path.join(ROOT, 'data', 'stores.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            sido = sidos[STORE_SIDO[r['시도']]]
            node = sido
            for part in r['시군구'].split(' '):
                node = node.children[part]
            sg = node
            # 매장 동(법정동)을 목록의 행정동에 연결: 행정동 데이터에서 숫자·'본'을 뺀 이름으로 찾음
            cands = [r['동']] + [re.sub(r'[0-9·]+|본(?=동$)', '', x).strip() for x in r['행정동'].split('·')]
            cands += [re.sub(r'제(?=동$)', '', c) for c in cands] + [r['행정동'].replace('·', '')]
            target = None
            for c in cands:
                c = re.sub(r'(\d+)', '', c)
                if not c.endswith(('동', '읍', '면')):
                    c += '동'
                hit = sg.children.get(c) or next((g.children[c] for g in sg.children.values() if c in g.children), None)
                if hit:
                    target = hit
                    break
            s = {'name': r['공개표기명'], 'sido': sido.name, 'sg': r['시군구'], 'dong': r['동'],
                 'near': r['맞닿은동'], 'node': target or sg}
            (target or sg).stores.append(s)
            stores.append(s)
    return stores


def load_keywords(sidos):
    out = []
    with open(os.path.join(ROOT, 'data', 'keywords.csv'), encoding='utf-8') as f:
        rows = list(csv.reader(f))[1:]
    for r in rows:
        if not r or not r[0].strip():
            continue
        kw, targets, note = r[0].strip(), r[1].strip(), (r[2].strip() if len(r) > 2 else '')
        out.append({'kw': kw, 'targets': [find(sidos, p) for p in targets.split('|')], 'note': note})
    return out


# ---------------------------------------------------------------- 주소(슬러그) 정하기
def sg_base(n):
    s = n.sido_short
    if n.level == 1:
        if (n.sido.name, n.name) in LEGACY_SLUG:
            return [LEGACY_SLUG[(n.sido.name, n.name)][:-len(SUFFIX)]]
        if s == '전남광주' and n.name in GWANGJU_GU:
            return ['광주' + n.name]
        if s in METRO:
            return [s + n.name]
        if s == '서울':
            return [n.name, s + n.name]
        # 시·군은 '시/군'을 떼고, 겹치면 시도를 붙인다 (경기 광주시 ↔ 광주, 강원/경남 고성군)
        return [strip_unit(n.name), s + strip_unit(n.name), s + n.name]
    # 일반구: 수원팔달구, 창원의창구
    return [strip_unit(n.parent.name) + n.name, s + strip_unit(n.parent.name) + n.name]


def dong_base(n):
    p = n.parent
    if p.level == 0:   # 세종처럼 시군구 없이 바로 읍면동
        return [n.name, p.sido_short + n.name]
    pl = p.slug[:-len(SUFFIX)]   # 상위 지역 주소를 그대로 앞에 붙임: 강남구신사동, 울산동구화정동, 수원팔달구매교동
    return [n.name, pl + n.name, n.sido_short + pl + n.name]


def assign_slugs(sidos, keywords):
    nodes = []
    for sd in sidos.values():
        sd.slug = sd.sido_short + SUFFIX
        stack = list(sd.children.values())
        while stack:
            n = stack.pop(0)
            nodes.append(n)
            stack[0:0] = list(n.children.values())
    taken = {sd.slug for sd in sidos.values()}
    # 키워드 주소는 미리 잡아 둔다. 키워드 대상 지역 하나가 원래 그 주소를 받을 때만 그 지역이 쓸 수 있다.
    reserved = {}
    for k in keywords:
        reserved[k['kw']] = k['targets'][0] if len(k['targets']) == 1 else None
    # 시군구·일반구 먼저 (기존 주소 우선), 그다음 읍면동
    for group in ([n for n in nodes if n.level in (1, 2)], [n for n in nodes if n.level == 3]):
        options = {n: [b + SUFFIX for b in (sg_base(n) if n.level < 3 else dong_base(n))] for n in group}
        idx = {n: 0 for n in group}
        while True:
            count = defaultdict(int)
            for n in group:
                count[options[n][idx[n]]] += 1
            clash = [n for n in group if count[options[n][idx[n]]] > 1 or options[n][idx[n]] in taken
                     or (options[n][idx[n]] in reserved and reserved[options[n][idx[n]]] is not n)]
            if not clash:
                break
            moved = False
            for n in clash:
                if idx[n] + 1 < len(options[n]):
                    idx[n] += 1
                    moved = True
            if not moved:
                raise SystemExit('주소가 겹칩니다: ' + ', '.join(options[n][idx[n]] for n in clash[:5]))
        for n in group:
            n.slug = options[n][idx[n]]
            taken.add(n.slug)
    by_slug = {}
    for n in list(sidos.values()) + nodes:
        by_slug[n.slug] = n
    # 키워드가 지역 페이지 주소와 같으면 그 페이지에 합친다
    kw_pages = []
    for k in keywords:
        node = by_slug.get(k['kw'])
        if node is not None and len(k['targets']) == 1 and node is k['targets'][0]:
            node.keyword = k
            continue
        if node is not None:
            raise SystemExit('키워드 주소가 다른 지역 페이지와 겹칩니다: ' + k['kw'])
        k['slug'] = k['kw']
        kw_pages.append(k)
    return nodes, by_slug, kw_pages


# ---------------------------------------------------------------- 가까운 매장
def nearest(nodes_in, limit=3):
    """같은 동 → 같은 구 → 같은 시군구 → 같은 시도 → 맞닿은 시도 순으로 매장을 찾는다."""
    seen, out = set(), []

    def add(stores, tier):
        for s in stores:
            if id(s) not in seen:
                seen.add(id(s))
                out.append((s, tier))

    for n in nodes_in:
        add(n.all_stores(), '이 지역')
    # 매장 데이터의 '맞닿은 동'에 이 지역(동 이름 또는 시군구 이름)이 나오면 가까운 매장으로 본다
    for n in nodes_in:
        for x in [n] + list(reversed(n.ancestors()[1:])):   # 동 이름이 맞으면 구 이름보다 먼저
            if x.level < 1:
                continue
            add([s for s in ALL_STORES if s['sido'] == n.sido.name and s['near'] and touches(s, x.name)], '맞닿은 지역')
    if len(out) < 1:
        for n in nodes_in:
            for a in reversed(n.ancestors()[1:]):
                add(a.all_stores(), a.name)
                if out:
                    break
    if len(out) < 1:
        sd = nodes_in[0].sido
        add(sd.all_stores(), sd.sido_short)
    if len(out) < 1:
        for short in ADJ.get(nodes_in[0].sido_short, []):
            sido = next(s for s in SIDOS.values() if s.sido_short == short)
            add(sido.all_stores(), '인근 ' + short)
            if out:
                break
    return out[:limit] if limit else out


def touches(store, name):
    """'구의동·능동·군자동, 중랑구 면목동' 같은 맞닿은 동 목록에 name(동·구·시)이 들어 있는지."""
    words = re.split(r'[·,\s]+', store['near'])
    base = re.sub(r'\d+(?=[가동]$)', '', name)
    return name in words or base in [re.sub(r'\d+(?=[가동]$)', '', w) for w in words]


# ---------------------------------------------------------------- HTML 조각
def head(title, desc, slug, extra_ld):
    url = '%s/%s/' % (SITE_URL, slug)
    ld = json.dumps(extra_ld, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    return '''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{t}</title>
<meta name="description" content="{d}">
<meta name="robots" content="index, follow, max-snippet:-1">
<link rel="canonical" href="{u}">
<meta property="og:type" content="article"><meta property="og:site_name" content="루니통신">
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}">
<meta property="og:url" content="{u}"><meta property="og:locale" content="ko_KR">
<link rel="icon" href="../assets/logo.jpg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Black+Han+Sans&family=Gothic+A1:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap">
<link rel="stylesheet" href="../assets/region.css">
<script type="application/ld+json">{ld}</script>
</head>
<body>
<div class="page">
  <nav class="nav">
    <div class="wrap nav-row">
      <a href="../" class="logo"><img class="logo-mark" src="../assets/logo.jpg" alt="루니통신 로고" width="34" height="34">루니통신</a>
      <div class="nav-links">
        <a href="../#how">개통방법</a>
        <a href="../#plans">요금제</a>
        <a href="../stores.html">방문 매장</a>
        <a href="../전국선불폰/">전국 지역</a>
      </div>
      <a href="../#check" class="btn btn-primary btn-sm">지금 개통하기</a>
    </div>
  </nav>
'''.format(t=esc(title), d=esc(desc), u=esc(url), ld=ld)


FOOT = '''
  <footer id="contact">
    <div class="wrap">
      <div class="entity-box">
        <span><strong>루니통신은 앤텔레콤과 계약된 회원이 운영하는 선불폰·선불유심 온라인 인증 판매점입니다.</strong> KT(K망)·LG U+(L망) 선불폰과 선불유심을 취급합니다.</span>
        <span>방문 개통 사무실은 앤텔레콤 개통 센터와 제휴 매장이며, 정확한 위치와 방문 가능 시간은 상담에서 안내합니다.</span>
        <span>알뜰폰 판매점 사전승낙(KAIT) 번호 : 등록 후 기재</span>
        <span class="updated">최종 업데이트 : {u}</span>
      </div>
      <div class="foot-row">
        <span><strong>루니통신</strong> · 대표 김도훈 · 사업자등록번호 000-00-00000 (준비 중) · 통신판매업신고 준비 중</span>
        <div class="foot-links"><a href="../">홈</a><a href="../stores.html">전국 방문 매장</a><a href="../전국선불폰/">전국 지역 찾기</a><a href="../guide/index.html">선불폰 가이드</a><span>전화 상담 준비 중 · 카카오톡 채널 상담 예정</span></div>
      </div>
    </div>
  </footer>
  <div class="sticky-cta" role="region" aria-label="빠른 상담">
    <a href="../#check" class="btn btn-primary">지금 개통 신청</a>
    <a href="../#contact" class="btn btn-ghost">상담 문의</a>
  </div>
</div>
</body>
</html>
'''.format(u=UPDATED)


def crumb(n):
    parts = ['<a href="../">홈</a>']
    for a in n.ancestors():
        parts.append('<a href="../%s/">%s</a>' % (esc(a.slug), esc(a.short())))
    parts.append('<span>%s</span>' % esc(n.short()))
    return '<nav class="crumb" aria-label="현재 위치">' + '<span>›</span>'.join(parts) + '</nav>'


def crumb_ld(n, title_name):
    items = [{'@type': 'ListItem', 'position': 1, 'name': '홈', 'item': SITE_URL + '/'}]
    for a in n.ancestors() + [n]:
        items.append({'@type': 'ListItem', 'position': len(items) + 1, 'name': a.short() + ' 선불폰',
                      'item': '%s/%s/' % (SITE_URL, a.slug)})
    return {'@type': 'BreadcrumbList', 'itemListElement': items}


def store_cards(near, area):
    if not near:
        return ''
    cards = []
    for i, (s, tier) in enumerate(near):
        where = '%s %s %s' % (SIDO_SHORT[s['sido']], s['sg'], s['dong'])
        tag = '가장 가까운 매장' if i == 0 else ('같은 지역' if tier == '이 지역' else tier + ' 매장')
        cards.append('<div class="store%s"><span class="store-tag">%s</span><b>%s</b><span class="where">%s</span>'
                     '<span class="note">정확한 위치·방문 가능 시간은 상담 시 안내</span></div>'
                     % (' store-top' if i == 0 else '', esc(tag), esc(s['name']), esc(where)))
    return '<div class="store-list">' + ''.join(cards) + '</div>'


def hero(n_or_kw_title, eyebrow, lead, chips, near, crumb_html):
    first = ''
    if near:
        s, tier = near[0]
        first = ('<a href="#store" class="hero-store"><span class="hs-k">가장 가까운 방문 매장</span>'
                 '<b>%s</b><span class="hs-w">%s %s %s · 신분증만 가져오세요</span></a>'
                 % (esc(s['name']), esc(SIDO_SHORT[s['sido']]), esc(s['sg']), esc(s['dong'])))
    return '''
  <header class="region-hero kw-hero">
    <div class="wrap">
      {crumb}
      <span class="eyebrow" style="margin-top:22px;">{eb}</span>
      <h1>{h1}</h1>
      <p class="hero-lead">{lead}</p>
      <div class="chips">{chips}</div>
      {first}
      <div class="hero-cta hero-cta-big"><a href="../#check" class="btn btn-primary">지금 개통 신청하기</a><a href="../#contact" class="btn btn-ghost">전화·카톡 상담</a></div>
    </div>
  </header>
'''.format(crumb=crumb_html, eb=esc(eyebrow), h1=n_or_kw_title, lead=lead,
           chips=''.join('<span class="chip">%s</span>' % esc(c) for c in chips), first=first)


def kw_h1(keyword):
    return '<em class="kw">%s</em> <span class="kw-sub">당일개통</span>' % esc(keyword)


SUMMARY = '''
  <section class="band">
    <div class="wrap">
      <div class="sec-head"><span class="eyebrow">한눈에 보기</span><h2>{a} 선불폰 개통 조건 요약</h2></div>
      <div class="sum-scroll"><table class="sum-table"><tbody><tr><th scope="row">준비물 (셀프개통)</th><td>신분증 + 망에 맞는 선불유심 + 간편인증서</td></tr>
<tr><th scope="row">준비물 (방문 개통)</th><td>실물 신분증 + 사용할 휴대폰 + 유심 비용 1만 원</td></tr>
<tr><th scope="row">신용조회</th><td><strong>없음</strong> · 미납·연체·개인회생 중에도 개통</td></tr>
<tr><th scope="row">가장 저렴한 요금제</th><td>월 <strong>12,100원</strong> (L망 LTE기본1) · 요금은 전국 동일</td></tr>
<tr><th scope="row">개통 가능 시간</th><td>신규 08:00~21:50 매일 · 번호이동 10:00~19:50 (일요일 제외)</td></tr>
<tr><th scope="row">{a} 지역 차이</th><td>없음 · 요금과 절차 모두 전국 동일</td></tr></tbody></table></div>
      <div class="links-row" style="margin-top:20px;">
        <a href="../#plans" class="pill">전체 요금제</a><a href="../guide/no-cert.html" class="pill">간편인증서 없이 개통</a>
        <a href="../guide/credit-check.html" class="pill">신용불량자 개통</a><a href="../guide/blocked.html" class="pill">개통이 막힐 때</a>
      </div>
    </div>
  </section>
'''


def faq_section(title, faqs):
    body = ''.join('<details class="faq"><summary>%s</summary><div class="faq-body">%s</div></details>'
                   % (esc(q), esc(a)) for q, a in faqs)
    return '''
  <section class="band band-alt" id="faq">
    <div class="wrap">
      <div class="sec-head"><span class="eyebrow">자주 묻는 질문</span><h2>{t}, 자주 묻는 질문</h2><p>작성·검수 루니통신 개통상담팀 · 기준일 {u}</p></div>
      <div class="faq-list">{b}</div>
    </div>
  </section>
'''.format(t=esc(title), u=UPDATED, b=body)


def faq_ld(faqs):
    return {'@type': 'FAQPage', 'mainEntity': [{'@type': 'Question', 'name': q,
            'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in faqs]}


def links_section(eyebrow, title, links):
    if not links:
        return ''
    return '''
  <section class="band">
    <div class="wrap">
      <div class="sec-head"><span class="eyebrow">{e}</span><h2>{t}</h2></div>
      <div class="links-row">{l}</div>
    </div>
  </section>
'''.format(e=esc(eyebrow), t=esc(title), l=''.join(
        '<a class="pill" href="../%s/">%s</a>' % (esc(slug), esc(text)) for slug, text in links))


def common_faqs(area, near):
    faqs = []
    if near:
        s, tier = near[0]
        if tier == '이 지역':
            faqs.append(('%s에 방문 개통 매장이 있나요?' % area,
                         '네. %s에는 %s이(가) 있습니다. 정확한 위치와 방문 가능 시간은 상담에서 안내해 드립니다.'
                         % (area, '·'.join(x[0]['name'] for x in near if x[1] == '이 지역'))))
        else:
            faqs.append(('%s에서 가장 가까운 방문 개통 매장은 어디인가요?' % area,
                         '%s %s에 있는 %s이(가) 가장 가깝습니다. 간편인증서가 있으면 매장에 가지 않고 %s에서 바로 셀프개통할 수 있어요.'
                         % (SIDO_SHORT[s['sido']], s['sg'], s['name'], area)))
    else:
        faqs.append(('%s 근처에 방문 개통 매장이 있나요?' % area,
                     '%s 근처에는 아직 방문 개통 매장이 없습니다. 신분증과 간편인증서(PASS·카카오·네이버·토스)가 있으면 어디서든 비대면 셀프개통으로 약 10분이면 개통됩니다.' % area))
    faqs += [
        ('%s에서도 오늘 개통되나요?' % area,
         '네. 신분증·간편인증서·호환 유심이 있으면 %s에서도 비대면으로 당일 개통됩니다. 신규가입은 매일 08:00~21:50, 번호이동은 10:00~19:50(일요일 제외)에 진행됩니다.' % area),
        ('미납·연체나 신용불량이어도 %s에서 개통할 수 있나요?' % area,
         '네. 선불폰은 신용조회를 하지 않아 미납·연체, 신용회복, 개인회생·파산 중에도 본인 명의로 개통할 수 있습니다.'),
        ('방문 개통할 때 무엇을 가져가야 하나요?',
         '실물 신분증, 사용할 휴대폰, 유심 비용 1만 원을 준비하면 됩니다. 미성년자는 보호자 동의 서류가 필요하고, 여권만 있는 외국인은 방문 전 상담으로 조건을 확인해 주세요.'),
        ('%s 요금이 다른 지역과 다른가요?' % area,
         '아니요. 요금과 조건은 전국이 같습니다. 가장 저렴한 요금제는 월 12,100원(L망 LTE기본1)이고, 데이터 소진 후에도 속도 제한으로 계속 쓰는 무제한형은 월 39,600원부터입니다.'),
    ]
    return faqs


def near_prose(near):
    out = []
    for s, tier in near:
        if s['near']:
            out.append('<p><strong>%s</strong>은 %s %s %s에 있습니다. 맞닿은 %s에서도 가깝게 방문할 수 있습니다.</p>'
                       % (esc(s['name']), esc(SIDO_SHORT[s['sido']]), esc(s['sg']), esc(s['dong']), esc(s['near'])))
    return ''.join(out)


def store_section(area, near, nodes_in):
    if near:
        tier0 = near[0][1]
        intro = ('간편인증서가 없거나 직접 안내받고 싶다면 아래 매장에서 신분증으로 개통할 수 있습니다.' if tier0 == '이 지역'
                 else '%s에는 아직 방문 매장이 없어, 가장 가까운 매장을 안내해 드려요. 간편인증서가 있다면 방문 없이 셀프개통이 더 빠릅니다.' % area)
        body = store_cards(near, area) + ('<div class="prose" style="margin-top:22px;">%s</div>' % near_prose(near) if near_prose(near) else '')
    else:
        intro = '%s 근처에는 방문 매장이 없어요. 신분증과 간편인증서만 있으면 휴대폰으로 약 10분이면 셀프개통됩니다.' % area
        body = '<div class="callout">제주 등 매장이 먼 지역은 <a href="../#how">비대면 셀프개통</a>이 가장 빠릅니다. 막히는 부분은 상담으로 도와드려요.</div>'
    return '''
  <section class="band band-alt" id="store">
    <div class="wrap">
      <div class="sec-head"><span class="eyebrow">방문 개통</span><h2>{a}에서 방문 개통은 어디서 하나요?</h2>
      <p>{i}</p></div>
      {b}
    </div>
  </section>
'''.format(a=esc(area), i=esc(intro), b=body)


def lead_text(keyword, area_full, near):
    s = ('<strong class="kw-inline">%s</strong> 찾으세요? %s에서 선불폰을 개통하려면 간편인증서가 있으면 비대면 셀프개통으로, '
         % (esc(keyword), esc(area_full)))
    if near:
        st, tier = near[0]
        s += '없으면 %s %s에 있는 <strong>%s</strong>에서 방문 개통하면 됩니다. ' % (esc(st['sg']), esc(st['dong']), esc(st['name']))
    else:
        s += '없으면 상담으로 가장 빠른 방법을 안내받으면 됩니다. '
    return s + '<strong>신용조회 없이</strong> 본인 명의로 <strong>당일 개통</strong>되며 요금과 조건은 전국과 같습니다.'


def desc_text(keyword, area_full, near):
    t = '%s · %s 선불폰 당일개통. 미납·연체·신용불량도 신용조회 없이 본인 명의 개통, 간편인증서가 있으면 약 10분 셀프개통' % (keyword, area_full)
    if near:
        t += ', 없으면 %s 방문 개통' % near[0][0]['name']
    return t + '.'


# ---------------------------------------------------------------- 페이지
def child_links(n):
    return [(c.slug, c.name) for c in n.children.values()]


def area_page(n):
    keyword = getattr(n, 'keyword', None)
    kw = keyword['kw'] if keyword else (n.short() + SUFFIX)
    area_full = n.label()
    area = n.short() if n.level else n.sido_short
    near = nearest([n], limit=3)
    store_count = len(n.all_stores())
    level_name = {0: '시·도', 1: '시·군·구', 2: '구', 3: '읍·면·동'}[n.level]
    eyebrow = '%s · %s' % (area_full, ('방문 개통 매장 %d곳' % store_count) if store_count else '전국 비대면 셀프개통')
    chips = ['신용조회 없음', '미납·연체 OK', '월 12,100원부터', '매일 08:00~21:50']
    if store_count:
        chips.append('방문 매장 %d곳' % store_count)
    title = '%s 당일개통 · %s 선불폰 %s | 루니통신' % (kw, area_full, ('방문 매장 %d곳' % store_count) if store_count else '셀프개통')
    faqs = common_faqs(area, near)
    parts = [hero(kw_h1(kw), eyebrow, lead_text(kw, area_full, near), chips, near, crumb(n)),
             store_section(area, near, [n])]
    if n.children:
        sub = {1: '구·읍·면·동', 0: '시·군·구'}.get(n.level, '읍·면·동')
        if n.level == 1 and all(c.level == 3 for c in n.children.values()):
            sub = '읍·면·동'
        parts.append(links_section('%s 전체' % area, '%s의 %s별 선불폰 안내' % (area, sub), child_links(n)))
    parts.append(SUMMARY.format(a=esc(area)))
    parts.append(faq_section('%s 선불폰' % area, faqs))
    if n.parent:
        sib = [(c.slug, c.name) for c in n.parent.children.values() if c is not n]
        parts.append(links_section('주변 지역', '%s의 다른 지역 선불폰' % n.parent.short(),
                                   [(n.parent.slug, '%s 전체 안내' % n.parent.short())] + sib))
    else:
        parts.append(links_section('전국', '다른 시·도 선불폰', [(s.slug, s.sido_short) for s in SIDOS.values() if s is not n]))
    ld = {'@context': 'https://schema.org', '@graph': [crumb_ld(n, area), faq_ld(faqs)]}
    return head(title, desc_text(kw, area_full, near), n.slug, ld) + ''.join(parts) + FOOT, level_name


def keyword_page(k):
    targets = k['targets']
    first = targets[0]
    kw = k['kw']
    note = k['note']
    area_full = note or ' · '.join(t.label() for t in targets)
    area = kw[:-len(SUFFIX)] if kw.endswith(SUFFIX) else kw
    near = nearest(targets, limit=3)
    store_count = sum(len(t.all_stores()) for t in targets)
    eyebrow = '%s · %s' % (area_full, ('방문 개통 매장 %d곳' % store_count) if store_count else ('가까운 매장 %s' % near[0][0]['name'] if near else '비대면 셀프개통'))
    chips = ['신용조회 없음', '미납·연체 OK', '월 12,100원부터', '매일 08:00~21:50']
    title = '%s 당일개통 · %s 선불폰 개통 안내 | 루니통신' % (kw, area)
    faqs = common_faqs(area, near)
    parts = [hero(kw_h1(kw), eyebrow, lead_text(kw, area_full, near), chips, near,
                  '<nav class="crumb" aria-label="현재 위치"><a href="../">홈</a><span>›</span><a href="../%s/">%s</a><span>›</span><span>%s</span></nav>'
                  % (esc(first.sido.slug), esc(first.sido_short), esc(kw))),
             store_section(area, near, targets)]
    links = []
    for t in targets:
        links.append((t.slug, '%s 지역 안내' % t.label()))
    for t in targets:
        links += child_links(t)
    parts.append(links_section('%s 지역' % area, '%s 지역별 상세 안내' % area, links))
    parts.append(SUMMARY.format(a=esc(area)))
    parts.append(faq_section(kw, faqs))
    up = first.parent or first
    parts.append(links_section('주변 지역', '%s 주변 지역 선불폰' % up.short(),
                               [(up.slug, '%s 전체 안내' % up.short())] + [(c.slug, c.name) for c in up.children.values() if c not in targets][:40]))
    crumb_items = {'@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': '홈', 'item': SITE_URL + '/'},
        {'@type': 'ListItem', 'position': 2, 'name': first.sido_short + ' 선불폰', 'item': '%s/%s/' % (SITE_URL, first.sido.slug)},
        {'@type': 'ListItem', 'position': 3, 'name': kw, 'item': '%s/%s/' % (SITE_URL, kw)}]}
    ld = {'@context': 'https://schema.org', '@graph': [crumb_items, faq_ld(faqs)]}
    return head(title, desc_text(kw, area_full, near), kw, ld) + ''.join(parts) + FOOT


def index_page():
    title = '전국 선불폰 개통 지역 찾기 · 시·도/시·군·구별 안내 | 루니통신'
    desc = '전국 시·도와 시·군·구별 선불폰 당일개통 안내. 미납·연체·신용불량도 신용조회 없이 본인 명의로 개통하고, 가까운 방문 개통 매장을 찾아보세요.'
    blocks = []
    for sd in SIDOS.values():
        items = ''.join('<a class="pill" href="../%s/">%s</a>' % (esc(c.slug), esc(c.name)) for c in sd.children.values())
        blocks.append('<div class="store-group"><h3>%s <a href="../%s/">%s 전체 안내 →</a></h3><div class="links-row">%s</div></div>'
                      % (esc(sd.name), esc(sd.slug), esc(sd.sido_short), items))
    body = '''
  <header class="region-hero kw-hero">
    <div class="wrap">
      <nav class="crumb" aria-label="현재 위치"><a href="../">홈</a><span>›</span><span>전국 지역</span></nav>
      <span class="eyebrow" style="margin-top:22px;">전국 {n}개 지역 안내</span>
      <h1><em class="kw">전국 선불폰</em> <span class="kw-sub">지역 찾기</span></h1>
      <p class="hero-lead">사는 곳을 고르면 그 지역에서 가장 가까운 방문 개통 매장과 개통 방법을 바로 볼 수 있어요. 간편인증서가 있으면 어디서든 약 10분 셀프개통됩니다.</p>
      <div class="hero-cta hero-cta-big"><a href="../#check" class="btn btn-primary">지금 개통 신청하기</a><a href="../stores.html" class="btn btn-ghost">전국 방문 매장 보기</a></div>
    </div>
  </header>
  <section class="band band-alt">
    <div class="wrap">{b}</div>
  </section>
'''.format(n=PAGE_COUNT[0], b=''.join(blocks))
    ld = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': '홈', 'item': SITE_URL + '/'},
        {'@type': 'ListItem', 'position': 2, 'name': '전국 지역', 'item': SITE_URL + '/전국선불폰/'}]}
    return head(title, desc, '전국선불폰', ld) + body + FOOT


# ---------------------------------------------------------------- 쓰기
def write(slug, content):
    d = os.path.join(ROOT, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(content)


def clean_previous():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                d = os.path.join(ROOT, r['slug'])
                if r['slug'] and os.path.isdir(d) and os.path.exists(os.path.join(d, 'index.html')):
                    shutil.rmtree(d)


SIDOS = {}
ALL_STORES = []
PAGE_COUNT = [0]


def main():
    global SIDOS
    SIDOS = load_tree()
    ALL_STORES.extend(load_stores(SIDOS))
    keywords = load_keywords(SIDOS)
    nodes, by_slug, kw_pages = assign_slugs(SIDOS, keywords)
    clean_previous()
    manifest = []
    all_nodes = list(SIDOS.values()) + nodes
    PAGE_COUNT[0] = len(all_nodes) + len(kw_pages)
    for n in all_nodes:
        content, level_name = area_page(n)
        write(n.slug, content)
        manifest.append((n.slug, level_name, ' '.join(n.path), getattr(n, 'keyword', None) and n.keyword['kw'] or ''))
    for k in kw_pages:
        write(k['slug'], keyword_page(k))
        manifest.append((k['slug'], '키워드', ' | '.join(' '.join(t.path) for t in k['targets']), k['kw']))
    write('전국선불폰', index_page())
    manifest.append(('전국선불폰', '전국 목록', '', ''))
    with open(MANIFEST, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['slug', '종류', '지역', '키워드'])
        w.writerows(manifest)
    # sitemap
    static = ['', 'stores.html', 'guide/'] + ['guide/' + g for g in sorted(os.listdir(os.path.join(ROOT, 'guide'))) if g.endswith('.html') and g != 'index.html']
    urls = [SITE_URL + '/' + s for s in static] + ['%s/%s/' % (SITE_URL, m[0]) for m in manifest]
    with open(os.path.join(ROOT, 'sitemap.xml'), 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in urls:
            f.write('  <url><loc>%s</loc><lastmod>%s</lastmod></url>\n' % (esc(u), UPDATED_ISO))
        f.write('</urlset>\n')
    kinds = defaultdict(int)
    for m in manifest:
        kinds[m[1]] += 1
    print('pages:', len(manifest), dict(kinds))


if __name__ == '__main__':
    main()
