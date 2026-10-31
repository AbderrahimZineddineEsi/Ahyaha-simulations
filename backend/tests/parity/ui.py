"""
The dashboard and /dev in headless Chromium: scenario A with clicks, scenario 3 with its pop-ups, a second incident from a
`curl` blackbox, the older tool pages, and a check that no page asks for anything outside the server (the demo is offline).
Needs `pip install playwright && playwright install chromium`.
"""
import os
import subprocess
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SOLVEX_URL", "http://localhost:8000")
errors = []


def crossed_a_reset(url, status):
    """A refresh on its way while Reset all deletes the incident gets a 404. The page handles it."""
    return status == 404 and "/api/v1/incidents/" in url


def watch(page, name):
    page.on("console", lambda m: errors.append((name, m.text)) if m.type == "error" and not crossed_a_reset(m.location["url"], 404) else None)
    page.on("pageerror", lambda e: errors.append((name, str(e))))
    page.on("response", lambda r: errors.append((name, f"{r.status} {r.url}")) if r.status >= 400 and not crossed_a_reset(r.url, r.status) else None)   # says which URL a console error is about


def check(label, ok):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        raise SystemExit(1)


def open_pair(ctx):
    dash, dev = ctx.new_page(), ctx.new_page()
    watch(dash, "dashboard"), watch(dev, "dev")
    dash.goto(BASE + "/")
    dash.wait_for_function("!document.getElementById('login').hidden || document.getElementById('link').dataset.up === 'true'")
    if dash.is_visible("#login"):                          # a context that is already logged in skips the form
        dash.click("#login-form button")                   # the demo prefills the agent's login
    dash.wait_for_function("document.getElementById('link').dataset.up === 'true'")
    dev.goto(BASE + "/dev")
    dev.wait_for_selector(".phone")
    dev.click("#reset-all")
    time.sleep(1)
    return dash, dev


def phone(dev, number):
    return dev.locator(".phone").nth(number)


def scenario_b(ctx):
    dash, dev = open_pair(ctx)
    dev.check("#auto")
    dev.click("[data-start='b']")
    dash.wait_for_selector("text=High confidence", timeout=40000)
    check("extreme severity on the panel", dash.inner_text(".level") .lower() == "extreme")
    dash.wait_for_selector("text=confirmed by witness", timeout=40000)
    dash.click("#confirm")
    dash.wait_for_selector("text=You confirmed the crash")
    dash.click("[data-dispatch='amb-1']")
    dash.wait_for_selector("[data-eta]")
    dash.click("[data-help='5']")
    dash.wait_for_selector("[data-choose]", timeout=20000)
    dash.click("[data-choose]")
    dash.wait_for_selector("text=Amel Kaci is on the scene", timeout=40000)
    check("the helper is there and the ambulance is still coming", "On the way" in dash.inner_text("#fleet"))
    dev.uncheck("#auto")


def scenario_2(ctx):
    dash, dev = open_pair(ctx)
    dev.click("[data-start='2']")
    dash.wait_for_selector("text=nobody nearby", timeout=40000)
    check("the zoom shows the remote road", dash.evaluate("region") == "remote")
    check("no helper in 5 km", "No helpers within 5 km" in dash.inner_text("#panel"))
    phone(dev, 0).locator("button", has_text="Silent SOS").click()
    dash.wait_for_selector("text=silent SOS")
    check("the check-in is over on the phone", phone(dev, 0).locator("button", has_text="I'm OK").count() == 0)


def turned_back(ctx):
    dash, dev = open_pair(ctx)
    dev.click("[data-start='3']")
    dash.wait_for_selector("#popup h3", timeout=40000)
    dash.click("#popup [data-warn='dont_go']")
    time.sleep(1.5)
    phone(dev, 0).locator("button", has_text="Turn back").click()
    dash.wait_for_selector(".incident-head:has-text('Turned back')", timeout=10000)
    dash.wait_for_function("document.getElementById('incidents').innerText.includes('Turned back')", timeout=10000)   # two refreshes can cross: it settles
    check("the agent still sees how it ended", "Turned back" in dash.inner_text("#incidents"))


def false_alarm(ctx):
    dash, dev = open_pair(ctx)
    curl = subprocess.Popen(["curl", "-sN", f"{BASE}/blackbox/brake-01/braking"], stdout=subprocess.DEVNULL)
    dash.wait_for_selector(".incident-head:has-text('Analyzing')", timeout=30000)
    dash.wait_for_selector(".incident-head:has-text('No crash')", timeout=30000)
    check("a false alarm stays readable", "No crash" in dash.inner_text("#incidents"))
    curl.kill()


