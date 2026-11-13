# Generated with cloud onboarding skills selector @2026-11-12T16:16:00+05:30

terraform {
  required_version = ">= 1.3.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

variable "project_id" {
  description = "The Google Cloud Project ID"
  type        = string
}

variable "region" {
  description = "The GCP region for Cloud SQL (e.g., asia-south1 for Mumbai/India)"
  type        = string
  default     = "asia-south1"
}

variable "instance_name" {
  description = "The name of the Cloud SQL for PostgreSQL instance"
  type        = string
  default     = "kit-sales-pg-instance"
}

variable "database_name" {
  description = "The application database name"
  type        = string
  default     = "kit_sales_db"
}

variable "db_user" {
  description = "The application database username"
  type        = string
  default     = "kit_sales_admin"
}

variable "db_password" {
  description = "The password for the database user"
  type        = string
  sensitive   = true
}

provider "google" {
  project = var.project_id
  region  = var.region
  default_labels = {
    resource_generated_by = "cloud_db_onboarding_skill"
  }
}

resource "google_sql_database_instance" "kit_sales_postgres" {
  name                = var.instance_name
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true

  settings {
    tier              = "db-custom-2-7680"
    edition           = "ENTERPRISE"
    availability_type = "REGIONAL"
    disk_autoresize   = true
    disk_size         = 20
    disk_type         = "PD_SSD"

    user_labels = {
      resource_generated_by = "cloud_db_onboarding_skill"
      application           = "kit_sales_dashboard"
      environment           = "production"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
    }

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }
  }
}

resource "google_sql_database" "kit_sales_db" {
  name     = var.database_name
  project  = var.project_id
  instance = google_sql_database_instance.kit_sales_postgres.name
  charset  = "UTF8"
}

resource "google_sql_user" "kit_sales_admin" {
  name     = var.db_user
  project  = var.project_id
  instance = google_sql_database_instance.kit_sales_postgres.name
  password = var.db_password
}

output "instance_connection_name" {
  description = "Cloud SQL Instance Connection Name (for Cloud SQL Auth Proxy)"
  value       = google_sql_database_instance.kit_sales_postgres.connection_name
}

output "public_ip_address" {
  description = "Public IPv4 address of the Cloud SQL instance"
  value       = google_sql_database_instance.kit_sales_postgres.public_ip_address
}

