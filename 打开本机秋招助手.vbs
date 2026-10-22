Option Explicit
Dim shell, files, root, candidate, python, config, cache, environment
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
Set environment = shell.Environment("PROCESS")
config = root & "\private\python-path.txt"
cache = root & "\private\python-launch-cache.txt"
candidate = ""
If files.FileExists(config) Then
    candidate = ReadUtf8(config)
ElseIf environment("AUTUMN_PYTHON") <> "" Then
    candidate = environment("AUTUMN_PYTHON")
Else
    candidate = WindowlessPython(root & "\.venv\Scripts\pythonw.exe")
    If candidate = "" And files.FileExists(cache) Then candidate = ReadUtf8(cache)
End If
python = WindowlessPython(candidate)
If python <> "" Then
    shell.Run """" & python & """ -X utf8 """ & root & "\app\launch.py""", 0, False
Else
    shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\tools\open_app.ps1""", 0, False
End If

Function ReadUtf8(path)
    On Error Resume Next
    Dim stream
    ReadUtf8 = ""
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2
    stream.Charset = "utf-8"
    stream.Open
    stream.LoadFromFile path
    ReadUtf8 = Trim(Replace(stream.ReadText, ChrW(65279), ""))
    stream.Close
    On Error GoTo 0
End Function

Function WindowlessPython(value)
    On Error Resume Next
    Dim path, leaf
    WindowlessPython = ""
    path = Trim(value)
    If path = "" Then Exit Function
    If Left(path, 1) = """" And Right(path, 1) = """" Then path = Mid(path, 2, Len(path) - 2)
    If InStr(path, vbCr) > 0 Or InStr(path, vbLf) > 0 Then Exit Function
    path = files.GetAbsolutePathName(path)
    leaf = LCase(files.GetFileName(path))
    If leaf = "python.exe" Then
        path = files.BuildPath(files.GetParentFolderName(path), "pythonw.exe")
    ElseIf leaf <> "pythonw.exe" Then
        Exit Function
    End If
    If InStr(LCase(path), "\windowsapps\") > 0 Then Exit Function
    If files.FileExists(path) Then WindowlessPython = path
    On Error GoTo 0
End Function
