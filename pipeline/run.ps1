param (
    [Parameter(Mandatory=$true)]
    [string]$VideoDir,
    [string]$StoreId = "ST1008"
)

$ApiUrl = "http://localhost:8000/events/ingest"

Write-Host "Processing store intelligence clips for store: $StoreId..." -ForegroundColor Cyan

# 1. Process Entry Camera Clip
$EntryPath = Join-Path $VideoDir "entry.mp4"
if (Test-Path $EntryPath) {
    Write-Host "Running Entry Camera..." -ForegroundColor Yellow
    python detect.py --video "$EntryPath" --camera "CAM_ENTRY_01" --store "$StoreId" --api-url "$ApiUrl"
}

# 2. Process Main Floor Camera Clip
$FloorPath = Join-Path $VideoDir "floor.mp4"
if (Test-Path $FloorPath) {
    Write-Host "Running Floor Camera..." -ForegroundColor Yellow
    python detect.py --video "$FloorPath" --camera "CAM_FLOOR_01" --store "$StoreId" --api-url "$ApiUrl"
}

# 3. Process Billing Camera Clip
$BillingPath = Join-Path $VideoDir "billing.mp4"
if (Test-Path $BillingPath) {
    Write-Host "Running Billing Camera..." -ForegroundColor Yellow
    python detect.py --video "$BillingPath" --camera "CAM_BILLING_01" --store "$StoreId" --api-url "$ApiUrl"
}

Write-Host "Pipeline processing complete." -ForegroundColor Green
