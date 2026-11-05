import json
import os
import sys
import tempfile
import types
import unittest
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
        self.assertEqual(d['first_divergence'], 1)


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
        return types.SimpleNamespace(url='http://x', key='k', playlist=state['playlist'],
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


class Layout(unittest.TestCase):
    def test_report_dir_defaults_to_working_directory(self):
        args = t.build_parser().parse_args(['plan'])
        self.assertEqual(args.report_dir, os.path.join(os.getcwd(), 'reports'))


if __name__ == '__main__':
    unittest.main()


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
