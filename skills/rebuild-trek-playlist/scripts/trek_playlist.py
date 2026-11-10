#!/usr/bin/env python3
"""Rebuild the chronological Star Trek Jellyfin playlist.

Read-only until `rename`/`create`. No delete call exists in this file.
"""
import argparse
import difflib
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

GUIDE_URL = 'https://www.startrekviewingguide.com/lo-fi-print-ready-listing.html'
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130 Safari/537.36')
DEFAULT_URL = 'http://localhost:8096'
DEFAULT_PLAYLIST = 'Chronological Star Trek'
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
LEGACY_STATE_ROOT = os.path.join(
    os.environ.get('XDG_STATE_HOME', os.path.expanduser('~/.local/state')),
    'trek-playlist')

SERIES = {
    'TOS': 'Star Trek',
    'TAS': 'Star Trek: The Animated Series',
    'TNG': 'Star Trek: The Next Generation',
    'DS9': 'Star Trek: Deep Space Nine',
    'VOY': 'Star Trek: Voyager',
    'ENT': 'Star Trek: Enterprise',
    'DIS': 'Star Trek: Discovery',
    'DSC': 'Star Trek: Discovery',
    'SHO': 'Star Trek: Short Treks',
    'PIC': 'Star Trek: Picard',
    'LDS': 'Star Trek: Lower Decks',
    'PRO': 'Star Trek: Prodigy',
    'SNW': 'Star Trek: Strange New Worlds',
    'SFA': 'Star Trek: Starfleet Academy',
}

# Starts empty. Decisions are recorded per server with `trek_playlist.py decide`,
# so nothing about one library is baked into the code.
SEED_DECISIONS = {}

ROMAN = {'i': '1', 'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6',
         'vii': '7', 'viii': '8', 'ix': '9', 'x': '10'}
WORDS = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
         'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10'}


# ---------------------------------------------------------------- matching

