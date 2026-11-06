# 📚 Audible Series Dashboard

A local Python automation tool that syncs with your Audible library, intelligently analyzes series metadata (including automatically detecting box sets and omnibus ranges), and generates a clean, interactive, browser-persistent HTML dashboard to track your reading progress and spot missing volumes.

## ✨ Features

- **Automatic Library Sync:** Connects directly via the unofficial Audible API to pull your owned titles, listening status, and completion percentages.
- **Smart Box Set & Omnibus Unpacking:** Automatically detects range patterns (e.g., "Books 1-10") and suppresses redundant individual purchase recommendations for books you already own.
- **High-Water Mark Inference:** If you own a later book or box set, the script automatically infers completion of prior volumes in the sequence.
- **Interactive Browser Persistence (`localStorage`):** 
  - Click to cycle book statuses (`Completed`, `In Progress`, `Unread`).
  - Manually override series completion.
  - Filter out completed series with a single toggle.
  - All changes persist locally in your browser across sessions and dashboard regenerations.
- **Quick Links:** Built-in direct links to search series on Audible or purchase individual missing volumes.
- **Clean UI Controls:** Global "Expand All", "Collapse All", and "Hide Completed Series" controls.

## 🚀 Getting Started

### Prerequisites
- Python 3.8+ installed on your machine.
- An active Audible account.

### Installation

1. Clone or download this repository:
   ```bash
   git clone [https://github.com/rstlhiii/audible-series-dashboard.git](https://github.com/rstlhiii/audible-series-dashboard.git)
   cd audible-series-dashboard

Install the required dependencies:

Bash
pip install -r requirements.txt
Run the script:

Bash
python audible_visual_dashboard.py
   
