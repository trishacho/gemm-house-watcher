import asyncio
import os
import random
import re
import smtplib
import time
from email.message import EmailMessage

from playwright.async_api import async_playwright

BOOKING_URL = "https://www.tablecheck.com/en/gemm-house-bangkok/reserve/landing?utm_source=gemm_house"
TARGET_DATE = "2026-12-02"
TARGET_TIME = "10:30 AM"
INTERVALS_MINUTES = [3, 5, 7, 9, 10]
RUN_SECONDS = 5 * 60 * 60 + 45 * 60
NO_CHOSEN_TIME = "There is no availability for your chosen time"
NO_ALTERNATIVE_DATES = "No other availability found between"
ALTERNATIVES_HEADING = "Other dates with availability"


def send_alert(result_text):
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["ALERT_EMAIL"]
    message = EmailMessage()
    message["Subject"] = "Possible GEMM HOUSE reservation opening"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "TableCheck may have an opening for GEMM HOUSE!\n\n"
        "Party size: 4\n"
        "Preferred date/time: December 2, 2026 at 10:30 AM Bangkok time\n"
        "The result no longer shows both no-availability messages. "
        "Your preferred slot or an alternative date may have opened.\n\n"
        "Check the exact date and time here:\n" + BOOKING_URL + "\n\n"
        "No reservation was made. This watcher never confirms or pays.\n\n"
        "TableCheck result text:\n" + result_text[:5000]
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


async def prepare_search(page):
    response = await page.goto(BOOKING_URL, wait_until="domcontentloaded", timeout=60000)
    if response is None or response.status >= 400:
        raise RuntimeError("Booking page did not load successfully.")

    await page.wait_for_timeout(5000)
    terms = page.locator("button").filter(has_text="Confirm and continue")
    if await terms.count() == 1 and await terms.first.is_visible():
        await terms.first.click()
        await page.wait_for_timeout(4000)
    elif await page.get_by_role("button", name="2 Guests", exact=True).count() != 1:
        raise RuntimeError("Terms/landing page did not match the expected TableCheck flow.")

    guests = page.get_by_role("button", name="2 Guests", exact=True)
    if await guests.count() != 1:
        raise RuntimeError("Could not identify the default 2 Guests control.")
    await guests.click()
    await page.wait_for_timeout(400)
    plus = page.get_by_test_id("Counter Add Button")
    if await plus.count() != 1 or not await plus.is_enabled():
        raise RuntimeError("Could not identify the guest-count + control.")
    await plus.click()
    await page.wait_for_timeout(200)
    await plus.click()
    await page.wait_for_timeout(300)
    select = page.locator("select")
    if await select.count() < 1 or await select.first.input_value() != "4":
        raise RuntimeError("Guest count did not verify as four.")
    confirm = page.get_by_role("button", name="Confirm", exact=True)
    if await confirm.count() != 1 or not await confirm.is_enabled():
        raise RuntimeError("Guest picker Confirm button was not uniquely available.")
    await confirm.click()
    await page.wait_for_timeout(800)

    date_opener = page.get_by_test_id("Landing Date Panel Opener Button")
    if await date_opener.count() != 1:
        raise RuntimeError("Date picker opener was not found.")
    await date_opener.click()
    await page.wait_for_timeout(500)
    day = page.locator('button[data-testid="day"][data-date="2026-12-02"]')
    if await day.count() != 1:
        raise RuntimeError("December 2 was not present in the calendar.")
    if await day.get_attribute("data-state") == "outside":
        next_month = page.get_by_test_id("Calendar Next Month Button")
        if await next_month.count() != 1 or not await next_month.is_enabled():
            raise RuntimeError("Could not advance calendar to December.")
        await next_month.click()
        await page.wait_for_timeout(500)
    day = page.locator('button[data-testid="day"][data-date="2026-12-02"]')
    if await day.count() != 1 or await day.get_attribute("data-state") == "outside" or not await day.is_enabled():
        raise RuntimeError("December 2 was not selectable in the December calendar.")
    await day.click()
    await page.wait_for_timeout(700)
    date_text = await page.get_by_test_id("Landing Date Panel Opener Button").inner_text()
    if "Dec 2" not in date_text:
        raise RuntimeError("Selected date did not verify as Dec 2.")

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(250)
    panel = page.get_by_test_id("Bottom Panel")
    if await panel.count() and await panel.first.is_visible():
        await page.mouse.click(700, 140)
        await page.wait_for_timeout(300)
    if await panel.count() and await panel.first.is_visible():
        raise RuntimeError("Date picker overlay remained open.")

    time_opener = page.get_by_test_id("Landing Time Panel Opener Button")
    if await time_opener.count() != 1:
        raise RuntimeError("Time picker opener was not found.")
    await time_opener.click()
    await page.wait_for_timeout(500)
    await page.wait_for_function(
        """() => {
          const panel = document.querySelector('[data-testid="Bottom Panel"]');
          return !!panel && panel.querySelectorAll('.react-loading-skeleton').length === 0;
        }""",
        timeout=60000,
    )
    await page.wait_for_timeout(700)
    slot = page.get_by_role("button", name=re.compile(r"^10:30 AM$", re.I))
    if await slot.count() != 1 or not await slot.is_enabled():
        raise RuntimeError("10:30 AM was not selectable in the time picker.")
    await slot.click()
    await page.wait_for_timeout(300)
    find = page.get_by_role("button", name=re.compile(r"^Find availability$", re.I))
    if await find.count() != 1 or not await find.is_enabled():
        raise RuntimeError("Find availability button was not uniquely enabled.")
    await find.click()
    await page.wait_for_url("**/reserve/availability**", timeout=60000)


