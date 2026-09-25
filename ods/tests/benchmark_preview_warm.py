"""Owned capsule-only paired browser-process cost measurement; no inference."""

import json
import random
import statistics
import time
from playwright.sync_api import sync_playwright
from test_preview_inspection import bundle
from preview_inspection_capsule import run_browser
from preview_inspection_document import InspectionBrowser

data=bundle('<p id="item">Same immutable visible content</p>')
order=["AB"]*3+["BA"]*3
random.Random(9242026).shuffle(order)
pairs=[]
for index,sequence in enumerate(order):
    row={"pair":index+1,"order":sequence}
    for arm in sequence:
        preparation=time.monotonic()
        if arm=="A":
            with sync_playwright() as playwright:
                prepared=time.monotonic()-preparation
                begin=time.monotonic()
                browser=playwright.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
                try:result=run_browser(data,shared_browser=browser)
                finally:browser.close()
                elapsed=time.monotonic()-begin
        else:
            with InspectionBrowser(sync_playwright,calls=2) as warm:
                assert warm.inspect(data)["status"]=="passed"
                assert not warm.browser.contexts
                prepared=time.monotonic()-preparation
                begin=time.monotonic()
                result=warm.inspect(data)
                elapsed=time.monotonic()-begin
                assert not warm.browser.contexts
        assert result["status"]=="passed"
        row[arm]={"seconds":elapsed,"preparationSeconds":prepared,"siteId":result["siteId"],"planSha256":result["planSha256"],"status":result["status"]}
    pairs.append(row)
print(json.dumps({"schemaVersion":1,"scope":"One Chromium at a time under unchanged capsule limits. A times fresh Chromium launch, fresh context inspection and close after driver preparation. B times a fresh context in a process primed by one completed identical inspection; driver/browser/prime preparation is recorded separately and excluded. Excludes container startup, model work and whole task latency.",
    "seed":9242026,"pairs":pairs,
    "baselineMedianSeconds":statistics.median(row["A"]["seconds"] for row in pairs),
    "warmMedianSeconds":statistics.median(row["B"]["seconds"] for row in pairs)}))
