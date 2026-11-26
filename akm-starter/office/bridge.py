"""Local quote-only bridge; no login, credential handling, or trading operations."""
import datetime
import json
import math
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class Bridge:
    def __init__(self, mt5, clock=None):
        self.mt5 = mt5
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc).timestamp())
        self.lock = Lock()
        self.initialized = False

    def safe_account(self):
        """Check on every request, not just on startup; never return account details."""
        if not self.mt5:
            return False, 'MT5 Python package is not installed.'
        try:
            if not self.initialized:
                if not self.mt5.initialize():
                    return False, 'MT5 could not initialize. Open the correct signed-in terminal.'
                self.initialized = True
            terminal = self.mt5.terminal_info()
            if terminal is None or getattr(terminal, 'connected', None) is not True:
                return False, 'MT5 is not connected to its trade server.'
            account = self.mt5.account_info()
            if account is None or getattr(account, 'trade_allowed', None) is not False:
                return False, 'Read-only permission check failed. Sign in with the investor password.'
            return True, 'Connected; trading permission is disabled.'
        except Exception:
            return False, 'MT5 check failed. Check the terminal and restart the bridge.'

    def status(self):
        with self.lock:
            ok, message = self.safe_account()
            return {'connected': ok, 'message': message}

    def quotes(self):
        with self.lock:
            ok, message = self.safe_account()
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
                ok, message = self.safe_account()
                if not ok:
                    return {'connected': False, 'message': message, 'quotes': []}
                return {'connected': True, 'message': message, 'quotes': result}
            except Exception:
                return {'connected': False, 'message': 'Quote retrieval failed; no prices shown.', 'quotes': []}


def make_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log paths, query strings, or account details.

        def do_GET(self):
            # Reject DNS rebinding, cross-origin requests, query strings and arbitrary files.
            origin = self.headers.get('Origin')
            if self.headers.get('Host') != f'{HOST}:{PORT}' or (origin and origin != ORIGIN):
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
            elif path.path in ('/status', '/quotes'):
                data = bridge.status() if path.path == '/status' else bridge.quotes()
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
        server = HTTPServer((HOST, PORT), make_handler(bridge))
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
