@echo off
rem ============================================================
rem  WeFlow 5.0.0 (patched) launcher  --  bypasses the WCDB expiry
rem  gate that makes 6.3.2 return -101.
rem  - wcdb_api.dll patched: InitProtection deadline -> 2037-09-30;
rem    wcdb_init tm deadline -> year 2037 (was 2026).
rem  - ELECTRON_RUN_AS_NODE must be cleared or electron runs as node.
rem  - cwd MUST be the tree root: unpackaged imageDownloadService
rem    resolves img_helper.dll via process.cwd()\resources\image\...
rem    With a wrong cwd the hook silently fails, WeChat never caches
rem    incoming photos, the API has no mediaUrl and the bot is blind.
rem  - ffmpeg-static shipped WITHOUT ffmpeg.exe (the npm postinstall
rem    download never ran). Without it WeFlow cannot decode the
rem    HEVC/wxgf photos people send, so every incoming image fails to
rem    export. We restore it from the installed WeFlow 6.3.2 tree.
rem ============================================================
set "ELECTRON_RUN_AS_NODE="
set "WEFLOW50_ROOT=<WEFLOW_ROOT>"
set "FFMPEG_DST=%WEFLOW50_ROOT%\node_modules\ffmpeg-static\ffmpeg.exe"
set "FFMPEG_SRC=C:\Program Files\WeFlow\resources\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe"
if not exist "%FFMPEG_DST%" (
  if exist "%FFMPEG_SRC%" (
    echo [weflow50] restoring ffmpeg.exe ^(HEVC/wxgf image decode^)...
    copy /y "%FFMPEG_SRC%" "%FFMPEG_DST%" >nul
  ) else (
    echo [weflow50] WARNING: ffmpeg.exe missing and no source found - incoming images will fail.
  )
)
cd /d "%WEFLOW50_ROOT%"
start "" "%WEFLOW50_ROOT%\node_modules\electron\dist\electron.exe" "%WEFLOW50_ROOT%"
