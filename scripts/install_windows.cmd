@echo off
REM ---------------------------------------------------------------------------
REM One-shot install for Windows. Run from Anaconda Prompt:
REM
REM     cd /d D:\Code_renew\cifti_state_py
REM     scripts\install_windows.cmd
REM
REM Optional: pass a mirror to speed pip up, e.g.
REM     scripts\install_windows.cmd https://pypi.tuna.tsinghua.edu.cn/simple
REM
REM conda only creates the interpreter; every package comes from pip.
REM docs\INSTALL.md walks through the same steps with the expected output.
REM ---------------------------------------------------------------------------
setlocal
set ENV_NAME=cifti-state
set PY_VERSION=3.11
set MIRROR=%~1

echo.
echo ===============================================================
echo  cifti_state install
echo  environment : %ENV_NAME%  (python %PY_VERSION%)
if not "%MIRROR%"=="" echo  pip mirror  : %MIRROR%
echo ===============================================================

where conda >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: conda is not on PATH. Open "Anaconda Prompt" and run this again.
    exit /b 1
)

echo.
echo [1/5] creating the conda environment ...
call conda env list | findstr /B /C:"%ENV_NAME% " >nul
if errorlevel 1 (
    call conda create -n %ENV_NAME% python=%PY_VERSION% pip -y
    if errorlevel 1 goto :failed
) else (
    echo       already exists, reusing it
)

echo.
echo [2/5] activating ...
call conda activate %ENV_NAME%
if errorlevel 1 goto :failed

set PIP_ARGS=
if not "%MIRROR%"=="" set PIP_ARGS=-i %MIRROR%

echo.
echo [3/5] installing dependencies with pip ...
echo       (VTK and PySide6 are ~100 MB each; this is the slow part)
python -m pip install --upgrade pip %PIP_ARGS%
if errorlevel 1 goto :failed
python -m pip install -r requirements.txt %PIP_ARGS%
if errorlevel 1 goto :failed

echo.
echo [4/5] installing cifti_state itself (editable) ...
python -m pip install -e . %PIP_ARGS%
if errorlevel 1 goto :failed

echo.
echo [5/5] checking the configuration ...
cifti-state check --workbench --render

echo.
echo ===============================================================
echo  Done. Every day from now on:
echo.
echo      conda activate %ENV_NAME%
echo      cifti-state-gui
echo.
echo  If the check above reported PROBLEMS, fix the paths in
echo  configs\machines\%COMPUTERNAME%.yaml and run it again:
echo      cifti-state check --workbench --render
echo ===============================================================
endlocal
exit /b 0

:failed
echo.
echo ---------------------------------------------------------------
echo  Install failed at the step above. See docs\INSTALL.md for the
echo  troubleshooting table.
echo ---------------------------------------------------------------
endlocal
exit /b 1
