"""Small, standard-library-only Xiaomiao configuration and balance client."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib import request, error

BALANCE_URL = 'https://xiaomiao-ai.com/api/balance'
FEATURES = (
    ('images', '图片路径识别', '/api/images', 1),
    ('journal_figure', '期刊图生成', '/api/journal-figure-jobs', 45),
    ('watermark', '图片去水印', '/api/watermark-jobs', 1),
)
MESSAGES = {
    'ok': '配置与实时余额验证成功', 'needs_key': '未查询：需要配置密钥',
    'invalid_config': '配置格式无效；请使用 API_Key="..."，密钥内部不能有空白',
    'permission': '无法访问配置文件，请授予该文件或目录的读写权限',
    'auth': '密钥无效或已撤销', 'forbidden': '当前密钥没有请求权限',
    'insufficient': '可用额度不足，请充值或更换密钥',
    'rate_limit': '服务限流，请稍后重试', 'network': '网络连接失败，余额未知',
    'service': '服务异常，余额未知', 'protocol': '服务未返回有效余额，余额未知',
    'redirect': '拒绝跟随鉴权请求重定向', 'file_error': '文件操作失败，原配置尽量保留',
    'unsupported': '当前宿主缺少文件定位或编辑能力，请提供可访问的配置路径',
    'internal': '本地操作失败；未输出异常详情以保护密钥',
}
CODES = {'ok': 0, 'needs_key': 2, 'invalid_config': 2, 'auth': 3,
         'forbidden': 4, 'insufficient': 5, 'rate_limit': 6, 'network': 7,
         'service': 8, 'protocol': 9, 'redirect': 9, 'permission': 10,
         'file_error': 10, 'unsupported': 10, 'internal': 11}


class SetupError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(MESSAGES[status])


def platform_name():
    if hasattr(sys, 'getandroidapilevel') or 'ANDROID_ROOT' in os.environ:
        return 'android'
    return {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')


def windows_desktop():
    # Shell API honors redirected / OneDrive desktops without scanning files.
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) != 0:
        raise SetupError('unsupported')
    return Path(buf.value)


def config_path():
    system = platform_name()
    if system == 'windows':
        return windows_desktop() / 'xiaomiao_api.txt'
    if system == 'macos':
        return Path.home() / 'Desktop' / 'xiaomiao_api.txt'
    # Android requires a Python-capable host such as Termux; no shared-storage scan.
    return Path.home() / '.config' / 'xiaomiao' / 'xiaomiao_api.txt'


def normalize_key(value):
    value = value.strip()
    if not value:
        return ''
    if not re.fullmatch(r'img_live_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', value):
        raise SetupError('invalid_config')
    return value


def parse_config(text):
    match = re.fullmatch(r'\s*API_Key\s*=\s*"([^"]*)"\s*', text.lstrip('\ufeff'))
    if not match:
        raise SetupError('invalid_config')
    return normalize_key(match.group(1))


def read_key(path):
    path = Path(path)
    if path.is_symlink():
        raise SetupError('file_error')
    if path.stat().st_size > 16384:
        raise SetupError('invalid_config')
    try:
        return parse_config(path.read_text(encoding='utf-8-sig'))
    except UnicodeError:
        raise SetupError('invalid_config') from None


def hide_file(path):
    system = platform_name()
    if system == 'windows':
        kernel = ctypes.windll.kernel32
        kernel.GetFileAttributesW.restype = ctypes.c_uint32
        attrs = kernel.GetFileAttributesW(str(path))
        if attrs == 0xffffffff or not kernel.SetFileAttributesW(str(path), attrs | 2):
            raise SetupError('permission')
    elif system == 'macos':
        result = subprocess.run(['chflags', 'hidden', str(path)], capture_output=True)
        if result.returncode:
            raise SetupError('permission')
    # Android/Linux default path is beneath .config; an explicit --file may be visible.


def atomic_write(path, key):
    path = Path(path).absolute()
    if path.is_symlink():
        raise SetupError('file_error')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.xiaomiao-', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('API_Key="' + normalize_key(key) + '"\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        hide_file(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def editor_command(path):
    system = platform_name()
    if system == 'windows':
        return ['notepad.exe', str(path)]
    if system == 'macos':
        return ['open', '-e', str(path)]
    if system == 'android':
        if shutil.which('termux-open'):
            return ['termux-open', '--edit', '--content-type', 'text/plain', str(path)]
        return None
    return ['xdg-open', str(path)] if shutil.which('xdg-open') else None


def open_editor(path):
    command = editor_command(path)
    if not command:
        return 'unavailable'
    try:
        # Explicitly requested interactive editor; only helpers remain hidden.
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code = proc.wait(timeout=0.3)
            return 'requested' if code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            return 'requested'
    except OSError:
        return 'failed'


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_delay(headers, attempt):
    raw = headers.get('Retry-After')
    if raw is None:
        return 2 ** attempt
    try:
        seconds = float(raw)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(raw).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 2 ** attempt
    # Long Retry-After: return without an early retry, do not block a conversation.
    return max(0, seconds) if seconds <= 5 else None


def query_balance(key, *, transport=None, sleep=time.sleep):
    key = normalize_key(key)
    if not key:
        raise SetupError('needs_key')
    opener = transport or request.build_opener(NoRedirect())
    for attempt in range(3):
        req = request.Request(BALANCE_URL, headers={
            'Authorization': 'Bearer ' + key, 'Accept': 'application/json',
            'Cache-Control': 'no-cache', 'User-Agent': 'xiaomiao-api-setup/1.0'})
        try:
            with opener.open(req, timeout=10) as response:
                data = json.loads(response.read(262145))
            if not isinstance(data, dict) or data.get('ok') is not True:
                raise SetupError('protocol')
            return data
        except error.HTTPError as exc:
            code = exc.code
            headers = exc.headers
            exc.close()
            status = {401: 'auth', 403: 'forbidden', 402: 'insufficient',
                      429: 'rate_limit'}.get(code, 'service')
            if 300 <= code < 400:
                status = 'redirect'
            if code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise SetupError(status) from None
            delay = retry_delay(headers, attempt)
            if delay is None:
                raise SetupError(status) from None
            sleep(delay)
        except (error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise SetupError('network') from None
            sleep(2 ** attempt)
        except (ValueError, UnicodeError):
            raise SetupError('protocol') from None


def nonnegative(value):
    return value if type(value) is int and value >= 0 else None


def feature_report(data=None):
    services = (data or {}).get('services', {})
    result = []
    for ident, label, endpoint, cost in FEATURES:
        service = services.get(ident, {}) if isinstance(services, dict) else {}
        permission = None
        if isinstance(service, dict):
            for field in ('enabled', 'allowed'):
                if type(service.get(field)) is bool:
                    permission = service[field]
                    break
        result.append(dict(id=ident, name=label, endpoint='POST ' + endpoint,
                           documented=True, reference_cost=cost,
                           permission=permission, executed=False,
                           can_submit=service.get('can_submit') if isinstance(service, dict)
                           and type(service.get('can_submit')) is bool else None))
    return result


def setup(*, path=None, supplied=None, feature=None, auto_open=True, transport=None):
    report = dict(schema_version=1, status='ok', balance=None, features=feature_report(),
                  config_path=None, editor='not_needed', queried=False,
                  checked_at=datetime.now(timezone.utc).isoformat())
    chosen = None
    try:
        chosen = Path(path).absolute() if path else config_path()
        report['config_path'] = str(chosen)
        if supplied is not None:
            key = normalize_key(supplied)
            atomic_write(chosen, key)
            # The persisted file is authoritative, including chat configuration.
            key = read_key(chosen)
        else:
            if not chosen.exists():
                atomic_write(chosen, '')
            key = read_key(chosen)
            canonical = 'API_Key="' + key + '"\n'
            if chosen.read_text(encoding='utf-8-sig') != canonical:
                atomic_write(chosen, key)
            else:
                os.chmod(chosen, 0o600)
                hide_file(chosen)
        if not key:
            raise SetupError('needs_key')
        report['queried'] = True
        data = query_balance(key, transport=transport)
        report['features'] = feature_report(data)
        report['balance'] = nonnegative(data.get('available_credits'))
        if report['balance'] is None:
            raise SetupError('protocol')
        required = next((f[3] for f in FEATURES if f[0] == feature), 0)
        if report['balance'] == 0 or report['balance'] < required:
            raise SetupError('insufficient')
    except SetupError as exc:
        report['status'] = exc.status
    except PermissionError:
        report['status'] = 'permission'
    except OSError:
        report['status'] = 'file_error'
    except Exception:
        report['status'] = 'internal'
    if report['status'] in ('needs_key', 'invalid_config', 'auth', 'insufficient'):
        if auto_open and chosen is not None and chosen.is_file():
            report['editor'] = open_editor(chosen)
    report['message'] = MESSAGES[report['status']]
    return report


def child_environment(path=None):
    """For an authorized downstream caller; never modifies os.environ or prints keys."""
    key = read_key(Path(path) if path else config_path())
    if not key:
        raise SetupError('needs_key')
    env = os.environ.copy()
    env['XIAOMIAO_API_KEY'] = key
    return env


def main():
    parser = argparse.ArgumentParser(description='小描配置、实时余额和功能（不提交收费任务）')
    parser.add_argument('--file', type=Path, help='用户明确选择的配置路径')
    parser.add_argument('--stdin-key', action='store_true', help='通过标准输入接收聊天密钥')
    parser.add_argument('--feature', choices=[f[0] for f in FEATURES])
    parser.add_argument('--no-open', action='store_true', help='仅用于测试或用户明确要求不打开编辑器')
    args = parser.parse_args()
    supplied = sys.stdin.read(16385) if args.stdin_key else None
    result = setup(path=args.file, supplied=supplied, feature=args.feature,
                   auto_open=not args.no_open)
    print(json.dumps(result, ensure_ascii=False))
    return CODES[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
