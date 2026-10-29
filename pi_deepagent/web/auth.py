"""Local administrator credentials, sessions and known-secret filtering."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


class AuthError(Exception):
    def __init__(self, status: int, message: str = '請重新登入。'):
        self.status = status
        self.message = message
        super().__init__(message)


class Redactor:
    """Filter known credentials; non-final calls receive the complete raw buffer."""
    def __init__(self):
        self._values: set[str] = set()
        self.add(*(value for name, value in os.environ.items()
                   if re.search(r'API_?KEY|TOKEN|PASSWORD|SECRET', name, re.I)))

    def add(self, *values: str) -> None:
        self._values.update(value for value in values if isinstance(value, str) and value)

    def clean(self, text: str, final: bool = True) -> str:
        values = sorted(self._values, key=len, reverse=True)
        if not values:
            return text
        # A full match must win over a partial prefix of another credential.
        pattern = re.compile('|'.join(re.escape(value) for value in values))
        matches = list(pattern.finditer(text))
        cutoff = len(text)
        if not final:
            for value in values:
                for length in range(min(len(value)-1, len(text)), 0, -1):
                    position = len(text)-length
                    if text.endswith(value[:length]) and not any(m.start() <= position < m.end() for m in matches):
                        cutoff = min(cutoff, position)
                        break
        return pattern.sub('[REDACTED]', text[:cutoff])


@dataclass
class Session:
    csrf: str
    expires_at: float
    name: str = 'admin'
    role: str = 'admin'


class AuthService:
    def __init__(self, data_dir: Path, redactor: Redactor, *, clock=time.time):
        self.data_dir = Path(data_dir)
        self.redactor = redactor
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.failures: OrderedDict[str, list[float]] = OrderedDict()
        self.credential_file = self.data_dir / 'admin.json'
        self._record = None
        if self.credential_file.exists():
            self._record = json.loads(self.credential_file.read_text(encoding='utf-8'))
            if self._record.get('algorithm') != 'scrypt':
                raise ValueError('無效的管理員憑證。')
            self.redactor.add(self._record['salt'], self._record['hash'])

    def initialize(self, password: str) -> None:
        if len(password) < 12 or password == 'pi-demo':
            raise ValueError('密碼至少需要 12 個字元。')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1).hex()
        record = {'algorithm': 'scrypt', 'salt': salt.hex(), 'hash': digest}
        with self.credential_file.open('x', encoding='utf-8') as stream:
            json.dump(record, stream)
        if os.name != 'nt':
            self.credential_file.chmod(0o600)
        self._record = record
        self.redactor.add(password, record['salt'], record['hash'])

    @property
    def initialized(self) -> bool:
        return self._record is not None

    def clear_sessions(self) -> None:
        self.sessions.clear()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def login(self, username: str, password: str, source: str) -> tuple[str, str]:
        now = self.clock()
        failures = [stamp for stamp in self.failures.get(source, []) if stamp > now-60]
        if len(failures) >= 5:
            raise AuthError(429, '登入嘗試過多，請稍後再試。')
        if not self._record:
            raise AuthError(401, '帳號或密碼錯誤。')
        salt = bytes.fromhex(self._record['salt'])
        digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1).hex()
        valid = hmac.compare_digest(digest, self._record['hash'])
        if username != 'admin' or not valid:
            failures.append(now)
            self.failures[source] = failures
            self.failures.move_to_end(source)
            while len(self.failures) > 4096:
                self.failures.popitem(last=False)
            raise AuthError(401, '帳號或密碼錯誤。')
        self.failures.pop(source, None)
        # Do not retain expired sessions indefinitely.
        self.sessions = {key: session for key, session in self.sessions.items() if session.expires_at > now}
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self.sessions[self._digest(token)] = Session(csrf, now+8*3600)
        self.redactor.add(password, token, csrf)
        return token, csrf

    def authenticate(self, token: str) -> Session:
        key = self._digest(token)
        session = self.sessions.get(key)
        if session is None or session.expires_at <= self.clock():
            self.sessions.pop(key, None)
            raise AuthError(401)
        return session

    def logout(self, token: str) -> None:
        self.sessions.pop(self._digest(token), None)
