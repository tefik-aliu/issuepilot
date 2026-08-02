# Börja här på Windows

Det här är ditt första färdiga portfolio-projekt. Gränssnittet och dokumentationen är på engelska eftersom målet är internationella distansjobb.

## 1. Installera Python

Installera Python 3.12 eller nyare. Markera **Add Python to PATH** under installationen.

## 2. Packa upp projektet

Packa upp `issuepilot.zip` och öppna mappen `issuepilot`.

## 3. Öppna PowerShell i mappen

Högerklicka i mappen och välj **Open in Terminal**.

## 4. Installera projektet

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1
```

## 5. Lägg in demonstrationsdata

```powershell
.\.venv\Scripts\python.exe -m app.seed
```

## 6. Starta programmet

```powershell
.\run_windows.ps1
```

Öppna sedan:

- Programmet: `http://127.0.0.1:8000`
- API-dokumentationen: `http://127.0.0.1:8000/docs`

Stoppa servern med `Ctrl + C`.

## Testa att koden fungerar

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not e2e"
```

Du ska få resultatet **7 passed**.

## Vad du redan kan visa upp

- Ett fungerande fullstackprogram
- REST-API med validering
- SQLite-databas
- Sökning och filtrering
- Responsivt webbgränssnitt
- Automatiserade API-tester
- Ett Playwright-test för webbläsaren
- Docker och GitHub Actions

Nästa steg blir att lägga projektet på GitHub, driftsätta en publik demo och se till att du kan förklara de viktigaste delarna med egna ord.