def norm(s):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.casefold().replace('&', ' and ')
    s = s.replace("'", '').replace('\u2019', '')
    s = re.sub(r'^part\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s*:\s*', '', s)
    s = re.sub(r'\b(part|pt)\s*\.?\s*(i{1,3}|iv|v|vi{0,3}|ix|x|\d+)\b',
               lambda m: ' ' + ROMAN.get(m.group(2), m.group(2)) + ' ', s)
    s = re.sub(r'\b(ii|iii|iv|vi|vii|viii|ix|x)\b',
               lambda m: ROMAN.get(m.group(1), m.group(1)), s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if s.startswith('the '):
        s = s[4:]
    return s


def partkey(s):
    n = norm(s)
    m = re.search(r'(?:^| )(i|ii|iii|iv|v|vi|vii|viii|ix|x)$', n)
    if m and len(n) > len(m.group(1)) + 1:
        return n[:m.start()].strip(), int(ROMAN[m.group(1)])
    m = re.search(r'(?:^| )(\d{1,2})$', n)
    if m and len(n) > len(m.group(1)) + 2:
        return n[:m.start()].strip(), int(m.group(1))
    return n, None


def combined(name):
    m = re.match(r'^(.*?) \((\d)\) / .* \((\d)\)$', name)
    if m:
        return norm(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def prepare(inventory):
    for entry in inventory.values():
        for e in entry['episodes']:
            e['_norm'] = norm(e['Name'])
            e['_base'], e['_part'] = partkey(e['Name'])
            comb = combined(e['Name'])
            e['_comb'] = comb[0] if comb else None
    return inventory


def row_key(row):
    if row['code'] == 'Film':
        return 'Film|' + row['title']
    return '%s|%s|%s' % (row['code'], row['season'], row['ep'])


FLOOR = 0.55          # below this no candidate is worth reporting
LOW_CONF = {'episode': 0.80, 'film': 0.90}   # below this a match is a guess


def build_proposal(scored, tier, floor=FLOOR):
    """Best candidate plus the next two, from (score, item) pairs."""
    picked = [(s, e) for s, e in scored if s >= floor][:3]
    return [dict(item=e['Id'], title=e['Name'],
                 season=e.get('ParentIndexNumber'), ep=e.get('IndexNumber'),
                 tier=tier, score='%.2f' % s) for s, e in picked]


def match_rows(rows, inventory, decisions):
    """Return one record per canonical position."""
    index = {name: entry['episodes'] for name, entry in inventory.items()}
    films = inventory.get('__films__', {}).get('episodes', [])
    out = []
    for pos, row in enumerate(rows, 1):
        rec = dict(pos=pos, chap=row['chap'], code=row['code'],
                   season=row['season'], ep=row['ep'], title=row['title'],
                   units=row['units'], status='', tier='', items=[],
                   libtitle='', libseason=None, libep=None, conf='', note='',
                   proposal=None, low_conf=False)
        decision = decisions.get(row_key(row))
        if decision:
            if decision.get('exclude'):
                rec.update(status='EXCLUDED', tier='decision', conf='1.00',
                           note=decision.get('note', 'excluded by decision'))
            else:
                item = decision['item']
                eps = index.get(SERIES.get(row['code'], ''), [])
                known = any(e['Id'] == item for e in eps) or any(
                    e['Id'] == item for e in films)
                if not known:
                    rec.update(status='MISSING', tier='decision', conf='0.00',
                               note='decision item %s is not in the library' % item)
                else:
                    rec.update(status='MATCHED', tier='decision', items=[item],
                               conf='1.00', note=decision.get('note', 'decision'))
            out.append(rec)
            continue

        if row['code'] == 'Film':
            cands = [f for f in films if f['Name'].strip() == row['title'].strip()]
            tier = 'exact'
            if not cands and row['title'] == 'Star Trek (2009)':
                cands = [f for f in films if f['Name'] == 'Star Trek']
                tier = 'exact'
            if not cands:
                cands = [f for f in films if norm(f['Name']) == norm(row['title'])]
                tier = 'normalized'
            if not cands:
                scored = sorted(((ratio(norm(row['title']), norm(f['Name'])), f)
                                 for f in films), reverse=True, key=lambda x: x[0])
                if scored and scored[0][0] >= 0.90:
                    if len(scored) == 1 or scored[1][0] < scored[0][0]:
                        cands = [scored[0][1]]
                        tier = 'fuzzy'
                    else:
                        rivals = [f for s, f in scored if s >= 0.90]
                        rec.update(status='AMBIGUOUS', tier='fuzzy',
                                   conf='%.2f' % scored[0][0],
                                   note='; '.join('%s %s' % (c['Name'], c['Id'])
                                                  for c in rivals))
                        rec['proposal'] = build_proposal(scored, 'fuzzy')
                        out.append(rec)
                        continue
                elif scored:
                    rec['proposal'] = build_proposal(scored, 'fuzzy')
                    rec['low_conf'] = True
            if len(cands) == 1:
                rec.update(status='MATCHED', tier=tier, items=[cands[0]['Id']],
                           libtitle=cands[0]['Name'], conf='1.00')
            elif cands:
                rec.update(status='AMBIGUOUS', tier=tier, conf='1.00',
                           note='; '.join('%s %s' % (c['Name'], c['Id']) for c in cands))
            else:
                rec.update(status='MISSING', tier='none', conf='0.00',
                           note='no film match')
            out.append(rec)
            continue

        sname = SERIES.get(row['code'])
        if sname is None:
            raise SystemExit('unknown series code %r: add it to SERIES' % row['code'])
        cands = index.get(sname, [])
        tb, tp = partkey(row['title'])
        tn = norm(row['title'])
        c = []
        tier = 'exact'
        conf = '1.00'

        multi = re.search(r'parts? (\d+) and (\d+)', row['title'], re.I)
        if multi:
            mb = norm(re.sub(r',?\s*parts? \d+ and \d+.*$', '', row['title'], flags=re.I))
            c = [e for e in cands if e['_comb'] == mb]
            tier = 'normalized+combined'
            if not c:
                c = [e for e in cands if e['_base'] == mb]
                tier = 'normalized+single'
        if not c:
            c = [e for e in cands if e['Name'].strip() == row['title'].strip()]
        if not c:
            c = [e for e in cands if e['_norm'] == tn]
            tier = 'normalized'
        if not c:
            c = [e for e in cands if e['_base'] == tb and e['_part'] == tp]
            tier = 'normalized+part'
        if not c and tp is not None:
            c = [e for e in cands if e['_base'] == tb and e['_part'] is None]
            tier = 'normalized+part-omitted'
        if not c and tp is None:
            c = [e for e in cands if e['_base'] == tb]
            tier = 'normalized+library-part'
        if not c:
            c = [e for e in cands if e['_comb'] == tb]
            tier = 'normalized+combined'
        if not c:
            c = [e for e in cands
                 if (e['_norm'].startswith(tn + ' ') or tn.startswith(e['_norm'] + ' '))
                 and len(tn) >= 4]
            if c:
                tier = 'normalized+prefix'
                conf = '0.95'
        if not c:
            scored = sorted(((ratio(tn, e['_norm']), e) for e in cands),
                            reverse=True, key=lambda x: x[0])
            best = scored[0] if scored else (0, None)
            tier = 'fuzzy'
            conf = '%.2f' % best[0]
            same = [e for s, e in scored if s >= 0.80
                    and e.get('ParentIndexNumber') == row['season']
                    and e.get('IndexNumber') == row['ep']]
            if len(same) == 1:
                c = [same[0]]
            elif best[0] >= 0.80 and len([1 for s, _ in scored if s >= 0.80]) == 1:
                c = [best[1]]
            else:
                cand = scored[:3] if best[0] >= 0.55 else []
                rec.update(status='AMBIGUOUS' if cand else 'MISSING', tier=tier,
                           conf=conf,
                           note='; '.join('%s [S%sE%s] %.2f %s' % (
                               e['Name'], e.get('ParentIndexNumber'),
                               e.get('IndexNumber'), s, e['Id'])
                               for s, e in cand) or 'no candidate >= 0.55')
                if cand:
                    rec['proposal'] = build_proposal(cand, tier)
                    rec['low_conf'] = best[0] < LOW_CONF['episode']
                out.append(rec)
                continue

        if len(c) > 1:
            if row['season'] is not None:
                cs = [e for e in c if e.get('ParentIndexNumber') == row['season']]
                if cs:
                    c = cs
            if len(c) > 1:
                ps = sorted([e for e in c if e['_part'] is not None],
                            key=lambda e: e['_part'])
                if row['units'] == 2 and len(ps) == 2 and \
                        ps[0]['_part'] == 1 and ps[1]['_part'] == 2:
                    c = ps
                    tier = 'units-expansion'
                elif tp is None:
                    p1 = [e for e in c if e['_part'] == 1]
                    if len(p1) == 1:
                        c = p1
                        tier = 'normalized+implicit-part1'
                if len(c) > 1 and tier != 'units-expansion':
                    rec.update(status='AMBIGUOUS', tier=tier, conf=conf,
                               note='; '.join('%s [S%sE%s] %s' % (
                                   e['Name'], e.get('ParentIndexNumber'),
                                   e.get('IndexNumber'), e['Id']) for e in c))
                    rec['proposal'] = build_proposal(
                        [(float(conf), e) for e in c], tier)
                    out.append(rec)
                    continue
        if len(c) > 1:
            es = sorted(c, key=lambda e: (e['_part'] or 0))
            rec.update(status='MATCHED', tier=tier, conf=conf,
                       items=[e['Id'] for e in es],
                       libtitle=' | '.join(e['Name'] for e in es),
                       libseason=es[0].get('ParentIndexNumber'),
                       libep=es[0].get('IndexNumber'),
                       note='canonical 2 units -> 2 library files')
            out.append(rec)
            continue

        e = c[0]
        notes = []
        if row['season'] is not None and e.get('ParentIndexNumber') != row['season']:
            notes.append('canon S%s vs lib S%s' % (row['season'], e.get('ParentIndexNumber')))
        if row['ep'] is not None and e.get('IndexNumber') != row['ep']:
            notes.append('canon E%s vs lib E%s' % (row['ep'], e.get('IndexNumber')))
        if e['_comb']:
            notes.append('lib file combines parts')
        if tier == 'fuzzy':
            notes.append('fuzzy ' + conf)
        if tier == 'normalized+library-part':
            notes.append('lib title adds part number')
        if tier == 'normalized+prefix':
            notes.append('lib title extended')
        if tier == 'normalized+implicit-part1':
            notes.append('canon omits Part 1')
        rec.update(status='MATCHED', tier=tier, items=[e['Id']], libtitle=e['Name'],
                   libseason=e.get('ParentIndexNumber'), libep=e.get('IndexNumber'),
                   conf=conf, note='; '.join(notes))
        out.append(rec)
    return out


def playlist_ids(records):
    """Flatten to playlist entries, collapsing consecutive duplicates."""
    ids = []
    collapsed = []
    for rec in records:
        for item in rec['items']:
            if ids and ids[-1] == item:
                collapsed.append(rec['pos'])
                continue
            ids.append(item)
    return ids, collapsed


# ---------------------------------------------------------------- guide

TOKEN = re.compile(
    r'<section class="ck-chapter" data-chapter="([^"]*)"'
    r'|<li class="ck"[^>]*data-id="([^"]*)"[^>]*data-units="(\d+)"[^>]*>(.*?)</li>',
    re.S)
SE = re.compile(r'S(\d+)\s*·\s*E(\d+)(?:[–-](\d+))?')


def parse_guide(text):
    rows = []
    chap = ''
    for m in TOKEN.finditer(text):
        if m.group(1) is not None:
            chap = m.group(1)
            continue
        sid, units, body = m.group(2), int(m.group(3)), m.group(4)
        code = re.search(r'ck-code">([^<]*)', body).group(1).strip()
        se = re.search(r'ck-se">([^<]*)', body).group(1).strip()
        title = html.unescape(
            re.sub(r'<[^>]+>', '', re.search(r'ck-t">(.*?)</span>', body, re.S).group(1))
        ).strip()
        sm = SE.match(se)
        season = int(sm.group(1)) if sm else None
        ep = int(sm.group(2)) if sm else None
        rows.append(dict(chap=chap, code=code, season=season, ep=ep,
                         title=title, sid=sid, units=units))
    bad = [r for r in rows if not r['code'] or not r['title']]
    if bad:
        raise SystemExit('guide parse: %d rows without code/title' % len(bad))
    return rows


def fetch_guide(url=GUIDE_URL):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode('utf-8')


# ---------------------------------------------------------------- jellyfin

class Api:
    def __init__(self, base, key):
        self.base = base.rstrip('/')
        self.key = key

    def _req(self, path, method='GET', body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Authorization': 'MediaBrowser Token="%s"' % self.key,
                   'User-Agent': UA}
        if data:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode('utf-8', 'replace')
            raise SystemExit('%s %s -> %s %s' % (method, path, e.code, detail))

    def get(self, path, **params):
        if params:
            path += '?' + urllib.parse.urlencode(params)
        return self._req(path)[1]

    def post(self, path, body):
        return self._req(path, 'POST', body)


def authenticate(base, username, password):
    """Log in as a user and return (access_token, user_id). The token stays in memory."""
    body = json.dumps({'Username': username, 'Pw': password}).encode()
    req = urllib.request.Request(
        base.rstrip('/') + '/Users/AuthenticateByName', data=body,
        headers={'Content-Type': 'application/json',
                 'Authorization': 'MediaBrowser Client="rebuild-trek-playlist", '
                                  'Device="cli", DeviceId="rebuild-trek-playlist", '
                                  'Version="0.1"',
                 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit('login failed for %r: HTTP %s' % (username, e.code))
    except urllib.error.URLError as e:
        raise SystemExit('login failed for %r: %s' % (username, e.reason))
    token = payload.get('AccessToken')
    uid = (payload.get('User') or {}).get('Id')
    if not token or not uid:
        raise SystemExit('login for %r returned no token or user id' % username)
    return token, uid


def folders(api, collection_type):
    """All libraries of a type: a server can hold several (e.g. Anime + TV Shows)."""
    found = [f for f in api.get('/Library/MediaFolders')['Items']
             if f.get('CollectionType') == collection_type]
    if not found:
        raise SystemExit('no library with CollectionType=%r' % collection_type)
    return found


def page_cap(items, limit, what):
    """A full page may mean more rows exist: stop rather than truncate silently."""
    if len(items) >= limit:
        raise SystemExit('%s returned %d rows, its limit; raise the limit or page it'
                         % (what, limit))
    return items


def discover(api):
    tv_folders = folders(api, 'tvshows')
    movies = folders(api, 'movies')[0]['Id']
    series = {}
    for folder in tv_folders:
        rows = api.get('/Items', ParentId=folder['Id'], Recursive='true',
                       IncludeItemTypes='Series', limit=400,
                       Fields='Path,ProviderIds')['Items']
        for s in page_cap(rows, 400, 'series read'):
            series[s['Id']] = s
    inventory = {}
    for s in series.values():
        if not s['Name'].startswith('Star Trek'):
            continue
        eps = page_cap(api.get('/Items', ParentId=s['Id'], Recursive='true',
                                IncludeItemTypes='Episode', limit=500,
                                Fields='Path,ProviderIds')['Items'],
                       500, 'episode read for %s' % s['Name'])
        inventory[s['Name']] = {'Id': s['Id'],
                                'ProviderIds': s.get('ProviderIds', {}),
                                'episodes': eps}
    films = page_cap(api.get('/Items', ParentId=movies, Recursive='true',
                              IncludeItemTypes='Movie', searchTerm='Star Trek',
                              limit=100, Fields='Path,ProviderIds')['Items'],
                     100, 'film read')
    inventory['__films__'] = {'Id': movies, 'ProviderIds': {}, 'episodes': films}
    return prepare(inventory)


def resolve_user(api, wanted=None):
    if wanted and re.fullmatch(r'[0-9a-fA-F]{32}', wanted):
        return wanted
    users = api.get('/Users')
    if wanted:
        for u in users:
            if wanted in (u['Id'], u['Name']):
                return u['Id']
        raise SystemExit('user %r not found' % wanted)
    admins = [u for u in users if u.get('Policy', {}).get('IsAdministrator')]
    if not admins:
        raise SystemExit('no administrator found')
    return admins[0]['Id']


def find_playlist(api, name):
    items = page_cap(api.get('/Items', IncludeItemTypes='Playlist', Recursive='true',
                             limit=200)['Items'], 200, 'playlist read')
    found = [i for i in items if i['Name'] == name and i.get('Type') == 'Playlist']
    if len(found) > 1:
        raise SystemExit('%d playlists named %r: %s' % (
            len(found), name, ', '.join(i['Id'] for i in found)))
    return found[0] if found else None


def playlist_items(api, pid, user_id):
    out = []
    start = 0
    while True:
        r = api.get('/Playlists/%s/Items' % pid, userId=user_id, startIndex=start,
                    limit=200, Fields='Name,Path,Type,MediaType,SeriesId,'
                                      'ParentIndexNumber,IndexNumber')
        batch = r['Items']
        out += batch
        if not batch or len(out) >= r['TotalRecordCount']:
            return out
        start = len(out)


def item_name(api, user_id, item_id):
    return api.get('/Users/%s/Items/%s' % (user_id, item_id)).get('Name')


# ---------------------------------------------------------------- state

def platform_state_dir():
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/AppData/Local')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'trek-playlist')


def skill_state_dir():
    """The skill's own refs/ directory, when the install is writable."""
    refs = os.path.join(SKILL_ROOT, 'refs')
    probe = os.path.join(refs, '.write-probe')
    try:
        os.makedirs(refs, exist_ok=True)
        with open(probe, 'w') as fh:
            fh.write('')
        os.remove(probe)
        return refs
    except OSError:
        return None


def default_state_dir():
    env = os.environ.get('TREK_STATE_DIR')
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return skill_state_dir() or platform_state_dir()


def migrate_legacy_state(state_dir, legacy=None):
    """Copy decisions and plan state from the old ~/.local/state directory."""
    legacy = legacy or LEGACY_STATE_ROOT
    if os.path.realpath(legacy) == os.path.realpath(state_dir) or not os.path.isdir(legacy):
        return []
    copied = []
    os.makedirs(state_dir, exist_ok=True)
    for name in sorted(os.listdir(legacy)):
        if not name.endswith('.json'):
            continue
        dst = os.path.join(state_dir, name)
        if os.path.exists(dst):
            continue
        shutil.copy2(os.path.join(legacy, name), dst)
        copied.append(name)
    return copied


def state_path(state_dir, playlist):
    slug = re.sub(r'[^a-z0-9]+', '-', playlist.lower()).strip('-')
    return os.path.join(state_dir, slug + '.json')


def decisions_path(state_dir):
    return os.path.join(state_dir, 'decisions.json')


def load_decisions(state_dir):
    path = decisions_path(state_dir)
    if not os.path.exists(path):
        os.makedirs(state_dir, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(SEED_DECISIONS, f, indent=2)
        return dict(SEED_DECISIONS)
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------- diff

def diff_sequences(current, intended):
    sm = difflib.SequenceMatcher(a=current, b=intended, autojunk=False)
    ops = sm.get_opcodes()
    added = [intended[j] for tag, _, _, j1, j2 in ops
             if tag in ('insert', 'replace') for j in range(j1, j2)]
    removed = [current[i] for tag, i1, i2, _, _ in ops
               if tag in ('delete', 'replace') for i in range(i1, i2)]
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops
                  if tag != 'equal')
    first = next((j1 + 1 for tag, _, _, j1, j2 in ops if tag != 'equal'), None)
    return dict(added=added, removed=removed, changed=changed,
                first_divergence=first)


# ---------------------------------------------------------------- links

def gap_links(row, inventory):
    links = []
    if row['code'] == 'Film':
        q = urllib.parse.quote_plus(row['title'])
        links.append(('TMDB', 'https://www.themoviedb.org/search?query=' + q))
        links.append(('IMDb', 'https://www.imdb.com/find/?q=' + q))
    else:
        sname = SERIES.get(row['code'])
        entry = inventory.get(sname) if sname else None
        tvdb = (entry or {}).get('ProviderIds', {}).get('Tvdb')
        if tvdb:
            links.append(('TVDB', 'https://thetvdb.com/dereferrer/series/' + tvdb))
        q = urllib.parse.quote_plus('%s S%sE%s' % (sname, row['season'], row['ep']))
        links.append(('IMDb', 'https://www.imdb.com/find/?q=' + q))
    links.append(('Guide', GUIDE_URL))
    return links


def review_entry(rec):
    """One queue row, as printed and as JSON."""
    return dict(position=rec['pos'], key=row_key(rec), title=rec['title'],
                season=rec['season'], ep=rec['ep'], status=rec['status'],
                tier=rec['tier'], low_confidence=rec['low_conf'],
                proposal=rec['proposal'])


def review_command(rec):
    return "decide --key '%s' --item %s" % (row_key(rec), rec['proposal'][0]['item'])


def print_review(rec):
    head = 'GAP %d %s S%sE%s %s [%s]' % (rec['pos'], rec['code'], rec['season'],
                                         rec['ep'], rec['title'], rec['status'])
    if rec['low_conf']:
        head += ' low confidence'
    print(head)
    for cand in rec['proposal'] or []:
        print('  proposal: %r S%sE%s %s %s id=%s'
              % (cand['title'], cand['season'], cand['ep'], cand['tier'],
                 cand['score'], cand['item']))
    if rec['proposal']:
        print('  %s' % review_command(rec))
        print("  or exclude: decide --key '%s' --exclude --note 'why'"
              % row_key(rec))
    else:
        print('  no candidate above %.2f; see the report links' % FLOOR)


# ---------------------------------------------------------------- commands

def repo_root():
    return os.getcwd()


def cmd_plan(args):
    decisions = load_decisions(args.state_dir)
    if args.site_file:
        with open(args.site_file, encoding='utf-8') as fh:
            rows = parse_guide(fh.read())
    else:
        rows = parse_guide(fetch_guide(args.guide_url))
    if len(rows) < 500:
        raise SystemExit('guide returned only %d positions; page layout changed?'
                         % len(rows))
    if args.inventory_file:
        with open(args.inventory_file) as fh:
            inventory = prepare(json.load(fh))
    else:
        inventory = discover(Api(args.url, args.api_key))

    records = match_rows(rows, inventory, decisions)
    ids, collapsed = playlist_ids(records)
    user = resolve_user(Api(args.url, args.api_key), args.user)
    api = Api(args.url, args.api_key)
    pl = find_playlist(api, args.playlist)
    current = playlist_items(api, pl['Id'], user) if pl else []
    current_ids = [i['Id'] for i in current]

    gaps = [r for r in records if r['status'] in ('MISSING', 'AMBIGUOUS')]
    excluded = [r for r in records if r['status'] == 'EXCLUDED']
    if gaps:
        verdict = 'BLOCKED'
    elif pl is None:
        verdict = 'NO_PLAYLIST'
    elif current_ids == ids:
        verdict = 'IDENTICAL'
    else:
        verdict = 'DIFFERENT'
    diff = diff_sequences(current_ids, ids)

    now = datetime.now()
    report = os.path.join(args.report_dir,
                          'rebuild-%s.md' % now.strftime('%Y-%m-%d-%H%M'))
    write_report(report, args, verdict, records, ids, collapsed, gaps,
                 excluded, pl, current_ids, diff, inventory, now)
    working = os.path.join(args.report_dir, 'playlist.local.md')
    write_working_copy(working, args, records, now)

    guide = [[pos, row_key(row), row['title']] for pos, row in enumerate(rows, 1)]
    state = dict(playlist=args.playlist, phase='planned', verdict=verdict,
                 guide=guide,
                 ts=now.isoformat(timespec='seconds'), report=report,
                 intended=ids, excluded=[r['pos'] for r in excluded],
                 old=None, new=None)
    if pl:
        state['old'] = dict(id=pl['Id'], name=pl['Name'],
                            count=len(current_ids))
    save_json(state_path(args.state_dir, args.playlist), state)

    print('state: %s' % args.state_dir)
    print('verdict: %s' % verdict)
    print('positions: %d  playlist entries: %d  excluded: %d  gaps: %d'
          % (len(records), len(ids), len(excluded), len(gaps)))
    if pl:
        print('playlist: %s (%s), %d entries' % (pl['Name'], pl['Id'], len(current_ids)))
    else:
        print('playlist: none named %r' % args.playlist)
    if verdict == 'DIFFERENT':
        print('changes: %d added, %d removed, %d positions changed; first divergence %s'
              % (len(diff['added']), len(diff['removed']), diff['changed'],
                 diff['first_divergence']))
    if gaps:
        print('review: %d gap(s) to resolve, then run plan again' % len(gaps))
        for r in gaps:
            print_review(r)
    if getattr(args, 'review_json', False):
        print(json.dumps([review_entry(r) for r in gaps], indent=2))
    print('working copy: %s' % working)
    print('report: %s' % report)
    return 0


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def write_report(path, args, verdict, records, ids, collapsed, gaps,
                 excluded, pl, current_ids, diff, inventory, now):
    lines = []
    a = lines.append
    a('# Rebuild report — %s' % now.strftime('%Y-%m-%d %H:%M'))
    a('')
    a('**Server:** `%s` · **Playlist:** `%s` · **Verdict:** **%s**'
      % (args.url, args.playlist, verdict))
    a('')
    a('| Measure | Value |')
    a('|---|---|')
    a('| Canonical positions | %d |' % len(records))
    a('| Playlist entries intended | %d |' % len(ids))
    a('| Excluded by decision | %d |' % len(excluded))
    a('| Gaps (MISSING/AMBIGUOUS) | %d |' % len(gaps))
    a('| Current playlist | %s |' % ('none' if pl is None else '%d entries (%s)' % (len(current_ids), pl['Id'])))
    a('| Collapsed positions | %s |' % (', '.join(map(str, collapsed)) or 'none'))
    if pl:
        a('| Added / removed / changed | %d / %d / %d |'
          % (len(diff['added']), len(diff['removed']), diff['changed']))
        a('| First divergence | %s |' % diff['first_divergence'])
    a('')
    if gaps:
        a('## Gaps')
        a('')
        a('| Pos | Code | S | E | Title | Status | Candidates / links |')
        a('|---|---|---|---|---|---|---|')
        for r in gaps:
            links = ' '.join('[%s](%s)' % (k, u) for k, u in gap_links(r, inventory))
            a('| %d | %s | %s | %s | %s | %s | %s; %s |'
              % (r['pos'], r['code'], r['season'], r['ep'], cell(r['title']),
                 r['status'], cell(r['note']), links))
        a('')
    if excluded:
        a('## Excluded by decision')
        a('')
        for r in excluded:
            a('- %d %s S%sE%s %s — %s' % (r['pos'], r['code'], r['season'],
                                          r['ep'], r['title'], r['note']))
        a('')
    if pl and diff['added']:
        a('## Added (first 20)')
        a('')
        for i in diff['added'][:20]:
            a('- `%s`' % i)
        a('')
    if pl and diff['removed']:
        a('## Removed (first 20)')
        a('')
        for i in diff['removed'][:20]:
            a('- `%s`' % i)
        a('')
    a('## Mapping')
    a('')
    lines += mapping_lines(records)
    a('')
    a('## Planned API calls')
    a('')
    a('1. `POST /Playlists/{id}` `{"Name": "%s (superseded ...)"}` — rename, only after approval.' % args.playlist)
    a('2. `POST /Playlists` `{Name, Ids, UserId, MediaType: "Video"}` — create, only after approval.')
    a('3. `GET /Playlists/{id}/Items` — verification, read-only.')
    a('')
    a('No delete call is made. The superseded playlist is left for manual deletion.')
    a('')
    a('## Verification plan')
    a('')
    a('- Read the new playlist back; assert count, order, uniqueness, Episode/Movie types.')
    a('- Re-read the old playlist; assert it still exists under the superseded name with the same count.')
    a('- Compare every position against the intended list above.')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def mapping_lines(records, review=False):
    head = ('| # | Code | S | E | Canonical title | Library title | Item ID | '
            'Status | Tier | Conf | Notes |')
    rule = '|---|---|---|---|---|---|---|---|---|---|---|'
    if review:
        head = ('| # | Code | S | E | Canonical title | Library title | Item ID | '
                'Status | Tier | Conf | Notes | Review |')
        rule = '|---|---|---|---|---|---|---|---|---|---|---|---|'
    out = [head, rule]
    for r in records:
        row = ('| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |'
               % (r['pos'], r['code'], r['season'], r['ep'], cell(r['title']),
                  cell(r['libtitle']), ', '.join(r['items']), r['status'],
                  r['tier'] or '-', r['conf'], cell(r['note'])))
        if review:
            row = row + ' %s |' % cell(review_cell(r))
        out.append(row)
    return out


def review_cell(rec):
    if not rec['proposal']:
        return '-'
    best = rec['proposal'][0]
    return '%r %s %s id=%s%s' % (best['title'], best['tier'], best['score'],
                                 best['item'], ' (low confidence)' if rec['low_conf'] else '')


def write_working_copy(path, args, records, now):
    lines = ['# Playlist working copy — %s' % now.strftime('%Y-%m-%d %H:%M'), '',
             '**Server:** `%s` · **Playlist:** `%s` · **State:** `%s` · **Guide:** %s'
             % (args.url, args.playlist, args.state_dir, args.guide_url), '']
    lines += mapping_lines(records, review=True)
    lines += ['', 'Overwritten on every plan run. Answers are recorded with `decide`,',
              'not by editing this file.']
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def cmd_decide(args):
    decisions = load_decisions(args.state_dir)
    if args.exclude:
        decisions[args.key] = {'exclude': True, 'note': args.note or 'excluded by decision'}
    else:
        if not args.item:
            raise SystemExit('--item or --exclude required')
        decisions[args.key] = {'item': args.item, 'note': args.note or 'decision'}
    save_json(decisions_path(args.state_dir), decisions)
    print('decision saved: %s -> %s' % (args.key, decisions[args.key]))
    return 0


def cmd_rename(args):
    state = load_state(args)
    if not state.get('old'):
        raise SystemExit('no playlist to rename (verdict %s)' % state.get('verdict'))
    if not args.restore and not args.force and \
            state.get('verdict') not in ('DIFFERENT', 'NO_PLAYLIST'):
        raise SystemExit('plan verdict is %s; nothing to rebuild' % state.get('verdict'))
    if state['phase'] not in ('planned', 'renamed'):
        raise SystemExit('rename not valid at phase %s' % state['phase'])
    api = Api(args.url, args.api_key)
    user = resolve_user(api, args.user)
    old = state['old']
    if args.restore:
        new_name = old['name']
    else:
        new_name = '%s (superseded %s)' % (args.playlist, datetime.now().strftime('%Y-%m-%d %H%M'))
    api.post('/Playlists/' + old['id'], {'Name': new_name})
    actual = item_name(api, user, old['id'])
    state['phase'] = 'planned' if args.restore else 'renamed'
    if args.force:
        state['rehearsal'] = True
    state['old']['current_name'] = actual
    save_json(state_path(args.state_dir, args.playlist), state)
    print('renamed %s -> %r' % (old['id'], actual))
    return 0


def cmd_create(args):
    state = load_state(args)
    if state.get('verdict') not in ('DIFFERENT', 'NO_PLAYLIST') and \
            not state.get('rehearsal'):
        raise SystemExit('plan verdict is %s; nothing to create' % state.get('verdict'))
    if not state.get('intended'):
        raise SystemExit('plan has no intended entries')
    if state['phase'] != 'renamed' and state.get('old'):
        raise SystemExit('rename the old playlist first (phase %s)' % state['phase'])
    if state.get('new'):
        raise SystemExit('a new playlist was already created: %s' % state['new']['id'])
    api = Api(args.url, args.api_key)
    user = resolve_user(api, args.user)
    status, body = api.post('/Playlists', {
        'Name': args.playlist,
        'Ids': state['intended'],
        'UserId': user,
        'MediaType': 'Video',
    })
    pid = (body or {}).get('Id')
    if not pid:
        raise SystemExit('create returned no playlist id (HTTP %s)' % status)
    state['phase'] = 'created'
    state['new'] = dict(id=pid, name=args.playlist)
    save_json(state_path(args.state_dir, args.playlist), state)
    print('created %s with %d ids (HTTP %s)' % (pid, len(state['intended']), status))
    return 0


def guide_observation(state, args):
    """Compare the guide's current order with the one the plan recorded."""
    recorded = state.get('guide') or []
    if not recorded:
        return 'guide check skipped: the state records no order'
    try:
        if args.site_file:
            with open(args.site_file, encoding='utf-8') as fh:
                rows = parse_guide(fh.read())
        else:
            rows = parse_guide(fetch_guide(args.guide_url))
    except (SystemExit, OSError, urllib.error.URLError) as exc:
        return 'guide check skipped: %s' % exc
    now = [[pos, row_key(row), row['title']] for pos, row in enumerate(rows, 1)]
    moved = [n for n, (before, after) in enumerate(zip(recorded, now), 1)
             if before[1] != after[1]]
    if len(recorded) != len(now):
        return ('guide check: %d positions recorded, %d now; moved at %s'
                % (len(recorded), len(now), moved[:20] or 'none'))
    if moved:
        return 'guide check: %d position(s) moved; %s' % (len(moved), moved[:20])
    return 'guide check: the guide still lists the recorded order'


def cmd_verify(args):
    state = load_state(args)
    if not state.get('new'):
        raise SystemExit('no new playlist in state; '
                         'run rename --restore to put the old playlist back')
    api = Api(args.url, args.api_key)
    user = resolve_user(api, args.user)
    want = state['intended']
    items = playlist_items(api, state['new']['id'], user)
    got = [i['Id'] for i in items]
    checks = {
        'count': len(got) == len(want),
        'order': got == want,
        'unique': len(set(got)) == len(got),
        'types': all(i.get('Type') in ('Episode', 'Movie') for i in items),
        'named': all(i.get('Name') for i in items),
    }
    first_bad = next((n + 1 for n, (x, y) in enumerate(zip(got, want)) if x != y), None)
    old_ok = True
    if state.get('old'):
        try:
            old_items = playlist_items(api, state['old']['id'], user)
            old_ok = len(old_items) == state['old']['count']
        except SystemExit:
            old_ok = False
    checks['old_intact'] = old_ok
    ok = all(checks.values())
    observation = guide_observation(state, args)
    with open(state['report'], 'a') as f:
        f.write('\n## Verification — %s\n\n' % datetime.now().strftime('%Y-%m-%d %H:%M'))
        f.write('New playlist `%s` read back with %d entries.\n\n'
                % (state['new']['id'], len(got)))
        f.write('| Check | Result |\n|---|---|\n')
        for k, v in checks.items():
            f.write('| %s | %s |\n' % (k, 'PASS' if v else 'FAIL'))
        if first_bad:
            f.write('\nFirst divergent position: %d\n' % first_bad)
        f.write('\n%s\n' % observation)
        f.write('\n%s\n' % ('**PASS**' if ok else '**FAIL**'))
    print('verify: %s' % ('PASS' if ok else 'FAIL'))
    for k, v in checks.items():
        print('  %-11s %s' % (k, 'PASS' if v else 'FAIL'))
    if first_bad:
        print('  first divergent position: %d' % first_bad)
    print('  %s' % observation)
    if ok:
        print('Old playlist is left in place for manual deletion after you check the new one.')
    return 0 if ok else 1


def load_state(args):
    path = state_path(args.state_dir, args.playlist)
    if not os.path.exists(path):
        raise SystemExit('no plan state at %s; run plan first' % path)
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default=os.environ.get('JELLYFIN_URL', DEFAULT_URL))
    p.add_argument('--playlist', default=os.environ.get('TREK_PLAYLIST', DEFAULT_PLAYLIST))
    p.add_argument('--user', default=os.environ.get('JELLYFIN_USER'))
    p.add_argument('--password', default=os.environ.get('JELLYFIN_PASSWORD'),
                   help='user password; prefer the JELLYFIN_PASSWORD environment variable')
    p.add_argument('--state-dir')
    p.add_argument('--report-dir', default=os.path.join(repo_root(), 'reports'))
    p.add_argument('--site-file')
    p.add_argument('--inventory-file')
    p.add_argument('--guide-url', default=GUIDE_URL)
    sub = p.add_subparsers(dest='cmd', required=True)
    pl = sub.add_parser('plan')
    pl.add_argument('--review-json', action='store_true',
                    help='print the review queue as JSON on stdout')
    d = sub.add_parser('decide')
    d.add_argument('--key', required=True, help='CODE|S|E or Film|title')
    d.add_argument('--item')
    d.add_argument('--exclude', action='store_true')
    d.add_argument('--note')
    r = sub.add_parser('rename')
    r.add_argument('--restore', action='store_true')
    r.add_argument('--force', action='store_true',
                   help='rename even when the plan is IDENTICAL (rehearsal)')
    sub.add_parser('create')
    sub.add_parser('verify')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.state_dir = args.state_dir or default_state_dir()
    copied = migrate_legacy_state(args.state_dir)
    if copied:
        print('state: copied %s from %s' % (', '.join(copied), LEGACY_STATE_ROOT))
    args.api_key = os.environ.get('JELLYFIN_API_KEY')
    if args.cmd == 'decide':
        return cmd_decide(args)
    if args.password:
        if not args.user:
            raise SystemExit('JELLYFIN_USER (username) is required when a password is set')
        args.api_key, args.user = authenticate(args.url, args.user, args.password)
    if not args.api_key:
        raise SystemExit('JELLYFIN_API_KEY is not set (or set JELLYFIN_USER and JELLYFIN_PASSWORD)')
    return {'plan': cmd_plan, 'decide': cmd_decide, 'rename': cmd_rename,
            'create': cmd_create, 'verify': cmd_verify}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
