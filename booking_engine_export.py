#!/usr/bin/env python3
"""
Booking Engine Daily Export
Runs via GitHub Actions every morning at 08:00 UK time.
Logs into manage.flashpack.com, exports the departures CSV,
and pastes it into the BE Export tab of the target Google Sheet.
"""

import os
import time
import glob
import csv
import shutil
import json
import tempfile
from datetime import datetime
import pytz
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

FLASHPACK_EMAIL    = os.environ["BOOKING_ENGINE_EMAIL_ADDRESS"]
FLASHPACK_PASSWORD = os.environ["BOOKING_ENGINE_PASSWORD"]

SPREADSHEET_ID     = "1Ecf8ZgNk91os5Gi-z_1e34J6jy1bsDc4B3YUwoF2aog"
EXPORT_TAB         = "BE Export"
TIMESTAMP_TAB      = "Last Updated"

DOWNLOAD_DIR       = tempfile.gettempdir()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ─────────────────────────────────────────────
# STEP 1: Download the CSV
# ─────────────────────────────────────────────

def download_csv():
    print("Starting browser...")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    })

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 20)

    try:
        print("Logging in...")
        driver.get("https://manage.flashpack.com/departures")
        time.sleep(3)

        email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[name='email']")))
        email_field.clear()
        email_field.send_keys(FLASHPACK_EMAIL)

        password_field = driver.find_element(By.CSS_SELECTOR, "input[type='password'], input[name='password']")
        password_field.clear()
        password_field.send_keys(FLASHPACK_PASSWORD)

        submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
        submit_btn.click()
        time.sleep(8)

        print("Setting date filters...")

        # Set start date
        start_date = wait.until(EC.presence_of_element_located((By.NAME, "from_date")))
        driver.execute_script("arguments[0].value = '2026-01-01';", start_date)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", start_date)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", start_date)
        time.sleep(1)
        start_date.click()
        start_date.send_keys(Keys.TAB)
        time.sleep(2)

        # Set end date
        end_date = wait.until(EC.presence_of_element_located((By.NAME, "to_date")))
        driver.execute_script("arguments[0].value = '2027-12-31';", end_date)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", end_date)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", end_date)
        time.sleep(1)
        end_date.click()
        end_date.send_keys(Keys.TAB)
        time.sleep(4)

        # Record time just before clicking Export
        click_time = time.time()

        print("Clicking Export...")
        export_btn = wait.until(EC.element_to_be_clickable((
            By.XPATH, "//button[contains(translate(text(),'EXPORT','export'),'export')] | //a[contains(translate(text(),'EXPORT','export'),'export')]"
        )))
        export_btn.click()

        # Wait for a new file to appear after click_time
        print("Waiting for download...")
        timeout = 60
        elapsed = 0
        new_csv = None
        while elapsed < timeout:
            time.sleep(2)
            elapsed += 2
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*.csv")):
                if os.path.getmtime(f) >= click_time:
                    new_csv = f
                    break
            if new_csv:
                break

    finally:
        driver.quit()

    if not new_csv:
        raise FileNotFoundError("No new CSV file appeared after Export. The export may have failed.")

    print(f"Downloaded: {new_csv}")
    return new_csv

# ─────────────────────────────────────────────
# STEP 2: Authenticate with Google Sheets
# ─────────────────────────────────────────────

def get_sheets_service():
    service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)

# ─────────────────────────────────────────────
# STEP 3: Update the Google Sheet
# ─────────────────────────────────────────────

def update_sheet(csv_path, service):
    print("Reading CSV data...")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        data = list(csv.reader(f))

    if not data:
        raise ValueError("The downloaded CSV appears to be empty.")

    sheets = service.spreadsheets()

    print("Clearing existing sheet data...")
    sheets.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{EXPORT_TAB}'"
    ).execute()

    print(f"Pasting {len(data)} rows into {EXPORT_TAB} tab...")
    sheets.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{EXPORT_TAB}'!A1",
        valueInputOption="RAW",
        body={"values": data}
    ).execute()

    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(uk_tz)
    timestamp = now_uk.strftime("Last updated on %d/%m/%Y at %H:%M")

    print(f"Updating timestamp: {timestamp}")
    sheets.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TIMESTAMP_TAB}'!A1",
        valueInputOption="RAW",
        body={"values": [[timestamp]]}
    ).execute()

    print("Sheet updated successfully.")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=== Booking Engine Daily Export ===")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    csv_path = download_csv()
    service  = get_sheets_service()
    update_sheet(csv_path, service)

    os.remove(csv_path)
    print("Temporary CSV file removed.")
    print("=== All done! ===")

if __name__ == "__main__":
    main()
