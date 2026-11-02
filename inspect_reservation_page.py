import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

BOOKING_URL = "https://www.tablecheck.com/en/gemm-house-bangkok/reserve/landing?utm_source=gemm_house"
TARGET_DATE_LABEL = "Dec 2"
TARGET_TIMES = ["9:00 AM", "9:30 AM", "10:00 AM", "10:30 AM", "11:00 AM"]

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
          text: clean(el.innerText || ''),
          ariaLabel: el.getAttribute('aria-label') || '',
          title: el.getAttribute('title') || '',
          testId: el.getAttribute('data-testid') || '',
          value: el.value || '',
          disabled: !!el.disabled,
          outerHTML: el.tagName === 'BUTTON' ? el.outerHTML.slice(0, 700) : ''
        })).slice(0, 300);
      return {
        title: document.title,
        url: location.href,
        bodyText: clean(document.body?.innerText || '').slice(0, 18000),
        controls: nodes
      };
    }""")
    lines = [
        f"=== {label} ===",
        f"Final URL: {details['url']}",
        f"Page title: {details['title']}",
        "",
        "VISIBLE PAGE TEXT:",
        details["bodyText"],
        "",
        "VISIBLE CONTROLS (first 300):",
    ]
    lines.extend(str(item) for item in details["controls"])
    return "\n".join(lines)

async def click_first_enabled(locator, label, sections):
    count = await locator.count()
    if count == 1 and await locator.is_visible() and await locator.is_enabled():
        await locator.click(timeout=8000)
        await sections.append(f"Clicked {label}.")
        return True
    await sections.append(f"Did not click {label}: matching visible/enabled control count={count}.")
    return False

async def main():
    sections = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.set_default_timeout(8000)
        errors = []
        page.on("console", lambda msg: errors.append(f"console[{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
        response = await page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)
        sections.append(f"Initial HTTP status: {response.status if response else 'no response'}")
        sections.append(await capture(page, "BEFORE TERMS CONFIRMATION"))

        terms = page.locator("button").filter(has_text="Confirm and continue")
        if await terms.count() == 1 and await terms.first.is_visible() and await terms.first.is_enabled():
            await terms.first.click()
            await page.wait_for_timeout(7000)
            sections.append(await capture(page, "AFTER TERMS CONFIRMATION"))
        else:
            sections.append(f"Terms confirmation not clicked; matching button count={await terms.count()}.")
            raise RuntimeError("Could not safely pass the terms screen.")

        # Party size: use the visible plus/increment control twice from the
        # observed default of two guests. If the control is not identifiable,
        # save its markup rather than guessing which unlabeled button is +.
        party_size_set = False
        guests = page.get_by_role("button", name="2 Guests", exact=True)
        if await guests.count() == 1:
            await guests.click()
            await page.wait_for_timeout(500)
            sections.append(await capture(page, "GUEST PICKER OPEN"))
            plus = page.locator(
                'button[aria-label*="increase" i], button[aria-label*="increment" i], '
                'button[title*="increase" i], button[title*="increment" i], '
                'button[data-testid*="increment" i], button[data-testid*="increase" i]'
            )
            if await plus.count() == 1 and await plus.is_visible() and await plus.is_enabled():
                await plus.click()
                await page.wait_for_timeout(250)
                await plus.click()
                await page.wait_for_timeout(300)
                selected_value = await page.locator("select").first.input_value() if await page.locator("select").count() else ""
                party_size_set = selected_value == "4"
                sections.append(f"Clicked the identified + control twice; guest selector value is {selected_value!r}.")
                sections.append(await capture(page, "GUEST COUNT AFTER PLUS x2"))
            else:
                sections.append(f"Could not safely identify a labelled + control; count={await plus.count()}. No unlabeled button was guessed.")
            if party_size_set:
                confirm_guest = page.get_by_role("button", name="Confirm", exact=True)
                if await confirm_guest.count() == 1 and await confirm_guest.is_enabled():
                    await confirm_guest.click()
                    await page.wait_for_timeout(1000)
                    sections.append(await capture(page, "GUEST PICKER CONFIRMED"))
                else:
                    party_size_set = False
                    sections.append("Guest picker Confirm button was not uniquely found/enabled.")
            else:
                sections.append("Safety stop: did not confirm the guest picker because 4 guests was not verified.")
                await page.keyboard.press("Escape")
        else:
            sections.append(f"Could not uniquely find the 2 Guests opener; count={await guests.count()}.")
        if not party_size_set:
            sections.append("Safety stop: date/time availability checks skipped because party size 4 was not confirmed.")
            Path("page-inspection.txt").write_text("\\n\\n".join(sections), encoding="utf-8")
            await page.screenshot(path="page-screenshot.png", full_page=True)
            await browser.close()
            print("\\n\\n".join(sections[:10]))
            print("\\nSaved page-inspection.txt and page-screenshot.png")
            return

        # Open calendar and capture the actual calendar controls. Dec 2 may not
        # be within TableCheck's 30-day booking window until Nov 2.
        date_button = page.get_by_test_id("Landing Date Panel Opener Button")
        if await date_button.count() == 1:
            await date_button.click()
            await page.wait_for_timeout(700)
            sections.append(await capture(page, "DATE PICKER OPEN"))
            # Select only a clearly-labelled Dec 2 option; do not guess at a
            # bare day-number button that could belong to another month.
            date_candidates = page.get_by_role("button", name=re.compile(r"(Wed,?\s*)?Dec\s*2", re.I))
            if await date_candidates.count() == 1 and await date_candidates.is_enabled():
                await date_candidates.click()
                await page.wait_for_timeout(700)
                sections.append(await capture(page, "DECEMBER 2 SELECTED"))
            else:
                sections.append(f"Dec 2 not uniquely selectable by full date label; count={await date_candidates.count()}. Calendar left unchanged.")
                await page.keyboard.press("Escape")
        else:
            sections.append(f"Date opener not found uniquely; count={await date_button.count()}.")

        # Select each requested morning slot in turn and press Find availability.
        # This is a search only; the script never selects a result or books.
        time_button = page.get_by_test_id("Landing Time Panel Opener Button")
        if await time_button.count() == 1:
            for time_label in TARGET_TIMES:
                # Re-open the time panel for each independent time search.
                if not await time_button.is_visible():
                    break
                await time_button.click()
                await page.wait_for_timeout(400)
                sections.append(await capture(page, f"TIME PICKER OPEN FOR {time_label}"))
                slot = page.get_by_role("button", name=re.compile(rf"^{re.escape(time_label)}$", re.I))
                if await slot.count() != 1 or not await slot.is_enabled():
                    sections.append(f"Time {time_label} not uniquely available as a selectable button; count={await slot.count()}.")
                    await page.keyboard.press("Escape")
                    continue
                await slot.click()
                await page.wait_for_timeout(300)
                sections.append(await capture(page, f"TIME {time_label} SELECTED"))
                find_button = page.get_by_role("button", name=re.compile(r"^Find availability$", re.I))
                if await find_button.count() == 1 and await find_button.is_enabled():
                    await find_button.click()
                    await page.wait_for_timeout(3500)
                    sections.append(await capture(page, f"AVAILABILITY RESULT FOR {time_label}"))
                else:
                    sections.append(f"Find availability button not uniquely found/enabled for {time_label}; count={await find_button.count()}.")
                # Return to the landing form for the next slot, without booking.
                if await page.get_by_test_id("Landing Time Panel Opener Button").count() != 1:
                    break
                time_button = page.get_by_test_id("Landing Time Panel Opener Button")
        else:
            sections.append(f"Time opener not found uniquely; count={await time_button.count()}.")

        if errors:
            sections += ["=== BROWSER ERRORS ===", *errors[:50]]
        Path("page-inspection.txt").write_text("\n\n".join(sections), encoding="utf-8")
        await page.screenshot(path="page-screenshot.png", full_page=True)
        await browser.close()
        print("\n\n".join(sections[:10]))
        print("\nSaved page-inspection.txt and page-screenshot.png")

if __name__ == "__main__":
    asyncio.run(main())
