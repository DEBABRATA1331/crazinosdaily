#!/usr/bin/env bash
# Generated with cloud onboarding skills selector @2026-11-12T16:16:00+05:30

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-<PROJECT_ID>}"
REGION="${REGION:-asia-south1}"
INSTANCE_NAME="${INSTANCE_NAME:-kit-sales-pg-instance}"
DATABASE_NAME="${DATABASE_NAME:-kit_sales_db}"
DB_USER="${DB_USER:-kit_sales_admin}"
DB_PASSWORD="${DB_PASSWORD:-<STRONG_DB_PASSWORD>}"

export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

# 1. Provision Cloud SQL for PostgreSQL Enterprise Instance
gcloud beta sql instances create "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --cpu=2 \
  --memory=7680MB \
  --availability-type=REGIONAL \
  --storage-type=SSD \
  --storage-size=20GB \
  --storage-auto-increase \
  --backup-start-time=02:00 \
  --enable-point-in-time-recovery \
  --ssl-mode=ENCRYPTED_ONLY \
  --deletion-protection \
  --labels=resource_generated_by=cloud_db_onboarding_skill,application=kit_sales_dashboard

# 2. Create Application Database
gcloud beta sql databases create "${DATABASE_NAME}" \
  --instance="${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --charset=UTF8

# 3. Create Application Database User
gcloud beta sql users create "${DB_USER}" \
  --instance="${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --password="${DB_PASSWORD}"

