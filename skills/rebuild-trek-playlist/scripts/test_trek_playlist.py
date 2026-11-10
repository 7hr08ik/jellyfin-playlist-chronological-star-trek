import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trek_playlist as t


def ep(i, name, s, e):
    return {'Id': i, 'Name': name, 'ParentIndexNumber': s, 'IndexNumber': e}


def inventory(eps, films=()):
    return t.prepare({
        'Star Trek: Voyager': {'Id': 'v', 'ProviderIds': {'Tvdb': '199'}, 'episodes': eps},
        '__films__': {'Id': 'm', 'ProviderIds': {}, 'episodes': list(films)},
    })


def row(code, s, e, title, units=1):
    return dict(chap='c', code=code, season=s, ep=e, title=title, sid='x', units=units)


class Helpers(unittest.TestCase):
    def test_norm_apostrophe_and_superscript(self):
        self.assertEqual(t.norm("The 37's"), t.norm('The 37s'))
        self.assertEqual(t.norm('E²'), t.norm('E2'))

    def test_norm_strips_leading_the(self):
        self.assertEqual(t.norm('The Cage'), 'cage')

    def test_partkey_roman_and_numeric(self):
        self.assertEqual(t.partkey('Unification I'), ('unification', 1))
        self.assertEqual(t.partkey('Unification (1)'), ('unification', 1))
        self.assertEqual(t.partkey('Caretaker'), ('caretaker', None))

    def test_combined(self):
        self.assertEqual(t.combined('Flesh and Blood (1) / Flesh and Blood (2)'),
                         ('flesh and blood', 1, 2))


class Guide(unittest.TestCase):
    HTML = '''
    <section class="ck-chapter" data-chapter="a">
    <li class="ck" data-id="ent-broken-bow" data-units="2" style="x"><span class="ck-code">ENT</span><span class="ck-se">S1 · E1–2</span><span class="ck-t">Broken Bow</span></li>
    <li class="ck" data-id="ent-fight" data-units="1" style="x"><span class="ck-code">ENT</span><span class="ck-se">S1 · E3</span><span class="ck-t">Fight &amp; Flight</span></li>
    </section>
    <section class="ck-chapter" data-chapter="b">
    <li class="ck" data-id="film-tmp" data-units="1" style="x"><span class="ck-code">Film</span><span class="ck-se"></span><span class="ck-t">Star Trek: The Motion Picture</span></li>
    </section>'''

    def test_rows(self):
        rows = t.parse_guide(self.HTML)
        self.assertEqual([r['code'] for r in rows], ['ENT', 'ENT', 'Film'])
        self.assertEqual(rows[0]['season'], 1)
        self.assertEqual(rows[0]['ep'], 1)
        self.assertEqual(rows[0]['units'], 2)
        self.assertEqual(rows[1]['title'], 'Fight & Flight')
        self.assertIsNone(rows[2]['season'])
        self.assertEqual(rows[1]['chap'], 'a')
        self.assertEqual(rows[2]['chap'], 'b')


class FakeApi:
    def __init__(self, folders, series, episodes, films):
        self.folders, self.series, self.episodes, self.films = folders, series, episodes, films

    def get(self, path, **params):
        if path == '/Library/MediaFolders':
            return {'Items': self.folders}
        kind = params.get('IncludeItemTypes')
        if path == '/Items' and kind == 'Series':
            return {'Items': self.series.get(params.get('ParentId'), [])}
        if path == '/Items' and kind == 'Episode':
            return {'Items': self.episodes.get(params.get('ParentId'), [])}
        if path == '/Items' and kind == 'Movie':
            return {'Items': self.films}
        raise AssertionError((path, params))


class Discovery(unittest.TestCase):
    def test_reads_every_tvshows_library(self):
        folders = [
            {'Id': 'anime', 'Name': 'Anime', 'CollectionType': 'tvshows'},
            {'Id': 'tv', 'Name': 'TV Shows', 'CollectionType': 'tvshows'},
            {'Id': 'mov', 'Name': 'Movies', 'CollectionType': 'movies'},
        ]
        api = FakeApi(folders,
                      {'anime': [{'Id': 'a1', 'Name': 'Afro Samurai'}],
                       'tv': [{'Id': 'v', 'Name': 'Star Trek: Voyager',
                               'ProviderIds': {'Tvdb': '199'}}]},
                      {'v': [ep('e1', 'Caretaker (1) / Caretaker (2)', 1, 1)]},
                      [{'Id': 'f1', 'Name': 'Star Trek: Section 31'}])
        inv = t.discover(api)
        self.assertIn('Star Trek: Voyager', inv)
        self.assertNotIn('Afro Samurai', inv)
        self.assertEqual(inv['__films__']['episodes'][0]['Id'], 'f1')


