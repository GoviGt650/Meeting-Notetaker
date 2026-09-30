$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopPath "Chrome (Auto-Admit).lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$ExtensionDir = Join-Path $PSScriptRoot "extensions\auto_admit"
$Shortcut.Arguments = "--load-extension=`"$ExtensionDir`""
$Shortcut.IconLocation = "C:\Program Files\Google\Chrome\Application\chrome.exe,0"
$Shortcut.Description = "Google Chrome with Notetaker Auto-Admit preloaded in background"
$Shortcut.Save()
Write-Host "Created Desktop Shortcut: $ShortcutPath"
