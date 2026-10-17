<#
.SYNOPSIS
  Pauses Azure billing without deleting anything: stops every web app, moves the plan to Free (F1), stops Postgres.
  Resume with ./scripts/azure-deploy.ps1 (restores B1 and starts everything). API Management Consumption and
  Static Web Apps (Free) bill per use, so they need no action. Azure restarts a stopped Postgres after 7 days.
#>
param([string]$ResourceGroup = "rg-pulseboard")
$ErrorActionPreference = "Continue"

foreach ($app in (az webapp list -g $ResourceGroup --query "[].name" -o tsv)) {
  az webapp stop -g $ResourceGroup -n $app --output none
  az webapp config set -g $ResourceGroup -n $app --always-on false --output none  # Free tier has no Always On
  Write-Host "stopped $app"
}
$plan = az appservice plan list -g $ResourceGroup --query "[0].name" -o tsv
if ($plan) { az appservice plan update -g $ResourceGroup -n $plan --sku F1 --output none; Write-Host "plan $plan -> F1" }
$pg = az postgres flexible-server list -g $ResourceGroup --query "[0].name" -o tsv
if ($pg) { az postgres flexible-server stop -g $ResourceGroup -n $pg --output none; Write-Host "stopped $pg" }
