' ============================================================================
' ODS Windows-native app -- Start menu launcher
' ============================================================================
' Runs "ods-native.ps1 open -ShowErrors" from this folder with no console
' window. ods-native.ps1 starts ODS if needed, waits for the dashboard and
' opens http://127.0.0.1:3002/ in the default browser; on failure it shows a
' message box and writes %LOCALAPPDATA%\ODS\Native\logs\ods-native.log.
' ============================================================================
Option Explicit
Dim shell, fso, here, powershell
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
shell.Run """" & powershell & """ -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\ods-native.ps1"" open -ShowErrors", 0, False
