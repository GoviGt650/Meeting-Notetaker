@echo off
title Launch Chrome with Notetaker Auto-Admit
echo ==============================================================
echo  Launching Google Chrome with Silent Auto-Admit Active...
echo  All Google Meet calls in this session will auto-admit your bot.
echo ==============================================================

set EXT_DIR=%~dp0extensions\auto_admit
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --load-extension="%EXT_DIR%" https://meet.google.com
) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --load-extension="%EXT_DIR%" https://meet.google.com
) else (
    start chrome --load-extension="%EXT_DIR%" https://meet.google.com
)

echo Chrome launched successfully!
exit /b 0
