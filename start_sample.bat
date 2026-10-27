@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 create_samples.py
  if errorlevel 1 goto failed
  py -3 server.py --port 8772 --data samples/sample-designs.sqlite3
) else (
  python create_samples.py
  if errorlevel 1 goto failed
  python server.py --port 8772 --data samples/sample-designs.sqlite3
)
if errorlevel 1 goto failed
exit /b 0
:failed
echo Sample launch failed. See the message above.
pause
exit /b 1
