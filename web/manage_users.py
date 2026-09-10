"""Server-local administrator bootstrap/password recovery; never exposes a web setup endpoint."""
import argparse
import getpass
import re
import secrets
import time

import app
import auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create-admin', 'reset-password'])
    parser.add_argument('username')
    parser.add_argument('--name', help='Display name; defaults to username for a new administrator')
    args = parser.parse_args()
    username = args.username.lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,31}', username):
        parser.error('username must be 3–32 ASCII letters/digits/_.-')
    name = (args.name or username).strip()
    if not 1 <= len(name) <= 60 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 ._-]*', name):
        parser.error('display name must start with an English letter; only ASCII letters/digits/spaces/._- are allowed')
    password = getpass.getpass('New password (6–128 characters): ')
    if not 6 <= len(password) <= 128 or password != getpass.getpass('Confirm password: '):
        parser.error('password length invalid or confirmation does not match')
    encoded = auth.password_hash(password)
    app.init_db()
    with app.db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        user = connection.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        if args.action == 'create-admin':
            if user:
                parser.error('user already exists; no account was changed')
            user_id = secrets.token_hex(16)
            connection.execute('INSERT INTO users(id,username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,\'admin\',?)',
                               (user_id, username, name, encoded, int(time.time())))
        else:
            if not user:
                parser.error('user does not exist')
            user_id = user['id']
            connection.execute('UPDATE users SET password_hash=? WHERE id=?', (encoded, user_id))
            connection.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        auth.event(connection, None, 'cli-' + args.action, user_id)
    print(f'{args.action}: {username} OK')


if __name__ == '__main__':
    main()
