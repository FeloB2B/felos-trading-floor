import io
import json
import unittest
from unittest.mock import Mock, patch
import bridge


def opener_for(data):
    opener = Mock()
    opener.open.return_value = io.BytesIO(data)
    return opener


def rss(items='', time='<lastBuildDate>Thu, 26 Nov 2026 09:00:00 GMT</lastBuildDate>'):
    return ('<rss><channel>' + time + items + '</channel></rss>').encode()


def item(title, link, date='Thu, 26 Nov 2026 08:00:00 GMT'):
    return f'<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate></item>'


class FedTests(unittest.TestCase):
    def test_parses_and_restricts_links(self):
        data = rss(item('&lt;script&gt;alert(1)&lt;/script&gt;', 'https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm') +
                   item('bad', 'https://www.federalreserve.gov.evil.test/test') +
                   item('bad', 'http://federalreserve.gov/test') +
                   item('bad', 'https://federalreserve.gov@evil.test/test') +
                   item('bad', 'https://u@federalreserve.gov/test'))
        op = opener_for(data)
        d = bridge.fed_headlines(op)
        self.assertIsNone(d['error'])
        self.assertEqual(d['feed_time'], 'Thu, 26 Nov 2026 09:00:00 GMT')
        self.assertEqual(len(d['items']), 1)
        self.assertEqual(d['items'][0]['title'], '<script>alert(1)</script>')
        self.assertEqual(d['items'][0]['published'], 'Thu, 26 Nov 2026 08:00:00 GMT')
        self.assertTrue(d['checked_at_utc'].endswith('Z'))
        args, kwargs = op.open.call_args
        self.assertEqual(args[0].full_url, bridge.FED_FEED)
        self.assertEqual(kwargs['timeout'], bridge.FED_TIMEOUT)

    def test_limits_and_invalid_documents(self):
        for data in [b'x' * (bridge.FED_MAX_BYTES + 1), b'<rss>',
                     b'<!DOCTYPE rss [<!ENTITY x "abc">]><rss><channel>&x;</channel></rss>',
                     b'\xff', b'<rss/>']:
            with self.subTest(data=data[:40]):
                d = bridge.fed_headlines(opener_for(data))
                self.assertTrue(d['error'])
                self.assertEqual(d['items'], [])
        d = bridge.fed_headlines(opener_for(rss(item('title', 'https://federalreserve.gov/a') * 25, '')))
        self.assertEqual(len(d['items']), bridge.FED_MAX_ITEMS)
        self.assertIsNone(d['feed_time'])

    def test_network_error_and_redirects(self):
        op = Mock(); op.open.side_effect = TimeoutError('secret')
        d = bridge.fed_headlines(op)
        self.assertIn('timed out', d['error'])
        self.assertNotIn('secret', json.dumps(d))
        self.assertIsNone(bridge.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_endpoint_and_same_origin(self):
        import test_bridge
        test_bridge.HttpTests.setUpClass()
        try:
            case = test_bridge.HttpTests()
            with patch('bridge.fed_headlines', return_value={'error': None, 'items': [], 'feed_time': None, 'checked_at_utc': 'x'}):
                status, headers, body = case.request('GET', '/fed-headlines')
                self.assertEqual(status, 200)
                self.assertIn('application/json', headers['Content-Type'])
                self.assertEqual(json.loads(body)['items'], [])
                self.assertEqual(case.request('GET', '/fed-headlines', {'Origin': 'https://evil.test'})[0], 403)
                self.assertEqual(case.request('GET', '/fed-headlines?foo=bar')[0], 404)
        finally:
            test_bridge.HttpTests.tearDownClass()

if __name__ == '__main__':
    unittest.main()
