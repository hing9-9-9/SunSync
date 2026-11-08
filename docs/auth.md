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
| 2 | The server remembers the login (state, nonce, PKCE) for 10 minutes, gives this browser the short-lived cookie `sunsync_login` and sends it to Google |
| 3 | The user picks an account at Google |
| 4 | Google sends the browser to `/api/auth/google/callback` with a one-time code |
| 5 | The server checks that the callback carries the `sunsync_login` cookie of the login that started it, gives the code to Google, gets the ID token back and checks it: issuer, audience (our client id), expiry, nonce and a verified e-mail |
| 6 | The server saves the user (Google id, e-mail, name), creates a session and sets the cookie `sunsync_session` |
| 7 | The browser goes to `next`. If anything fails it goes to `/?login=failed` |

The ID token comes straight from Google over TLS, so its signature is not checked (OpenID Connect
allows this); the claims above are. The cookie is `HttpOnly` and `SameSite=Lax`. Over https it is also
`Secure` and called `__Host-sunsync_session` (the prefix makes browsers take it only from this exact
host); over plain http, for local tests, it is `sunsync_session`. The database keeps only the hash of
the cookie value, and the login answers are sent with `Cache-Control: no-store`.

The `sunsync_login` cookie ties a login to the browser that started it, so a callback address made
in another browser (with someone else's Google account) cannot log this browser in as that person.
It is removed when the callback comes back, whether the login worked or not. A login that is
started in one browser and finished in another therefore fails with `/?login=failed`.

The phone logs in on its own: the phone browser that opens the QR link (handoff) or the installed app
has its own cookie, so a person who is logged in on the computer still has to log in on the phone.

## Sessions

- A session ends after **30 minutes without use** and in any case **12 hours** after the login.
  Every request counts as use; the time is written at most once a minute.
- The numbers can be changed with `SUNSYNC_SESSION_IDLE_MINUTES` and `SUNSYNC_SESSION_MAX_HOURS`.
- Requests that change data must come from this site: a browser `Origin` of another site gets 403.
- At most 1000 logins can wait for Google at the same time (they are removed after 10 minutes), because the login route is open to everybody; beyond that it answers 429.

## Routes (the contract for the screen)

| Route | Answer |
|---|---|
| `GET /api/auth/session` | Logged in: `200 {"auth_enabled": true, "user": {"name", "email"}}`. Not logged in or expired: `401 {"detail": "로그인이 필요합니다."}`. Login off: always `200` with `"auth_enabled": false` and a development user |
| `GET /api/auth/check` | For Caddy only: `204` with a valid session, `401` in every other case, **also when the login is off** |
| `GET /api/auth/google/login?next=...` | `302` to Google. `next` must be an address inside this site (otherwise `/`). `404` when the login is off |
| `GET /api/auth/google/callback` | Called by Google. `302` to `next` with the cookie, or to `/?login=failed` |
| `POST /api/auth/logout` | `204` and the cookie is removed |
| `GET /api/health` | Always open |
| every other `/api` route | `401` with the same text when there is no valid session |

`/measure` is not part of the app. Caddy asks `GET /api/auth/check` for every `/measure`
request (`forward_auth`) and lets it through only when the answer is 2xx. Because `check` is 401 also
when the login is off, a Caddyfile that is switched too early closes `/measure` instead of opening it.

## Settings

| Variable | Meaning |
|---|---|
| `SUNSYNC_AUTH` | `google` (any case) switches the login on; empty or `off` keeps it off; any other value stops the server at start-up |
| `SUNSYNC_GOOGLE_CLIENT_ID`, `SUNSYNC_GOOGLE_CLIENT_SECRET` | From the Google console. The secret stays only in `/etc/sunsync/sunsync.env` |
| `SUNSYNC_PUBLIC_URL` | The address people use, for example `https://YOUR_NAME.duckdns.org`, without a slash at the end |
| `SUNSYNC_SESSION_IDLE_MINUTES`, `SUNSYNC_SESSION_MAX_HOURS` | Optional, default 30 and 12 |

When `SUNSYNC_AUTH=google` is set without the client id, the secret or the public address, the
API refuses to start and says which one is missing. At start-up the log says whether the login is
on (`sudo journalctl -u sunsync-api | grep 'Google login'`).

## Who can use it

**Logging in is only the entrance.** Until the spaces are separated by account (issue #106), every
logged-in user sees, changes and deletes all spaces. The only limit on who can log in is the Google
consent screen: while it is in **Testing**, only the listed test users can. The server has no list of
allowed e-mail addresses (decided to leave it for later), so if the consent screen is ever published,
every Google account can enter. Keep the app in Testing and keep the test users list short.

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
2. In `/etc/caddy/Caddyfile` (the server copy is not updated by the automatic update) add the `(login)` block from `deploy/Caddyfile` next to the `(locked)` block. Then take the temporary password away: remove `import locked` from `/api/*`, and in `/measure/*` replace `import locked` with `import login`. Then `caddy validate --config /etc/caddy/Caddyfile` and `sudo systemctl reload caddy`.
3. Check in a private window: the landing page opens; going into a space leads to Google; after the login the page opens again; after 30 minutes without use the login is asked again.
4. Check with `curl -i https://YOUR_NAME.duckdns.org/api/ui/spaces` (401) and `curl -i https://YOUR_NAME.duckdns.org/measure/health` (401).

Do step 1 before step 2. If a request that has the right session still gets 401 on `/measure`, look at the `uri` in the `(login)` block: it must be `/api/auth/check`.

To go back: remove `SUNSYNC_AUTH` from the file, restart the API, and put `import locked` back in
the `Caddyfile`.

## When something is wrong

| Sign | Likely reason |
|---|---|
| Google says `redirect_uri_mismatch` | The redirect URI in the console is not exactly `SUNSYNC_PUBLIC_URL` + `/api/auth/google/callback` |
| Google says the app is blocked or not verified | The account is not under **Test users** |
| Back at `/?login=failed` | The login took longer than 10 minutes, was used twice, or Google refused the code; look at `journalctl -u sunsync-api` |
| Logged in but every call is 401 | The cookie is not kept: `SUNSYNC_PUBLIC_URL` is not the address in the browser, or the site is opened over `http` while the cookie is `Secure` |
| Google answers fine but the page says `login=failed` many times in a row, or the login route answers 429 | Too many logins are waiting (flood or a loop); they are removed after 10 minutes |
| `/measure` always 401 | `forward_auth` does not reach the API: check the port in the `login` snippet and `caddy validate` |

## What is stored

For each user: the Google id, the e-mail, the name and the times of the first and the last login.
Nothing else from Google is kept, and the Google token itself is not stored.