async def read_completed_result(page):
    deadline = time.monotonic() + 75
    previous = None
    stable_since = None
    while time.monotonic() < deadline:
        text = re.sub(r"\s+", " ", await page.locator("body").inner_text()).strip()
        loading = await page.evaluate(
            """() => {
              const skeletons = document.querySelectorAll('.react-loading-skeleton');
              const loadingCells = [...document.querySelectorAll('[data-testid="Time Grid Cell"]')]
                .some(cell => cell.getAttribute('data-loading') === 'true');
              return skeletons.length > 0 || loadingCells;
            }"""
        )
        meaningful = (
            NO_CHOSEN_TIME in text
            or NO_ALTERNATIVE_DATES in text
            or ALTERNATIVES_HEADING in text
        )
        if text == previous and meaningful and not loading:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 4:
                if "Dec 2" not in text or "4 Guests" not in text:
                    raise RuntimeError("Result did not confirm the requested date and party size.")
                return text
        else:
            stable_since = None
        previous = text
        await page.wait_for_timeout(1000)
    raise TimeoutError("TableCheck result did not reach a stable, recognizable state.")


async def check_once():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await prepare_search(page)
            result_text = await read_completed_result(page)
            if errors:
                raise RuntimeError("Browser page error: " + "; ".join(errors[:3]))
            unavailable_for_selected = NO_CHOSEN_TIME in result_text
            no_alternative_dates = NO_ALTERNATIVE_DATES in result_text
            potential_opening = not (unavailable_for_selected and no_alternative_dates)
            return potential_opening, result_text
        finally:
            await browser.close()


async def main():
    alerted = os.environ.get("ALERTED", "false").lower() == "true"
    started = time.monotonic()
    cycle = 0
    while time.monotonic() - started < RUN_SECONDS:
        cycle += 1
        check_started = time.monotonic()
        print(f"Check {cycle}: starting TableCheck search.", flush=True)
        try:
            potential, result = await check_once()
            print("Completed TableCheck result:\n" + result[:5000], flush=True)
            if potential and not alerted:
                send_alert(result)
                alerted = True
                print("Potential opening detected; email sent.", flush=True)
            elif potential:
                print("Potential opening remains; duplicate email suppressed.", flush=True)
            else:
                alerted = False
                print("No availability for selected slot or alternative dates.", flush=True)
        except Exception as exc:
            print(f"Check failed safely; no email sent: {type(exc).__name__}: {exc}", flush=True)

        interval = random.choice(INTERVALS_MINUTES)
        elapsed = time.monotonic() - check_started
        sleep_for = max(0, interval * 60 - elapsed)
        print(f"Next check interval: {interval} minutes.", flush=True)
        if time.monotonic() - started + sleep_for >= RUN_SECONDS:
            break
        await asyncio.sleep(sleep_for)

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"alerted={'true' if alerted else 'false'}\n")
    print(f"Watcher segment complete. Alert state: {alerted}.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
