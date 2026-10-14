# Security

What the starter protects, how, and where the switch is. Everything here is
covered by tests; the file names point at the code.

## 1. Threat model, briefly

A public-facing API used by a single-page application, holding personal data and
operator-managed configuration. Attackers are anonymous internet clients (credential
stuffing, enumeration, injection through GraphQL, file abuse), authenticated users
reaching beyond their role, and misconfiguration reaching production.

## 2. Configuration cannot be unsafe by accident

`config/checks.py` runs at the end of `config/settings/production.py` and refuses to
start — listing **every** problem at once — when:

- `SECRET_KEY` is the insecure default or shorter than 50 characters
- `DEBUG` is on, `ALLOWED_HOSTS` is empty or contains `*`
- `ENCRYPTION_KEYS` is empty (stored secrets would be plaintext)
- the database password is empty or `postgres`
- `CORS_ALLOWED_ORIGINS` is empty or lists localhost
- `EXPOSE_ERROR_DETAILS`, `LDAP_SIMULATE_DIRECTORY` or `SMS_ALLOW_LOG_PROVIDER` is on
- `AXES_ENABLED` is off
- batching is on without a batch limit; SSO is on without a signing key

`SKIP_PRODUCTION_CHECKS=1` exists only for build-time commands that need the
production module but no secrets; never set it on a running service.
`.env.example` marks every variable production requires.

## 3. Authentication

**Tokens.** OAuth2 (`django-oauth-toolkit`): the first-party application uses the
password grant at `POST /oauth2/access_token`; only `password` and `refresh_token`
are served there. Access tokens expire in an hour, refresh tokens in a day
(configurable). `FirstPartyOAuth2Backend` (`apps/authentication/backends.py`)
admits a bearer token to the API only when it carries a first-party scope; the
identity tokens the SSO module mints for other systems are refused, so an
external system can never use a token as an API key. Every DRF view needs a
first-party token unless it is on the `PUBLIC_DRF_VIEWS` allow-list of
`tests/test_security_guards.py` (currently the public-file lookup).

**Account gates** (`apps/authentication/gates.py`): deactivated, blocked,
suspended and unverified accounts are refused at sign-in **and on every bearer
request** (`oauth_validator.validate_bearer_token`). Flipping a gate revokes the
account's live sessions.

**Brute force.** Two independent defences with different thresholds:
`django-axes` locks the *account* after `AXES_FAILURE_LIMIT` wrong passwords
wherever they come from (`AXES_USERNAME_CALLABLE` reads both the `username`
credential the password grant sends and the `email` field — without it the lockout
never engages); the `login` rate limit slows an *address* with a much higher ceiling,
because one address is often a whole office. OTP attempts, MFA tickets and every
throttle live in the cache (`apps/core/ratelimit.py`), so a resolver that raises and
rolls back its transaction cannot reset them.

**Two-factor** (`apps/authentication/mfa_services.py`): TOTP secrets encrypted at
rest; the accepted time step is burnt with a conditional `UPDATE` so a code cannot
be replayed even in a race; ten single-use recovery codes stored hashed; an
administrator can reset a locked-out person (which signs them out everywhere);
policy can require 2FA by account type or role, and a session that must comply can
call only the enrolment operations (`RestrictedSessionMiddleware`).

**Passwords** (`apps/authentication/password_services.py`, `password_validators.py`):
Django's validators plus minimum length, history (`PASSWORD_HISTORY_COUNT`),
expiry (`PASSWORD_MAX_AGE_DAYS`) and an optional k-anonymity check against Have I
Been Pwned. Reset and invitation links carry a random token stored **hashed**,
single-use, with a short TTL; a new request retires the previous link; setting a
password signs the account out everywhere. Forgot-password answers the same
message whether or not the address exists.

**External sign-in** (`external_services.py`): ID tokens from configured OpenID
Connect issuers are verified against the issuer's JWKS (cached), with audience,
issuer, expiry and nonce checks; `alg=none` is refused. An existing account is
linked only on a *verified* e-mail claim; a provider can be limited to allowed
domains and to existing accounts only.

**Directory sign-in** (`ldap.py`, `directory_services.py`): bind passwords
encrypted at rest; the simulated directory is a development aid that production
refuses.

