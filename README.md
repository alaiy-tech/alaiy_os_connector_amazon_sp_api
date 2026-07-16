### Alaiy Os Connector Sp Api

AlaiyOS Connector to interact with SP API

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app alaiy_os_connector_sp_api
```

### Configuration

App identity (the SP-API *application's* own credentials) lives in
`site_config.json` — never in DocTypes and never in the browser. The seller
never pastes a long-lived secret; they authorize via Amazon's consent screen
and only the returned refresh token is stored (encrypted).

Set the keys with `bench set-config`:

```bash
bench --site <site> set-config amazon_lwa_client_id     "amzn1.application-oa2-client...."
bench --site <site> set-config amazon_lwa_client_secret "amzn1.oa2-cs...."
bench --site <site> set-config amazon_sp_app_id         "amzn1.sp.solution...."
# Optional:
bench --site <site> set-config amazon_app_beta 1                 # Draft app -> version=beta on consent (default: 1)
bench --site <site> set-config amazon_consent_base_url "https://sellercentral.amazon.com"  # else region default
bench --site <site> set-config app_url "https://erp.example.com" # else the site URL; used to build the OAuth redirect
```

| Key | Required | Purpose |
| --- | --- | --- |
| `amazon_lwa_client_id` | yes | Login with Amazon application client id |
| `amazon_lwa_client_secret` | yes | Login with Amazon application client secret |
| `amazon_sp_app_id` | yes | SP-API application id (consent screen) |
| `amazon_app_beta` | no | `1` while the app is in Draft (adds `version=beta`) |
| `amazon_consent_base_url` | no | Seller Central consent host; falls back to the region default |
| `app_url` | no | Base URL for the OAuth redirect URI; falls back to the site URL |

Register the redirect URL in Seller Central → Develop Apps as
`{app_url}/amazon-oauth/callback`.

The **Amazon Connection** form shows which required keys are still missing and
only enables **Connect** once all are set (see `alaiy_os_connector_sp_api.app_config`).

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/alaiy_os_connector_sp_api
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

agpl-3.0
