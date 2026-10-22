"""Owner-only email notifications, persisted separately from pipeline execution."""
from __future__ import annotations

import json
import logging
import re
import smtplib
import sqlite3
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import auth

LOG = logging.getLogger(__name__)
EVENTS = {
    'SUCCEEDED': ('分析完成', '可打开任务详情查看和下载结果。'),
    'FAILED': ('分析失败', '请查看错误原因和运行日志，修正后可按需续跑。'),
    'INTERRUPTED': ('执行中断', '请检查任务详情与日志，确认当前运行状态。'),
    'COMPLETED_WITHOUT_MARKER': ('执行异常', '未发现完整完成标记，请检查任务详情与日志。'),
}
DEFAULTS = dict(enabled=False, host='smtp.qq.com', port=465, encryption='ssl', sender_email='',
                username='', public_base_url='')


def email_address(value: str, *, optional=False) -> str:
    value = value.strip()
    if not value and optional:
        return ''
    # A single ASCII mailbox; no display names, lists, whitespace or header controls.
    if (len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9.-]+", value)
            or value.count('@') != 1):
        raise HTTPException(400, '请填写一个有效邮箱地址')
    local, domain = value.rsplit('@', 1)
    if (len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local
            or '.' not in domain or any(not label or len(label) > 63 or label.startswith('-')
                                      or label.endswith('-') for label in domain.split('.'))):
        raise HTTPException(400, '请填写一个有效邮箱地址')
    return local + '@' + domain.lower()


