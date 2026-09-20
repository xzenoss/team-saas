import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from datetime import date, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('TEAMSAAS_DB', str(ROOT / 'data' / 'team.db')))
SESSION_AGE = 7 * 86400


def connect():
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members(workspace_id TEXT REFERENCES workspaces(id), user_id TEXT REFERENCES users(id), role TEXT NOT NULL, PRIMARY KEY(workspace_id,user_id));
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), workspace_id TEXT REFERENCES workspaces(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id), name TEXT NOT NULL, description TEXT NOT NULL, color TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id), project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, title TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL, priority TEXT NOT NULL, assignee_id TEXT REFERENCES users(id), due TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id), expires REAL NOT NULL, created_by TEXT REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT REFERENCES workspaces(id), actor TEXT NOT NULL, message TEXT NOT NULL, created REAL NOT NULL);
        ''')


def uid():
    return secrets.token_hex(12)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    return salt + ':' + digest


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class ApiError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def field(data, key, maximum=160, required=True):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ApiError('Invalid ' + key.replace('_', ' ') + '.')
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise ApiError(key.replace('_', ' ').capitalize() + ' must be between 1 and ' + str(maximum) + ' characters.' if required else 'Value is too long.')
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = 'Gather/1.0'

    def send_json(self, data, status=200, cookie=None):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(payload)

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['gather_session'].value if 'gather_session' in cookie else ''
        except Exception:
            return ''

    def auth(self, db):
        session = db.execute('SELECT s.*, u.name, u.email, m.role FROM sessions s JOIN users u ON u.id=s.user_id JOIN members m ON m.user_id=s.user_id AND m.workspace_id=s.workspace_id WHERE s.token=? AND s.expires>?', (digest(self.token()), time.time())).fetchone()
        if not session:
            raise ApiError('Please sign in to continue.', 401)
        return session

    def state(self, db, s):
        wid = s['workspace_id']
        return {
            'user': {'id': s['user_id'], 'name': s['name'], 'email': s['email'], 'role': s['role']},
            'workspace': dict(db.execute('SELECT * FROM workspaces WHERE id=?', (wid,)).fetchone()),
            'csrf': s['csrf'],
            'projects': [dict(r) for r in db.execute('SELECT * FROM projects WHERE workspace_id=? ORDER BY created', (wid,))],
            'tasks': [dict(r) for r in db.execute('SELECT * FROM tasks WHERE workspace_id=? ORDER BY created DESC', (wid,))],
            'members': [dict(r) for r in db.execute('SELECT u.id,u.name,u.email,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=? ORDER BY u.name', (wid,))],
            'activity': [dict(r) for r in db.execute('SELECT actor,message,created FROM activity WHERE workspace_id=? ORDER BY id DESC LIMIT 12', (wid,))]
        }

    def log_activity(self, db, s, message):
        db.execute('INSERT INTO activity(workspace_id,actor,message,created) VALUES(?,?,?,?)', (s['workspace_id'], s['name'], message, time.time()))

    def start_session(self, db, user_id, workspace_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (digest(token), user_id, workspace_id, csrf, time.time() + SESSION_AGE))
        cookie = 'gather_session=' + token + '; HttpOnly; SameSite=Lax; Path=/; Max-Age=' + str(SESSION_AGE)
        if os.environ.get('TEAMSAAS_SECURE_COOKIE') == '1':
            cookie += '; Secure'
        return cookie

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            try:
                with connect() as db:
                    s = self.auth(db)
                    if path != '/api/state':
                        raise ApiError('Not found.', 404)
                    self.send_json(self.state(db, s))
            except ApiError as e:
                self.send_json({'error': e.message}, e.status)
            return
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        if path not in files:
            self.send_error(404)
            return
        filename, content_type = files[path]
        payload = (ROOT / 'static' / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')):
                raise ApiError('Request origin is not allowed.', 403)
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 32768:
                raise ApiError('Invalid request size.', 413)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ApiError('Expected a JSON object.')
            with connect() as db:
                result, cookie = self.handle_post(db, urlparse(self.path).path, data)
            self.send_json(result, cookie=cookie)
        except ApiError as e:
            self.send_json({'error': e.message}, e.status)
        except (ValueError, TypeError):
            self.send_json({'error': 'Invalid request.'}, 400)
        except sqlite3.IntegrityError:
            self.send_json({'error': 'That change conflicts with an existing record.'}, 409)
        except Exception as e:
            self.log_error('Request failed: %s', type(e).__name__)
            self.send_json({'error': 'Something went wrong. Please try again.'}, 500)

    def handle_post(self, db, path, data):
        if path in ('/api/register', '/api/demo', '/api/login'):
            if path == '/api/login':
                email = field(data, 'email', 254).lower()
                password = field(data, 'password', 128)
                user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                salt = user['password'].split(':')[0] if user else '00' * 16
                computed = hash_password(password, salt)
                if not user or not hmac.compare_digest(computed, user['password']):
                    raise ApiError('Email or password is incorrect.', 401)
                user_id = user['id']
                wid = db.execute('SELECT workspace_id FROM members WHERE user_id=? ORDER BY rowid LIMIT 1', (user_id,)).fetchone()['workspace_id']
            else:
                demo = path == '/api/demo'
                name = 'Alex Morgan' if demo else field(data, 'name', 80)
                email = 'demo-' + uid() + '@example.test' if demo else field(data, 'email', 254).lower()
                password = secrets.token_urlsafe(32) if demo else field(data, 'password', 128)
                workspace = 'Studio North' if demo else field(data, 'workspace', 80)
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    raise ApiError('Enter a valid email address.')
                if len(password) < 10:
                    raise ApiError('Use a password with at least 10 characters.')
                if db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
                    raise ApiError('An account with that email already exists.', 409)
                user_id, wid = uid(), uid()
                db.execute('INSERT INTO users VALUES(?,?,?,?)', (user_id, name, email, hash_password(password)))
                db.execute('INSERT INTO workspaces VALUES(?,?)', (wid, workspace))
                db.execute('INSERT INTO members VALUES(?,?,?)', (wid, user_id, 'owner'))
                if demo:
                    self.seed(db, wid, user_id)
            cookie = self.start_session(db, user_id, wid)
            return {'ok': True}, cookie

        s = self.auth(db)
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), s['csrf']):
            raise ApiError('Session validation failed. Refresh and try again.', 403)
        wid = s['workspace_id']
        if path == '/api/logout':
            db.execute('DELETE FROM sessions WHERE token=?', (s['token'],))
            return {'ok': True}, 'gather_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'
        if path == '/api/projects/create':
            name = field(data, 'name', 100)
            description = field(data, 'description', 1000, False)
            color = field(data, 'color', 20)
            if color not in ('violet', 'orange', 'blue', 'green'):
                raise ApiError('Choose a valid color.')
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (uid(), wid, name, description, color, time.time()))
            self.log_activity(db, s, 'created ' + name)
        elif path in ('/api/tasks/create', '/api/tasks/update'):
            title = field(data, 'title', 160)
            description = field(data, 'description', 3000, False)
            project = field(data, 'project_id', 40)
            status = field(data, 'status', 20)
            priority = field(data, 'priority', 20)
            assignee = field(data, 'assignee_id', 40, False) or None
            due = field(data, 'due', 10, False)
            if status not in ('todo', 'progress', 'done') or priority not in ('low', 'medium', 'high'):
                raise ApiError('Invalid task status or priority.')
            if due:
                try:
                    date.fromisoformat(due)
                except ValueError:
                    raise ApiError('Enter a valid due date.')
            if not db.execute('SELECT 1 FROM projects WHERE id=? AND workspace_id=?', (project, wid)).fetchone():
                raise ApiError('Project not found.', 404)
            if assignee and not db.execute('SELECT 1 FROM members WHERE user_id=? AND workspace_id=?', (assignee, wid)).fetchone():
                raise ApiError('Assignee must belong to this workspace.')
            values = (project, title, description, status, priority, assignee, due)
            if path.endswith('/create'):
                db.execute('INSERT INTO tasks(id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created) VALUES(?,?,?,?,?,?,?,?,?,?)', (uid(), wid) + values + (time.time(),))
                self.log_activity(db, s, 'added ' + title)
            else:
                task_id = field(data, 'id', 40)
                result = db.execute('UPDATE tasks SET project_id=?,title=?,description=?,status=?,priority=?,assignee_id=?,due=? WHERE id=? AND workspace_id=?', values + (task_id, wid))
                if not result.rowcount:
                    raise ApiError('Task not found.', 404)
                self.log_activity(db, s, 'updated ' + title)
        elif path == '/api/tasks/delete':
            task_id = field(data, 'id', 40)
            row = db.execute('SELECT title FROM tasks WHERE id=? AND workspace_id=?', (task_id, wid)).fetchone()
            if not row:
                raise ApiError('Task not found.', 404)
            db.execute('DELETE FROM tasks WHERE id=? AND workspace_id=?', (task_id, wid))
            self.log_activity(db, s, 'deleted ' + row['title'])
        elif path == '/api/invites/create':
            if s['role'] != 'owner':
                raise ApiError('Only workspace owners can invite people.', 403)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO invites VALUES(?,?,?,?)', (digest(token), wid, time.time() + 86400 * 7, s['user_id']))
            self.log_activity(db, s, 'created a team invitation')
            return {'token': token}, None
        elif path == '/api/join':
            token = field(data, 'token', 100)
            invite = db.execute('SELECT * FROM invites WHERE token=? AND expires>?', (digest(token), time.time())).fetchone()
            if not invite:
                raise ApiError('This invitation has expired or has already been used.', 404)
            db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)', (invite['workspace_id'], s['user_id'], 'member'))
            db.execute('UPDATE sessions SET workspace_id=? WHERE token=?', (invite['workspace_id'], s['token']))
            db.execute('DELETE FROM invites WHERE token=?', (digest(token),))
            self.log_activity(db, {'workspace_id': invite['workspace_id'], 'name': s['name']}, 'joined the workspace')
        elif path == '/api/settings':
            if s['role'] != 'owner':
                raise ApiError('Only workspace owners can change workspace settings.', 403)
            db.execute('UPDATE workspaces SET name=? WHERE id=?', (field(data, 'name', 80), wid))
            self.log_activity(db, s, 'updated workspace settings')
        else:
            raise ApiError('Not found.', 404)
        return self.state(db, self.auth(db)), None

    def seed(self, db, wid, user_id):
        projects = [
            ('Website redesign', 'A fresh digital home for our next chapter.', 'violet'),
            ('Product launch', 'Bring something great into the world.', 'orange'),
            ('Brand identity', 'Make every touchpoint feel like us.', 'blue'),
            ('Customer experience', 'Small details. Happier customers.', 'green')
        ]
        ids = []
        for name, description, color in projects:
            pid = uid()
            ids.append(pid)
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (pid, wid, name, description, color, time.time()))
        tasks = [
            (0, 'Explore homepage concepts', 'Try two directions for the new homepage. Keep the story simple and human.', 'progress', 'high', 1),
            (0, 'Build the component library', 'Document buttons, inputs, cards, and navigation states.', 'progress', 'medium', 3),
            (0, 'Review navigation structure', 'Walk through the main journeys before the next design review.', 'todo', 'medium', 2),
            (0, 'Complete discovery interviews', 'Summarize our five customer conversations.', 'done', 'high', -2),
            (1, 'Draft launch announcement', 'Write the launch email and a short product update.', 'todo', 'high', 2),
            (1, 'Prepare launch checklist', 'Bring all launch dependencies into one place.', 'done', 'medium', -1),
            (1, 'Review early access feedback', 'Group the feedback into themes and priorities.', 'progress', 'medium', 4),
            (2, 'Explore the color palette', 'Test accessible combinations for the refreshed identity.', 'todo', 'low', 5),
            (2, 'Finalize brand principles', 'Capture the three ideas that define our voice.', 'done', 'medium', -3),
            (2, 'Design social templates', 'Create reusable templates for the launch campaign.', 'todo', 'low', 7),
            (3, 'Map the onboarding journey', 'Identify the moments that need clearer guidance.', 'progress', 'high', 0),
            (3, 'Audit the help center', 'Find gaps in the most frequently visited articles.', 'todo', 'medium', 6)
        ]
        for index, (p, title, description, status, priority, days) in enumerate(tasks):
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)', (uid(), wid, ids[p], title, description, status, priority, user_id if index % 3 != 2 else None, (date.today() + timedelta(days=days)).isoformat(), time.time() - index * 90))
        for i, message in enumerate(['created the workspace', 'added the first projects', 'completed discovery interviews', 'started exploring homepage concepts']):
            db.execute('INSERT INTO activity(workspace_id,actor,message,created) VALUES(?,?,?,?)', (wid, 'Alex Morgan', message, time.time() - (4 - i) * 1800))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gather team workspace')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    initialize()
    print('Gather is running at http://' + args.host + ':' + str(args.port), flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()

