import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BOOKING_URL = "https://www.tablecheck.com/en/gemm-house-bangkok/reserve/landing?utm_source=gemm_house"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("console", lambda msg: errors.append(f"console[{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
        response = await page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(12000)

        details = await page.evaluate("""() => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const nodes = [...document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="combobox"], [role="option"], [role="radio"], [role="checkbox"]')]
            .filter(visible).map((el) => ({
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') || '',
              text: clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || ''),
              ariaLabel: el.getAttribute('aria-label') || '',
              title: el.getAttribute('title') || '',
              type: el.getAttribute('type') || '',
              value: el.value || '',
              disabled: !!el.disabled,
              name: el.getAttribute('name') || '',
              placeholder: el.getAttribute('placeholder') || ''
            })).slice(0, 250);
          return {
            title: document.title,
            url: location.href,
            readyState: document.readyState,
            bodyText: clean(document.body?.innerText || '').slice(0, 16000),
            controls: nodes
          };
        }""")

        lines = [
            f"HTTP status: {response.status if response else 'no response'}",
            f"Final URL: {details['url']}",
            f"Page title: {details['title']}",
            f"Document readyState: {details['readyState']}",
            "",
            "=== VISIBLE PAGE TEXT ===",
            details["bodyText"],
            "",
            "=== VISIBLE CONTROLS (first 250) ===",
        ]
        for item in details["controls"]:
            lines.append(str(item))
        if errors:
            lines += ["", "=== BROWSER ERRORS ===", *errors[:50]]

        Path("page-inspection.txt").write_text("\n".join(lines), encoding="utf-8")
        await page.screenshot(path="page-screenshot.png", full_page=True)
        await browser.close()
        print("\n".join(lines[:80]))
        print("\nSaved page-inspection.txt and page-screenshot.png")

if __name__ == "__main__":
    asyncio.run(main())
