@echo off
chcp 65001 >nul
rem 研发工作台：数据放在旁边的 data 文件夹。本机管理端 http://127.0.0.1:8850（不用登录）。
rem 要开外网访问口（要登录）就把下面 LAB_PUBLIC_PORT 设成 8860，内网穿透只指向它。
cd /d "%~dp0"
if not defined LAB_HOME set "LAB_HOME=%~dp0data"
if not defined LAB_PORT set "LAB_PORT=8850"
if not defined LAB_PUBLIC_PORT set "LAB_PUBLIC_PORT=0"
set "LAB_LOG=%LAB_HOME%\服务日志.txt"
if not exist "%LAB_HOME%" mkdir "%LAB_HOME%"
start "" pythonw -B -X utf8 -c "import sys; sys.path.insert(0, r'%~dp0.'); from lab.server import main; main()"
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:%LAB_PORT%"
