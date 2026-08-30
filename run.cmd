@echo off
REM AI Usage Dashboard — Windows launcher. Usage: run.cmd [--port 9000] [--rebuild]
where python >nul 2>nul && (python "%~dp0dashboard.py" %*) || (py -3 "%~dp0dashboard.py" %*)
