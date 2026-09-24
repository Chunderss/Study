@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto no_python
if not exist .venv-build\Scripts\python.exe py -3.12 -m venv .venv-build
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe -m pip install -e ".[desktop-build]"
if errorlevel 1 goto failed
.venv-build\Scripts\python.exe tools\build_desktop.py
if errorlevel 1 goto failed
echo.
echo Ready: dist\VocabStudy\VocabStudy.exe
echo Share dist\VocabStudy-Windows.zip. Keep the entire extracted folder together.
pause
exit /b 0
:no_python
echo Install Python 3.12 from python.org, including the Python Launcher, then try again.
pause
exit /b 1
:failed
echo Build failed. Read the error above. Python 3.12 and internet access are needed to build.
pause
exit /b 1
