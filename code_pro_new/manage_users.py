"""
用户管理命令行工具
=================
用于管理 K 线系统的多用户账号。密码以 bcrypt 哈希存储在 users.json
(自带随机盐 + 自适应工作因子,比无盐 sha256 安全得多)。

用法：
  python3 manage_users.py list                        # 列出所有用户
  python3 manage_users.py add <用户名> <密码>          # 添加用户
  python3 manage_users.py passwd <用户名> <新密码>     # 修改密码
  python3 manage_users.py delete <用户名>              # 删除用户（保留其 settings 文件）
  python3 manage_users.py show <用户名>                # 显示用户的 settings 文件路径

示例：
  python3 manage_users.py add alice mypassword123
  python3 manage_users.py add bob  secretpass
  python3 manage_users.py list
"""
import sys
import os
import json

try:
    import bcrypt
except ImportError:
    print('[错误] 未安装 bcrypt。请运行: pip install bcrypt')
    sys.exit(1)

_DIR       = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(_DIR, 'users.json')


def _hash(password: str) -> str:
    """生成 bcrypt 密码哈希(字符串形式,可直接 JSON 序列化)。"""
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt(),
    ).decode('utf-8')


def _load() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[错误] 读取 users.json 失败: {e}')
        sys.exit(1)


def _save(data: dict):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _settings_path(username: str) -> str:
    return os.path.join(_DIR, f'user_settings_{username}.json')


def cmd_list():
    users = _load()
    if not users:
        print('（暂无用户，请用 add 命令添加）')
        return
    h1, h2 = '用户名', 'Settings 文件'
    print(f'{h1:<20} {h2}')
    print('-' * 60)
    for name, info in sorted(users.items()):
        path = _settings_path(name)
        exists = '✓' if os.path.exists(path) else '（尚未保存过设置）'
        print(f'{name:<20} {exists}  {path}')


def cmd_add(username: str, password: str):
    if not username.replace('_', '').replace('-', '').isalnum():
        print('[错误] 用户名只能含字母、数字、下划线、连字符')
        sys.exit(1)
    users = _load()
    if username in users:
        print(f'[错误] 用户 {username!r} 已存在，请用 passwd 命令改密码')
        sys.exit(1)
    users[username] = {'password_hash': _hash(password)}
    _save(users)
    print(f'[OK] 已添加用户 {username!r}')
    print(f'     Settings 文件将在首次登录并保存设置后自动创建：')
    print(f'     {_settings_path(username)}')


def cmd_passwd(username: str, password: str):
    users = _load()
    if username not in users:
        print(f'[错误] 用户 {username!r} 不存在')
        sys.exit(1)
    users[username]['password_hash'] = _hash(password)
    _save(users)
    print(f'[OK] 已更新用户 {username!r} 的密码')


def cmd_delete(username: str):
    users = _load()
    if username not in users:
        print(f'[错误] 用户 {username!r} 不存在')
        sys.exit(1)
    del users[username]
    _save(users)
    path = _settings_path(username)
    print(f'[OK] 已删除用户 {username!r}（账号已移除，settings 文件保留）')
    if os.path.exists(path):
        print(f'     如需一并删除 settings 文件，请手动删除：{path}')


def cmd_show(username: str):
    users = _load()
    if username not in users:
        print(f'[错误] 用户 {username!r} 不存在')
        sys.exit(1)
    path = _settings_path(username)
    print(f'用户名   : {username}')
    print(f'Settings : {path}')
    print(f'文件存在 : {"是" if os.path.exists(path) else "否（尚未保存过设置）"}')


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()
    if cmd == 'list':
        cmd_list()
    elif cmd == 'add' and len(args) == 3:
        cmd_add(args[1], args[2])
    elif cmd == 'passwd' and len(args) == 3:
        cmd_passwd(args[1], args[2])
    elif cmd == 'delete' and len(args) == 2:
        cmd_delete(args[1])
    elif cmd == 'show' and len(args) == 2:
        cmd_show(args[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
