@echo off
setlocal
rem Run every forward corridor at the same time, each in its own minimised window.
rem Each corridor repeats its sweep ROUNDS times; close a window to stop that corridor.
rem Safe in parallel: every corridor and direction has its own browser profile and capture folder.
rem Do not start this file twice: two copies of the same corridor would share a browser profile.

cd /d "%~dp0"
set "ROUNDS=63"
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
if "%~1"==":run" goto run

for %%C in (e314 e17_west west_flanders wallonia_e42 e411_n4 e19_e420 ardennes_e25 central_east) do (
    start "Stretches %%C" /min cmd /c ""%~f0" :run %%C"
)
echo Started 8 corridor collectors in separate minimised windows.
exit /b 0

:run
for /L %%R in (1,1,%ROUNDS%) do (
    echo === %~2 round %%R of %ROUNDS% ===
    "%PYTHON%" scripts\collect_google_maps_stretches.py --corridor %~2 --direction forward --once
    if errorlevel 1 echo WARNING: %~2 round %%R finished with errors, continuing.
)
exit /b 0
