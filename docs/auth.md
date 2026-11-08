# Google login

The server logs people in with their Google account. When the login is switched on, every `/api`
route except the health check and the login routes needs a logged-in user, and so does `/measure`.
The screen part is tracked in issue #121; the server part in issue #105.

The login is **off by default**: without `SUNSYNC_AUTH=google` nothing is checked, so local
development and the tests work as before.

## How a login goes

| Step | What happens |
|---|---|
| 1 | The page sends the browser to `/api/auth/google/login?next=<page to come back to>` |
| 2 | The server remembers the login (state, nonce, PKCE) for 10 minutes and sends the browser to Google |
| 3 | The user picks an account at Google |
| 4 | Google sends the browser to `/api/auth/google/callback` with a one-time code |
| 5 | The server gives the code to Google, gets the ID token back and checks it: issuer, audience (our client id), expiry, nonce and a verified e-mail |
| 6 | The server saves the user (Google id, e-mail, name), creates a session and sets the cookie `sunsync_session` |
| 7 | The browser goes to `next`. If anything fails it goes to `/?login=failed` |

The ID token comes straight from Google over TLS, so its signature is not checked (OpenID Connect
allows this); the claims above are. The cookie is `HttpOnly`, `SameSite=Lax` and `Secure` when
`SUNSYNC_PUBLIC_URL` starts with `https://`. The database keeps only the hash of the cookie value.

## Sessions

- A session ends after **30 minutes without use** and in any case **12 hours** after the login.
  Every request counts as use; the time is written at most once a minute.
- The numbers can be changed with `SUNSYNC_SESSION_IDLE_MINUTES` and `SUNSYNC_SESSION_MAX_HOURS`.
- Requests that change data must come from this site: a browser `Origin` of another site gets 403.

## Routes (the contract for the screen)

| Route | Answer |
|---|---|
| `GET /api/auth/session` | Logged in: `200 {"auth_enabled": true, "user": {"name", "email"}}`. Not logged in or expired: `401 {"detail": "로그인이 필요합니다."}`. Login off: always `200` with `"auth_enabled": false` and a development user |
| `GET /api/auth/google/login?next=...` | `302` to Google. `next` must be an address inside this site (otherwise `/`). `404` when the login is off |
| `GET /api/auth/google/callback` | Called by Google. `302` to `next` with the cookie, or to `/?login=failed` |
| `POST /api/auth/logout` | `204` and the cookie is removed |
| `GET /api/health` | Always open |
| every other `/api` route | `401` with the same text when there is no valid session |

`/measure` is not part of the app. Caddy asks `GET /api/auth/session` for every `/measure`
request (`forward_auth`) and lets it through only when the answer is 200.

## Settings

| Variable | Meaning |
|---|---|
| `SUNSYNC_AUTH` | `google` switches the login on; anything else keeps it off |
| `SUNSYNC_GOOGLE_CLIENT_ID`, `SUNSYNC_GOOGLE_CLIENT_SECRET` | From the Google console. The secret stays only in `/etc/sunsync/sunsync.env` |
| `SUNSYNC_PUBLIC_URL` | The address people use, for example `https://YOUR_NAME.duckdns.org`, without a slash at the end |
| `SUNSYNC_SESSION_IDLE_MINUTES`, `SUNSYNC_SESSION_MAX_HOURS` | Optional, default 30 and 12 |

When `SUNSYNC_AUTH=google` is set without the client id, the secret or the public address, the
API refuses to start and says which one is missing.

## Google console (once)

The menu names change now and then; look for the same item.

1. In `console.cloud.google.com` create a project `sunsync`.
2. OAuth consent screen: app name `SunSync`, your e-mail as support and contact address, audience **External**, publishing status **Testing**.
3. Add every Google account that has to log in under **Test users** (your own, and the accounts of people who will try the demo). Only these accounts can log in while the app is in testing.
4. Credentials → create **OAuth client ID** → type **Web application**.
5. Authorised redirect URI: `https://YOUR_NAME.duckdns.org/api/auth/google/callback`, exactly like the address in the browser and without a slash at the end. The JavaScript origins can stay empty.
6. Copy the client id and the client secret. Never put the secret in the repository, an issue or a chat.

If the address of the site changes, change the redirect URI here and `SUNSYNC_PUBLIC_URL` too.

## Switch it on at the server

Do this only after the screen change (issue #121) is deployed. With the old screen, the login on
the server would lock everybody out.

1. Add the four settings to `/etc/sunsync/sunsync.env` (see `deploy/env.example`), keep the mode `600`, and restart the API: `sudo systemctl restart sunsync-api`. Check `sudo journalctl -u sunsync-api -n 20 --no-pager` for a start without errors.
2. In `/etc/caddy/Caddyfile` take the temporary password away: remove `import locked` from `/api/*`, and in `/measure/*` replace `import locked` with `import login`. Then `caddy validate --config /etc/caddy/Caddyfile` and `sudo systemctl reload caddy`.
3. Check in a private window: the landing page opens; going into a space leads to Google; after the login the page opens again; after 30 minutes without use the login is asked again.
4. Check with `curl -i https://YOUR_NAME.duckdns.org/api/ui/spaces` (401) and `curl -i https://YOUR_NAME.duckdns.org/measure/health` (401).

To go back: remove `SUNSYNC_AUTH` from the file, restart the API, and put `import locked` back in
the `Caddyfile`.

## When something is wrong

| Sign | Likely reason |
|---|---|
| Google says `redirect_uri_mismatch` | The redirect URI in the console is not exactly `SUNSYNC_PUBLIC_URL` + `/api/auth/google/callback` |
| Google says the app is blocked or not verified | The account is not under **Test users** |
| Back at `/?login=failed` | The login took longer than 10 minutes, was used twice, or Google refused the code; look at `journalctl -u sunsync-api` |
| Logged in but every call is 401 | The cookie is not kept: `SUNSYNC_PUBLIC_URL` is not the address in the browser, or the site is opened over `http` while the cookie is `Secure` |
| `/measure` always 401 | `forward_auth` does not reach the API: check the port in the `login` snippet and `caddy validate` |

## What is stored

For each user: the Google id, the e-mail, the name and the times of the first and the last login.
Nothing else from Google is kept, and the Google token itself is not stored.
