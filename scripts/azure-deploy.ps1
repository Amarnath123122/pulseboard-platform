<#
.SYNOPSIS
  Deploys (or resumes) the whole Pulseboard system on Azure:
  infrastructure -> wake paused services -> identity + api code -> web UI -> smoke tests.

.EXAMPLE
  ./scripts/azure-deploy.ps1 -OwnerEmail you@example.com              # everything
  ./scripts/azure-deploy.ps1 -OwnerEmail you@example.com -SkipInfra   # code only
  ./scripts/azure-deploy.ps1 -OwnerEmail you@example.com -DeployRedis # add Azure Managed Redis (extra cost)

  Requires Azure CLI (az login), Python 3.11+ with `cryptography`, Node 18+, and the service repos checked out
  next to this one: ../pulseboard-identity ../pulseboard-api ../pulseboard-web
  Secrets are generated once into infra/secrets.local.json (git-ignored). Pause billing: ./scripts/azure-stop.ps1
#>
param(
  [Parameter(Mandatory = $true)][string]$OwnerEmail,
  [string]$ResourceGroup = "rg-pulseboard",
  [string]$Location = "centralindia",
  [switch]$SkipInfra,
  [switch]$SkipWeb,
  [switch]$DeployRedis
)
$ErrorActionPreference = "Continue"  # az writes warnings to stderr; real failures are caught by Assert-Ok
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$repos = Resolve-Path (Join-Path $root "..")

