@echo off
rem SkillHub 启动器 —— 无控制台窗口
setlocal

rem 优先用 WorkBuddy 自带的 Python 环境 (已装好 pywebview), 回退到 PATH 里的 pythonw
set "PYW=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"

start "" "%PYW%" "%~dp0app.py"
endlocal
