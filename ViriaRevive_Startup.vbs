' ViriaRevive - startup launcher for packaged builds or source checkouts.
' The app starts minimized to the system tray.

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

scriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
exeFile = scriptDir & "\ViriaRevive.exe"
pythonw = scriptDir & "\venv\Scripts\pythonw.exe"
appFile = scriptDir & "\app.pyw"

WshShell.CurrentDirectory = scriptDir

If FSO.FileExists(exeFile) Then
    WshShell.Run """" & exeFile & """ --minimized", 0, False
ElseIf FSO.FileExists(pythonw) And FSO.FileExists(appFile) Then
    WshShell.Run """" & pythonw & """ """ & appFile & """ --minimized", 0, False
End If
