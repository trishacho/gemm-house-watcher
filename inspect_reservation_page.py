import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BOOKING_URL = "https://www.tablecheck.com/en/gemm-house-bangkok/reserve/landing?utm_source=gemm_house"

async def capture(page, label):
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
        f"=== {label} ===",
        f"Final URL: {details['url']}",
        f"Page title: {details['title']}",
        f"Document readyState: {details['readyState']}",
        "",
        "VISIBLE PAGE TEXT:",
        details["bodyText"],
        "",
        "VISIBLE CONTROLS (first 250):",
    ]
    lines.extend(str(item) for item in details["controls"])
    return "\n".join(lines)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("console", lambda msg: errors.append(f"console[{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
        response = await page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)
        sections = [f"Initial HTTP status: {response.status if response else 'no response'}"]
        sections.append(await capture(page, "BEFORE TERMS CONFIRMATION"))

        # This confirms the displayed terms only. It does not choose a date/time,
        # submit a reservation, or make a payment.
        # The page exposes both a wrapper with role=button and its nested
        # native button. Target the native button to avoid counting both.
        confirm = page.locator("button").filter(has_text="Confirm and continue")
        count = await confirm.count()
        sections.append(f"Native terms confirmation buttons found: {count}")
        if count >= 1:
            target = confirm.first
            if await target.is_visible() and await target.is_enabled():
                await target.click()
                await page.wait_for_timeout(8000)
                sections.append(await capture(page, "AFTER TERMS CONFIRMATION"))

                # Open only the guest/date/time pickers to inspect their options.
                # Do not choose a slot or press Find availability.
                guests = page.get_by_role("button", name="2 Guests", exact=True)
                if await guests.count() == 1 and await guests.is_enabled():
                    await guests.click()
                    await page.wait_for_timeout(700)
                    sections.append(await capture(page, "GUEST PICKER OPEN"))
                    await page.keyboard.press("Escape")

                date_button = page.get_by_role("button", name="No dates available", exact=True)
                if await date_button.count() == 1 and await date_button.is_enabled():
                    await date_button.click()
                    await page.wait_for_timeout(700)
                    sections.append(await capture(page, "DATE PICKER OPEN"))
                    await page.keyboard.press("Escape")

                time_button = page.get_by_role("button", name="Select a time", exact=True)
                if await time_button.count() == 1 and await time_button.is_enabled():
                    await time_button.click()
                    await page.wait_for_timeout(700)
                    sections.append(await capture(page, "TIME PICKER OPEN"))
                    await page.keyboard.press("Escape")
            else:
                sections.append("Did not click: native confirmation button is not visible/enabled.")
        else:
            sections.append("Did not click: no native confirmation button found.")

        if errors:
            sections += ["=== BROWSER ERRORS ===", *errors[:50]]
        Path("page-inspection.txt").write_text("\n\n".join(sections), encoding="utf-8")
        await page.screenshot(path="page-screenshot.png", full_page=True)
        await browser.close()
        print("\n\n".join(sections[:8]))
        print("\nSaved page-inspection.txt and page-screenshot.png")

if __name__ == "__main__":
    asyncio.run(main())
