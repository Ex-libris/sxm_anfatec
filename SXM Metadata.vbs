' Double-click launcher. Uses an existing Python installation; no console commands.
Option Explicit
Dim fso, shell, root, candidates, candidate, exe, item, folder, pythonRoot
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
exe = ""
candidates = Array(root & "\runtime\pythonw.exe", _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\anaconda3\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%USERPROFILE%\anaconda3\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%USERPROFILE%\miniconda3\pythonw.exe"))
For Each candidate In candidates
  If fso.FileExists(candidate) Then
    exe = candidate
    Exit For
  End If
Next
If exe = "" Then
  For Each item In Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
    item = Replace(item, Chr(34), "")
    If Len(item) > 0 Then
      candidate = fso.BuildPath(item, "pythonw.exe")
      If fso.FileExists(candidate) Then
        exe = candidate
        Exit For
      End If
    End If
  Next
End If
If exe = "" Then
  pythonRoot = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python")
  If fso.FolderExists(pythonRoot) Then
    For Each folder In fso.GetFolder(pythonRoot).SubFolders
      candidate = fso.BuildPath(folder.Path, "pythonw.exe")
      If fso.FileExists(candidate) Then exe = candidate
    Next
  End If
End If
If exe = "" Then
  MsgBox "SXM Metadata needs a one-time installation by the instrument maintainer. No compatible runtime was found.", vbExclamation, "SXM Metadata"
  WScript.Quit 1
End If
shell.Run Chr(34) & exe & Chr(34) & " -B " & Chr(34) & root & "\tools\recorder_control.py" & Chr(34), 1, False