def false_alarm_on_dev(ctx):
    """The quick no-crash scenario: the trigger fires, the model says no, and nobody's phone is disturbed."""
    dash, dev = open_pair(ctx)
    dev.click("[data-start='n']")
    dev.wait_for_selector("#steps li[data-s='end']", timeout=30000)
    check("nobody was asked anything", all(dev.locator(".phone").nth(i).locator("button").count() == 0 for i in range(6)))


def panel_is_stable(ctx):
    """The panel is rebuilt when something changes, not each time an ambulance moves: a click must not land on a dead button."""
    dash, dev = open_pair(ctx)
    dev.click("[data-start='a']")
    dash.wait_for_selector("text=High confidence", timeout=40000)
    dash.click("[data-dispatch='amb-1']")
    dash.wait_for_selector("[data-eta]")
    dash.evaluate("window.keep = document.querySelector('[data-help]')")
    time.sleep(6)
    check("the buttons survive the ambulance moving", dash.evaluate("document.body.contains(window.keep)"))
    before = dash.inner_text("[data-eta]")
    time.sleep(6)
    check("while the ETA keeps counting", dash.inner_text("[data-eta]") != before)


def scenario_a(ctx):
    dash, dev = open_pair(ctx)
    check("six phones", dev.locator(".phone").count() == 6)
    dev.click("[data-start='a']")
    dash.wait_for_selector("text=High confidence", timeout=40000)
    dev.locator(".phone").nth(0).locator("h4", has_text="Are you OK?").wait_for(timeout=8000)    # the phones follow the dashboard by a moment
    check("the victim's phone asks 'Are you OK?'", "Are you OK?" in dev.locator(".phone").nth(0).inner_text())
    dev.locator(".phone").nth(1).locator("button", has_text="Confirm").click()
    dash.wait_for_selector("text=confirmed by witness")
    check("the other people nearby are no longer asked", all(dev.locator(".phone").nth(i).locator("button", has_text="Confirm").count() == 0 for i in (2, 3, 4, 5)))
    dash.click("#confirm")
    dash.wait_for_selector("text=You confirmed the crash")
    dash.click("[data-dispatch='amb-1']")
    dash.wait_for_selector("[data-eta]")
    dash.wait_for_selector("#fleet .tag:has-text('On scene')", timeout=60000)      # the line over the map fades, this stays
    check("the drivers on the road were told to clear the yellow line", "Clear the yellow line" in dev.locator(".phone").nth(2).inner_text())


def scenario_3(ctx):
    dash, dev = open_pair(ctx)
    dev.click("[data-start='3']")
    dash.wait_for_selector("#popup h3", timeout=40000)
    check("the agent is asked about the driver", "no-coverage zone" in dash.inner_text("#popup h3"))
    dash.click("#popup [data-warn='dont_go']")
    time.sleep(1.5)
    dev.locator(".phone").nth(0).locator("button", has_text="Continue anyway").click()
    dash.click("#popup [data-ack]")
    # This state lasts 7 s now, and frame-based polling can sleep through it in a background tab
    dash.wait_for_function("document.getElementById('panel').textContent.includes('Out of reach until')", polling=200, timeout=60000)
    dash.wait_for_selector("#popup h3:has-text('not back')", timeout=60000)
    dash.click("#popup [data-ack]")
    dash.click("[data-help='20']")
    time.sleep(1)
    dash.click("[data-dispatch='amb-1']")
    dash.wait_for_selector("text=Found: it was a crash", timeout=90000)
    # a second, unrelated incident from the one-command blackbox
    curl = subprocess.Popen(["curl", "-sN", f"{BASE}/blackbox/friend-01/crash"], stdout=subprocess.DEVNULL)
    dash.wait_for_function("document.querySelectorAll('#incidents button').length >= 2", timeout=40000)
    check("two incidents at once", dash.locator("#incidents button").count() >= 2)
    curl.kill()


def tools(ctx):
    for path in ("/dev/monitor", "/dev/live", "/dev/simulator"):
        page = ctx.new_page()
        watch(page, path)
        page.goto(BASE + path)
        time.sleep(1.5)


with sync_playwright() as p:
    browser = p.chromium.launch()
    outside = []
    ctx = browser.new_context(viewport={"width": 1500, "height": 900})
    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(BASE) else (outside.append(r.request.url), r.abort()))
    for step in (scenario_a, scenario_b, scenario_2, scenario_3, turned_back, false_alarm, false_alarm_on_dev, panel_is_stable, tools):
        print("==", step.__name__)
        step(ctx)
    browser.close()
if errors or outside:
    print(errors, outside)
check("no request left the server (offline works)", not outside)
check("no errors in any page", not errors)
