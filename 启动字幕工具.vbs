Option Explicit
Dim shell, fso, root, scriptPath, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(fso.BuildPath(root, "runtime"), "launcher.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""$env:SUBTITLE_TOOL_ROOT='" & Replace(root, "'", "''") & "'; $p='" & Replace(scriptPath, "'", "''") & "'; $s=[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8); . ([ScriptBlock]::Create($s))"""
shell.Run command, 0, False
