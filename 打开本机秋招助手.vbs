Option Explicit
Dim shell, files, root
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
shell.Run """D:\APP\miniconda\pythonw.exe"" """ & root & "\app\launch.py""", 0, False
