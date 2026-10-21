"""Initialize a fresh Windows installation, never migrate or overwrite runtime."""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def initialize(config, address, code):
    from backend.storage import vault, repository
    from backend.mail.core import open_db
    from backend.cli.manager import locked
    if os.name != 'nt':
        raise RuntimeError('仅支持 Windows 当前用户的 DPAPI 凭证存储')
    if address.rsplit('@', 1)[-1].lower() not in {'163.com','126.com','yeah.net'}:
        raise ValueError('请输入支持的网易邮箱地址（163.com / 126.com / yeah.net）')
    if not address.split('@')[0] or '@' not in address or not code.strip():
        raise ValueError('邮箱地址和客户端授权码不能为空')
    if config.get('model_provider') != 'codebuddy_cn':
        raise ValueError('仅支持 CodeBuddy CN')
    executable = shutil.which(config.get('codebuddy_cli', ''))
    if not executable:
        raise ValueError('找不到 CodeBuddy CLI，请在配置中填写实际可执行文件路径')
    config = {key:config[key] for key in ('model_provider','codebuddy_cli','codebuddy_model','codebuddy_fallback_model','reasoning_effort')}
    config.update(codebuddy_cli=executable, notifications_enabled=False)
    # Refuse before writing anything if any earlier installation exists.
    if vault.HOME.exists() and any(vault.HOME.iterdir()):
        raise RuntimeError('运行目录已有文件，拒绝覆盖；现有安装请使用页面设置')
    with locked():
        for name in ('ledger.sqlite3','credentials.dpapi','local-gate.json'):
            if (vault.HOME/name).exists():
                raise RuntimeError('运行数据已存在，拒绝覆盖')
        db = open_db(vault.HOME/'ledger.sqlite3')
        try: repository.initialize(db)
        finally: db.close()
        vault.save_credentials({'email':address,'mail_code':code})
        (vault.HOME/'local-gate.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config.example.json')
    args = parser.parse_args()
    from backend.storage.vault import HOME
    if HOME.exists() and any(HOME.iterdir()):
        raise SystemExit('运行目录已有文件，拒绝覆盖。')
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    address = input('网易邮箱：').strip()
    code = getpass.getpass('客户端授权码（输入不回显）：')
    try: initialize(config, address, code)
    finally: code = ''
    print('初始化完成；未连接邮箱、调用模型或安装定时任务。')

if __name__ == '__main__':
    main()
