# Builds GameArt.scr with the .NET Framework compiler that ships with Windows (no installs).
$fw  = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$wpf = "$fw\WPF"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$fw\csc.exe" /nologo /target:winexe /optimize "/out:$here\GameArt.scr" `
  "/r:$wpf\PresentationFramework.dll" "/r:$wpf\PresentationCore.dll" "/r:$wpf\WindowsBase.dll" `
  "/r:$fw\System.Xaml.dll" "/r:$fw\System.Windows.Forms.dll" "/r:$fw\System.Drawing.dll" `
  "$here\GameArtSaver.cs"
if ($LASTEXITCODE -eq 0) { "built $here\GameArt.scr" }
