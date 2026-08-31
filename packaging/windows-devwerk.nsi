!ifndef VERSION
!define VERSION "0.1.0"
!endif
!ifndef ROOT
!define ROOT "."
!endif
!ifndef OUT
!define OUT "dist\installers\devwerk-${VERSION}-windows.exe"
!endif

Name "DevWerk"
OutFile "${OUT}"
InstallDir "$PROGRAMFILES64\DevWerk"
RequestExecutionLevel admin

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${ROOT}\dist\DevWerk\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\DevWerk"
  CreateShortcut "$SMPROGRAMS\DevWerk\Start DevWerk.lnk" "$INSTDIR\startup.bat"
  CreateShortcut "$SMPROGRAMS\DevWerk\Stop DevWerk.lnk" "$INSTDIR\shutdown.bat"
  CreateShortcut "$SMPROGRAMS\DevWerk\Uninstall DevWerk.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\DevWerk\Start DevWerk.lnk"
  Delete "$SMPROGRAMS\DevWerk\Stop DevWerk.lnk"
  Delete "$SMPROGRAMS\DevWerk\Uninstall DevWerk.lnk"
  RMDir "$SMPROGRAMS\DevWerk"
  RMDir /r "$INSTDIR"
SectionEnd
