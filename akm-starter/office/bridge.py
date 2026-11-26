"""Local quote-only bridge; no login, credential handling, or trading operations."""
import datetime
import json
import math
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

HOST = '127.0.0.1'
PORT = 8765
ORIGIN = f'http://{HOST}:{PORT}'
MAX_AGE_SECONDS = 120
FUTURE_TOLERANCE_SECONDS = 5
SYMBOLS = ('XAUUSD', 'US30', 'USTEC', 'US500')
# Only exact names and the explicitly supported Exness-style m suffix.
SUFFIXES = ('', 'm')
OFFICE = Path(__file__).with_name('AKM_Trading_Office.html')
FED_FEED = 'https://www.federalreserve.gov/feeds/press_monetary.xml'
FED_MAX_BYTES = 256 * 1024
FED_TIMEOUT = 5
FED_MAX_ITEMS = 15


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fed_link(link):
    """Permit only HTTPS links to the Fed's main public website."""
    try:
        u = urlsplit(link)
        return (u.scheme == 'https' and u.hostname in ('federalreserve.gov', 'www.federalreserve.gov')
                and u.username is None and u.password is None and u.port in (None, 443)
                and not any(ord(c) < 33 or ord(c) == 127 for c in link))
    except (ValueError, TypeError):
        return False


def fed_headlines(opener=None):
    """Fetch a small, untrusted RSS document; never expose exception details."""
    checked = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    base = {'checked_at_utc': checked, 'feed_time': None, 'items': []}
    try:
        opener = opener or urllib.request.build_opener(NoRedirect())
        req = urllib.request.Request(FED_FEED, headers={'User-Agent': 'AKM-Office/1.0', 'Accept': 'application/rss+xml, application/xml, text/xml'})
        with opener.open(req, timeout=FED_TIMEOUT) as response:
            payload = response.read(FED_MAX_BYTES + 1)
        if len(payload) > FED_MAX_BYTES:
            return {**base, 'error': 'Federal Reserve feed exceeds the size limit.'}
        text = payload.decode('utf-8-sig')
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.IGNORECASE):
            raise ValueError('DTD not allowed')
        root = ET.fromstring(text)
        if root.tag != 'rss':
            raise ValueError('Not RSS')
        channel = root.find('channel')
        if channel is None:
            raise ValueError('No channel')
        def field(node, name, limit):
            return (node.findtext(name) or '').strip()[:limit]
        base['feed_time'] = field(channel, 'lastBuildDate', 150) or field(channel, 'pubDate', 150) or None
        for item in channel.findall('item'):
            title, link = field(item, 'title', 500), field(item, 'link', 2048)
            if title and fed_link(link):
                base['items'].append({'title': title, 'link': link, 'published': field(item, 'pubDate', 150) or None})
                if len(base['items']) >= FED_MAX_ITEMS:
                    break
        return {**base, 'error': None}
    except (ET.ParseError, UnicodeError, ValueError):
        return {**base, 'error': 'Federal Reserve feed could not be safely parsed.'}
    except Exception:
        return {**base, 'error': 'Federal Reserve feed unavailable or timed out.'}


