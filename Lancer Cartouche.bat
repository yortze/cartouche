@echo off
rem Lance Cartouche sans fenˆtre de console (pythonw), sinon avec python.
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0app.py" & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0app.py" & exit /b 0)
where py >nul 2>nul && (py -3 "%~dp0app.py" & exit /b 0)
where python >nul 2>nul && (python "%~dp0app.py" & exit /b 0)
echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/ puis relance ce fichier.
pause
