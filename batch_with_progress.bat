@echo off
setlocal
rem Run every forward corridor at the same time, each in its own minimised window, and wait here
rem until they have all finished. Each corridor repeats its sweep ROUNDS times.
rem Safe in parallel: every corridor and direction has its own browser profile and capture folder.
rem Do not start this file twice: two copies of the same corridor would share a browser profile.
rem Ctrl+C here only stops the waiting; close a corridor's window to stop that corridor.

cd /d "%~dp0"
set "ROUNDS=63"
set "CORRIDORS=e314 e17_west west_flanders wallonia_e42 e411_n4 e19_e420 ardennes_e25 central_east"
set "STATUS=data\logs\stretch_status"
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
if "%~1"==":run" goto run

if not exist "%STATUS%" mkdir "%STATUS%"
del /q "%STATUS%\*" 2>nul
set "TOTAL=0"
for %%C in (%CORRIDORS%) do (
    set /a TOTAL+=1
    > "%STATUS%\%%C.txt" echo starting
    start "Stretches %%C" /min cmd /c ""%~f0" :run %%C"
)
echo %TIME:~0,5%  Started %TOTAL% corridor collectors in minimised windows. Progress every minute:

:wait
rem Wait a minute (ping works without an interactive console, unlike timeout).
ping -n 61 127.0.0.1 >nul
set "FINISHED=0"
set "LINE="
for %%C in (%CORRIDORS%) do call :progress %%C
echo %TIME:~0,5%  %FINISHED% of %TOTAL% finished ^|%LINE%
if %FINISHED% lss %TOTAL% goto wait
echo.
echo %TIME:~0,5%  All %TOTAL% corridors finished. Run collect.bat to import the captures.
exit /b 0

:progress
set /p STATE=<"%STATUS%\%1.txt"
if "%STATE%"=="done" set /a FINISHED+=1
set "LINE=%LINE% %1 %STATE% ^|"
exit /b 0

:run
for /L %%R in (1,1,%ROUNDS%) do (
    > "%STATUS%\%~2.txt" echo %%R/%ROUNDS%
    echo === %~2 round %%R of %ROUNDS% ===
    "%PYTHON%" scripts\collect_google_maps_stretches.py --corridor %~2 --direction forward --once
    if errorlevel 1 echo WARNING: %~2 round %%R finished with errors, continuing.
)
> "%STATUS%\%~2.txt" echo done
exit /b 0