class Bridge:
    def __init__(self, mt5, clock=None):
        self.mt5 = mt5
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc).timestamp())
        self.lock = Lock()
        self.initialized = False

    def safe_account(self):
        """Check on every request; identity is internal and never returned to clients."""
        if not self.mt5:
            return False, 'MT5 Python package is not installed.', None
        try:
            if not self.initialized:
                if not self.mt5.initialize():
                    return False, 'MT5 could not initialize. Open the correct signed-in terminal.', None
                self.initialized = True
            terminal = self.mt5.terminal_info()
            if terminal is None or getattr(terminal, 'connected', None) is not True:
                return False, 'MT5 is not connected to its trade server.', None
            account = self.mt5.account_info()
            if account is None or getattr(account, 'trade_allowed', None) is not False:
                return False, 'Read-only permission check failed. Sign in with the investor password.', None
            login, server = getattr(account, 'login', None), getattr(account, 'server', None)
            if type(login) is not int or login <= 0 or not isinstance(server, str) or not server.strip():
                return False, 'Account identity unavailable; no prices shown.', None
            return True, 'Connected; trading permission is disabled.', (login, server)
        except Exception:
            return False, 'MT5 check failed. Check the terminal and restart the bridge.', None

    def status(self):
        with self.lock:
            ok, message, _identity = self.safe_account()
            return {'connected': ok, 'message': message}

    def quotes(self):
        with self.lock:
            ok, message, identity = self.safe_account()
            if not ok:
                return {'connected': False, 'message': message, 'quotes': []}
            try:
                available = self.mt5.symbols_get()
                if available is None:
                    return {'connected': False, 'message': 'Could not discover broker symbols.', 'quotes': []}
                names = {getattr(s, 'name', '') for s in available}
                result = []
                for canonical in SYMBOLS:
                    matches = [canonical + suffix for suffix in SUFFIXES if canonical + suffix in names]
                    q = {'market': canonical, 'state': 'unavailable'}
                    if not matches:
                        q['reason'] = 'No supported exact symbol found.'
                    elif len(matches) != 1:
                        q['state'] = 'ambiguous'
                        q['reason'] = 'More than one supported symbol found; none selected.'
                    else:
                        name = matches[0]
                        q['symbol'] = name
                        if not self.mt5.symbol_select(name, True):
                            q['reason'] = 'Could not enable this symbol in Market Watch.'
                        else:
                            tick = self.mt5.symbol_info_tick(name)
                            if tick is None:
                                q['reason'] = 'No tick available.'
                            else:
                                msc = getattr(tick, 'time_msc', 0)
                                seconds = getattr(tick, 'time', 0)
                                timestamp = msc / 1000 if isinstance(msc, (int, float)) and msc > 0 else seconds
                                if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= 0:
                                    q['reason'] = 'Tick has no valid timestamp.'
                                else:
                                    instant = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
                                    q['tick_time_utc'] = instant.isoformat(timespec='milliseconds' if msc else 'seconds').replace('+00:00', 'Z')
                                    age = self.clock() - timestamp
                                    if age > MAX_AGE_SECONDS or age < -FUTURE_TOLERANCE_SECONDS:
                                        q['state'] = 'stale'
                                        q['reason'] = 'Tick is old or in the future; check market hours and PC clock.'
                                    else:
                                        bid, ask = getattr(tick, 'bid', None), getattr(tick, 'ask', None)
                                        if (isinstance(bid, (int, float)) and isinstance(ask, (int, float))
                                                and math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
                                            q.update(state='fresh', bid=bid, ask=ask)
                                        else:
                                            q['reason'] = 'Tick has invalid bid or ask.'
                    result.append(q)
                # Fail closed if the account was switched or disconnected during collection.
                ok, message, end_identity = self.safe_account()
                if not ok:
                    return {'connected': False, 'message': message, 'quotes': []}
                if identity != end_identity:
                    return {'connected': False, 'message': 'Account changed during collection; no prices shown.', 'quotes': []}
                return {'connected': True, 'message': message, 'quotes': result}
            except Exception:
                return {'connected': False, 'message': 'Quote retrieval failed; no prices shown.', 'quotes': []}


def make_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log paths, query strings, or account details.

        def do_GET(self):
            # Reject DNS rebinding, cross-origin requests, query strings and arbitrary files.
            hosts = self.headers.get_all('Host', [])
            origins = self.headers.get_all('Origin', [])
            sites = self.headers.get_all('Sec-Fetch-Site', [])
            if (hosts != [f'{HOST}:{PORT}'] or len(origins) > 1
                    or (origins and origins[0] != ORIGIN)
                    or len(sites) > 1 or (sites and sites[0] not in ('same-origin', 'none'))):
                self.send_error(403)
                return
            path = urlsplit(self.path)
            if path.query or path.fragment:
                self.send_error(404)
                return
            if path.path in ('/', '/index.html'):
                try:
                    body = OFFICE.read_bytes()
                except OSError:
                    self.send_error(503)
                    return
                mime = 'text/html; charset=utf-8'
            elif path.path in ('/status', '/quotes', '/fed-headlines'):
                data = bridge.status() if path.path == '/status' else bridge.quotes() if path.path == '/quotes' else fed_headlines()
                body = json.dumps(data, allow_nan=False).encode('utf-8')
                mime = 'application/json; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        mt5 = None
    bridge = Bridge(mt5)
    try:
        server = ThreadingHTTPServer((HOST, PORT), make_handler(bridge))
    except OSError:
        print('Could not start: port 8765 is already in use. Close the other bridge and try again.')
        return
    print('AKM local office: ' + ORIGIN, flush=True)
    print('Keep this window open. Close it or press Ctrl+C to stop.', flush=True)
    try:
        import webbrowser
        webbrowser.open(ORIGIN)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if mt5 and bridge.initialized:
            mt5.shutdown()


if __name__ == '__main__':
    main()
