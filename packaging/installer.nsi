; Hertzon installer (NSIS 3). Build with packaging/build_installer.sh, which stages the files and passes
; VERSION, STAGE (folder with the files to install) and OUT (installer path).
;
; Installs for the current user into %LOCALAPPDATA%\Programs\Hertzon (no administrator rights needed; the
; Python environment .venv is created next to Hertzon.exe and must stay writable), adds Hertzon to the
; start menu, the desktop and Settings > Apps, opens .hzn projects with Hertzon, and prepares the Python
; environment (setup.ps1) at the end of the installation.

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!include "MUI2.nsh"

!define APP "Hertzon"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hertzon"

Name "${APP}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\Hertzon"
InstallDirRegKey HKCU "Software\Hertzon" "InstallDir"
BrandingText "${APP} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APP} Setup"
VIAddVersionKey "LegalCopyright" "GPL-2.0-or-later"

!define MUI_ICON "${STAGE}\hertzon\gui\hertzon.ico"
!define MUI_UNICON "${STAGE}\hertzon\gui\hertzon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\Hertzon.exe"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${STAGE}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "German"
!insertmacro MUI_LANGUAGE "English"

LangString SecApp ${LANG_GERMAN} "Hertzon (Programm)"
LangString SecApp ${LANG_ENGLISH} "Hertzon (program)"
LangString SecDesktop ${LANG_GERMAN} "Verknüpfung auf dem Desktop"
LangString SecDesktop ${LANG_ENGLISH} "Desktop shortcut"
LangString SecAssoc ${LANG_GERMAN} ".hzn-Projekte mit Hertzon öffnen"
LangString SecAssoc ${LANG_ENGLISH} "Open .hzn projects with Hertzon"
LangString SecEnv ${LANG_GERMAN} "Python-Umgebung jetzt einrichten bzw. aktualisieren (ca. 1 GB Download bei der ersten Installation)"
LangString SecEnv ${LANG_ENGLISH} "Set up or update the Python environment now (about 1 GB download on the first installation)"
LangString EnvRunning ${LANG_GERMAN} "Python-Umgebung wird eingerichtet (eigenes Fenster, einige Minuten) ..."
LangString EnvRunning ${LANG_ENGLISH} "Setting up the Python environment (separate window, a few minutes) ..."
LangString EnvFailed ${LANG_GERMAN} "Die Python-Umgebung konnte nicht eingerichtet werden. Hertzon versucht es beim ersten Start erneut.$\r$\n$\r$\nProtokoll: $INSTDIR\setup.log"
LangString EnvFailed ${LANG_ENGLISH} "The Python environment could not be set up. Hertzon tries again at its first start.$\r$\n$\r$\nLog: $INSTDIR\setup.log"
LangString Running ${LANG_GERMAN} "Hertzon läuft noch. Bitte schließe Hertzon und klicke dann auf OK."
LangString Running ${LANG_ENGLISH} "Hertzon is still running. Please close it, then click OK."

Function .onInit
    !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

; Hertzon must not run while its files are replaced: the launcher holds Hertzon.exe open
!macro WaitClosed
    retry:
    ClearErrors
    FileOpen $0 "$INSTDIR\Hertzon.exe" a
    IfErrors 0 free
    IfFileExists "$INSTDIR\Hertzon.exe" 0 free
    MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(Running)" IDOK retry
    Abort
    free:
    FileClose $0
!macroend

Section "!$(SecApp)" SEC_APP
    SectionIn RO
    SetOutPath "$INSTDIR"
    !insertmacro WaitClosed
    ; an update replaces the program files completely; the Python environment (.venv) stays
    RMDir /r "$INSTDIR\hertzon"
    File /r "${STAGE}\*.*"
    ; the new program files may need other packages (EMerge, numba, ...) and new solver kernels: without
    ; this marker Hertzon.exe runs setup.ps1 at its next start, unless the Python environment is set up below
    Delete "$INSTDIR\.venv\hertzon_installed.txt"

    CreateShortcut "$SMPROGRAMS\Hertzon.lnk" "$INSTDIR\Hertzon.exe" "" "$INSTDIR\Hertzon.exe" 0
    WriteRegStr HKCU "Software\Hertzon" "InstallDir" "$INSTDIR"

    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Hertzon"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\Hertzon.exe,0"
    WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
    ; size of the program files plus about 1 GB for the Python environment, which is downloaded afterwards;
    ; taken from the installer itself, not by walking $INSTDIR: on an update that walk went through the whole
    ; existing .venv and took minutes
    SectionGetSize ${SEC_APP} $0
    IntOp $0 $0 + 1048576
    WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" $0
SectionEnd

Section "$(SecDesktop)" SEC_DESKTOP
    CreateShortcut "$DESKTOP\Hertzon.lnk" "$INSTDIR\Hertzon.exe" "" "$INSTDIR\Hertzon.exe" 0
SectionEnd

Section "$(SecAssoc)" SEC_ASSOC
    WriteRegStr HKCU "Software\Classes\.hzn" "" "Hertzon.Project"
    WriteRegStr HKCU "Software\Classes\Hertzon.Project" "" "Hertzon Project"
    WriteRegStr HKCU "Software\Classes\Hertzon.Project\DefaultIcon" "" "$INSTDIR\Hertzon.exe,0"
    WriteRegStr HKCU "Software\Classes\Hertzon.Project\shell\open\command" "" '"$INSTDIR\Hertzon.exe" "%1"'
    System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

Section "$(SecEnv)" SEC_ENV
    DetailPrint "$(EnvRunning)"
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup.ps1"' $0
    IfFileExists "$INSTDIR\.venv\hertzon_installed.txt" +2 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "$(EnvFailed)" /SD IDOK
SectionEnd

Section "Uninstall"
    !insertmacro WaitClosed
    Delete "$SMPROGRAMS\Hertzon.lnk"
    Delete "$DESKTOP\Hertzon.lnk"
    ReadRegStr $0 HKCU "Software\Classes\.hzn" ""
    StrCmp $0 "Hertzon.Project" 0 +2
    DeleteRegKey HKCU "Software\Classes\.hzn"
    DeleteRegKey HKCU "Software\Classes\Hertzon.Project"
    System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
    DeleteRegKey HKCU "${UNINST_KEY}"
    DeleteRegKey HKCU "Software\Hertzon\InstallDir"
    DeleteRegValue HKCU "Software\Hertzon" "InstallDir"
    ; program files and the Python environment; projects saved elsewhere are not touched
    RMDir /r "$INSTDIR\hertzon"
    RMDir /r "$INSTDIR\examples"
    RMDir /r "$INSTDIR\docs"
    RMDir /r "$INSTDIR\.venv"
    Delete "$INSTDIR\Hertzon.exe"
    Delete "$INSTDIR\setup.ps1"
    Delete "$INSTDIR\setup.log"
    Delete "$INSTDIR\run.ps1"
    Delete "$INSTDIR\pyproject.toml"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\LICENSE"
    RMDir /r "$INSTDIR\hertzon.egg-info"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
SectionEnd

Function un.onInit
    !insertmacro MUI_UNGETLANGUAGE
FunctionEnd
