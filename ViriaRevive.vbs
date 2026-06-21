' ViriaRevive - silent launcher for either packaged builds or a source checkout.
' Packaged builds launch ViriaRevive.exe. Developer checkouts launch app.pyw
' through the local virtual environment.

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

scriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
exeFile = scriptDir & "\ViriaRevive.exe"
pythonw = scriptDir & "\venv\Scripts\pythonw.exe"
appFile = scriptDir & "\app.pyw"

WshShell.CurrentDirectory = scriptDir

If FSO.FileExists(exeFile) Then
    WshShell.Run """" & exeFile & """", 0, False
ElseIf FSO.FileExists(pythonw) And FSO.FileExists(appFile) Then
    WshShell.Run """" & pythonw & """ """ & appFile & """", 0, False
Else
    MsgBox "ViriaRevive could not find ViriaRevive.exe or venv\Scripts\pythonw.exe.", vbExclamation, "ViriaRevive"
End If
