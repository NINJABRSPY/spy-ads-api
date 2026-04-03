@echo off
echo === SSH Tunnel para SimilarWeb On-Demand ===
echo Conectando ao VPS para expor porta 4000...
echo.
ssh -R 4000:localhost:4000 -N -o ServerAliveInterval=60 -o ServerAliveCountMax=3 ninjabr@167.99.6.113
echo Tunnel desconectado. Reiniciando em 5 segundos...
timeout /t 5
goto :start
:start
ssh -R 4000:localhost:4000 -N -o ServerAliveInterval=60 -o ServerAliveCountMax=3 ninjabr@167.99.6.113
