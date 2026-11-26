import http.client
import json
import threading
import unittest
from http.server import HTTPServer
from types import SimpleNamespace as NS
from unittest.mock import Mock
import bridge

NOW = 1700000000.123

def fake(names=('XAUUSDm', 'US30', 'USTECm', 'US500m')):
    mt = Mock()
    mt.initialize.return_value = True
    mt.terminal_info.return_value = NS(connected=True)
    mt.account_info.return_value = NS(trade_allowed=False, login=123456, password='secret')
    mt.symbols_get.return_value = [NS(name=x) for x in names]
    mt.symbol_select.return_value = True
    mt.symbol_info_tick.return_value = NS(time=int(NOW), time_msc=1700000000123, bid=2300.5, ask=2300.7)
    return mt

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.mt = fake()
        self.b = bridge.Bridge(self.mt, clock=lambda: NOW)

    def test_fresh_and_precise_timestamp(self):
        data = self.b.quotes()
        self.assertTrue(data['connected'])
        self.assertEqual([x['symbol'] for x in data['quotes']], ['XAUUSDm','US30','USTECm','US500m'])
        q = data['quotes'][0]
        self.assertEqual(q['tick_time_utc'], '2023-11-14T22:13:20.123Z')
        self.assertEqual(q['bid'], 2300.5)
        self.assertNotIn('123456', json.dumps(data))
        self.assertNotIn('secret', json.dumps(data))

    def test_rejects_trade_allowed_true_missing_and_none(self):
        for account in (NS(trade_allowed=True), NS(), None, NS(trade_allowed=0)):
            with self.subTest(account=account):
                self.mt.account_info.return_value = account
                self.assertFalse(self.b.status()['connected'])
                self.assertEqual(self.b.quotes()['quotes'], [])
        self.mt.symbols_get.assert_not_called()

    def test_no_terminal_or_package(self):
        self.assertFalse(bridge.Bridge(None).status()['connected'])
        for terminal in (None, NS(connected=False), NS()):
            self.mt.terminal_info.return_value = terminal
            self.assertFalse(self.b.status()['connected'])

    def test_initialize_failure_and_exception(self):
        self.mt.initialize.return_value = False
        self.assertFalse(self.b.quotes()['connected'])
        self.mt.initialize.side_effect = RuntimeError('secret')
        self.assertNotIn('secret', json.dumps(self.b.quotes()))

    def test_changes_account_during_collection(self):
        self.mt.account_info.side_effect = [NS(trade_allowed=False), NS(trade_allowed=True)]
        result = self.b.quotes()
        self.assertFalse(result['connected'])
        self.assertEqual(result['quotes'], [])

    def test_ambiguous_and_leveraged_do_not_match(self):
        self.mt = fake(['XAUUSD','XAUUSDm','US30x','USTEC3L','US500m'])
        result = bridge.Bridge(self.mt, lambda: NOW).quotes()['quotes']
        self.assertEqual([x['state'] for x in result], ['ambiguous','unavailable','unavailable','fresh'])
        self.assertEqual(self.mt.symbol_select.call_count, 1)
        self.mt.symbol_select.assert_called_once_with('US500m', True)

    def test_stale_future_and_missing_timestamp(self):
        for ts, state in [(NOW-121, 'stale'), (NOW+6,'stale'), (0,'unavailable')]:
            with self.subTest(ts=ts):
                self.mt.symbol_info_tick.return_value = NS(time=ts, time_msc=int(ts*1000), bid=1, ask=2)
                q = self.b.quotes()['quotes'][0]
                self.assertEqual(q['state'], state)
                self.assertNotIn('bid', q)

    def test_seconds_timestamp_fallback(self):
        self.mt.symbol_info_tick.return_value = NS(time=1700000000, time_msc=0, bid=1, ask=2)
        self.assertEqual(self.b.quotes()['quotes'][0]['tick_time_utc'], '2023-11-14T22:13:20Z')

    def test_invalid_prices_and_tick_missing(self):
        for tick in (None, NS(time_msc=int(NOW*1000),time=int(NOW),bid=float('nan'),ask=3), NS(time_msc=int(NOW*1000),time=int(NOW),bid=5,ask=3)):
            self.mt.symbol_info_tick.return_value = tick
            self.assertEqual(self.b.quotes()['quotes'][0]['state'], 'unavailable')

    def test_symbol_list_failure(self):
        self.mt.symbols_get.return_value = None
        self.assertFalse(self.b.quotes()['connected'])

    def test_no_trading_methods_called(self):
        self.b.quotes()
        used = [x[0] for x in self.mt.method_calls]
        self.assertTrue(set(used).issubset({'initialize','terminal_info','account_info','symbols_get','symbol_select','symbol_info_tick'}))

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = HTTPServer(('127.0.0.1', 0), bridge.make_handler(bridge.Bridge(fake(), lambda:NOW)))
        cls.port = cls.s.server_port
        cls.thread=threading.Thread(target=cls.s.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.s.shutdown(); cls.s.server_close(); cls.thread.join()
    def request(self, method, path, headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        h={'Host':'127.0.0.1:8765'}
        h.update(headers or {})
        c.request(method,path,headers=h);r=c.getresponse(); raw=r.read();status=r.status; hdr=dict(r.getheaders());c.close()
        return status,hdr,raw
    def test_office_and_quotes(self):
        status,h,body=self.request('GET','/')
        self.assertEqual(status,200);self.assertIn(b'Local read-only quote office',body)
        self.assertIn('no-store',h['Cache-Control'])
        status,h,body=self.request('GET','/quotes')
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['connected'])
        self.assertEqual(self.request('GET','/status')[0],200)
    def test_reject_untrusted_and_arbitrary_paths(self):
        self.assertEqual(self.request('GET','/quotes',{'Host':'evil.test:8765'})[0],403)
        self.assertEqual(self.request('GET','/quotes',{'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.request('GET','/bridge.py')[0],404)
        self.assertEqual(self.request('GET','/quotes?password=secret')[0],404)
        self.assertEqual(self.request('POST','/quotes')[0],501)

if __name__ == '__main__': unittest.main()