function New-Secret([int]$bytes = 32) {
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return ([Convert]::ToBase64String($b) -replace '[+/=]', '')
}
function Assert-Ok([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }
function Find-Python {
  foreach ($c in @("$repos\pulseboard-identity\.venv\Scripts\python.exe", "$repos\pulseboard-api\.venv\Scripts\python.exe", "python")) {
    if ((Get-Command $c -ErrorAction SilentlyContinue)) { return $c }
  }
  throw "Python with the 'cryptography' package is required"
}

# ---------- secrets (generated once, reused on every run) ----------
$secretsFile = Join-Path $root "infra\secrets.local.json"
$secrets = if (Test-Path $secretsFile) { Get-Content $secretsFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
$changed = $false
if (-not $secrets.postgresAdminPassword) { $secrets | Add-Member -Force postgresAdminPassword ("Pb1!" + (New-Secret 18)); $changed = $true }
if (-not $secrets.serviceKey) { $secrets | Add-Member -Force serviceKey (New-Secret 32); $changed = $true }
if (-not $secrets.adminPassword) { $secrets | Add-Member -Force adminPassword ("Adm1n!" + (New-Secret 12)); $changed = $true }
if (-not $secrets.identityJwtKeyB64) {
  $py = Find-Python
  $key = & $py -c "import base64;from cryptography.hazmat.primitives import serialization as s;from cryptography.hazmat.primitives.asymmetric import rsa;k=rsa.generate_private_key(public_exponent=65537,key_size=2048);print(base64.b64encode(k.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption())).decode())"
  Assert-Ok "Generating the identity signing key"
  $secrets | Add-Member -Force identityJwtKeyB64 $key.Trim(); $changed = $true
}
if ($changed) { $secrets | ConvertTo-Json | Out-File -Encoding utf8 $secretsFile }

# ---------- infrastructure ----------
if (-not $SkipInfra) {
  Write-Host "==> Resource group $ResourceGroup ($Location)"
  az group create --name $ResourceGroup --location $Location --tags app=pulseboard --output none
  Assert-Ok "az group create"

  $pg = az postgres flexible-server list -g $ResourceGroup --query "[0].{name:name,state:state}" -o json 2>$null | ConvertFrom-Json
  if ($pg -and $pg.state -eq "Stopped") {
    Write-Host "==> Starting Postgres $($pg.name)"
    az postgres flexible-server start -g $ResourceGroup -n $pg.name --output none
    Assert-Ok "Postgres start"
  }

  Write-Host "==> Infrastructure (first run ~15 min; API Management takes the longest)"
  az deployment group create `
    --resource-group $ResourceGroup `
    --name "pulseboard-infra" `
    --template-file (Join-Path $root "infra\main.bicep") `
    --parameters ownerEmail=$OwnerEmail `
                 postgresAdminPassword=$($secrets.postgresAdminPassword) `
                 serviceKey=$($secrets.serviceKey) `
                 identityJwtKeyB64=$($secrets.identityJwtKeyB64) `
                 adminPassword=$($secrets.adminPassword) `
                 deployRedis=$($DeployRedis.IsPresent.ToString().ToLower()) `
    --output none
  Assert-Ok "Infrastructure deployment"
}

$out = az deployment group show --resource-group $ResourceGroup --name "pulseboard-infra" --query properties.outputs --output json | ConvertFrom-Json
Assert-Ok "Reading deployment outputs"

# ---------- services ----------
function Deploy-Service([string]$repo, [string]$app, [string]$healthUrl) {
  Write-Host "==> $repo -> $app"
  az webapp start -g $ResourceGroup -n $app --output none
  $zip = Join-Path $env:TEMP "$repo.zip"
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Push-Location (Join-Path $repos $repo)
  try {
    # bsdtar writes forward-slash paths; Compress-Archive on PowerShell 5.1 writes backslashes Linux can't unpack.
    tar -a -c -f $zip --exclude "__pycache__" app migrations alembic.ini requirements.txt startup.sh
    Assert-Ok "Packaging $repo"
  } finally { Pop-Location }
  # Async: the remote pip install + migrations can outlast Kudu's gateway timeout. Readiness is polled instead.
  az webapp deploy --resource-group $ResourceGroup --name $app --src-path $zip --type zip --async true --only-show-errors --output none
  Assert-Ok "Deploying $repo"
  $script:pending += , @{ name = $repo; url = $healthUrl }
}

$script:pending = @()
Deploy-Service "pulseboard-identity" $out.identityAppName.value "$($out.identityUrl.value)/readyz"
Deploy-Service "pulseboard-api" $out.apiAppName.value "$($out.apiUrl.value)/readyz"

Write-Host "==> Waiting for services"
Start-Sleep 60  # let the async deployments start replacing the running code before polling
foreach ($svc in $script:pending) {
  $ready = $false
  for ($i = 0; $i -lt 50 -and -not $ready; $i++) {
    try { $ready = (Invoke-RestMethod $svc.url -TimeoutSec 20).status -eq "ok" } catch { Start-Sleep 15 }
  }
  if (-not $ready) { throw "$($svc.name) did not become ready ($($svc.url))" }
  Write-Host "    $($svc.name) ready"
}

# ---------- web ----------
if (-not $SkipWeb) {
  Write-Host "==> pulseboard-web -> $($out.webAppName.value)"
  $token = az staticwebapp secrets list -n $out.webAppName.value -g $ResourceGroup --query properties.apiKey -o tsv
  Assert-Ok "Reading Static Web Apps token"
  Push-Location (Join-Path $repos "pulseboard-web")
  try {
    if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund; Assert-Ok "npm ci" }
    npm run build; Assert-Ok "Web build"
    # Runtime config: REST + SSE through the gateway, WebSocket straight to the API.
    @{ apiUrl = "$($out.gatewayUrl.value)/core"; authUrl = "$($out.gatewayUrl.value)/identity"; wsUrl = $out.websocketUrl.value } |
      ConvertTo-Json | Out-File -Encoding ascii "dist\pulseboard\browser\config.json"
    npx --yes @azure/static-web-apps-cli@2.0.2 deploy ./dist/pulseboard/browser --deployment-token $token --env production
    Assert-Ok "Web deployment"
  } finally { Pop-Location }
}

# ---------- smoke test through the gateway ----------
Write-Host "==> Smoke test through the gateway"
$login = Invoke-RestMethod -Method Post "$($out.gatewayUrl.value)/identity/api/v1/auth/login" -ContentType "application/json" `
  -Body (@{ email = $OwnerEmail; password = $secrets.adminPassword } | ConvertTo-Json)
$meta = Invoke-RestMethod "$($out.gatewayUrl.value)/core/api/v1/meta" -Headers @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "    admin login OK (role $($login.user.role)); API reachable ($($meta.areas.Count) metric areas)"

Write-Host ""
Write-Host "Deployed."
Write-Host "  Web:        $($out.webUrl.value)"
Write-Host "  Gateway:    $($out.gatewayUrl.value)   (/identity, /core)"
Write-Host "  WebSocket:  $($out.websocketUrl.value)"
Write-Host "  Admin:      $OwnerEmail  (password in infra/secrets.local.json -> adminPassword)"
