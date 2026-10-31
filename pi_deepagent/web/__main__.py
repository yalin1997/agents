"""Run the local authenticated Pi workspace (uvicorn from the a2a extra)."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from ..config import load_environment
from .auth import AuthService, Redactor


def main(argv=None):
    load_environment()
    parser = argparse.ArgumentParser(description='Pi 本機工作台')
    parser.add_argument('command', nargs='?', choices=['serve', 'init-admin'], default='serve')
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.pi-deepagent-web')
    parser.add_argument('--workspace', type=Path, default=Path(os.environ.get('PI_DEEPAGENT_WORKSPACE', '.')))
    parser.add_argument('--model', default=os.environ.get('PI_DEEPAGENT_MODEL', 'google_genai:gemini-3.6-flash'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--collaboration', action='store_true', help='啟用團隊草稿 API；須先明確完成 collaboration schema migration')
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    data_dir = args.data_dir.resolve()
    if not workspace.is_dir():
        parser.error('工作目錄不存在。')
    if data_dir == workspace or data_dir.is_relative_to(workspace):
        parser.error('服務資料目錄不可位於 Agent 工作目錄內。')
    if args.command == 'init-admin':
        password = getpass.getpass('管理員密碼（至少 12 字元）：')
        if password != getpass.getpass('再次輸入密碼：'):
            parser.error('兩次密碼不一致。')
        try:
            AuthService(args.data_dir.resolve(), Redactor()).initialize(password)
        except (ValueError, FileExistsError):
            parser.error('密碼須至少 12 字元；已初始化的憑證不會被覆寫。')
        print('管理員已初始化。請以作業系統權限保護資料目錄。')
        return 0
    if not 1 <= args.port <= 65535:
        parser.error('port 必須介於 1 與 65535。')
    try:
        import uvicorn
    except ImportError:
        parser.error('需要現有 a2a extra：uv sync --extra a2a')
    from .app import create_app
    if args.collaboration:
        from ..collaboration.contracts import CollaborationError
        from ..collaboration.storage import CollaborationStore
        try:
            with CollaborationStore(data_dir / 'tasks.sqlite'):
                pass
        except CollaborationError:
            parser.error('協作資料表尚未準備或版本不符；請先審查、備份並明確套用 migration。啟動器不自動升級。')
    try:
        app = create_app(args.data_dir, args.workspace, args.model, f'http://127.0.0.1:{args.port}', collaboration_enabled=args.collaboration)
    except ValueError as error:
        parser.error(str(error))
    print('僅供本機管理員使用。停止不會復原檔案，亦不保證終止外部子程序。')
    print(f'除錯日誌：{data_dir / "diagnostics.log"}')
    uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1, access_log=False, log_level='warning')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
