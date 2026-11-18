@echo off
rem Builds LiteRecorder.exe with the C# compiler that ships with Windows (.NET Framework 4). No SDK needed.
setlocal
cd /d "%~dp0"
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe

"%CSC%" /nologo /target:exe /out:IconMaker.exe /r:System.Drawing.dll IconMaker.cs MicIcon.cs || goto :fail
.\IconMaker.exe app.ico || goto :fail
del IconMaker.exe

"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu /out:LiteRecorder.exe /win32icon:app.ico ^
  /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll Program.cs MainForm.cs Audio.cs Mp3.cs MicIcon.cs || goto :fail
echo Built LiteRecorder.exe
exit /b 0
:fail
echo BUILD FAILED
exit /b 1


