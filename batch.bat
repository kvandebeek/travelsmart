@echo off
setlocal
rem Run each forward corridor once per round, one after another.
rem Stop with Ctrl+C. Do not start a second copy while this one is running:
rem both copies would share the same browser profiles and Chromium hangs on launch.

cd /d "%~dp0"

set "ROUNDS=63"
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

for /L %%R in (1,1,%ROUNDS%) do (
    echo === Round %%R of %ROUNDS% ===
    for %%C in (e314 e17_west west_flanders wallonia_e42 e411_n4 e19_e420 ardennes_e25 central_east) do (
        echo --- %%C forward ---
        "%PYTHON%" scripts\collect_google_maps_stretches.py --corridor %%C --direction forward --once
        if errorlevel 1 echo WARNING: %%C finished with errors, continuing.
    )
)

endlocal
