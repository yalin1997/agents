"""Small ASGI transport; authorization precedes every private dispatch."""
from __future__ import annotations

import hmac
import json
import logging
import re
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from uuid import UUID

from .auth import AuthError, AuthService, Redactor

AUDIT = logging.getLogger('pi_deepagent.web.audit')
STATIC = Path(__file__).with_name('static')
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.css': ('app.css', 'text/css; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/state.mjs': ('state.mjs', 'text/javascript; charset=utf-8')}


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def _uuid(value):
    if not isinstance(value, str):
        raise HTTPError(400, '識別碼格式錯誤。')
    try:
        UUID(value)
    except (ValueError, AttributeError):
        raise HTTPError(400, '識別碼格式錯誤。') from None
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


class WebApp:
    def __init__(self, data_dir, workspace, model, origin, runner_factory=None):
        self.workspace = Path(workspace).resolve()
        self.data_dir = Path(data_dir).resolve()
        if not self.workspace.is_dir():
            raise ValueError('工作目錄不存在。')
        if self.data_dir == self.workspace or self.data_dir.is_relative_to(self.workspace):
            raise ValueError('服務資料目錄不可位於 Agent 工作目錄內。')
        if not isinstance(model, str) or ':' not in model or not all(model.split(':', 1)):
            raise ValueError('模型必須包含 provider:model。')
        parsed = urlsplit(origin)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('來源必須是 http://127.0.0.1:PORT。')
        self.origin = origin
        self.host = parsed.netloc
        self.redactor = Redactor()
        self.auth = AuthService(self.data_dir, self.redactor)
        if not self.auth.initialized:
            raise ValueError('請先執行 init-admin 初始化管理員密碼。')
        if runner_factory is None:
            from .runner import make_runner_factory
            runner_factory = make_runner_factory(self.workspace, model)
        from .service import WorkspaceService
        self.service = WorkspaceService(self.data_dir / 'tasks.sqlite', self.workspace, runner_factory, self.redactor)
        self.ready = False
        self.audit = logging.getLogger(f'pi_deepagent.web.audit.{id(self)}')
        self.audit.propagate = False
        self.audit.setLevel(logging.INFO)
        self.audit_handler = None

    async def startup(self):
        await self.service.startup()
        try:
            self.audit_handler = logging.FileHandler(self.data_dir / 'audit.log', encoding='utf-8')
            self.audit_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.audit.addHandler(self.audit_handler)
        except Exception:
            await self.service.shutdown()
            raise
        self.auth.clear_sessions()
        self.ready = True

    async def shutdown(self):
        self.ready = False
        try:
            await self.service.shutdown()
        finally:
            self.auth.clear_sessions()
            if self.audit_handler:
                self.audit.removeHandler(self.audit_handler)
                self.audit_handler.close()
                self.audit_handler = None

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    try:
                        await self.startup()
                    except Exception:
                        await send({'type': 'lifespan.startup.failed', 'message': '工作台啟動失敗，請檢查本機設定。'})
                        return
                    await send({'type': 'lifespan.startup.complete'})
                elif message['type'] == 'lifespan.shutdown':
                    try:
                        await self.shutdown()
                    except Exception:
                        await send({'type': 'lifespan.shutdown.failed', 'message': '工作台關閉失敗。'})
                        return
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
            return
        if scope['type'] != 'http':
            return
        status, payload, content_type, extra = 500, {'error': '處理失敗，請稍後再試。'}, 'application/json; charset=utf-8', []
        identity = 'anonymous'
        try:
            status, payload, content_type, extra, identity = await self.dispatch(scope, receive)
        except (HTTPError, AuthError) as error:
            status, payload = error.status, {'error': error.message}
        except Exception as error:
            from .service import WebError
            if isinstance(error, WebError):
                status, payload = error.status, {'error': self.redactor.clean(str(getattr(error, 'message', error)))}
            # Unknown exceptions intentionally never reach browser or logs.
        path = scope.get('path', '')
        # Do not log attacker-controlled path or headers (may contain credentials).
        route = path if path in ASSETS or path in ('/api/login', '/api/logout', '/api/session', '/api/tasks', '/api/tasks/purge') else '/api/resource'
        match = re.fullmatch(r'/api/tasks/([0-9a-fA-F-]{36})(?:/(?:result|messages|stop|retry|archive|restore))?', path)
        if match:
            try:
                route = '/api/tasks/' + str(UUID(match.group(1)))
                if path.endswith(('/archive', '/restore')):
                    route += '/' + path.rsplit('/', 1)[-1]
            except ValueError:
                pass
        if scope.get('_audit_targets'):
            route += ' ids=' + scope['_audit_targets']
        self.audit.info('action=%s identity=%s target=%s outcome=%s', scope.get('method', '') if scope.get('method', '') in ('GET','POST','PUT','DELETE','PATCH','OPTIONS','HEAD') else 'OTHER', scope.get('_audit_identity', identity), route, 'allow' if status < 400 else 'deny')
        headers = [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'), (b'content-type', content_type.encode()), (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]+extra
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': body})

    async def dispatch(self, scope, receive):
        headers = {}
        for raw_name, raw_value in scope.get('headers', []):
            name = raw_name.decode('latin-1').lower()
            if name in headers and name in ('host', 'origin', 'cookie', 'x-csrf-token', 'content-type'):
                raise HTTPError(400, '請求標頭重複。')
            headers[name] = raw_value.decode('latin-1')
        if headers.get('host') != self.host:
            raise HTTPError(403, '來源不被允許。')
        method, path = scope['method'], scope.get('path', '')
        if method == 'GET' and path in ASSETS:
            filename, content_type = ASSETS[path]
            return 200, (STATIC / filename).read_bytes(), content_type, [], 'anonymous'
        if not path.startswith('/api/'):
            raise HTTPError(404, '找不到此頁面。')
        if not self.ready:
            raise HTTPError(503, '工作台尚未就緒。')
        is_login = path == '/api/login' and method == 'POST'
        session, token = None, ''
        if not is_login:
            try:
                cookies = SimpleCookie(); cookies.load(headers.get('cookie', ''))
                token = cookies['pi_session'].value if 'pi_session' in cookies else ''
            except CookieError:
                token = ''
            session = self.auth.authenticate(token)
            scope['_audit_identity'] = session.name if session.name == 'admin' else 'other'
            if session.role != 'admin' or session.name != 'admin':
                raise HTTPError(403, '沒有操作權限。')
        if method not in ('GET', 'POST'):
            raise HTTPError(405, '不支援此操作。')
        data = None
        if method == 'POST':
            if headers.get('origin') != self.origin:
                raise HTTPError(403, '來源不被允許。')
            if headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise HTTPError(415, '請使用 JSON。')
            if session and not hmac.compare_digest(headers.get('x-csrf-token', '').encode('utf-8'), session.csrf.encode('utf-8')):
                raise HTTPError(403, '驗證碼已失效，請重新整理。')
            chunks, size = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    raise HTTPError(400, '請求中斷。')
                chunk = message.get('body', b''); size += len(chunk)
                if size > 128*1024:
                    raise HTTPError(413, '內容過長。')
                chunks.append(chunk)
                if not message.get('more_body', False):
                    break
            try:
                data = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise HTTPError(400, 'JSON 格式錯誤。') from None
            if not isinstance(data, dict):
                raise HTTPError(400, 'JSON 必須是物件。')
            try:
                for key, value in data.items():
                    key.encode('utf-8')
                    if isinstance(value, str):
                        value.encode('utf-8')
            except UnicodeError:
                raise HTTPError(400, '文字編碼錯誤。') from None
        def fields(*expected):
            if data is None or set(data) != set(expected):
                raise HTTPError(400, '請求欄位不正確。')
        identity = 'admin' if session else 'anonymous'
        if is_login:
            fields('username', 'password')
            if not isinstance(data['username'], str) or not isinstance(data['password'], str):
                raise HTTPError(400, '帳密格式不正確。')
            token, csrf = self.auth.login(data['username'], data['password'], str(scope.get('client', ('local',))[0]))
            return 200, self.identity(csrf), 'application/json; charset=utf-8', [(b'set-cookie', f'pi_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'.encode())], 'admin'
        result, status = None, 200
        if method == 'GET' and path == '/api/session':
            result = self.identity(session.csrf)
        elif method == 'POST' and path == '/api/logout':
            fields(); self.auth.logout(token)
            return 200, {}, 'application/json; charset=utf-8', [(b'set-cookie', b'pi_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')], identity
        elif path == '/api/tasks' and method == 'GET':
            query = parse_qs(scope.get('query_string', b'').decode('ascii', errors='replace'))
            result = {'tasks': self.service.list_tasks(archived=query.get('archived') == ['true'])}
        elif path == '/api/tasks/purge' and method == 'POST':
            fields('request_id', 'task_ids')
            if isinstance(data['task_ids'], list):
                scope['_audit_targets'] = ','.join(str(UUID(_uuid(i))) for i in data['task_ids'])
            result = self.service.purge(data['task_ids'], _uuid(data['request_id']))
        elif path == '/api/tasks' and method == 'POST':
            fields('request_id'); result = self.service.create(_uuid(data['request_id'])); status = 201
        else:
            segments = path.split('/')
            if len(segments) not in (4, 5, 6) or segments[1:3] != ['api', 'tasks']:
                raise HTTPError(404, '找不到此操作。')
            task_id = _uuid(segments[3])
            if method == 'GET' and len(segments) == 6 and segments[4] == 'artifacts':
                from urllib.parse import quote
                artifact_id = _uuid(segments[5])
                name, content = self.service.artifact(task_id, artifact_id)
                filename = quote(name.rsplit('/', 1)[-1], safe='')
                return 200, content, 'application/octet-stream', [(b'content-disposition', f"attachment; filename=artifact.txt; filename*=UTF-8''{filename}".encode('ascii'))], identity
            elif method == 'GET' and len(segments) == 4:
                result = self.service.get_task(task_id)
            elif method == 'GET' and len(segments) == 5 and segments[4] == 'result':
                task = self.service.get_task(task_id)
                if not task.get('output'):
                    raise HTTPError(409, '尚無成果。')
                return 200, self.redactor.clean(task['output']).encode('utf-8'), 'text/plain; charset=utf-8', [(b'content-disposition', f'attachment; filename="pi-result-{UUID(task_id)}.txt"'.encode())], identity
            elif method == 'POST' and len(segments) == 5 and segments[4] in ('archive', 'restore'):
                fields('request_id')
                result = self.service.set_archived(task_id, segments[4] == 'archive', _uuid(data['request_id']))
            elif method == 'POST' and len(segments) == 5 and segments[4] in ('messages', 'stop', 'retry'):
                action = segments[4]
                fields('request_id', 'text' if action == 'messages' else 'expected_run_id')
                request_id = _uuid(data['request_id'])
                if action == 'messages':
                    text = data['text']
                    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
                        raise HTTPError(400, '需求須為 1 至 32000 字元。')
                    result = await self.service.message(task_id, text, request_id)
                else:
                    expected = _uuid(data['expected_run_id'])
                    result = await getattr(self.service, action)(task_id, expected, request_id)
                status = 202
            else:
                raise HTTPError(404, '找不到此操作。')
        return status, result, 'application/json; charset=utf-8', [], identity

    def identity(self, csrf):
        return {'identity': {'name': 'admin', 'role': 'admin'}, 'csrf': csrf, 'workspace': self.workspace.name}


def create_app(data_dir, workspace, model, origin, runner_factory=None):
    return WebApp(data_dir, workspace, model, origin, runner_factory)
