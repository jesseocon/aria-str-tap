# tap-str

Singer tap for CoStar STR's STAR tab. It has three streams:

- `str_series`: every value from every STAR view;
- `comp_set_members`: the comp set's hotels;
- `comp_set_response`: which comp set hotels reported.

A run opens a headed browser, signs in, and reads the same GraphQL calls the STR app makes.

`access_path` picks how the browser signs in. Both paths load the same data:

- `direct` types the CoStar `username` and `password` at https://product.costar.com/.
- `hilton_lobby` signs in through Hilton SSO, using `portal_username`, `portal_password` and `portal_totp_secret`, then follows the CoStar Benchmark link.

```bash
uv run meltano config set tap-str username 'you@example.com'
uv run meltano config set tap-str password 'your-password'
uv run meltano invoke tap-str --discover
```