**Sessions and audit**: session list with device fingerprints, remote sign-out,
new-device e-mail alert, login history, and an authentication event trail whose
*failure* rows are written after the request so a rollback cannot erase them
(`apps/core/context.after_request`).

## 4. Authorisation

`apps/rbac`: a declarative permission catalogue (each app's `permissions.py`),
roles, a locked system role that holds everything. `@has_permission(...)` on a
resolver checks codes and account types; results are cached per request. Guard
tests fail the build when a resolver requires an undeclared code or a declared
code is never checked, so there is no dead or unreachable permission.

Records answer **404, not 403**, to people who may not see them (files, workflow
items), so the API cannot be used to discover what exists.

## 5. The API surface

- **Error text never leaks.** An unexpected exception answers code `9004`, a
  generic message and the request id; the traceback goes to the endpoint log
  (`apps/audit`) for someone with `can_view_endpoint_logs`. `EXPOSE_ERROR_DETAILS`
  turns the text on in development only.
- **Query limits** (`apps/core/graphql/limits.py`): depth, complexity (fields
  weighted by list-ness and pagination size), aliases, root fields, batch size,
  page size — checked before execution, configurable from the environment.
- Introspection and GraphiQL only when `GRAPHIQL` is on.
- Inputs logged by the endpoint log are **sanitised**: any key that looks like a
  secret (`password`, `token`, `secret`, `apiKey`, `otp`, `codeHash`, … in
  snake_case or camelCase) is replaced by `[REDACTED]`; multipart bodies are never
  read.
- `X-Request-ID` is accepted or minted, put on every log line and echoed on the
  response, so a user's screenshot leads to the exact log rows.
- Headers: `Content-Security-Policy` (strict for JSON, workable for admin and
  GraphiQL pages), `Permissions-Policy`, HSTS, secure cookies, SSL redirect in
  production; the trusted-proxy count decides which `X-Forwarded-For` hop is the
  client.
- CORS is an explicit allow-list.

## 6. Files

`apps/attachments`: a file's address is not a permission. Every read — by id or
by path — asks the **policy of the file's purpose** whether this person may open
it (`policies.py`); the built-ins are *owner or `can_view_all_attachments`*,
*any signed-in person* (profile photos), *anyone* (public files, which need
`can_publish_public_files` to create). Path lookups refuse `..`, backslashes and
absolute paths. Uploads are size-limited before they are read, content-sniffed
against the extension (a `.pdf` must start like a PDF, a `.docx` must contain
Word parts), images are re-encoded through Pillow (dropping EXIF and anything
that is not pixels), and optionally scanned by ClamAV (`CLAMAV_ADDRESS`, failing
closed by default). Downloads carry `X-Content-Type-Options: nosniff` and
`Cache-Control: no-store`.

## 7. Data at rest

- `apps/core/crypto.py`: Fernet with key rotation (`ENCRYPTION_KEYS`, first key
  encrypts, all decrypt); `manage.py rotate_secrets` re-encrypts every
  `*_encrypted` column on every model. Development without a key stores a marked
  passthrough that is upgraded to ciphertext once a key exists; production refuses
  to run without one.
- The change trail (`apps/audit/signals.py`) records before/after values of every
  audited model but masks fields that look like secrets, and never records
  `password`, tokens or recovery codes in clear.
- Soft delete keeps rows for the trail; personal data leaves through the explicit
  account-deletion flow (OTP-confirmed, cooling-off, purge task) and
  `exportMyData`.

## 8. Operational

- Health: `/healthz` touches nothing; `/readyz` checks the database, cache and
  (optionally) the broker and answers 503 without saying why.
- Logs: JSON in production, every line stamped with request id, user and
  environment; the endpoint log ages out by severity and unresolved errors are never
  purged; the daily error digest goes to the subscribed operators.
- Dependencies are pinned **with hashes** (`requirements/*.txt`, `make deps`) and
  audited (`make audit`) in CI; the services run as an unprivileged user under
  systemd hardening (`deploy/systemd`), and `deploy.sh` refuses to release an
  unsafe configuration.

## 9. Reporting

Treat a security finding as a defect with a private merge request: add a failing
test under the module's `tests/` that demonstrates it, fix it, and record the
status code or behaviour change in `docs/API.md` if a client can observe it.
