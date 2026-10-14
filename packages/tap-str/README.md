# tap-str

Singer tap for the CoStar STR daily grid.

`auth_method` is `password`. A run opens a headed browser, signs in at https://product.costar.com/, and reads the `PortfolioListingAll` calls the daily page makes. Toggling Last Year records the prior-year window.

```bash
uv run meltano config set tap-str username 'you@example.com'
uv run meltano config set tap-str password 'your-password'
uv run meltano invoke tap-str --discover
```
