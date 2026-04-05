@echo off
title NinjaSpy Services
echo === Iniciando NinjaSpy Services ===
echo.

cd /d C:\Users\felip\Desktop\bigspy_scraper

echo [1/2] Iniciando SimilarWeb Server...
start "SimilarWeb Server" cmd /c "node similarweb_server.js"
timeout /t 3 /nobreak >nul

echo [2/2] Iniciando SSH Tunnel...
start "SSH Tunnel" cmd /c "keep_tunnel.bat"

echo.
echo === Todos os servicos iniciados ===
echo Nao feche esta janela.
echo.
echo Servicos rodando:
echo   - SimilarWeb Server: http://localhost:4000
echo   - SSH Tunnel: traffic.ninjabrhub.online
echo.
pause
