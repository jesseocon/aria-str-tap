# aria-str-tap

Meltano project for the **CoStar STR** report (`tap-str`).

Depends on [aria-singer-playwright](https://github.com/jesseocon/aria-singer-playwright.git) only—not on aria-lighthouse.

## Data

Every connection syncs the STAR-tab streams (`str_series`, `comp_set_members`, `comp_set_response`). A community login and a full CoStar login see the same app, because community just locks the other tabs. The login path only decides how the browser gets in.

## Auth

`access_path` picks the way in:

- `direct` (the default): CoStar username and password.
- `hilton_lobby`: Hilton SSO with an authenticator secret.

For `direct`, `auth_method` defaults to `password`. The run opens a headed browser, starts at https://product.costar.com/, and types the username and password. If CoStar shows "Verify it's you", the password is submitted once. A rejection stops the run.

Other taps keep `auth_method: storage_state` and are unchanged.

Meltano encrypts the sensitive settings:

```bash
uv run meltano config set tap-str username 'you@example.com'
uv run meltano config set tap-str password --from-file -
```

## Quick start

```bash
uv sync
uv run playwright install chromium
uv run meltano config set tap-str username 'you@example.com'
uv run meltano config set tap-str password --from-file -

uv run meltano install
uv run meltano invoke tap-str --discover
uv run meltano --environment=local run tap-str target-jsonl
```

## Workshop

See aria-singer-playwright `.cursor/skills/playwright-scraper-workshop/SKILL.md`. Add briefs under `briefs/`.
