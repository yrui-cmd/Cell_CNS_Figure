"""Use only the shared Xiaomiao configuration, never legacy credentials."""
import sys
from pathlib import Path


def main():
    shared = Path(__file__).resolve().parents[2] / 'xiaomiao-api-setup' / 'scripts'
    if not (shared / 'xiaomiao_setup.py').is_file():
        shared = Path(__file__).resolve().parents[1] / 'dependencies' / 'xiaomiao-api-setup' / 'scripts'
    if not (shared / 'xiaomiao_setup.py').is_file():
        print('{"ok":false,"error":"请先安装 xiaomiao-api-setup"}')
        return 2
    sys.path.insert(0, str(shared))
    import xiaomiao_setup as setup
    import xiaomiao_client as client
    # Keep shared-file-only discovery even if authentication fails.
    def discover(self, explicit=None):
        key = setup.read_key(setup.config_path())
        return [('shared_config', key)] if key else []
    client.CredentialManager.discover = discover
    if len(sys.argv) < 2 or sys.argv[1] not in {'balance', 'submit', 'status', 'fetch', 'fetch-pptx', 'cancel'}:
        print('{"ok":false,"error":"只支持 balance、submit、status、fetch、fetch-pptx、cancel"}')
        return 2
    try:
        return client.main()
    except Exception:
        print('{"ok":false,"error":"共享配置不可用，请运行 xiaomiao-api-setup"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