class Matcher(unittest.TestCase):
    def test_exact(self):
        inv = inventory([ep('r24', 'Relativity', 5, 24)])
        rec = t.match_rows([row('VOY', 5, 24, 'Relativity')], inv, {})[0]
        self.assertEqual(rec['status'], 'MATCHED')
        self.assertEqual(rec['items'], ['r24'])

    def test_duplicate_title_is_ambiguous(self):
        inv = inventory([ep('a', 'Hegemony (1)', 2, 10), ep('b', 'Hegemony (1)', 2, 10)])
        rec = t.match_rows([row('VOY', 2, 10, 'Hegemony')], inv, {})[0]
        self.assertEqual(rec['status'], 'AMBIGUOUS')

    def test_units_expansion(self):
        inv = inventory([ep('b1', 'Broken Bow (1)', 1, 1), ep('b2', 'Broken Bow (2)', 1, 2)])
        inv['Star Trek: Enterprise'] = inv.pop('Star Trek: Voyager')
        rec = t.match_rows([row('ENT', 1, 1, 'Broken Bow', units=2)], inv, {})[0]
        self.assertEqual(rec['items'], ['b1', 'b2'])
        self.assertEqual(rec['tier'], 'units-expansion')

    def test_consecutive_duplicate_collapses(self):
        inv = inventory([ep('r24', 'Relativity', 5, 24)])
        rows = [row('VOY', 5, 24, 'Relativity'), row('VOY', 5, 25, 'Relativity')]
        recs = t.match_rows(rows, inv, {})
        ids, collapsed = t.playlist_ids(recs)
        self.assertEqual(ids, ['r24'])
        self.assertEqual(collapsed, [2])

    def test_combined_file_collapses_part_two(self):
        inv = inventory([ep('fb', 'Flesh and Blood (1) / Flesh and Blood (2)', 7, 9)])
        rows = [row('VOY', 7, 9, 'Flesh and Blood, Part 1'),
                row('VOY', 7, 10, 'Flesh and Blood, Part 2')]
        recs = t.match_rows(rows, inv, {})
        self.assertEqual([r['status'] for r in recs], ['MATCHED', 'MATCHED'])
        self.assertEqual(recs[1]['items'], ['fb'])
        ids, collapsed = t.playlist_ids(recs)
        self.assertEqual(ids, ['fb'])
        self.assertEqual(collapsed, [2])

    def test_units_two_with_one_combined_file(self):
        inv = inventory([ep('df', 'Dark Frontier (1) / Dark Frontier (2)', 5, 15)])
        rec = t.match_rows([row('VOY', 5, 15, 'Dark Frontier, parts 1 and 2', units=2)],
                           inv, {})[0]
        self.assertEqual(rec['status'], 'MATCHED')
        self.assertEqual(rec['items'], ['df'])

    def test_decision_overrides(self):
        inv = inventory([ep('r24', 'Relativity', 5, 24), ep('w25', 'Warhead', 5, 25)])
        dec = {'VOY|5|25': {'item': 'w25', 'note': 'x'}}
        rec = t.match_rows([row('VOY', 5, 25, 'Relativity')], inv, dec)[0]
        self.assertEqual(rec['items'], ['w25'])
        self.assertEqual(rec['tier'], 'decision')

    def test_exclude(self):
        inv = inventory([ep('r24', 'Relativity', 5, 24)])
        rec = t.match_rows([row('VOY', 5, 24, 'Relativity')], inv,
                           {'VOY|5|24': {'exclude': True}})[0]
        self.assertEqual(rec['status'], 'EXCLUDED')
        self.assertEqual(t.playlist_ids([rec])[0], [])

    def test_missing(self):
        inv = inventory([])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'MISSING')

    def test_unknown_code_fails(self):
        with self.assertRaises(SystemExit):
            t.match_rows([row('XYZ', 1, 1, 'Nope')], inventory([]), {})

    def test_gap_links(self):
        links = dict(t.gap_links(row('VOY', 1, 1, 'Caretaker'), inventory([])))
        self.assertIn('thetvdb.com', links['TVDB'])
        self.assertIn('imdb.com', links['IMDb'])
        film = dict(t.gap_links(row('Film', None, None, 'Star Trek: Section 31'), inventory([])))
        self.assertIn('themoviedb.org', film['TMDB'])


class Diff(unittest.TestCase):
    def test_identical(self):
        d = t.diff_sequences(['a', 'b'], ['a', 'b'])
        self.assertEqual((d['added'], d['removed'], d['changed']), ([], [], 0))

    def test_added_and_removed(self):
        d = t.diff_sequences(['a', 'b', 'c'], ['a', 'x', 'c'])
        self.assertEqual(d['added'], ['x'])
        self.assertEqual(d['removed'], ['b'])
        self.assertEqual(d['first_divergence'], 2)


class WriteApi:
    def __init__(self, *args, **kwargs):
        self.posts = []
        self.rename_name = None
        self.items = []

    def post(self, path, body):
        self.posts.append((path, body))
        if path.startswith('/Playlists/') and 'Name' in body:
            self.rename_name = body['Name']
        return 204, ({'Id': 'new'} if path == '/Playlists' else None)

    def get(self, path, **params):
        if path.startswith('/Users/'):
            return {'Name': self.rename_name}
        raise AssertionError(path)


