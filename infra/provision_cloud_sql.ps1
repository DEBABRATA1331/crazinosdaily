# Generated with cloud onboarding skills selector @2026-11-12T16:16:00+05:30

param(
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [string]$Region = "asia-south1",
    [string]$InstanceName = "kit-sales-pg-instance",
    [string]$DatabaseName = "kit_sales_db",
    [string]$DbUser = "kit_sales_admin",
    [Parameter(Mandatory=$true)][string]$DbPassword
)

$env:CLOUDSDK_METRICS_ENVIRONMENT = if ($env:CLOUDSDK_METRICS_ENVIRONMENT) {
    "$env:CLOUDSDK_METRICS_ENVIRONMENT datacloud.antigravity"
} else {
    "datacloud.antigravity"
}

Write-Host "1. Creating Cloud SQL for PostgreSQL Enterprise instance '$InstanceName' in $Region..."
gcloud beta sql instances create $InstanceName `
  --project=$ProjectId `
  --region=$Region `
  --database-version=POSTGRES_16 `
  --edition=ENTERPRISE `
  --cpu=2 `
  --memory=7680MB `
  --availability-type=REGIONAL `
  --storage-type=SSD `
  --storage-size=20GB `
  --storage-auto-increase `
  --backup-start-time=02:00 `
  --enable-point-in-time-recovery `
  --ssl-mode=ENCRYPTED_ONLY `
  --deletion-protection `
  --labels=resource_generated_by=cloud_db_onboarding_skill,application=kit_sales_dashboard

Write-Host "2. Creating database '$DatabaseName'..."
gcloud beta sql databases create $DatabaseName `
  --instance=$InstanceName `
  --project=$ProjectId `
  --charset=UTF8

Write-Host "3. Creating database user '$DbUser'..."
gcloud beta sql users create $DbUser `
  --instance=$InstanceName `
  --project=$ProjectId `
  --password=$DbPassword

