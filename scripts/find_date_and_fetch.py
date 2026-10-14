"""Find the daily date control, then fetch PortfolioListingAll for a chosen range."""

from __future__ import annotations

import json
from pathlib import Path

from singer_playwright.browser import BrowserConfig, BrowserRuntime
from singer_playwright.login import apply_credentials
from singer_playwright.workshop.runs import create_run_dir, write_json

DAILY_URL = "https://product.costar.com/app/str/detail/4603810/data/day"
GRAPHQL = "https://product.costar.com/app/str-mfe/api/graphql"
BEGIN = "2025-08-01T00:00:00.000Z"
END = "2026-10-09T00:00:00.000Z"

FIND_JS = """
() => {
  const hits = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('input, textarea, [role="textbox"]')) {
      const r = el.getBoundingClientRect();
      hits.push({
        kind: 'input',
        tag: el.tagName,
        type: el.getAttribute('type'),
        value: el.value || '',
        aria: el.getAttribute('aria-label'),
        placeholder: el.getAttribute('placeholder'),
        name: el.getAttribute('name'),
        cls: String(el.className).slice(0, 140),
        top: Math.round(r.top),
        left: Math.round(r.left),
        w: Math.round(r.width),
        h: Math.round(r.height),
      });
    }
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return hits.filter((hit) => hit.w > 0 && hit.h > 0 && hit.top > 0 && hit.top < 500);
}
"""

def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def main() -> None:
    env = load_env(Path(".env"))
    run_dir, run_id = create_run_dir()
    runtime = BrowserRuntime(BrowserConfig(storage_state_path=None, headless=False))
    runtime.start()
    page = runtime.page
    templates: list[dict] = []

    def on_finished(request) -> None:
        if "/app/str-mfe/api/graphql" not in request.url or not request.post_data:
            return
        payload = json.loads(request.post_data)
        if payload.get("operationName") != "PortfolioListingAll":
            return
        if len(templates) < 3:
            templates.append(payload)

    page.context.on("requestfinished", on_finished)
    try:
        apply_credentials(
            page,
            username=env["TAP_STR_USERNAME"],
            password=env["TAP_STR_PASSWORD"],
            login_url="https://product.costar.com/",
            timeout_ms=90_000,
        )
        page.wait_for_timeout(3000)
        for _ in range(3):
            try:
                page.goto(DAILY_URL, wait_until="domcontentloaded", timeout=90_000)
                break
            except Exception as exc:
                if "ERR_ABORTED" not in str(exc):
                    raise
                page.wait_for_timeout(2000)
        page.wait_for_timeout(8000)
        page.set_default_timeout(120_000)
        fields = page.evaluate(FIND_JS)
        write_json(run_dir / "date-fields.json", fields)
        bodies = []
        for template in templates:
            body = json.loads(json.dumps(template))
            body["variables"]["input"]["beginDate"] = BEGIN
            body["variables"]["input"]["endDate"] = END
            bodies.append(body)
        full = page.evaluate(
            """
            async ({url, bodies}) => {
              const out = [];
              for (const body of bodies) {
                const resp = await fetch(url, {
                  method: 'POST',
                  credentials: 'include',
                  headers: {'content-type': 'application/json', 'accept': 'application/json'},
                  body: JSON.stringify(body),
                });
                const json = await resp.json();
                const series = (((json || {}).data || {}).PortfolioListingAll || {}).data || {};
                const first = Object.values(series)[0] || {};
                const keys = Object.keys(first);
                out.push({status: resp.status, metrics: Object.keys(series).length, dates: keys.length, first: keys[0] || null, last: keys[keys.length - 1] || null, error: json.error || null});
              }
              return out;
            }
            """,
            {"url": GRAPHQL, "bodies": bodies},
        )
        write_json(run_dir / "fetch-summary.json", {"run_id": run_id, "fields": fields, "templates": len(templates), "full": full})
        print(json.dumps({"run_id": run_id, "fields": fields, "templates": len(templates), "full": full}))
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
