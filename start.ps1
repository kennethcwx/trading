$root = $PSScriptRoot

Write-Host ""
Write-Host "=== TRADING DASHBOARD ===" -ForegroundColor Cyan
Write-Host ""

# Backend
Write-Host "Starting backend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; python main.py"

Start-Sleep -Seconds 2

# Frontend
Write-Host "Starting frontend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "  Backend API : http://localhost:8000" -ForegroundColor Green
Write-Host "  Dashboard   : http://localhost:3000" -ForegroundColor Green
Write-Host ""
Write-Host "  IBKR: Start IB Gateway -> Paper -> port 4002" -ForegroundColor Yellow
Write-Host "  Watchlist & portfolio size: edit backend\config.py" -ForegroundColor Yellow
Write-Host ""