class Writes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = WriteApi()
        self.patches = [mock.patch.object(t, 'Api', lambda *a, **k: self.api),
                        mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def state(self, **over):
        state = dict(playlist='Chronological Star Trek', phase='planned',
                     verdict='DIFFERENT', intended=['a', 'b'], excluded=[],
                     report=os.path.join(self.tmp.name, 'r.md'),
                     old=dict(id='old1', name='Chronological Star Trek', count=2),
                     new=None)
        state.update(over)
        path = t.state_path(self.tmp.name, state['playlist'])
        with open(path, 'w') as fh:
            json.dump(state, fh)
        return types.SimpleNamespace(url='http://x', api_key='k', playlist=state['playlist'],
                                     state_dir=self.tmp.name, user=None,
                                     restore=False, force=False)

    def test_rename_refuses_identical(self):
        args = self.state(verdict='IDENTICAL')
        with self.assertRaises(SystemExit):
            t.cmd_rename(args)
        self.assertEqual(self.api.posts, [])

    def test_rename_posts_only_the_name(self):
        args = self.state()
        t.cmd_rename(args)
        path, body = self.api.posts[0]
        self.assertEqual(path, '/Playlists/old1')
        self.assertEqual(list(body), ['Name'])
        self.assertIn('(superseded', body['Name'])

    def test_rename_force_allows_rehearsal(self):
        args = self.state(verdict='IDENTICAL')
        args.force = True
        t.cmd_rename(args)
        self.assertIn('(superseded', self.api.posts[0][1]['Name'])
        with open(t.state_path(self.tmp.name, 'Chronological Star Trek')) as fh:
            state = json.load(fh)
        self.assertTrue(state['rehearsal'])
        t.cmd_create(args)
        self.assertEqual(self.api.posts[-1][1]['Ids'], ['a', 'b'])

    def test_create_requires_rename_first(self):
        args = self.state()
        with self.assertRaises(SystemExit):
            t.cmd_create(args)
        self.assertEqual(self.api.posts, [])

    def test_create_posts_the_intended_ids(self):
        args = self.state(phase='renamed')
        t.cmd_create(args)
        path, body = self.api.posts[0]
        self.assertEqual(path, '/Playlists')
        self.assertEqual(body['Ids'], ['a', 'b'])
        self.assertEqual(body['MediaType'], 'Video')

    def test_create_without_old_playlist(self):
        args = self.state(verdict='NO_PLAYLIST', old=None)
        t.cmd_create(args)
        self.assertEqual(self.api.posts[0][1]['Ids'], ['a', 'b'])

    def test_restore_moves_phase_back_to_planned(self):
        args = self.state(phase='renamed')
        args.restore = True
        t.cmd_rename(args)
        path, body = self.api.posts[0]
        self.assertEqual(path, '/Playlists/old1')
        self.assertEqual(body['Name'], 'Chronological Star Trek')
        with open(t.state_path(self.tmp.name, 'Chronological Star Trek')) as fh:
            self.assertEqual(json.load(fh)['phase'], 'planned')

    def test_restore_is_refused_after_create(self):
        args = self.state(phase='created',
                          new=dict(id='new', name='Chronological Star Trek'))
        args.restore = True
        with self.assertRaises(SystemExit):
            t.cmd_rename(args)
        self.assertEqual(self.api.posts, [])

    def test_second_create_is_refused(self):
        args = self.state(phase='created',
                          new=dict(id='new', name='Chronological Star Trek'))
        with self.assertRaises(SystemExit):
            t.cmd_create(args)
        self.assertEqual(self.api.posts, [])

    def test_create_without_an_id_stops_the_run(self):
        self.api.post = lambda path, body: (204, None)
        args = self.state(phase='renamed')
        with self.assertRaises(SystemExit):
            t.cmd_create(args)
        with open(t.state_path(self.tmp.name, 'Chronological Star Trek')) as fh:
            self.assertEqual(json.load(fh)['phase'], 'renamed')

    def verify(self, new_ids):
        args = self.state(phase='created', new=dict(id='new', name='Chronological Star Trek'))
        items = [{'Id': i, 'Type': 'Episode', 'Name': i} for i in new_ids]
        old = [{'Id': 'x', 'Type': 'Episode', 'Name': 'x'},
               {'Id': 'y', 'Type': 'Episode', 'Name': 'y'}]
        with mock.patch.object(t, 'playlist_items', side_effect=[items, old]):
            return t.cmd_verify(args)

    def test_verify_passes_on_exact_playlist(self):
        self.assertEqual(self.verify(['a', 'b']), 0)

    def test_verify_fails_on_wrong_order(self):
        self.assertEqual(self.verify(['b', 'a']), 1)


class Safety(unittest.TestCase):
    def test_no_delete_call(self):
        with open(os.path.join(os.path.dirname(__file__), 'trek_playlist.py')) as fh:
            src = fh.read()
        self.assertNotIn("'DELETE'", src)
        self.assertNotIn('"DELETE"', src)


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class Auth(unittest.TestCase):
    def test_authenticate_posts_credentials_and_returns_session(self):
        payload = {'AccessToken': 'tok', 'User': {'Id': 'u1'}}
        with mock.patch.object(t.urllib.request, 'urlopen', return_value=_Resp(payload)) as u:
            self.assertEqual(t.authenticate('http://jf', 'bob', 'pw'), ('tok', 'u1'))
        req = u.call_args[0][0]
        self.assertEqual(json.loads(req.data), {'Username': 'bob', 'Pw': 'pw'})
        self.assertIn('/Users/AuthenticateByName', req.full_url)
        self.assertTrue(req.get_header('Authorization').startswith('MediaBrowser Client='))

    def test_authenticate_failure_does_not_echo_password(self):
        err = t.urllib.error.HTTPError('http://jf/Users/AuthenticateByName', 401,
                                       'Unauthorized', {}, None)
        with mock.patch.object(t.urllib.request, 'urlopen', side_effect=err):
            with self.assertRaises(SystemExit) as cm:
                t.authenticate('http://jf', 'bob', 's3cret')
        self.assertNotIn('s3cret', str(cm.exception))

    def test_resolve_user_accepts_guid_without_lookup(self):
        api = mock.Mock()
        uid = '0123456789abcdef0123456789abcdef'
        self.assertEqual(t.resolve_user(api, uid), uid)
        api.get.assert_not_called()

    def test_main_requires_user_with_password(self):
        env = {'JELLYFIN_PASSWORD': 'pw', 'JELLYFIN_USER': ''}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(SystemExit):
                t.main(['plan'])


class Layout(unittest.TestCase):
    def test_report_dir_defaults_to_working_directory(self):
        args = t.build_parser().parse_args(['plan'])
        self.assertEqual(args.report_dir, os.path.join(os.getcwd(), 'reports'))


if __name__ == '__main__':
    unittest.main()


class TierCoverage(unittest.TestCase):
    def test_normalized_tier_ignores_case_punctuation_and_articles(self):
        inv = inventory([ep('q1', 'Q Who?', 2, 16)])
        rec = t.match_rows([row('VOY', 2, 16, 'Q, Who')], inv, {})[0]
        self.assertEqual((rec['status'], rec['tier']), ('MATCHED', 'normalized'))
        self.assertEqual(rec['items'], ['q1'])

    def test_normalized_tier_strips_a_leading_the(self):
        inv = inventory([ep('c1', 'Cage', 1, 1)])
        rec = t.match_rows([row('VOY', 1, 1, 'The Cage')], inv, {})[0]
        self.assertEqual(rec['tier'], 'normalized')

    def test_prefix_tier_accepts_an_extended_library_title(self):
        inv = inventory([ep('x1', 'Caretaker Extended', 1, 1)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['tier'], 'normalized+prefix')
        self.assertEqual(rec['conf'], '0.95')

    def test_fuzzy_tier_accepts_one_candidate_in_the_same_episode(self):
        inv = inventory([ep('f1', 'Caretakers', 1, 1)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'MATCHED')
        self.assertEqual(rec['tier'], 'fuzzy')
        self.assertEqual(rec['items'], ['f1'])
        self.assertIn('fuzzy', rec['note'])

    def test_fuzzy_tier_refuses_two_close_candidates(self):
        inv = inventory([ep('a', 'Cartaker', 3, 3), ep('b', 'Caretakr', 4, 4)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'AMBIGUOUS')
        self.assertIn('a', rec['note'])
        self.assertIn('b', rec['note'])

    def test_floor_reports_candidates_instead_of_silence(self):
        inv = inventory([ep('a', 'Caretaking', 3, 3), ep('b', 'Caretakings', 4, 4)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'AMBIGUOUS')
        self.assertIn('caretak', rec['note'].lower())

    def test_below_the_floor_is_missing_with_a_reason(self):
        inv = inventory([ep('a', 'Voyagers End', 1, 1)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'MISSING')
        self.assertIn('no candidate >= 0.55', rec['note'])

    def test_film_tier_accepts_the_2009_alias(self):
        inv = inventory([], films=[{'Id': 'st', 'Name': 'Star Trek'}])
        rec = t.match_rows([row('Film', None, None, 'Star Trek (2009)')], inv, {})[0]
        self.assertEqual(rec['status'], 'MATCHED')
        self.assertEqual(rec['items'], ['st'])

    def test_film_normalized_tier_ignores_punctuation(self):
        inv = inventory([], films=[{'Id': 'tmp', 'Name': 'Star Trek The Motion Picture'}])
        rec = t.match_rows([row('Film', None, None, 'Star Trek: The Motion Picture')],
                           inv, {})[0]
        self.assertEqual((rec['status'], rec['tier']), ('MATCHED', 'normalized'))

    def test_film_fuzzy_tier_needs_a_clear_top_score(self):
        inv = inventory([], films=[{'Id': 'b1', 'Name': 'Star Trek Beyonds'}])
        rec = t.match_rows([row('Film', None, None, 'Star Trek Beyond')], inv, {})[0]
        self.assertEqual((rec['status'], rec['tier']), ('MATCHED', 'fuzzy'))

    def test_film_fuzzy_tier_refuses_a_tie(self):
        inv = inventory([], films=[{'Id': 'b1', 'Name': 'Star Trek Beyonds'},
                                   {'Id': 'b2', 'Name': 'Star Trek Beyonds'}])
        rec = t.match_rows([row('Film', None, None, 'Star Trek Beyond')], inv, {})[0]
        self.assertEqual(rec['status'], 'AMBIGUOUS')


class PlanGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.args = types.SimpleNamespace(
            url='http://x', api_key='k', playlist='Chronological Star Trek',
            state_dir=self.tmp.name, report_dir=self.tmp.name, user=None,
            site_file=None, inventory_file=None, guide_url=t.GUIDE_URL)

    def test_a_row_without_a_code_stops_the_parse(self):
        html = ('<li class="ck" data-id="x" data-units="1">'
                '<span class="ck-code"></span><span class="ck-se">S1 · E1</span>'
                '<span class="ck-t">Title</span></li>')
        with self.assertRaises(SystemExit):
            t.parse_guide(html)

    def test_a_short_guide_stops_the_run(self):
        rows = [row('VOY', 1, 1, 'Caretaker') for _ in range(499)]
        with mock.patch.object(t, 'parse_guide', return_value=rows):
            with mock.patch.object(t, 'fetch_guide', return_value=''):
                with self.assertRaises(SystemExit) as cm:
                    t.cmd_plan(self.args)
        self.assertIn('499', str(cm.exception))


class ReportShape(unittest.TestCase):
    def test_mapping_table_names_the_tier(self):
        rec = dict(pos=1, chap='c', code='VOY', season=1, ep=1, title='Caretaker',
                   units=1, status='MATCHED', tier='normalized', items=['e1'],
                   libtitle='Caretaker', libseason=1, libep=1, conf='1.00', note='')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'r.md')
            args = types.SimpleNamespace(url='http://x', playlist='Chronological Star Trek')
            t.write_report(path, args, 'DIFFERENT', [rec], ['e1'], [], [], [], None,
                           [], t.diff_sequences([], ['e1']), {}, __import__('datetime').datetime.now())
            with open(path) as fh:
                body = fh.read()
        self.assertIn('| Tier |', body)
        self.assertIn('| normalized |', body)


class NoNetwork:
    def __init__(self, *a, **k):
        pass

    def get(self, *a, **k):
        raise AssertionError('network read')

    def post(self, *a, **k):
        raise AssertionError('network write')


class OfflinePlan(unittest.TestCase):
    def test_plan_runs_end_to_end_from_saved_files(self):
        rows, eps = [], []
        for n in range(1, 501):
            title = 'Episode %d' % n
            rows.append('<li class="ck" data-id="e%d" data-units="1">'
                        '<span class="ck-code">TOS</span>'
                        '<span class="ck-se">S1 · E%d</span>'
                        '<span class="ck-t">%s</span></li>' % (n, n, title))
            eps.append({'Id': 'i%d' % n, 'Name': title,
                        'ParentIndexNumber': 1, 'IndexNumber': n})
        page = '<section class="ck-chapter" data-chapter="c">%s</section>' % ''.join(rows)
        inv = {'Star Trek': {'Id': 'tos', 'ProviderIds': {}, 'episodes': eps},
               '__films__': {'Id': 'm', 'ProviderIds': {}, 'episodes': []}}
        with tempfile.TemporaryDirectory() as tmp:
            site = os.path.join(tmp, 'site.html')
            invf = os.path.join(tmp, 'inv.json')
            with open(site, 'w') as fh:
                fh.write(page)
            with open(invfile := invf, 'w') as fh:
                json.dump(inv, fh)
            args = types.SimpleNamespace(
                url='http://x', api_key='k', playlist='Chronological Star Trek',
                state_dir=tmp, report_dir=tmp, user=None, site_file=site,
                inventory_file=invf, guide_url=t.GUIDE_URL)
            with mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u'), \
                    mock.patch.object(t, 'find_playlist', lambda *a, **k: None), \
                    mock.patch.object(t, 'Api', NoNetwork):
                self.assertEqual(t.cmd_plan(args), 0)
            with open(t.state_path(tmp, 'Chronological Star Trek')) as fh:
                state = json.load(fh)
            self.assertEqual(state['verdict'], 'NO_PLAYLIST')
            self.assertEqual(len(state['intended']), 500)
            self.assertTrue(os.path.exists(state['report']))


class PagingCaps(unittest.TestCase):
    def test_a_full_series_page_stops_the_run(self):
        folders = [{'Id': 'tv', 'Name': 'TV', 'CollectionType': 'tvshows'},
                   {'Id': 'mov', 'Name': 'Movies', 'CollectionType': 'movies'}]
        series = [{'Id': str(n), 'Name': 'Star Trek %d' % n} for n in range(400)]
        api = FakeApi(folders, {'tv': series}, {}, [])
        with self.assertRaises(SystemExit) as cm:
            t.discover(api)
        self.assertIn('series read', str(cm.exception))

    def test_a_full_playlist_page_stops_the_run(self):
        api = mock.Mock()
        api.get.return_value = {'Items': [{'Id': str(n), 'Name': 'x',
                                           'Type': 'Playlist'} for n in range(200)]}
        with self.assertRaises(SystemExit) as cm:
            t.find_playlist(api, 'Chronological Star Trek')
        self.assertIn('playlist read', str(cm.exception))

    def test_a_library_below_the_limit_is_read_normally(self):
        folders = [{'Id': 'tv', 'Name': 'TV', 'CollectionType': 'tvshows'},
                   {'Id': 'mov', 'Name': 'Movies', 'CollectionType': 'movies'}]
        api = FakeApi(folders, {'tv': [{'Id': 'v', 'Name': 'Star Trek: Voyager'}]},
                      {'v': [ep('e1', 'Caretaker', 1, 1)]}, [])
        self.assertIn('Star Trek: Voyager', t.discover(api))


class OfflineDecide(unittest.TestCase):
    def test_a_decision_is_recorded_without_a_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {'JELLYFIN_API_KEY': '',
                                              'JELLYFIN_PASSWORD': '',
                                              'JELLYFIN_USER': ''}):
                self.assertEqual(t.main(['--state-dir', tmp, 'decide',
                                         '--key', 'SNW|2|10', '--exclude']), 0)
            with open(os.path.join(tmp, 'decisions.json')) as fh:
                self.assertTrue(json.load(fh)['SNW|2|10']['exclude'])


class PlaylistLookup(unittest.TestCase):
    def test_duplicate_names_are_an_error(self):
        api = mock.Mock()
        api.get.return_value = {'Items': [
            {'Id': 'p1', 'Name': 'Chronological Star Trek', 'Type': 'Playlist'},
            {'Id': 'p2', 'Name': 'Chronological Star Trek', 'Type': 'Playlist'}]}
        with self.assertRaises(SystemExit) as cm:
            t.find_playlist(api, 'Chronological Star Trek')
        self.assertIn('p1', str(cm.exception))
        self.assertIn('p2', str(cm.exception))

    def test_a_different_name_is_ignored(self):
        api = mock.Mock()
        api.get.return_value = {'Items': [
            {'Id': 'p1', 'Name': 'Star Trek', 'Type': 'Playlist'}]}
        self.assertIsNone(t.find_playlist(api, 'Chronological Star Trek'))


class StateDir(unittest.TestCase):
    def test_order_of_precedence(self):
        with mock.patch.dict(os.environ, {'TREK_STATE_DIR': '/from-env'}), \
                mock.patch.object(t, 'skill_state_dir', lambda: '/from-skill'):
            self.assertEqual(t.default_state_dir(), '/from-env')
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(t, 'skill_state_dir', lambda: '/from-skill'):
            self.assertEqual(t.default_state_dir(), '/from-skill')
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(t, 'skill_state_dir', lambda: None), \
                mock.patch.object(t, 'platform_state_dir', lambda: '/from-platform'):
            self.assertEqual(t.default_state_dir(), '/from-platform')

    def test_read_only_install_falls_back(self):
        with mock.patch.object(t.os, 'makedirs', side_effect=OSError('read-only')):
            self.assertIsNone(t.skill_state_dir())

    def test_legacy_directory_migrated(self):
        with tempfile.TemporaryDirectory() as legacy, \
                tempfile.TemporaryDirectory() as state:
            with open(os.path.join(legacy, 'decisions.json'), 'w') as fh:
                json.dump({'TOS|1|1': {'exclude': True}}, fh)
            with open(os.path.join(legacy, 'chronological-star-trek.json'), 'w') as fh:
                json.dump({'phase': 'renamed'}, fh)
            copied = t.migrate_legacy_state(state, legacy)
            self.assertEqual(sorted(copied), ['chronological-star-trek.json', 'decisions.json'])
            self.assertTrue(os.path.exists(os.path.join(state, 'chronological-star-trek.json')))
            self.assertEqual(t.migrate_legacy_state(state, legacy), [])

    def test_plan_prints_the_state_path(self):
        rows = [row('TOS', 1, n, 'Episode %d' % n) for n in range(1, 501)]
        eps = [ep('i%d' % n, 'Episode %d' % n, 1, n) for n in range(1, 501)]
        inv = inventory(eps)
        with tempfile.TemporaryDirectory() as tmp:
            args = types.SimpleNamespace(
                url='http://x', api_key='k', playlist='Chronological Star Trek',
                state_dir=tmp, report_dir=tmp, user=None, site_file=None,
                inventory_file=None, guide_url=t.GUIDE_URL)
            out = io.StringIO()
            with mock.patch.object(t, 'parse_guide', lambda html: rows), \
                    mock.patch.object(t, 'fetch_guide', lambda url: ''), \
                    mock.patch.object(t, 'discover', lambda api: inv), \
                    mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u'), \
                    mock.patch.object(t, 'find_playlist', lambda *a, **k: None), \
                    mock.patch.object(t, 'Api', NoNetwork), \
                    mock.patch('sys.stdout', out):
                self.assertEqual(t.cmd_plan(args), 0)
            self.assertIn('state: %s' % tmp, out.getvalue())


class Proposal(unittest.TestCase):
    def test_gap_carries_best_candidate(self):
        inv = inventory([ep('a', 'Caretaking', 3, 3), ep('b', 'Caretakings', 4, 4)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'AMBIGUOUS')
        self.assertEqual(rec['proposal'][0]['item'], 'a')
        self.assertEqual(rec['proposal'][0]['tier'], 'fuzzy')

    def test_runners_up_capped_at_three(self):
        names = ['Caretaking', 'Caretakers', 'Caretakins', 'Caretakerr']
        inv = inventory([ep(str(n), name, 3, 3) for n, name in enumerate(names)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertLessEqual(len(rec['proposal']), 3)
        scores = [p['score'] for p in rec['proposal']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_candidate_means_no_proposal(self):
        inv = inventory([ep('a', 'Voyagers End', 1, 1)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'MISSING')
        self.assertIsNone(rec['proposal'])

    def test_low_confidence_flag(self):
        inv = inventory([ep('a', 'Caretaking', 3, 3), ep('b', 'Caretakings', 4, 4)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertTrue(rec['low_conf'])
        exact = t.match_rows([row('VOY', 1, 1, 'Caretaker')],
                             inventory([ep('c', 'Caretaker', 1, 1)]), {})[0]
        self.assertEqual(exact['status'], 'MATCHED')
        self.assertFalse(exact['low_conf'])

    def test_ambiguous_exact_duplicate_carries_a_proposal(self):
        inv = inventory([ep('a', 'Caretaker', 1, 1), ep('b', 'Caretaker', 1, 2)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv, {})[0]
        self.assertEqual(rec['status'], 'AMBIGUOUS')
        self.assertEqual([p['item'] for p in rec['proposal']], ['a', 'b'])

    def test_proposal_keeps_verdict_blocked(self):
        rows = [row('VOY', 1, 1, 'Caretaker')]
        inv = inventory([ep('a', 'Caretaking', 3, 3), ep('b', 'Caretakings', 4, 4)])
        rec = t.match_rows(rows, inv, {})[0]
        self.assertIn(rec['status'], ('MISSING', 'AMBIGUOUS'))

    def test_decision_row_not_queued(self):
        inv = inventory([ep('a', 'Caretaker', 1, 1)])
        rec = t.match_rows([row('VOY', 1, 1, 'Caretaker')], inv,
                           {'VOY|1|1': {'item': 'a', 'note': 'mapped'}})[0]
        self.assertEqual(rec['tier'], 'decision')
        self.assertIsNone(rec['proposal'])
        self.assertFalse(rec['low_conf'])


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(SKILL_DIR))


def plan_run(tmp, rows, inv, review_json=False):
    args = types.SimpleNamespace(
        url='http://x', api_key='k', playlist='Chronological Star Trek',
        state_dir=tmp, report_dir=tmp, user=None, site_file=None,
        inventory_file=None, guide_url=t.GUIDE_URL, review_json=review_json)
    out = io.StringIO()
    with mock.patch.object(t, 'parse_guide', lambda html: rows), \
            mock.patch.object(t, 'fetch_guide', lambda url: ''), \
            mock.patch.object(t, 'discover', lambda api: inv), \
            mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u'), \
            mock.patch.object(t, 'find_playlist', lambda *a, **k: None), \
            mock.patch.object(t, 'Api', NoNetwork), \
            mock.patch('sys.stdout', out):
        code = t.cmd_plan(args)
    return code, out.getvalue()


class Review(unittest.TestCase):
    def build(self):
        rows = [row('TOS', 1, n, 'Episode %d' % n) for n in range(1, 500)]
        eps = [ep('i%d' % n, 'Episode %d' % n, 1, n) for n in range(1, 500)]
        voy = [ep('a', 'Caretaking', 3, 3), ep('b', 'Caretakings', 4, 4)]
        rows.append(row('VOY', 1, 1, 'Caretaker'))
        inv = t.prepare({
            'Star Trek': {'Id': 'tos', 'ProviderIds': {}, 'episodes': eps},
            'Star Trek: Voyager': {'Id': 'v', 'ProviderIds': {}, 'episodes': voy},
            '__films__': {'Id': 'm', 'ProviderIds': {}, 'episodes': []},
        })
        return rows, inv

    def test_queue_row_prints_a_decide_command(self):
        rows, inv = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            _, out = plan_run(tmp, rows, inv)
        self.assertIn("decide --key 'VOY|1|1' --item a", out)
        self.assertIn('review: 1 gap(s)', out)

    def test_review_json_stdout(self):
        rows, inv = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            _, out = plan_run(tmp, rows, inv, review_json=True)
            self.assertFalse(any('review' in n for n in os.listdir(tmp)))
        start = out.index('[\n')
        queue = json.loads(out[start:out.rindex(']') + 1])
        self.assertEqual(queue[0]['key'], 'VOY|1|1')
        self.assertEqual(queue[0]['proposal'][0]['item'], 'a')

    def test_queue_shrinks_after_answer(self):
        rows, inv = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            plan_run(tmp, rows, inv)
            args = types.SimpleNamespace(state_dir=tmp, key='VOY|1|1',
                                         item='a', exclude=False, note='mapped')
            t.cmd_decide(args)
            _, out = plan_run(tmp, rows, inv)
        self.assertNotIn('review:', out)
        self.assertIn('verdict: NO_PLAYLIST', out)

    def test_skill_mentions_the_review_and_the_replan(self):
        with open(os.path.join(SKILL_DIR, 'SKILL.md')) as fh:
            skill = fh.read()
        self.assertIn('Review', skill)
        self.assertIn('plan', skill)
        self.assertIn('accept', skill)
        self.assertIn('exclude', skill)
        with open(os.path.join(REPO, 'commands', 'rebuild-trek-playlist.md')) as fh:
            self.assertIn('review', fh.read().lower())

    def test_rename_refuses_pre_review_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = t.state_path(tmp, 'Chronological Star Trek')
            t.save_json(path, dict(playlist='x', phase='planned', verdict='BLOCKED',
                                   intended=['a'], old=dict(id='p', name='old', count=1)))
            args = types.SimpleNamespace(state_dir=tmp, playlist='Chronological Star Trek',
                                         url='http://x', api_key='k', user=None,
                                         restore=False, force=False)
            with self.assertRaises(SystemExit):
                t.cmd_rename(args)


class WorkingCopy(unittest.TestCase):
    def read(self, tmp):
        path = os.path.join(tmp, 'playlist.local.md')
        with open(path) as fh:
            return path, fh.read()

    def test_rows_in_guide_order(self):
        rows, inv = Review().build()
        with tempfile.TemporaryDirectory() as tmp:
            plan_run(tmp, rows, inv)
            _, text = self.read(tmp)
        positions = [ln.split('|')[1].strip() for ln in text.splitlines()
                     if ln.startswith('| ') and ln.split('|')[1].strip().isdigit()]
        self.assertEqual(positions, [str(n) for n in range(1, 501)])

    def test_review_column(self):
        rows, inv = Review().build()
        with tempfile.TemporaryDirectory() as tmp:
            plan_run(tmp, rows, inv)
            _, text = self.read(tmp)
        self.assertIn('| Review |', text)
        line = [ln for ln in text.splitlines() if "| 500 |" in ln][0]
        self.assertIn('Caretaking', line)
        self.assertIn('id=a', line)
        first = [ln for ln in text.splitlines() if "| 1 | TOS" in ln][0]
        self.assertTrue(first.rstrip().endswith('| - |'))

    def test_overwritten_not_appended(self):
        rows, inv = Review().build()
        with tempfile.TemporaryDirectory() as tmp:
            plan_run(tmp, rows, inv)
            plan_run(tmp, rows, inv)
            _, text = self.read(tmp)
        self.assertEqual(text.count('# Playlist working copy'), 1)

    def test_uses_report_dir(self):
        rows, inv = Review().build()
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as reports:
                args = types.SimpleNamespace(
                    url='http://x', api_key='k', playlist='Chronological Star Trek',
                    state_dir=tmp, report_dir=reports, user=None, site_file=None,
                    inventory_file=None, guide_url=t.GUIDE_URL, review_json=False)
                with mock.patch.object(t, 'parse_guide', lambda html: rows), \
                        mock.patch.object(t, 'fetch_guide', lambda url: ''), \
                        mock.patch.object(t, 'discover', lambda api: inv), \
                        mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u'), \
                        mock.patch.object(t, 'find_playlist', lambda *a, **k: None), \
                        mock.patch.object(t, 'Api', NoNetwork):
                    t.cmd_plan(args)
                self.assertTrue(os.path.exists(os.path.join(reports, 'playlist.local.md')))


class CredentialDocs(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(SKILL_DIR, *parts)) as fh:
            return fh.read()

    def test_skill_names_the_credential_variables(self):
        skill = self.read('SKILL.md')
        for name in ('JELLYFIN_API_KEY', 'JELLYFIN_USER', 'JELLYFIN_PASSWORD'):
            self.assertIn(name, skill)

    def test_skill_never_passes_password_as_a_flag(self):
        skill = self.read('SKILL.md')
        self.assertNotIn('--password', skill)
        self.assertNotIn('export JELLYFIN_PASSWORD', skill)

    def test_docs_name_the_state_directory(self):
        for text in (self.read('SKILL.md'), self.read('..', '..', 'README.md')):
            self.assertIn('TREK_STATE_DIR', text)
            self.assertIn('refs/', text)


class GuideCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def state(self, guide, phase='created'):
        state = dict(playlist='Chronological Star Trek', phase=phase,
                     verdict='DIFFERENT', intended=['a', 'b'], excluded=[],
                     report=os.path.join(self.tmp.name, 'r.md'),
                     old=None, new=dict(id='new', name='Chronological Star Trek'),
                     guide=guide)
        with open(t.state_path(self.tmp.name, state['playlist']), 'w') as fh:
            json.dump(state, fh)
        return types.SimpleNamespace(
            url='http://x', api_key='k', playlist=state['playlist'],
            state_dir=self.tmp.name, report_dir=self.tmp.name, user=None,
            site_file=None, inventory_file=None, guide_url=t.GUIDE_URL,
            restore=False, force=False)

    def verify(self, args, rows, new_ids=('a', 'b')):
        items = [{'Id': i, 'Type': 'Episode', 'Name': i} for i in new_ids]
        with mock.patch.object(t, 'playlist_items', return_value=items), \
                mock.patch.object(t, 'Api', NoNetwork), \
                mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u'), \
                mock.patch.object(t, 'fetch_guide', lambda url: ''), \
                mock.patch.object(t, 'parse_guide', lambda html: rows):
            return t.cmd_verify(args)

    def test_plan_stores_the_guide_order(self):
        rows, inv = Review().build()
        with tempfile.TemporaryDirectory() as tmp:
            plan_run(tmp, rows, inv)
            with open(t.state_path(tmp, 'Chronological Star Trek')) as fh:
                state = json.load(fh)
        self.assertEqual(state['guide'][0], [1, 'TOS|1|1', 'Episode 1'])
        self.assertEqual(len(state['guide']), 500)

    def test_moved_position_reported(self):
        guide = [[1, 'VOY|1|1', 'Caretaker'], [2, 'VOY|1|2', 'Parallax']]
        args = self.state(guide)
        rows = [row('VOY', 1, 1, 'Caretaker'), row('VOY', 1, 3, 'Time and Again')]
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            code = self.verify(args, rows)
        self.assertEqual(code, 0)
        self.assertIn('1 position(s) moved; [2]', out.getvalue())

    def test_moved_position_does_not_fail_the_run(self):
        guide = [[1, 'VOY|1|1', 'Caretaker'], [2, 'VOY|1|2', 'Parallax']]
        rows = [row('VOY', 1, 1, 'Caretaker'), row('VOY', 1, 3, 'Time and Again')]
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            self.assertEqual(self.verify(self.state(guide), rows), 0)
        self.assertIn('guide check', out.getvalue())

    def test_unreachable_guide_skipped(self):
        guide = [[1, 'VOY|1|1', 'Caretaker'], [2, 'VOY|1|2', 'Parallax']]
        args = self.state(guide)
        items = [{'Id': i, 'Type': 'Episode', 'Name': i} for i in ('a', 'b')]
        out = io.StringIO()
        with mock.patch.object(t, 'playlist_items', return_value=items), \
                mock.patch.object(t, 'Api', NoNetwork), \
                mock.patch.object(t, 'resolve_user', lambda *a, **k: 'u'), \
                mock.patch.object(t, 'fetch_guide',
                                  side_effect=urllib.error.URLError('offline')), \
                mock.patch('sys.stdout', out):
            self.assertEqual(t.cmd_verify(args), 0)
        self.assertIn('guide check skipped', out.getvalue())

    def test_verify_names_restore_when_no_new_playlist(self):
        args = self.state([], phase='renamed')
        path = t.state_path(self.tmp.name, 'Chronological Star Trek')
        with open(path) as fh:
            state = json.load(fh)
        state['new'] = None
        with open(path, 'w') as fh:
            json.dump(state, fh)
        with self.assertRaises(SystemExit) as caught:
            t.cmd_verify(args)
        self.assertIn('rename --restore', str(caught.exception))
