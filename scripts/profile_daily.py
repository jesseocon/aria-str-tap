"""Headed password login, then record the STR daily page's own API calls."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from singer_playwright.browser import BrowserConfig, BrowserRuntime
from singer_playwright.login import apply_credentials
from singer_playwright.workshop.network import NetworkRecorder
from singer_playwright.workshop.runs import create_run_dir, write_json

DAILY_URL = "https://product.costar.com/app/str/detail/4603810/data/day"
GLANCE_URL = "https://product.costar.com/app/str/detail/4603810/star/month/glance"
LOGIN_URL = "https://product.costar.com/"
DATE_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def shot(page, run_dir: Path, name: str) -> None:
    page.screenshot(path=str(run_dir / f"{name}.png"), full_page=False)


def toolbar_dates(page) -> list[dict]:
    return page.evaluate(
        """() => {
          const period = [...document.querySelectorAll('*')].find(
            (el) => el.childElementCount === 0 && (el.textContent || '').trim() === 'Period'
          );
          const yMax = period ? period.getBoundingClientRect().top : 420;
          const hits = [];
          for (const el of document.querySelectorAll('*')) {
            const text = (el.innerText || '').trim();
            if (!/^[A-Z][a-z]{2} \\d{1,2}, \\d{4}$/.test(text)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 40 || r.width > 220 || r.height < 10 || r.height > 48) continue;
            if (r.top < 60 || r.top > yMax) continue;
            hits.push({text, x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, top: r.top});
          }
          hits.sort((a, b) => a.x - b.x);
          return hits;
        }"""
    )


def overlay_snapshot(page) -> dict:
    return page.evaluate(
        """() => {
          const nodes = [...document.querySelectorAll('[role=dialog], [class*=picker], [class*=calendar], [class*=Popover]')]
            .filter((el) => {
              const r = el.getBoundingClientRect();
              return r.width > 80 && r.height > 80;
            })
            .slice(0, 4)
            .map((el) => ({
              cls: String(el.className).slice(0, 180),
              role: el.getAttribute('role'),
              text: (el.innerText || '').trim().slice(0, 500),
            }));
          const buttons = [...document.querySelectorAll('button')]
            .map((el) => {
              const r = el.getBoundingClientRect();
              return {text: (el.innerText || '').trim().slice(0, 40), aria: el.getAttribute('aria-label'), top: r.top, left: r.left, w: r.width};
            })
            .filter((b) => b.w > 0 && b.top > 80 && b.top < 700)
            .slice(0, 40);
          return {nodes, buttons};
        }"""
    )


def click_point(page, x: float, y: float) -> None:
    page.mouse.click(x, y)


def set_start_date(page, run_dir: Path) -> str:
    dates = toolbar_dates(page)
    write_json(run_dir / "toolbar-dates.json", dates)
    if not dates:
        return "no-toolbar-dates"
    click_point(page, dates[0]["x"], dates[0]["y"])
    page.wait_for_timeout(800)
    shot(page, run_dir, "date-open")
    overlay = overlay_snapshot(page)
    write_json(run_dir / "date-overlay.json", overlay)
    # Walk a month picker back to August 2025, then choose day 1.
    for _ in range(20):
        overlay = overlay_snapshot(page)
        text = " ".join(node["text"] for node in overlay["nodes"])
        if re.search(r"August 2025", text):
            break
        prev = page.locator(
            'button[aria-label*="Previous" i], button[aria-label*="prev" i], button[title*="Previous" i]'
        )
        if prev.count() == 0:
            return f"no-prev-month:{text[:120]}"
        prev.first.click()
        page.wait_for_timeout(200)
    day = page.locator('[role=dialog] button, [class*=picker] button').filter(has_text=re.compile(r"^1$"))
    if day.count() == 0:
        return "no-day-1"
    day.first.click()
    page.wait_for_timeout(1500)
    return "clicked-aug-1"


def toggle_last_year(page) -> str:
    thumb = page.locator(".csg-tw-switch-thumb")
    if thumb.count() == 0:
        return "no-switch"
    thumb.first.click()
    page.wait_for_timeout(2500)
    return "clicked-switch"


def profile(recorder: NetworkRecorder, run_dir: Path, label: str) -> list[dict]:
    rows = []
    for call in recorder.calls:
        if not call.get("graphql_operation"):
            continue
        variables = call.get("graphql_variables") or {}
        payload = variables.get("input") if isinstance(variables, dict) else None
        if not isinstance(payload, dict):
            payload = variables if isinstance(variables, dict) else {}
        rows.append(
            {
                "seq": call.get("seq"),
                "status": call.get("status"),
                "operation": call.get("graphql_operation"),
                "path": call.get("path"),
                "beginDate": payload.get("beginDate"),
                "endDate": payload.get("endDate"),
                "interval": payload.get("interval"),
                "ids": payload.get("ids"),
                "idType": payload.get("idType"),
                "dataTypes": payload.get("dataTypes"),
                "requestFlags": payload.get("requestFlags"),
            }
        )
    write_json(run_dir / f"profile-{label}.json", rows)
    return rows


def main() -> None:
    env = load_env(Path(".env"))
    username = env.get("TAP_STR_USERNAME")
    password = env.get("TAP_STR_PASSWORD")
    if not username or not password:
        raise SystemExit("TAP_STR_USERNAME and TAP_STR_PASSWORD must be in .env")

    run_dir, run_id = create_run_dir()
    runtime = BrowserRuntime(BrowserConfig(storage_state_path=None, headless=False))
    runtime.start()
    recorder = NetworkRecorder(
        run_dir / "network",
        resource_types=("document", "xhr", "fetch"),
    )
    recorder.attach(runtime.context)
    page = runtime.page
    try:
        apply_credentials(page, username=username, password=password, login_url=LOGIN_URL, timeout_ms=90_000)
        page.wait_for_timeout(3000)
        shot(page, run_dir, "after-login")
        write_json(run_dir / "after-login.json", {"url": page.url, "title": page.title()})
        recorder.mark("daily")
        page.goto(DAILY_URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(8000)
        shot(page, run_dir, "daily-default")
        write_json(run_dir / "daily-url.json", {"url": page.url, "title": page.title()})
        profile(recorder, run_dir, "daily-default")
        date_result = set_start_date(page, run_dir)
        page.wait_for_timeout(4000)
        shot(page, run_dir, "after-start-date")
        last_year = toggle_last_year(page)
        page.wait_for_timeout(5000)
        shot(page, run_dir, "after-last-year")
        recorder.mark("glance")
        page.goto(GLANCE_URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(8000)
        shot(page, run_dir, "glance")
        rows = profile(recorder, run_dir, "all")
        summary = {
            "run_id": run_id,
            "final_url": page.url,
            "date_result": date_result,
            "last_year": last_year,
            "operations": rows,
        }
        write_json(run_dir / "summary.json", summary)
        print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "date_result": date_result, "last_year": last_year, "url": page.url}))
    finally:
        time.sleep(1)
        runtime.stop()


if __name__ == "__main__":
    main()
