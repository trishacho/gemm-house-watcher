import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

BOOKING_URL = "https://www.tablecheck.com/en/gemm-house-bangkok/reserve/landing?utm_source=gemm_house"
TARGET_DATE_LABEL = "Dec 2"
TARGET_TIMES = ["10:30 AM"]

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
        controls: nodes,
        bottomPanelHTML: (() => {
          const el = document.querySelector('[data-testid="Bottom Panel"]');
          return el ? el.outerHTML.slice(0, 14000) : '<Bottom Panel not present>';
        })(),
        panelCandidates: (() => {
          const panel = document.querySelector('[data-testid="Bottom Panel"]');
          if (!panel) return [];
          return [...panel.querySelectorAll('*')].filter(visible).slice(0, 160).map(el => ({
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            text: clean(el.innerText || el.textContent || '').slice(0, 180),
            testId: el.getAttribute('data-testid') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            dataTime: el.getAttribute('data-time') || '',
            dataState: el.getAttribute('data-state') || '',
            disabled: !!el.disabled,
            className: String(el.className || '').slice(0, 180)
          }));
        })()
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
        "BOTTOM PANEL HTML (first 14000 chars):",
        details["bottomPanelHTML"],
        "",
        "BOTTOM PANEL VISIBLE DESCENDANTS:",
    ]
    lines.extend(str(item) for item in details["panelCandidates"])
    lines += [
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



async def dismiss_bottom_panel(page, sections, label):
    # Dismiss TableCheck's fixed calendar/time bottom sheet before using the form.
    for _ in range(2):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        panel = page.get_by_test_id("Bottom Panel")
        if await panel.count() == 0 or not await panel.first.is_visible():
            sections.append(f"Closed bottom panel after {label} with Escape.")
            return True
    await page.mouse.click(700, 140)
    await page.wait_for_timeout(400)
    panel = page.get_by_test_id("Bottom Panel")
    closed = await panel.count() == 0 or not await panel.first.is_visible()
    status = "closed" if closed else "still visible"
    sections.append(f"Bottom panel dismissal after {label}: {status}.")
    return closed

async def main():
    sections = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.set_default_timeout(8000)
        errors = []
        responses = []
        page.on("response", lambda response: responses.append(f"HTTP {response.status} {response.url}") if response.status >= 400 else None)
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
            # TableCheck exposes the plus control with this exact test id.
            plus = page.get_by_test_id("Counter Add Button")
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
            # Dec 2 initially appears as an outside-month cell in the
            # November grid. Clicking that cell can merely change the visible
            # month, so first advance exactly one month and verify the exact
            # date is now an in-month cell before selecting it.
            dec2_button = page.locator('button[data-testid="day"][data-date="2026-12-02"]')
            if await dec2_button.count() == 1:
                state = await dec2_button.get_attribute("data-state")
                if state == "outside":
                    next_month = page.get_by_test_id("Calendar Next Month Button")
                    if await next_month.count() == 1 and await next_month.is_enabled():
                        await next_month.click()
                        await page.wait_for_timeout(700)
                        sections.append("Advanced calendar exactly one month; checking Dec 2 in the December grid.")
                        sections.append(await capture(page, "DECEMBER CALENDAR OPEN"))
                    else:
                        sections.append("Could not uniquely identify the enabled next-month button.")
                dec2_button = page.locator('button[data-testid="day"][data-date="2026-12-02"]')
                state = await dec2_button.get_attribute("data-state") if await dec2_button.count() == 1 else None
                if await dec2_button.count() == 1 and state != "outside" and await dec2_button.is_enabled():
                    await dec2_button.click()
                    await page.wait_for_timeout(1200)
                    # Selecting a date closes/unmounts the calendar, so do
                    # not query the old day locator afterward (it will time out).
                    date_opener = page.get_by_test_id("Landing Date Panel Opener Button")
                    date_label = await date_opener.inner_text() if await date_opener.count() == 1 else "<date opener missing>"
                    sections.append(f"Clicked Dec 2 in the December grid; date opener now reads {date_label!r}.")
                    sections.append(await capture(page, "DECEMBER 2 SELECTION CHECK"))
                else:
                    sections.append(f"Dec 2 was not selectable as an in-month date; count={await dec2_button.count()}, state={state!r}.")
                    sections.append(await capture(page, "DECEMBER 2 CELL NOT SELECTED"))
            else:
                sections.append(f"Dec 2 exact-date cell not uniquely found; count={await dec2_button.count()}.")
                sections.append(await capture(page, "DECEMBER 2 CELL NOT FOUND"))
            await dismiss_bottom_panel(page, sections, "date selection")
        else:
            sections.append(f"Date opener not found uniquely; count={await date_button.count()}.")

        # Ensure the calendar sheet is gone before trying to open the time sheet.
        panel_closed = await dismiss_bottom_panel(page, sections, "calendar selection")
        if not panel_closed:
            sections.append("Safety stop: time picker not opened because the calendar bottom panel remained on screen.")
        # Select each requested morning slot in turn and press Find availability.
        # This is a search only; the script never selects a result or books.
        time_button = page.get_by_test_id("Landing Time Panel Opener Button")
        if panel_closed and await time_button.count() == 1:
            for time_label in TARGET_TIMES:
                # Re-open the time panel for each independent time search.
                if not await time_button.is_visible():
                    break
                try:
                    await time_button.click(timeout=8000)
                except Exception as exc:
                    sections.append(f"Could not open time picker for {time_label}: {type(exc).__name__}: {exc}")
                    await dismiss_bottom_panel(page, sections, f"time opener for {time_label}")
                    break
                await page.wait_for_timeout(500)
                sections.append(f"Opened the time picker for {time_label}; waiting up to 60 seconds for its loading skeletons to disappear.")
                try:
                    await page.wait_for_function("""() => {
                      const panel = document.querySelector('[data-testid="Bottom Panel"]');
                      if (!panel) return false;
                      return panel.querySelectorAll('.react-loading-skeleton').length === 0;
                    }""", timeout=60000)
                    sections.append("Time picker loading skeletons have disappeared.")
                except Exception as exc:
                    sections.append(f"Time picker still appears to be loading after 60 seconds: {type(exc).__name__}: {exc}")
                await page.wait_for_timeout(700)
                sections.append(await capture(page, f"TIME PICKER OPEN FOR {time_label}"))
                slot = page.get_by_role("button", name=re.compile(rf"^{re.escape(time_label)}$", re.I))
                if await slot.count() != 1 or not await slot.is_enabled():
                    sections.append(f"Time {time_label} not uniquely available as a selectable button; count={await slot.count()}.")
                    await dismiss_bottom_panel(page, sections, f"missing time {time_label}")
                    continue
                await slot.click()
                await page.wait_for_timeout(300)
                sections.append(await capture(page, f"TIME {time_label} SELECTED"))
                find_button = page.get_by_role("button", name=re.compile(r"^(Find|Check) availability$", re.I))
                if await find_button.count() == 1 and await find_button.is_enabled():
                    await find_button.click()
                    sections.append(f"Submitted availability search for {time_label}; waiting up to 60 seconds for Find availability to stop loading.")
                    try:
                        # The landing-page button disappears during navigation,
                        # so its absence is NOT proof that results have loaded.
                        # Wait for the availability route and for every result
                        # cell to leave its loading state.
                        await page.wait_for_url("**/reserve/availability**", timeout=60000)
                        # The result view may not render Time Grid Cell controls
                        # at all when the requested time is unavailable. Treat
                        # the visible no-availability copy as a completed result.
                        await page.wait_for_function("""() => {
                          const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                          return text.includes('There is no availability for your chosen time')
                            || document.querySelectorAll('[data-testid="Time Grid Cell"]').length > 0
                            && [...document.querySelectorAll('[data-testid="Time Grid Cell"]')].every(cell => cell.getAttribute('data-loading') !== 'true');
                        }""", timeout=60000)
                        sections.append("Availability result reached a completed state (no-availability message or settled time grid).")
                    except Exception as exc:
                        sections.append(f"Availability results did not finish loading within 60 seconds: {type(exc).__name__}: {exc}")
                    await page.wait_for_timeout(1200)
                    sections.append(await capture(page, f"AVAILABILITY RESULT FOR {time_label}"))
                else:
                    sections.append(f"Find availability button not uniquely found/enabled for {time_label}; count={await find_button.count()}.")
                # Return to the landing form for the next slot, without booking.
                if await page.get_by_test_id("Landing Time Panel Opener Button").count() != 1:
                    break
                time_button = page.get_by_test_id("Landing Time Panel Opener Button")
        else:
            sections.append(f"Time opener not found uniquely; count={await time_button.count()}.")

        if responses:
            sections += ["=== HTTP RESPONSES >= 400 ===", *responses[:50]]
        if errors:
            sections += ["=== BROWSER ERRORS ===", *errors[:50]]
        Path("page-inspection.txt").write_text("\n\n".join(sections), encoding="utf-8")
        await page.screenshot(path="page-screenshot.png", full_page=True)
        await browser.close()
        print("\n\n".join(sections[:10]))
        print("\nSaved page-inspection.txt and page-screenshot.png")

if __name__ == "__main__":
    asyncio.run(main())
