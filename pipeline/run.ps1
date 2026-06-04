param (
    [Parameter(Mandatory=$true)]
    [string]$VideoDir,
    [string]$StoreId = "ST1008"
)

$ApiUrl = "http://localhost:8000/events/ingest"
$DetectScript = Join-Path $PSScriptRoot "detect.py"

Write-Host "Processing store intelligence clips for store: $StoreId..." -ForegroundColor Cyan

# 1. Process Entry Camera Clip
$EntryPath = Join-Path $VideoDir "entry.mp4"
$Entry1Path = Join-Path $VideoDir "entry 1.mp4"
if (Test-Path $EntryPath) {
    Write-Host "Running Entry Camera..." -ForegroundColor Yellow
    python "$DetectScript" --video "$EntryPath" --camera "CAM_ENTRY_01" --store "$StoreId" --api-url "$ApiUrl"
} elseif (Test-Path $Entry1Path) {
    Write-Host "Running Entry Camera 1..." -ForegroundColor Yellow
    python "$DetectScript" --video "$Entry1Path" --camera "CAM_ENTRY_01" --store "$StoreId" --api-url "$ApiUrl"
}

$Entry2Path = Join-Path $VideoDir "entry 2.mp4"
if (Test-Path $Entry2Path) {
    Write-Host "Running Entry Camera 2..." -ForegroundColor Yellow
    python "$DetectScript" --video "$Entry2Path" --camera "CAM_ENTRY_02" --store "$StoreId" --api-url "$ApiUrl"
}

# 2. Process Main Floor Camera Clip
$FloorPath = Join-Path $VideoDir "floor.mp4"
$ZonePath = Join-Path $VideoDir "zone.mp4"
if (Test-Path $FloorPath) {
    Write-Host "Running Floor Camera..." -ForegroundColor Yellow
    python "$DetectScript" --video "$FloorPath" --camera "CAM_FLOOR_01" --store "$StoreId" --api-url "$ApiUrl"
} elseif (Test-Path $ZonePath) {
    Write-Host "Running Floor Camera (Zone)..." -ForegroundColor Yellow
    python "$DetectScript" --video "$ZonePath" --camera "CAM_FLOOR_01" --store "$StoreId" --api-url "$ApiUrl"
}

# 3. Process Billing Camera Clip
$BillingPath = Join-Path $VideoDir "billing.mp4"
$BillingAreaPath = Join-Path $VideoDir "billing_area.mp4"
if (Test-Path $BillingPath) {
    Write-Host "Running Billing Camera..." -ForegroundColor Yellow
    python "$DetectScript" --video "$BillingPath" --camera "CAM_BILLING_01" --store "$StoreId" --api-url "$ApiUrl"
} elseif (Test-Path $BillingAreaPath) {
    Write-Host "Running Billing Camera (Area)..." -ForegroundColor Yellow
    python "$DetectScript" --video "$BillingAreaPath" --camera "CAM_BILLING_01" --store "$StoreId" --api-url "$ApiUrl"
}

Write-Host "Pipeline processing complete." -ForegroundColor Green
