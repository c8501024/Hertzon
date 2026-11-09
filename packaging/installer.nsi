; Hertzon installer (NSIS 3). Build with packaging/build_installer.sh, which stages the files and passes
; VERSION, STAGE (folder with the files to install) and OUT (installer path).
;
; Installs for the current user into %LOCALAPPDATA%\Programs\Hertzon (no administrator rights needed; the
; Python environment .venv is created next to Hertzon.exe and must stay writable), adds Hertzon to the
; start menu, the desktop and Settings > Apps, opens .hzn projects with Hertzon, and prepares the Python
; environment (setup.ps1) at the end of the installation.
;
; Help > Check for Updates > Install starts this installer with /WAITPID=<process id of Hertzon> and then
; closes Hertzon: the installer waits without a window until that process has ended (at most 2 minutes),
; so no file of the program or of .venv is still in use, and writes what it does to update.log next to
; itself.

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!include "MUI2.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!define APP "Hertzon"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hertzon"
; PowerShell by its full path (with the 64-bit file system view, see below): by its bare name Windows looks
; for it in PATH only, which can lack its folder (the in-app update failed with "[WinError 2]" that way)
!define POWERSHELL "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"

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
!define MUI_CUSTOMFUNCTION_ABORT LogCancel
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

Var WaitPid

; one line "<date>T<time> installer <version>: <text>" in update.log next to the installer (the folder Hertzon
; downloaded it to; Hertzon writes its own lines there), only for an update started from Hertzon
Function UpdateLog
    Exch $R9
    ${If} $WaitPid != ""
        Push $R0
        Push $R1
        Push $R2
        Push $R3
        Push $R4
        Push $R5
        Push $R6
        Push $R7
        Push $R8
        ${GetTime} "" "L" $R0 $R1 $R2 $R3 $R4 $R5 $R6
        ; Hertzon may be writing its own line at the same moment (the file is then in use): a few tries
        StrCpy $R8 0
        ${Do}
            ClearErrors
            FileOpen $R7 "$EXEDIR\update.log" a
            ${IfNot} ${Errors}
                FileSeek $R7 0 END
                FileWrite $R7 "$R2-$R1-$R0T$R4:$R5:$R6 installer ${VERSION}: $R9$\r$\n"
                FileClose $R7
                ${Break}
            ${EndIf}
            IntOp $R8 $R8 + 1
            ${If} $R8 >= 10
                ${Break}
            ${EndIf}
            Sleep 50
        ${Loop}
        Pop $R8
        Pop $R7
        Pop $R6
        Pop $R5
        Pop $R4
        Pop $R3
        Pop $R2
        Pop $R1
        Pop $R0
    ${EndIf}
    Pop $R9
FunctionEnd

!macro UpdateLog text
    Push "${text}"
    Call UpdateLog
!macroend

; started by Hertzon (/WAITPID=<process id>): wait until that Hertzon has closed, at most 2 minutes, before
; anything is shown. Hertzon starts the installer itself and closes right after; before, a hidden PowerShell
; did the waiting, and where it did not run Hertzon closed and nothing happened.
Function WaitForHertzon
    ${GetParameters} $R0
    ClearErrors
    ${GetOptions} $R0 "/WAITPID=" $WaitPid
    ${If} ${Errors}
    ${OrIf} $WaitPid == ""
        Return
    ${EndIf}
    IntOp $WaitPid $WaitPid + 0
    !insertmacro UpdateLog "started, waiting for Hertzon (process $WaitPid) to close"
    System::Call 'kernel32::OpenProcess(i 0x00100000, i 0, i $WaitPid) p .R1'   ; SYNCHRONIZE
    ${If} $R1 == 0
        !insertmacro UpdateLog "Hertzon (process $WaitPid) has closed already"
        Return
    ${EndIf}
    System::Call 'kernel32::WaitForSingleObject(p $R1, i 120000) i .R2'
    System::Call 'kernel32::CloseHandle(p $R1)'
    ${If} $R2 == 0
        !insertmacro UpdateLog "Hertzon (process $WaitPid) has closed"
    ${Else}
        !insertmacro UpdateLog "Hertzon (process $WaitPid) is still running after 2 minutes (wait result $R2)"
    ${EndIf}
FunctionEnd

Function .onInit
    Call WaitForHertzon
    !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function .onInstSuccess
    !insertmacro UpdateLog "installed"
FunctionEnd

Function .onInstFailed
    !insertmacro UpdateLog "installation failed"
FunctionEnd

Function LogCancel
    !insertmacro UpdateLog "cancelled"
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
    ; the MCP server of an AI app, the remote service and solver processes run from this folder without a
    ; window and keep files in use (hzmesh.dll, packages in .venv): stopped here, otherwise the update could
    ; only replace those files after a restart of the computer
    IfFileExists "$INSTDIR\.venv\*.*" 0 nobackground
    ; the installer is a 32-bit program: without this it starts the 32-bit PowerShell, which cannot see the
    ; modules loaded in a 64-bit Python and so does not find the processes that use .venv
    ${DisableX64FSRedirection}
    InitPluginsDir
    ; (a path relative to this script: makensis works in its folder)
    File "/oname=$PLUGINSDIR\stop_background.ps1" "stop_background.ps1"
    ; this installer may run from inside $INSTDIR (Updates folder of an installation in %LOCALAPPDATA%\Hertzon):
    ; its process id keeps the script from stopping it
    System::Call 'kernel32::GetCurrentProcessId() i .r0'
    nsExec::ExecToLog '"${POWERSHELL}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\stop_background.ps1" -Root "$INSTDIR" -Keep $0'
    Pop $0
    nobackground:
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
    ${DisableX64FSRedirection}   ; the 64-bit PowerShell (see above)
    ClearErrors
    ExecWait '"${POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup.ps1"' $0
    ; ExecWait leaves $0 as it was when PowerShell cannot be started (blocked by a policy, missing)
    ${If} ${Errors}
        !insertmacro UpdateLog "PowerShell could not be started: ${POWERSHELL}"
    ${Else}
        !insertmacro UpdateLog "setup.ps1 ended (exit code $0)"
    ${EndIf}
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
    Delete "$INSTDIR\setup.prev.log"
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