def init_schema(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(users)')}
    if 'notification_email' not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN notification_email TEXT NOT NULL DEFAULT ''")
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS mail_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL,
            password TEXT NOT NULL DEFAULT '', updated_by TEXT, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mail_outbox (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, recipient TEXT NOT NULL,
            job_id TEXT, attempt_no INTEGER NOT NULL, event TEXT NOT NULL,
            payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL, sent_at INTEGER, last_error TEXT,
            UNIQUE(job_id,attempt_no,event)
        );
        CREATE INDEX IF NOT EXISTS idx_mail_outbox_due ON mail_outbox(status,next_attempt_at);
    ''')


def settings(connection, *, secret=False):
    row = connection.execute('SELECT * FROM mail_settings WHERE id=1').fetchone()
    values = {**DEFAULTS, **({key: value for key, value in json.loads(row['config_json']).items()
                            if key in DEFAULTS} if row else {})}
    values['password_configured'] = bool(row and row['password'])
    if secret:
        values['password'] = row['password'] if row else ''
    return values


def enqueue(connection, job_id, registry):
    """Called within the status transaction; never connects to SMTP."""
    if not settings(connection)['enabled']:
        return
    row = connection.execute('SELECT j.*,u.notification_email,u.active FROM jobs j '
                             'JOIN users u ON u.id=j.owner_id WHERE j.id=?', (job_id,)).fetchone()
    if not row or row['status'] not in EVENTS or not row['active'] or not row['notification_email']:
        return
    payload = {key: row[key] for key in ('id', 'dataset', 'status', 'attempt_no', 'started_at',
                                       'ended_at', 'current_stage', 'last_error')}
    pipeline = registry.get(row['pipeline'], {})
    payload['pipeline_label'] = pipeline.get('label', row['pipeline'])
    payload['stage_label'] = pipeline.get('stage_labels', {}).get(row['current_stage'], row['current_stage'])
    timestamp = int(time.time())
    connection.execute('INSERT OR IGNORE INTO mail_outbox '
                       '(id,owner_id,recipient,job_id,attempt_no,event,payload_json,next_attempt_at,created_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, row['owner_id'], row['notification_email'], job_id,
                        row['attempt_no'], row['status'], json.dumps(payload, ensure_ascii=False), timestamp, timestamp))


def date_text(value):
    if not value:
        return '—'
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S（北京时间）')


def message(config, item):
    mail = EmailMessage()
    mail['From'] = 'SCigblast <' + config['sender_email'] + '>'
    mail['To'] = item['recipient']
    mail['Date'] = formatdate(usegmt=True)
    mail['Message-ID'] = f"<scigblast-{item['id']}@{config['sender_email'].rsplit('@', 1)[1]}>"
    if item['event'] == 'TEST':
        mail['Subject'] = '[SCigblast] 邮件提醒测试'
        text = '这是一封 SCigblast 测试邮件。发件服务已成功提交邮件。\n\n平台地址：' + config['public_base_url']
    else:
        data = json.loads(item['payload_json'])
        label, instruction = EVENTS[item['event']]
        dataset = ' '.join(data['dataset'].split())[:160]
        mail['Subject'] = f'[SCigblast] {label} · {dataset}'
        lines = [f'任务：{data["dataset"]}', f'流程：{data["pipeline_label"]}', f'状态：{label}',
                 f'任务编号：{data["id"]}', f'执行次数：{data["attempt_no"]}',
                 f'开始：{date_text(data["started_at"])}', f'结束：{date_text(data["ended_at"])}']
        if data.get('stage_label'):
            lines.append(f'阶段：{data["stage_label"]}')
        if data.get('last_error'):
            lines.append('说明：' + str(data['last_error'])[:1000])
        lines += ['', instruction, '任务详情：' + config['public_base_url'].rstrip('/') +
                  '/jobs/' + quote(data['id'], safe=''), '', '请登录平台查看；邮件不附分析结果或原始序列。']
        text = '\n'.join(lines)
    mail.set_content(text)
    return mail


def send(config, mail):
    context = ssl.create_default_context()
    if config['encryption'] == 'ssl':
        client = smtplib.SMTP_SSL(config['host'], config['port'], timeout=10, context=context)
    else:
        client = smtplib.SMTP(config['host'], config['port'], timeout=10)
    try:
        if config['encryption'] == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if config['username']:
            client.login(config['username'], config['password'])
        client.send_message(mail, from_addr=config['sender_email'], to_addrs=[str(mail['To'])])
    finally:
        # An acknowledged message must not be retried just because QUIT failed.
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            client.close()


def delivery_error(error):
    # SMTP server replies can contain credentials or addresses; never persist them.
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'SMTP 认证失败，请检查账号与授权码'
    if isinstance(error, ssl.SSLError):
        return 'TLS 连接失败，请检查加密方式与服务器证书'
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return '收件邮箱被服务器拒绝，请检查接收邮箱'
    if isinstance(error, smtplib.SMTPSenderRefused):
        return '发件地址被拒绝，请检查发件邮箱与 SMTP 账号'
    if isinstance(error, (TimeoutError, ConnectionError, OSError)) and not isinstance(error, smtplib.SMTPException):
        return '无法连接 SMTP，请检查地址、端口与网络'
    return '邮件发送失败，请检查 SMTP 配置'


class Dispatcher:
    def __init__(self, db):
        self.db = db
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        with self.db() as connection:
            connection.execute("UPDATE mail_outbox SET status='queued' WHERE status='sending'")
        self.thread = threading.Thread(target=self.run, name='scigblast-mail', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=1)

    def run(self):
        while not self.stopping.is_set():
            try:
                worked = self.deliver_one()
            except Exception as error:
                LOG.warning('Mail queue unavailable: %s', type(error).__name__)
                worked = False
            if not worked:
                self.stopping.wait(5)

    def deliver_one(self):
        with self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            config = settings(connection, secret=True)
            if not config['enabled']:
                return False
            item = connection.execute("SELECT * FROM mail_outbox WHERE status IN ('queued','retry') "
                                      'AND next_attempt_at<=? ORDER BY created_at,id LIMIT 1', (int(time.time()),)).fetchone()
            if not item:
                return False
            user = connection.execute('SELECT active,notification_email FROM users WHERE id=?', (item['owner_id'],)).fetchone()
            valid = (item['event'] in EVENTS or item['event'] == 'TEST') and user and user['active'] and user['notification_email'] == item['recipient']
            if valid and item['job_id']:
                job = connection.execute('SELECT owner_id,attempt_no,status FROM jobs WHERE id=?', (item['job_id'],)).fetchone()
                statuses = {'SUCCEEDED', 'ARCHIVING', 'ARCHIVED'} if item['event'] == 'SUCCEEDED' else {item['event']}
                valid = job and job['owner_id'] == item['owner_id'] and job['attempt_no'] == item['attempt_no'] and job['status'] in statuses
            if not valid:
                connection.execute("UPDATE mail_outbox SET status='skipped' WHERE id=?", (item['id'],))
                return True
            connection.execute("UPDATE mail_outbox SET status='sending',attempts=attempts+1 WHERE id=?", (item['id'],))
        try:
            send(config, message(config, item))
        except Exception as error:
            attempts = item['attempts'] + 1
            with self.db() as connection:
                connection.execute('UPDATE mail_outbox SET status=?,last_error=?,next_attempt_at=? WHERE id=?',
                                   ('failed' if attempts >= 3 else 'retry', delivery_error(error),
                                    int(time.time()) + (60 if attempts == 1 else 300), item['id']))
        else:
            with self.db() as connection:
                connection.execute("UPDATE mail_outbox SET status='sent',sent_at=?,last_error=NULL WHERE id=?",
                                   (int(time.time()), item['id']))
        return True


class Preference(BaseModel):
    email: str = Field(default='', max_length=254)


class MailConfig(BaseModel):
    enabled: bool = False
    host: str = Field(default='smtp.qq.com', max_length=253)
    port: int = Field(default=465, ge=1, le=65535)
    encryption: Literal['starttls', 'ssl'] = 'ssl'
    sender_email: str = Field(default='', max_length=254)
    username: str = Field(default='', max_length=254)
    password: str = Field(default='', max_length=512, repr=False)
    clear_password: bool = False
    public_base_url: str = Field(default='', max_length=500)


def router(db):
    routes = APIRouter()

    @routes.get('/api/notifications/preferences')
    def get_preference():
        user = auth.require_user()
        with db() as connection:
            email = connection.execute('SELECT notification_email FROM users WHERE id=?', (user['id'],)).fetchone()[0]
            return {'email': email, 'service_enabled': settings(connection)['enabled']}

    @routes.post('/api/notifications/preferences')
    def save_preference(body: Preference):
        user = auth.require_user()
        email = email_address(body.email, optional=True)
        with db() as connection:
            connection.execute('UPDATE users SET notification_email=? WHERE id=?', (email, user['id']))
        return {'email': email}

    @routes.get('/api/admin/mail-settings')
    def get_settings():
        auth.require_admin()
        with db() as connection:
            return settings(connection)

    @routes.post('/api/admin/mail-settings')
    def save_settings(body: MailConfig):
        admin = auth.require_admin()
        values = body.model_dump(exclude={'password', 'clear_password'})
        values.update(host=body.host.strip(), username=body.username.strip(), public_base_url=body.public_base_url.strip().rstrip('/'))
        if values['host'] and not re.fullmatch(r'[A-Za-z0-9_.:-]+', values['host']):
            raise HTTPException(400, 'SMTP 地址仅填写主机名或 IP，不包含协议或路径')
        values['sender_email'] = email_address(body.sender_email, optional=True)
        if values['public_base_url']:
            try:
                parsed = urlsplit(values['public_base_url'])
                port = parsed.port
            except ValueError:
                raise HTTPException(400, '请填写有效的平台地址') from None
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.path not in {'', '/'}
                    or port == 0 or any(character.isspace() or ord(character) < 32 for character in values['public_base_url'])):
                raise HTTPException(400, '平台地址应为完整的 http/https 地址，不包含路径、查询参数或账号')
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = settings(connection, secret=True)
            password = '' if body.clear_password else body.password or previous['password']
            if body.enabled and (not values['host'] or not values['sender_email'] or not values['public_base_url']):
                raise HTTPException(400, '启用前请填写 SMTP 地址、发件邮箱与平台地址')
            if body.enabled and values['username'] and not password:
                raise HTTPException(400, '请填写 SMTP 密码或邮箱授权码')
            connection.execute('INSERT INTO mail_settings VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                               'config_json=excluded.config_json,password=excluded.password,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                               (json.dumps(values), password, admin['id'], int(time.time())))
            return settings(connection)

    @routes.post('/api/admin/mail-settings/test')
    def test_message():
        admin = auth.require_admin()
        auth.rate_limit(db, 'mail-test:' + admin['id'], 5)
        with db() as connection:
            if not settings(connection)['enabled']:
                raise HTTPException(400, '请先保存并启用发件设置')
            email = connection.execute('SELECT notification_email FROM users WHERE id=?', (admin['id'],)).fetchone()[0]
            if not email:
                raise HTTPException(400, '请先设置您自己的提醒邮箱，再发送测试邮件')
            notification_id, timestamp = uuid.uuid4().hex, int(time.time())
            connection.execute('INSERT INTO mail_outbox '
                               '(id,owner_id,recipient,attempt_no,event,payload_json,next_attempt_at,created_at) VALUES(?,?,?,0,?,?,?,?)',
                               (notification_id, admin['id'], email, 'TEST', '{}', timestamp, timestamp))
        return {'id': notification_id, 'status': 'queued'}

    @routes.get('/api/admin/mail-settings/test/{notification_id}')
    def test_status(notification_id: str):
        admin = auth.require_admin()
        with db() as connection:
            row = connection.execute("SELECT status,last_error FROM mail_outbox WHERE id=? AND owner_id=? AND event='TEST'",
                                     (notification_id, admin['id'])).fetchone()
        if not row:
            raise HTTPException(404, '测试记录不存在')
        return dict(row)

    return routes
