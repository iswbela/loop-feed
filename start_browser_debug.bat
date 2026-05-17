@echo off
REM ============================================================
REM start_browser_debug.bat — Loop Feed
REM ------------------------------------------------------------
REM Inicia o Chrome (ou Opera como fallback) com CDP habilitado
REM na porta 9222 para que o Loop Feed possa conectar via CDP
REM e abrir as páginas em uma ABA NOVA do browser real.
REM
REM O que este script faz automaticamente:
REM   1. Detecta Chrome, Opera ou Opera GX instalado
REM   2. Encerra instâncias existentes do browser escolhido
REM      (necessário: Chrome ignora --remote-debugging-port se
REM       já existe um processo em execução)
REM   3. Inicia o browser com remote debugging na porta 9222
REM   4. Usa perfil persistente em %APPDATA%\LoopFeed\BrowserProfile
REM      (cookies e sessão são mantidos entre execuções)
REM
REM USO:
REM   Execute este .bat com dois cliques antes de abrir o Loop Feed.
REM   Aguarde o browser abrir. Você pode usá-lo normalmente.
REM   O Loop Feed se conectará automaticamente via CDP.
REM
REM Para parar, basta fechar o browser.
REM ============================================================

setlocal EnableDelayedExpansion

set "DEBUG_PORT=9222"
set "DEBUG_PROFILE=%APPDATA%\LoopFeed\BrowserProfile"

REM ------------------------------------------------------------
REM Criar diretório de perfil se não existir
REM ------------------------------------------------------------
if not exist "%DEBUG_PROFILE%" (
    mkdir "%DEBUG_PROFILE%"
    echo [INFO] Perfil criado em: %DEBUG_PROFILE%
)

REM ------------------------------------------------------------
REM Detectar browser — ordem de preferência: Chrome > Opera > Opera GX
REM ------------------------------------------------------------
set "BROWSER_EXE="
set "BROWSER_NAME="

REM Chrome (instalação por usuário — mais comum)
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    set "BROWSER_NAME=Google Chrome"
    set "BROWSER_PID=chrome.exe"
    goto :found_browser
)

REM Chrome (instalação global — Program Files)
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    set "BROWSER_NAME=Google Chrome"
    set "BROWSER_PID=chrome.exe"
    goto :found_browser
)

if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    set "BROWSER_NAME=Google Chrome"
    set "BROWSER_PID=chrome.exe"
    goto :found_browser
)

REM Opera (busca em subpastas versionadas)
for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Programs\Opera\opera.exe" 2^>nul') do (
    set "BROWSER_EXE=%%F"
    set "BROWSER_NAME=Opera"
    set "BROWSER_PID=opera.exe"
    goto :found_browser
)

REM Opera GX
for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Programs\Opera GX\opera.exe" 2^>nul') do (
    set "BROWSER_EXE=%%F"
    set "BROWSER_NAME=Opera GX"
    set "BROWSER_PID=opera.exe"
    goto :found_browser
)

echo [ERRO] Nenhum browser compativel encontrado.
echo        Instale o Google Chrome, Opera ou Opera GX e tente novamente.
echo.
pause
exit /b 1

:found_browser
echo ============================================================
echo  Loop Feed — Iniciando browser com CDP
echo ============================================================
echo  Browser : %BROWSER_NAME%
echo  Exe     : %BROWSER_EXE%
echo  Porta   : %DEBUG_PORT%
echo  Perfil  : %DEBUG_PROFILE%
echo ============================================================
echo.

REM ------------------------------------------------------------
REM Encerrar instâncias existentes
REM (Chrome ignora --remote-debugging-port se já está rodando)
REM ------------------------------------------------------------
echo [1/3] Encerrando instancias existentes de %BROWSER_PID%...
taskkill /F /IM "%BROWSER_PID%" /T >nul 2>&1
if %errorlevel% == 0 (
    echo        Instancias encerradas. Aguardando...
    timeout /t 2 /nobreak >nul
) else (
    echo        Nenhuma instancia em execucao.
)

REM ------------------------------------------------------------
REM Iniciar browser com remote debugging
REM ------------------------------------------------------------
echo [2/3] Iniciando %BROWSER_NAME% com remote debugging...
start "" "%BROWSER_EXE%" ^
    --remote-debugging-port=%DEBUG_PORT% ^
    --user-data-dir="%DEBUG_PROFILE%" ^
    --no-first-run ^
    --no-default-browser-check ^
    --disable-blink-features=AutomationControlled

REM ------------------------------------------------------------
REM Aguardar CDP ficar disponível
REM ------------------------------------------------------------
echo [3/3] Aguardando CDP ficar disponivel na porta %DEBUG_PORT%...
set /a "attempts=0"
:wait_loop
    timeout /t 1 /nobreak >nul
    set /a "attempts+=1"
    powershell -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%DEBUG_PORT%/json/version' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
    if %errorlevel% == 0 goto :cdp_ready
    if %attempts% geq 20 goto :cdp_timeout
goto :wait_loop

:cdp_ready
echo.
echo ============================================================
echo  CDP disponivel! Browser pronto para o Loop Feed.
echo ============================================================
echo.
echo  Pode usar o browser normalmente.
echo  Abra o Loop Feed — ele se conectara automaticamente.
echo.
echo  Para encerrar o modo debug, feche o browser.
echo ============================================================
goto :end

:cdp_timeout
echo.
echo [AVISO] CDP nao ficou disponivel em 20 segundos.
echo         O browser pode ter aberto sem o remote debugging.
echo         Tente fechar o browser e executar este script novamente.
echo.

:end
endlocal
