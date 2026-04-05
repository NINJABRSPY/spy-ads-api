@echo off
title NinjaSpy - SimilarWeb Tunnel (Auto-Reconnect)
echo === SimilarWeb Tunnel - Auto Reconnect ===
echo.

:loop
echo [%date% %time%] Conectando tunnel SSH...
ssh -R 4000:localhost:4000 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=no -o ExitOnForwardFailure=yes ninjabr@167.99.6.113
echo [%date% %time%] Tunnel desconectou. Reconectando em 10 segundos...
timeout /t 10 /nobreak >nul
goto loop
