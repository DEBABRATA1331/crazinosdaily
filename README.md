# Mobile-First Kit Sales & Management Dashboard

A fast, mobile-first Progressive Web App (PWA) for managing kit inventory, selling prices, daily sales across multiple platforms, automated reporting, and one-tap WhatsApp End-of-Day sales sharing.

## Quick Start

```powershell
cd C:\Users\crack\.gemini\antigravity\scratch\kit-sales-dashboard
.\.venv\Scripts\python.exe app.py
```

Then open **http://localhost:5000** in your browser or mobile device.

## Key Features

- **5 Primary Mobile-First Sections**:
  1. **Dashboard**: Today's summary cards (Total Kits Sold, Total Sales Revenue, Total Orders, Total Kits Remaining), quick actions (`+ Add Sale`, `+ Add New Kit`, `View Daily Report`, and `GENERATE TODAY'S REPORT`), and today's sales grouped by platform.
  2. **Add Sale**: Ultra-fast entry form with searchable kit picker, platform selector & quick chips, quantity stepper/presets, auto-populated editable selling price, automatic Total Sale Amount (`Quantity × Selling Price`) & Estimated Profit (`(Selling Price − Cost Price) × Quantity`) calculations, plus Edit/Delete support for recent entries.
  3. **Kits**: Add, edit, activate/deactivate kits, update selling prices (while strictly preserving historical sale prices on past transactions), and inspect individual kit details & sales history.
  4. **Reports**:
     - **A. Daily Report**
     - **B. Weekly Report**
     - **C. Monthly Report**
     - **D. Annual Report**
     - **Custom Date Range, Kit & Platform Filters**
     - **Report History & Two-Period Comparison**
     - **End-of-Day Shareable Report** (`Copy Report`, `Share on WhatsApp`, `Download PDF`, `Download Excel`, `Download CSV`).
  5. **Settings**: Business name, currency (`INR ₹` default), timezone (`Asia/Kolkata` default), stock tracking toggle, profit calculation toggle, custom platform manager, full Excel/CSV export, JSON database backup & restore, and administrator password lock (`admin123` default).

