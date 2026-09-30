import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        print("Navigating to meeting detail...")
        await page.goto("http://localhost:8000/meeting/cfd0cb97-7b0b-4c91-b39c-680d6278db49", wait_until="networkidle")
        await asyncio.sleep(2)

        # Screenshot 1: Tab 1 (Summary & Insights)
        await page.screenshot(path="ui_screenshot_tab1_summary.png")
        print("Captured Tab 1 (Summary & Insights)")

        # Click Tab 2 (Structured Transcript)
        await page.click("#tab-btn-transcript")
        await asyncio.sleep(1)
        await page.screenshot(path="ui_screenshot_tab2_transcript.png")
        print("Captured Tab 2 (Structured Transcript)")

        # Click Tab 3 (Analytics)
        await page.click("#tab-btn-analytics")
        await asyncio.sleep(1)
        await page.screenshot(path="ui_screenshot_tab3_analytics.png")
        print("Captured Tab 3 (Analytics)")

        # Switch back to Tab 1 and open Actions Dropdown
        await page.click("#tab-btn-summary")
        await asyncio.sleep(0.5)
        await page.click("#btn-actions-toggle")
        await asyncio.sleep(0.5)
        await page.screenshot(path="ui_screenshot_actions_dropdown.png")
        print("Captured Actions Dropdown")

        # Open Export PDF Modal
        await page.click("#btn-primary-export-pdf")
        await asyncio.sleep(0.5)
        await page.screenshot(path="ui_screenshot_export_modal.png")
        print("Captured Export Modal")

        # Test PDF Download
        print("Testing PDF Download...")
        os.makedirs("data/exports", exist_ok=True)
        pdf_out = os.path.abspath("data/exports/test_verified_report.pdf")
        async with page.expect_download(timeout=25000) as dl:
            await page.click("#modal-btn-download-pdf")
            download = await dl.value
            await download.save_as(pdf_out)
            print(f"Downloaded PDF: {pdf_out}, Size: {os.path.getsize(pdf_out)} bytes")

        # Render downloaded PDF in Chrome
        print("Rendering downloaded PDF...")
        pdf_page = await browser.new_page(viewport={"width": 900, "height": 1200})
        await pdf_page.goto("file:///" + pdf_out.replace("\\", "/"))
        await asyncio.sleep(2)
        await pdf_page.screenshot(path="pdf_page1_preview.png")
        print("Captured PDF page 1 preview")

        await browser.close()
        print("All tests completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
